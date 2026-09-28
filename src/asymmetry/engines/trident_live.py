"""Intraday monitoring for the trident — gate by gate, on closed candles only.

The scanner used to run once at 19:30, after the close, so everything it found was already a
day old and unactionable. This evaluates during the session and alerts at each gate.

Three rules shape the whole module.

**Closed candles only.** The doji test is a body-to-range ratio and the reclaim test is a
close; neither means anything on a bar that is still filling. A forming bar's high and low
only widen, so a body that looks like a doji at 10:03 can be a full-bodied invalidation by
10:15. Every frame is passed through `drop_forming_bar` before the detector sees it, and the
monitor wakes a settling delay *after* each boundary rather than on it.

**Gate 4 and gate 5 are separate alerts.** A full bar separates the doji from its
confirmation — thirty minutes on the anchor track. Collapsing them into one "trigger" throws
away the only operational advantage of running live at all: the warning. Gate 4 is *get
ready*, gate 5 is *the trade is live now*.

**Every failure is logged with its numbers.** With a sample this small the failure log is
worth more than the winners: it is the only evidence that can say whether a gate is doing
real work or merely starving the strategy. "Rejected: body too large" is an opinion.
"Rejected at gate 4: body 47.3% of range, limit 30.0%" is a measurement, and a season of
those is what tells you whether 30% is the right number.

Nothing here places an order, holds a credential that could, or ever will.
"""

from __future__ import annotations

import json
import time as _time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd
from loguru import logger

from ..config import DATA_DIR
from .trident import (
    GATE_NAMES,
    SESSION_CLOSE,
    SESSION_OPEN,
    GateEvent,
    TridentSettings,
    TridentSignal,
    _cost_r,
    detect_trident_setup,
    drop_forming_bar,
)
from .trident_universe import FetchReport, fetch_pair, resolve_universe

ALERT_PATH = DATA_DIR / "trident_alerts.jsonl"
FAILURE_PATH = DATA_DIR / "trident_failures.jsonl"

# How long after a candle boundary to wake. Yahoo stamps a bar with its start and publishes
# it a little after its window ends; polling exactly on the boundary reliably reads the bar
# as still forming and wastes the cycle.
SETTLE_SECONDS = 45


@dataclass
class Alert:
    """One gate-4 or gate-5 event, carrying everything needed to act on it or to audit it.

    At gate 4 the confirmation bar has not printed, so the entry is not yet known. The stop
    is: it is the doji's low, and it does not move. What is quoted there is therefore an
    explicitly **provisional** entry (the doji's close) with the arithmetic that follows from
    it, plus the level that invalidates the whole thing. Quoting a gate-4 entry as if it were
    final would be a fabricated price — the confirmation bar closes wherever it closes.
    """

    gate: int = 0
    kind: str = ""                    # "get ready" | "trigger"
    symbol: str = ""
    session: str = ""
    at: str = ""                      # the bar this was decided on, ISO
    interval: str = ""
    provisional: bool = False

    gap_high: float = 0.0
    gap_low: float = 0.0
    gap_midpoint: float = 0.0

    doji_open: float = 0.0
    doji_high: float = 0.0
    doji_low: float = 0.0
    doji_close: float = 0.0
    body_to_range_pct: float = 0.0

    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_pct: float = 0.0             # risk as a percentage of price
    reward_risk: float = 0.0
    cost_r: float = 0.0               # round-trip cost expressed in R, at this stop distance
    net_r_at_target: float = 0.0      # what the trade is actually worth if it works

    invalidated_if: str = ""
    swept_pool: str = ""
    sweep_bars_before_gap: int = 0
    sweep_to_midpoint_pct: float = 0.0
    note: str = ""

    @property
    def key(self) -> tuple[str, str, int, str]:
        """Identity for de-duplication: the same bar re-read on a later pass is the same
        alert, and a monitor that re-fires it every cycle is a monitor nobody watches."""
        return (self.symbol, self.session, self.gate, self.at)


@dataclass
class Failure:
    """One candidate that did not make it, and the numeric reason it did not."""

    symbol: str = ""
    session: str = ""
    gate: int = 0
    gate_name: str = ""
    at: str = ""
    reason: str = ""                  # always carries numbers
    bucket: str = ""                  # the coarse condition, for funnel counts
    interval: str = ""


@dataclass
class LiveScan:
    """One pass over the universe at one candle boundary."""

    as_of: date | None = None
    at: pd.Timestamp | None = None
    interval: str = "30m"
    alerts: list[Alert] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)
    events: list[GateEvent] = field(default_factory=list)
    fetch: FetchReport = field(default_factory=FetchReport)
    universe_size: int = 0
    liquidity_refused: int = 0
    elapsed_sec: float = 0.0
    suppressed: int = 0               # alerts trimmed by the per-session budget

    def funnel(self) -> list[tuple[int, str, int]]:
        """How many symbols reached each gate. The count that makes the gates arguable:
        a gate nothing ever reaches is not strict, it is unmeasurable."""
        reached: dict[int, set[str]] = {g: set() for g in GATE_NAMES}
        for event in self.events:
            if event.passed:
                reached[event.gate].add(event.symbol)
        return [(g, GATE_NAMES[g], len(reached[g])) for g in sorted(GATE_NAMES)]

    def gate_failure_counts(self) -> dict[int, int]:
        counts: dict[int, int] = {}
        for failure in self.failures:
            counts[failure.gate] = counts.get(failure.gate, 0) + 1
        return counts


# ── Candle boundaries ─────────────────────────────────────────────────────────


def candle_closes(cfg: TridentSettings, on: date) -> list[datetime]:
    """Every candle-close instant inside the kill zone, in exchange time.

    Derived from the kill zone rather than the whole session: outside the window the pattern
    is not the pattern, so there is nothing to evaluate and no reason to spend a pass on it.
    The last boundary is clamped to the session close, because a 15:30 bar is the closing
    print rather than a window and nothing trades after it.
    """
    day = datetime.combine(on, cfg.killzone_start)
    end = datetime.combine(on, min(cfg.killzone_end, SESSION_CLOSE))
    step = timedelta(minutes=cfg.interval_minutes)
    out, cursor = [], day + step
    while cursor <= end + step:
        if cursor.time() <= SESSION_CLOSE:
            out.append(cursor)
        cursor += step
    return out


def next_close_after(cfg: TridentSettings, now: datetime) -> datetime | None:
    """The next candle boundary strictly after `now`, or None once the window has passed."""
    for boundary in candle_closes(cfg, now.date()):
        if boundary > now:
            return boundary
    return None


# ── One pass ──────────────────────────────────────────────────────────────────


def _alert_from_trigger(signal: TridentSignal, cfg: TridentSettings) -> Alert:
    return Alert(
        gate=5, kind="trigger", symbol=signal.symbol,
        session=str(signal.entry_at.date()), at=signal.entry_at.isoformat(),
        interval=cfg.anchor_interval, provisional=False,
        gap_high=signal.gap_high, gap_low=signal.gap_low,
        gap_midpoint=round(signal.consequent_encroachment, 2),
        doji_high=signal.doji_high, doji_low=signal.doji_low,
        body_to_range_pct=signal.doji_body_pct,
        entry=signal.entry, stop=signal.stop, target=signal.target,
        risk_pct=signal.risk_pct, reward_risk=cfg.reward_risk,
        cost_r=round(signal.cost_r, 3),
        net_r_at_target=round(signal.net_r_at_target, 3),
        invalidated_if=f"a close back below {signal.stop:,.2f} (the doji low)",
        swept_pool=signal.swept_pool,
        sweep_bars_before_gap=signal.sweep_bars_before_gap,
        sweep_to_midpoint_pct=signal.sweep_to_midpoint_pct,
        note=signal.note,
    )


def _alert_from_doji(event: GateEvent, cfg: TridentSettings) -> Alert | None:
    """The get-ready. Built from the gate-4 event, because at that moment no signal exists —
    the confirmation bar has not printed and there is nothing to build one from."""
    d = event.detail
    doji_low, doji_high = float(d.get("doji_low", 0.0)), float(d.get("doji_high", 0.0))
    doji_close = float(d.get("doji_close", 0.0))
    if doji_low <= 0 or doji_close <= 0 or doji_close <= doji_low:
        return None

    # Provisional arithmetic off the doji's own close. The real entry is the *next* bar's
    # close, which cannot be known yet; what is fixed is the stop and the invalidation.
    risk = doji_close - doji_low
    risk_pct = risk / doji_close * 100
    cost_r = _cost_r(risk_pct, cfg)
    return Alert(
        gate=4, kind="get ready", symbol=event.symbol,
        session=str(event.session), at=event.at.isoformat() if event.at is not None else "",
        interval=cfg.anchor_interval, provisional=True,
        gap_high=round(float(d.get("gap_high", 0.0)), 2),
        gap_low=round(float(d.get("gap_low", 0.0)), 2),
        gap_midpoint=round(float(d.get("midpoint", 0.0)), 2),
        doji_open=round(float(d.get("doji_open", 0.0)), 2),
        doji_high=round(doji_high, 2), doji_low=round(doji_low, 2),
        doji_close=round(doji_close, 2),
        body_to_range_pct=round(float(d.get("body_pct", 0.0)), 1),
        entry=round(doji_close, 2), stop=round(doji_low, 2),
        target=round(doji_close + cfg.reward_risk * risk, 2),
        risk_pct=round(risk_pct, 3), reward_risk=cfg.reward_risk,
        cost_r=round(cost_r, 3),
        net_r_at_target=round(cfg.reward_risk - cost_r, 3),
        invalidated_if=(
            f"the next candle closes at or above the doji high {doji_high:,.2f}, "
            f"or trades below {doji_low:,.2f}"
        ),
        note=(
            f"PROVISIONAL — the confirmation candle has not closed. Entry shown is the "
            f"doji's own close; the real entry is the next {cfg.anchor_interval} close, "
            f"wherever it lands below {doji_high:,.2f}. Stop and invalidation are fixed."
        ),
    )


def _rank_and_cap(alerts: list[Alert], cfg: TridentSettings) -> tuple[list[Alert], int]:
    """Keep the best `max_alerts_per_session`, ranked by net R after costs.

    Not by raw setup quality, and not by stop tightness. Cost in R is (cost% / stop%), so on
    the 5-minute track a 0.12% stop hands back more than 1.4R before the trade starts while a
    0.5% stop hands back 0.34R. Two setups with identical geometry are not equally worth
    taking, and ranking by anything that ignores the cost puts the worst ones at the top —
    which matters precisely because the tightest stops look most attractive on every other
    measure.
    """
    triggers = [a for a in alerts if a.gate == 5]
    ready = [a for a in alerts if a.gate == 4]
    triggers.sort(key=lambda a: -a.net_r_at_target)
    ready.sort(key=lambda a: -a.net_r_at_target)
    # Triggers outrank get-readies for the budget: one is actionable now, the other may never
    # become actionable at all.
    ordered = triggers + ready
    kept = ordered[: cfg.max_alerts_per_session]
    return kept, max(0, len(ordered) - len(kept))


def scan_once(
    cfg: TridentSettings,
    *,
    symbols: list[str] | None = None,
    universe: str = "nifty500",
    max_symbols: int = 0,
    on: date | None = None,
    now: pd.Timestamp | None = None,
    apply_liquidity: bool = True,
) -> LiveScan:
    """Evaluate every symbol once, on closed candles only.

    `now` is injectable so a pass can be replayed against a historical session without
    depending on the clock. It does two things: bars stamped after it are discarded outright,
    and the bar whose window straddles it is dropped as still forming. Live, only the second
    has any effect.
    """
    from ..data import nse_archive

    started = _time.monotonic()
    session = on or nse_archive.last_trading_day() or date.today()
    scan = LiveScan(as_of=session, interval=cfg.anchor_interval)

    names, screen = resolve_universe(
        cfg, universe=universe, symbols=symbols, max_symbols=max_symbols,
        as_of=session, apply_liquidity=apply_liquidity,
    )
    scan.universe_size = len(names)
    if screen is not None:
        scan.liquidity_refused = len(screen.refused) + len(screen.missing)
    if not names:
        logger.error("[trident-live] empty universe — nothing to evaluate")
        scan.elapsed_sec = _time.monotonic() - started
        return scan

    for position, symbol in enumerate(names, 1):
        intraday, daily = fetch_pair(symbol, cfg, scan.fetch, as_of=session)
        if intraday is None or daily is None:
            continue

        # Closed candles only, in two steps.
        #
        # First, nothing after `now` exists yet. Live this is a no-op — the feed only serves
        # bars up to the present — but it is what makes a pass replayable against a finished
        # session, and without it `scan_once(now=...)` silently evaluated the whole day and
        # reported the afternoon's setups as though it were standing in the morning.
        #
        # Second, the newest bar is dropped while its own window is still filling. A forming
        # bar's high and low only widen, so a body that reads as a doji mid-candle can be a
        # full-bodied invalidation by its close.
        stamp = _align(now, intraday)
        if stamp is not None:
            intraday = intraday[intraday.index <= stamp]
            if intraday.empty:
                continue
        intraday = drop_forming_bar(intraday, cfg.interval_minutes, stamp)
        if intraday is None or intraday.empty:
            continue

        events: list[GateEvent] = []
        signal = detect_trident_setup(intraday, daily, session, cfg, symbol, sink=events)
        scan.events.extend(events)
        scan.at = intraday.index[-1]

        if signal.found:
            scan.alerts.append(_alert_from_trigger(signal, cfg))
        else:
            scan.failures.append(_failure_from(signal, session, cfg, intraday))

        # The get-ready is emitted whether or not the trade eventually triggered, so the
        # record can later answer "how many get-readies became trades" — which is the number
        # that says whether the warning is worth acting on.
        last_bar = intraday.index[-1]
        for event in events:
            if event.gate == 4 and event.passed:
                alert = _alert_from_doji(event, cfg)
                if alert is None:
                    continue
                # Live, a doji on the newest closed bar is genuinely pending. An older one
                # has already had its confirmation bar and been decided, so re-alerting it
                # would be noise.
                if event.at is not None and event.at == last_bar:
                    scan.alerts.append(alert)

        if position % 25 == 0:
            logger.info(
                f"[trident-live] {position}/{len(names)} scanned, "
                f"{len(scan.alerts)} alert(s)"
            )

    scan.alerts, scan.suppressed = _rank_and_cap(scan.alerts, cfg)
    scan.elapsed_sec = _time.monotonic() - started

    if scan.fetch.failed:
        logger.warning(f"[trident-live] {scan.fetch.summary()}")
    if scan.elapsed_sec > cfg.interval_minutes * 60:
        # A pass that outlives its own candle cannot be a per-candle monitor: by the time it
        # finishes, the bar it was evaluating is two bars old. Said out loud rather than left
        # for the reader to notice from timestamps.
        logger.error(
            f"[trident-live] a pass took {scan.elapsed_sec:.0f}s against a "
            f"{cfg.interval_minutes}-minute candle — the monitor is behind the market. "
            "Reduce the universe or raise the interval."
        )
    return scan


def _align(now: pd.Timestamp | None, frame: pd.DataFrame) -> pd.Timestamp | None:
    """Put an injected `now` into the frame's timezone. None passes straight through, which
    lets `drop_forming_bar` fall back to the real clock."""
    if now is None or frame is None or frame.empty:
        return now
    tz = frame.index.tz
    if tz is None:
        return now.tz_localize(None) if now.tzinfo is not None else now
    return now.tz_localize(tz) if now.tzinfo is None else now.tz_convert(tz)


def _failure_from(
    signal: TridentSignal, session: date, cfg: TridentSettings, intraday: pd.DataFrame
) -> Failure:
    return Failure(
        symbol=signal.symbol,
        session=str(session),
        gate=signal.gate_reached,
        gate_name=GATE_NAMES.get(signal.gate_reached, "unknown"),
        at=str(intraday.index[-1]) if len(intraday) else "",
        reason=signal.gate_reason or signal.note,
        bucket=signal.rejected_by or "unknown",
        interval=cfg.anchor_interval,
    )


# ── Persistence ───────────────────────────────────────────────────────────────


def _append(path, rows: list) -> int:
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(asdict(row), default=str) + "\n")
    return len(rows)


def _existing_alert_keys() -> set[tuple[str, str, int, str]]:
    if not ALERT_PATH.exists():
        return set()
    keys = set()
    for line in ALERT_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        keys.add((row["symbol"], row["session"], int(row["gate"]), row["at"]))
    return keys


def persist(scan: LiveScan) -> tuple[int, int]:
    """Append new alerts and every failure. Returns (alerts written, failures written).

    Alerts are de-duplicated against what is already on disk, because the monitor re-reads
    the same closed bars on every pass and an alert fired once per cycle is an alert that
    gets muted. Failures are *not* de-duplicated by bar — the same symbol failing at gate 2
    at 10:00 and again at 10:30 is two observations of the window filling up, and collapsing
    them would lose the shape of the day.
    """
    seen = _existing_alert_keys()
    fresh = [a for a in scan.alerts if a.key not in seen]
    return _append(ALERT_PATH, fresh), _append(FAILURE_PATH, scan.failures)


def load_alerts() -> list[dict]:
    if not ALERT_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in ALERT_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_failures() -> list[dict]:
    if not FAILURE_PATH.exists():
        return []
    return [
        json.loads(line)
        for line in FAILURE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── The loop ──────────────────────────────────────────────────────────────────


def monitor(
    cfg: TridentSettings,
    *,
    universe: str = "nifty500",
    max_symbols: int = 0,
    on_scan=None,
    once: bool = False,
    sleeper=_time.sleep,
    clock=datetime.now,
    apply_liquidity: bool = True,
) -> list[LiveScan]:
    """Run a pass at each candle close inside the kill zone, until the window ends.

    `sleeper` and `clock` are injected so the loop can be exercised without waiting on real
    time. `on_scan` receives each completed pass, which is how the CLI renders progress
    without this module importing a renderer.

    The universe is resolved **once**, before the first pass. The liquidity screen reads
    twenty bhavcopies; re-running it every candle would spend the session's whole time budget
    on a number that cannot change intraday.
    """
    scans: list[LiveScan] = []
    started = clock()
    session = started.date()

    names, screen = resolve_universe(
        cfg, universe=universe, max_symbols=max_symbols, as_of=session,
        apply_liquidity=apply_liquidity,
    )
    if not names:
        logger.error("[trident-live] liquidity screen admitted nothing — monitor not started")
        return scans
    logger.info(
        f"[trident-live] monitoring {len(names)} names on {cfg.anchor_interval} candles, "
        f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M}"
    )

    while True:
        now = clock()
        boundary = next_close_after(cfg, now)
        if boundary is None:
            logger.info("[trident-live] kill zone closed; monitor finished for the day")
            break

        wake = boundary + timedelta(seconds=SETTLE_SECONDS)
        delay = (wake - now).total_seconds()
        if delay > 0:
            logger.info(
                f"[trident-live] next candle closes {boundary:%H:%M}; "
                f"waking {SETTLE_SECONDS}s after"
            )
            sleeper(delay)

        scan = scan_once(
            cfg, symbols=names, on=session, now=pd.Timestamp(clock()),
            apply_liquidity=False,
        )
        persist(scan)
        scans.append(scan)
        if on_scan is not None:
            on_scan(scan)
        if once:
            break
    return scans


def session_window_note(cfg: TridentSettings) -> str:
    """One line stating what the monitor can and cannot keep up with.

    Printed on every start because it is the constraint most likely to be forgotten: Yahoo is
    paced at ~1.2s a symbol, so a pass over N names costs roughly 1.2N seconds and a
    5-minute candle gives 300 of them.
    """
    budget = cfg.interval_minutes * 60
    capacity = int(budget / 1.2)
    return (
        f"A pass costs roughly 1.2s per symbol (Yahoo's pacing floor), so a "
        f"{cfg.interval_minutes}-minute candle affords about {capacity} names before the "
        f"monitor falls behind the market. Session {SESSION_OPEN:%H:%M}–{SESSION_CLOSE:%H:%M}."
    )
