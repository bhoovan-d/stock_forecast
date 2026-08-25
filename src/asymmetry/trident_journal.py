"""Forward record for the trident strategy — what it flagged, and what happened next.

The measurement in `docs/spec-trident.md` says plainly what it cannot do: ~60 sessions of
30-minute history against a pattern that fires roughly a third of a time per session is not
enough to separate a real hit rate from noise, and no amount of re-running history will fix
that because the history does not exist. The only remedy is **forward-collected data**, so
this module collects it.

It is deliberately not the V3 `journal` module. That one is bound to `ScanResult`, keeps its
own outcome vocabulary and settles against bhavcopy; wiring this into it would put two
strategies with different geometry through one settlement path, which is the same mistake as
sharing a cost constant. This writes a plain JSONL file instead — appendable, greppable,
no migration, and readable without the project loaded.

**Resolution is not reimplemented here.** Every open record is settled by handing a
reconstructed signal to `trident.resolve_trade`, the same function the backtest uses, so the
forward record and the replay cannot disagree about what counts as a win. That includes both
rules that keep the replay honest: a bar touching stop and target books a loss, and a gap
through a level resolves at the open rather than the level.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd
from loguru import logger

from .config import DATA_DIR
from .engines.trident import (
    TridentResult,
    TridentSettings,
    TridentSignal,
    TridentTrade,
    _cost_r,
    resolve_trade,
)

WATCH_PATH = DATA_DIR / "trident_watch.jsonl"


@dataclass
class WatchRecord:
    """One flagged setup and its fate. Written once, updated in place when it resolves."""

    as_of: str = ""
    symbol: str = ""
    entry_at: str = ""             # ISO, tz-aware, as the feed stamps it
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_pct: float = 0.0
    reward_risk: float = 0.0       # frozen at record time — see `record`
    required_move_pct: float = 0.0
    feasible: bool = False
    note: str = ""

    outcome: str = "open"          # open | target | stop | time-stop
    resolved_on: str = ""
    sessions_held: int = 0
    realised_r: float = 0.0
    cost_r: float = 0.0
    gapped: bool = False

    @property
    def key(self) -> tuple[str, str]:
        """Identity is symbol plus the exact bar, so re-running a scan for the same session
        cannot double-count a setup."""
        return (self.symbol, self.entry_at)

    @property
    def net_r(self) -> float:
        return self.realised_r - self.cost_r


def load() -> list[WatchRecord]:
    if not WATCH_PATH.exists():
        return []
    records = []
    for line in WATCH_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(WatchRecord(**json.loads(line)))
    return records


def save(records: list[WatchRecord]) -> None:
    WATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    WATCH_PATH.write_text(
        "\n".join(json.dumps(asdict(r)) for r in records) + "\n", encoding="utf-8"
    )


def record(signals: list[TridentSignal], cfg: TridentSettings) -> int:
    """Append today's setups. Returns how many were new.

    The reward-to-risk ratio is **frozen into each record** rather than read from `cfg` at
    settlement. Changing `--rr` later must not retroactively move the target of a setup that
    was already flagged — a target you can move after the fact is not a target, and a forward
    test whose rules drift mid-flight measures nothing.
    """
    existing = load()
    seen = {r.key for r in existing}
    added = 0

    for s in signals:
        if not s.found or s.entry_at is None:
            continue
        candidate = WatchRecord(
            as_of=str(s.entry_at.date()),
            symbol=s.symbol,
            entry_at=s.entry_at.isoformat(),
            entry=s.entry,
            stop=s.stop,
            target=s.target,
            risk_pct=s.risk_pct,
            reward_risk=cfg.reward_risk,
            required_move_pct=s.required_move_pct,
            feasible=s.feasible,
            note=s.note,
            cost_r=round(_cost_r(s.risk_pct, cfg), 3),
        )
        if candidate.key in seen:
            continue
        existing.append(candidate)
        seen.add(candidate.key)
        added += 1

    if added:
        save(existing)
        logger.info(f"[trident-watch] recorded {added} new setup(s)")
    return added


def settle(cfg: TridentSettings | None = None) -> int:
    """Walk every open record forward and mark the ones that finished.

    Returns how many changed state. Fetches are per symbol and paced by the shared client;
    the open population is small by construction, so this stays cheap enough to run daily.
    """
    from .data import yahoo

    cfg = cfg or TridentSettings()
    records = load()
    open_records = [r for r in records if r.outcome == "open"]
    if not open_records:
        return 0

    changed = 0
    for r in open_records:
        ysym = yahoo.to_yahoo_symbol(r.symbol)
        m30 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.anchor_interval)
        daily = yahoo.fetch_chart(ysym, range_="2y", interval="1d")
        if m30 is None or m30.empty or daily is None or daily.empty:
            logger.warning(f"[trident-watch] no data to settle {r.symbol}")
            continue

        # Reconstructed only far enough for `resolve_trade` to price it. The record's own
        # reward_risk is used, not the caller's, for the reason given in `record`.
        signal = TridentSignal(
            symbol=r.symbol, found=True, entry_at=pd.Timestamp(r.entry_at),
            entry=r.entry, stop=r.stop, target=r.target, risk_pct=r.risk_pct,
            required_move_pct=r.required_move_pct, feasible=r.feasible,
        )
        settle_cfg = (
            cfg if cfg.reward_risk == r.reward_risk
            else TridentSettings(**{**cfg.__dict__, "reward_risk": r.reward_risk})
        )
        trade = resolve_trade(m30, daily, signal, settle_cfg)
        if trade is None or trade.outcome == "open":
            continue

        r.outcome = trade.outcome
        r.resolved_on = str(trade.resolved_at.date()) if trade.resolved_at is not None else ""
        r.sessions_held = trade.sessions_held
        r.realised_r = round(trade.realised_r, 3)
        r.gapped = trade.gapped
        changed += 1

    if changed:
        save(records)
        logger.info(f"[trident-watch] settled {changed} record(s)")
    return changed


def as_result(records: list[WatchRecord] | None = None) -> TridentResult:
    """Re-express the forward record as a `TridentResult`.

    Done so the win rate, its Wilson interval and the net expectancy come from exactly one
    implementation. A forward tracker that computed its own statistics would eventually
    report a different win rate from the backtest for the same trades.
    """
    records = load() if records is None else records
    result = TridentResult()
    result.setups_found = len(records)
    result.sessions_spanned = len({r.as_of for r in records})
    result.symbols_tested = len({r.symbol for r in records})
    for r in records:
        result.trades.append(
            TridentTrade(
                symbol=r.symbol, entered_at=pd.Timestamp(r.entry_at), entry=r.entry,
                stop=r.stop, target=r.target, risk_pct=r.risk_pct,
                required_move_pct=r.required_move_pct, feasible=r.feasible,
                outcome=r.outcome, sessions_held=r.sessions_held,
                realised_r=r.realised_r, cost_r=r.cost_r, gapped=r.gapped,
            )
        )
    return result
