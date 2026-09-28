"""Append-only-source forward record for the V3 strategy.

The JSONL file is separate from the legacy journal. Settlement delegates to the V3 replay's
resolver so ambiguous bars and time stops have one definition in live and historical data.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger

from .config import DATA_DIR, settings
from .v3_backtest import BacktestResult, Trade, resolve_forward, simulate_entry

if TYPE_CHECKING:
    from .engines.v3_scan import V3Scan

WATCH_PATH = DATA_DIR / "v3_watch.jsonl"


@dataclass
class V3Record:
    symbol: str = ""
    direction: str = "long"
    sector: str = ""
    setup: str = ""
    setup_label: str = ""
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    stop_pct: float = 0.0
    trigger_bar: str = ""
    entry_rule: str = ""
    entry_min: float = 0.0
    entry_max: float = 0.0
    entry_available: bool = False
    entry_reason: str = "waiting for fill"
    reward_risk: float = 0.0
    carry_score: float = 0.0
    admitted: bool = False
    catalyst_note: str = ""
    catalyst_score: float = 50.0
    quality_score: float = 0.0
    rank: int = 0
    published: bool = False
    quantity: int = 0
    position_value_inr: float = 0.0
    risk_inr: float = 0.0
    cost_r: float = 0.0
    probability_3r: float | None = None
    probability_4r: float | None = None
    probability_3r_lower: float | None = None
    probability_3r_upper: float | None = None
    probability_4r_lower: float | None = None
    probability_4r_upper: float | None = None
    probability_3r_n: int = 0
    probability_4r_n: int = 0
    probability_2r: float | None = None
    probability_2r_n: int = 0
    probability_2r_lower: float | None = None
    probability_2r_upper: float | None = None
    selected_probability: float | None = None
    conservative_probability: float | None = None
    expected_net_r: float | None = None
    historically_approved: bool = False
    model_version: int = 0
    rollout_phase: str = "paper"
    prediction_qualified: bool = False
    refused_by: str = ""
    refusal_reason: str = ""
    actual_fill: float | None = None
    actual_slippage_pct: float | None = None
    outcome: str = "open"
    resolved_at: str = ""
    bars_held: int = 0
    realised_r: float = 0.0
    mae_r: float = 0.0
    mfe_r: float = 0.0
    hit_2r: bool = False
    reached_2r_at: str = ""
    hit_3r: bool = False
    reached_3r_at: str = ""
    hit_4r: bool = False
    reached_4r_at: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return self.symbol, self.trigger_bar

    @property
    def net_r(self) -> float:
        return self.realised_r - self.cost_r


def load(path: str | Path | None = None) -> list[V3Record]:
    source = Path(path) if path is not None else WATCH_PATH
    if not source.exists():
        return []
    return [
        V3Record(**json.loads(line))
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save(records: list[V3Record], path: str | Path | None = None) -> None:
    destination = Path(path) if path is not None else WATCH_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "".join(json.dumps(asdict(record), sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )


def record(scan: V3Scan) -> int:
    """Record every plan-bearing candidate, including demoted control names."""
    from .engines.carry import gate_applies

    records = load()
    seen = {item.key for item in records}
    published_ids = {id(candidate) for candidate in scan.trades}
    candidates = list(scan.trades) + [
        candidate for candidate in scan.near_miss if candidate.plan is not None
    ]
    # Rank is frozen across the complete plan-bearing population, not re-numbered after the
    # publication cut.
    candidates.sort(key=lambda candidate: -candidate.score)
    added = 0
    for rank, candidate in enumerate(candidates, 1):
        plan = candidate.plan
        if plan is None or plan.trigger_bar is None:
            continue
        risk = abs(plan.entry - plan.stop)
        target = candidate.recommended_target or plan.target
        reward_risk = abs(target - plan.entry) / risk if risk > 0 else 0.0
        p3 = candidate.probability_3r
        p4 = candidate.probability_4r
        p2 = candidate.probability_2r
        selected = {
            2: p2, 3: p3, 4: p4,
        }.get(int(round(reward_risk)))
        published = id(candidate) in published_ids
        live_qualified = False
        if selected is not None and published and getattr(selected, "accepted", False):
            from .v3_probability import market_is_open, trigger_is_fresh

            live_qualified = (
                candidate.direction == "long"
                and not candidate.rejected_by
                and candidate.intraday_tier.startswith("LIVE")
                and plan.is_live
                and market_is_open()
                and trigger_is_fresh(plan.trigger_bar)
            )
        item = V3Record(
            symbol=candidate.symbol,
            direction=candidate.direction,
            sector=candidate.sector,
            setup=candidate.setup.kind.value,
            setup_label=candidate.setup_name,
            entry=plan.entry,
            stop=plan.stop,
            target=target,
            stop_pct=plan.stop_pct,
            trigger_bar=pd.Timestamp(plan.trigger_bar).isoformat(),
            entry_rule=plan.entry_rule,
            entry_min=plan.entry_min or plan.entry,
            entry_max=plan.entry_max or plan.entry,
            reward_risk=reward_risk,
            carry_score=candidate.carry.score,
            admitted=candidate.carry.passes or not gate_applies(candidate.setup.kind),
            catalyst_note=candidate.catalyst_note,
            catalyst_score=candidate.catalyst_score,
            quality_score=candidate.score,
            rank=rank,
            published=published,
            quantity=plan.quantity,
            position_value_inr=plan.position_value_inr,
            risk_inr=round(plan.quantity * risk, 2),
            cost_r=(settings.cost_roundtrip_pct + settings.slippage_pct) / plan.stop_pct,
            probability_3r=p3.probability if p3 is not None else None,
            probability_4r=p4.probability if p4 is not None else None,
            probability_3r_lower=getattr(p3, "lower", None) if p3 is not None else None,
            probability_3r_upper=getattr(p3, "upper", None) if p3 is not None else None,
            probability_4r_lower=getattr(p4, "lower", None) if p4 is not None else None,
            probability_4r_upper=getattr(p4, "upper", None) if p4 is not None else None,
            probability_3r_n=p3.n if p3 is not None else 0,
            probability_4r_n=p4.n if p4 is not None else 0,
            probability_2r=p2.probability if p2 is not None else None,
            probability_2r_n=p2.n if p2 is not None else 0,
            probability_2r_lower=getattr(p2, "lower", None) if p2 is not None else None,
            probability_2r_upper=getattr(p2, "upper", None) if p2 is not None else None,
            selected_probability=(
                getattr(selected, "probability", None) if selected is not None else None
            ),
            conservative_probability=(
                getattr(selected, "lower", None) if selected is not None else None
            ),
            expected_net_r=(
                getattr(selected, "expected_net_r", None) if selected is not None else None
            ),
            historically_approved=live_qualified,
            model_version=3 if p2 is not None else 0,
            rollout_phase=scan.rollout_phase,
            prediction_qualified=live_qualified,
            refused_by=candidate.rejected_by,
            refusal_reason=candidate.reject_detail,
        )
        if item.key in seen:
            continue
        records.append(item)
        seen.add(item.key)
        added += 1
    if added:
        save(records)
        logger.info(f"[v3-watch] recorded {added} new plan(s)")
    return added


def _entry_index(bars: pd.DataFrame, timestamp: str) -> int | None:
    wanted = pd.Timestamp(timestamp)
    for position, value in enumerate(bars.index):
        current = pd.Timestamp(value)
        if current.tzinfo is not None and wanted.tzinfo is not None:
            if current.tz_convert("UTC") == wanted.tz_convert("UTC"):
                return position
        elif current.tz_localize(None) == wanted.tz_localize(None):
            return position
    return None


def settle() -> int:
    """Settle open records against fresh 15-minute bars."""
    from .data import yahoo

    records = load()
    changed = 0
    dirty = False
    max_bars = settings.max_holding_sessions * 25
    for item in (record for record in records if record.outcome == "open"):
        bars = yahoo.fetch_chart(
            yahoo.to_yahoo_symbol(item.symbol), range_="60d", interval="15m"
        )
        if bars is None or bars.empty:
            logger.warning(f"[v3-watch] no 15m data to settle {item.symbol}")
            continue
        bars = bars.sort_index()
        signal_position = _entry_index(bars, item.trigger_bar)
        if signal_position is None:
            logger.warning(f"[v3-watch] trigger bar missing for {item.symbol}")
            continue
        if item.entry_rule and not item.entry_available:
            from types import SimpleNamespace

            fill_position, fill, reason = simulate_entry(
                bars, signal_position,
                SimpleNamespace(
                    entry=item.entry, entry_rule=item.entry_rule,
                    entry_min=item.entry_min, entry_max=item.entry_max,
                ),
            )
            if fill_position is None or fill is None:
                if len(bars) - signal_position > 4:
                    item.outcome, item.entry_reason = "unfilled", reason
                    changed += 1
                    dirty = True
                continue
            item.entry_available, item.entry_reason = True, reason
            item.entry = fill
            risk = abs(item.entry - item.stop)
            item.stop_pct = risk / item.entry * 100
            item.target = item.entry + item.reward_risk * risk
            item.position_value_inr = item.quantity * item.entry
            item.risk_inr = item.quantity * risk
            item.cost_r = (settings.cost_roundtrip_pct + settings.slippage_pct) / item.stop_pct
            position = fill_position
            # Persist the fill even when there are not yet enough later bars to settle it.
            # Otherwise the next monitor pass would simulate the order again from scratch.
            dirty = True
        else:
            # Legacy records predate order simulation and retain their original semantics.
            position = signal_position
        trade = Trade(
            symbol=item.symbol, direction=item.direction, setup=item.setup,
            signal_at=pd.Timestamp(item.trigger_bar), entered_at=pd.Timestamp(bars.index[position]),
            entry=item.entry, stop=item.stop,
            target=item.target, stop_pct=item.stop_pct, cost_r=item.cost_r,
            carry_passed=item.admitted, entry_available=item.entry_available or not item.entry_rule,
        )
        resolve_forward(bars, position, trade, max_bars, include_entry_bar=bool(item.entry_rule))
        available = len(bars) - position - 1
        if trade.outcome == "timeout" and available < max_bars:
            continue
        item.outcome = trade.outcome
        item.resolved_at = trade.resolved_at.isoformat() if trade.resolved_at is not None else ""
        item.bars_held = trade.bars_held
        item.realised_r = round(trade.realised_r, 4)
        item.mae_r = round(trade.mae_r, 4)
        item.mfe_r = round(trade.mfe_r, 4)
        item.hit_2r = trade.hit_2r
        item.reached_2r_at = trade.reached_2r_at.isoformat() if trade.reached_2r_at is not None else ""
        item.hit_3r = trade.hit_3r
        item.reached_3r_at = (
            trade.reached_3r_at.isoformat() if trade.reached_3r_at is not None else ""
        )
        item.hit_4r = trade.hit_4r
        item.reached_4r_at = trade.reached_4r_at.isoformat() if trade.reached_4r_at is not None else ""
        changed += 1
        dirty = True
    if dirty:
        save(records)
        logger.info(f"[v3-watch] settled {changed} record(s)")
    return changed


def as_result(records: list[V3Record] | None = None) -> BacktestResult:
    records = load() if records is None else records
    result = BacktestResult(
        symbols_tested=len({record.symbol for record in records}),
        sessions_spanned=len({pd.Timestamp(record.trigger_bar).date() for record in records}),
    )
    for item in records:
        result.trades.append(Trade(
            symbol=item.symbol, direction=item.direction, setup=item.setup,
            signal_at=pd.Timestamp(item.trigger_bar), entered_at=pd.Timestamp(item.trigger_bar),
            entry=item.entry, stop=item.stop,
            target=item.target, stop_pct=item.stop_pct, outcome=item.outcome,
            resolved_at=pd.Timestamp(item.resolved_at) if item.resolved_at else None,
            bars_held=item.bars_held, realised_r=item.realised_r, mae_r=item.mae_r,
            mfe_r=item.mfe_r, carry_passed=item.admitted, cost_r=item.cost_r,
            entry_available=item.entry_available or not item.entry_rule,
            entry_reason=item.entry_reason, hit_2r=item.hit_2r,
            reached_2r_at=pd.Timestamp(item.reached_2r_at) if item.reached_2r_at else None,
            hit_3r=item.hit_3r,
            reached_3r_at=pd.Timestamp(item.reached_3r_at) if item.reached_3r_at else None,
            hit_4r=item.hit_4r,
            reached_4r_at=pd.Timestamp(item.reached_4r_at) if item.reached_4r_at else None,
            score=item.quality_score, catalyst_score=item.catalyst_score,
            catalyst_note=item.catalyst_note, has_catalyst=bool(item.catalyst_note),
        ))
    return result


@dataclass
class RolloutStatus:
    phase: str = "paper"
    progress: str = "0 of 30 completed forward signals"
    completed: int = 0
    wins: int = 0
    probability: float = float("nan")
    lower: float = float("nan")
    target_r: int = 0
    reason: str = "collecting forward evidence"


def rollout_status(records: list[V3Record] | None = None) -> RolloutStatus:
    from .v3_probability import wilson_interval

    records = load() if records is None else records
    completed = [record for record in records if record.prediction_qualified
                  and record.outcome in {"target", "stop", "timeout"}]
    best = RolloutStatus(progress=f"{len(completed)} of {settings.v3_forward_min_signals} completed forward signals")
    for target_r in (2, 3, 4):
        group = [record for record in completed if round(record.reward_risk) == target_r]
        if not group:
            continue
        wins = sum(record.outcome == "target" for record in group)
        probability = wins / len(group)
        lower, _ = wilson_interval(wins, len(group))
        point_floor = {2: settings.v3_min_probability_2r, 3: settings.v3_min_probability_3r,
                       4: settings.v3_min_probability_4r}[target_r]
        conservative_floor = {2: settings.v3_min_conservative_2r, 3: settings.v3_min_conservative_3r,
                              4: settings.v3_min_conservative_4r}[target_r]
        candidate = RolloutStatus(
            completed=len(group), wins=wins, probability=probability, lower=lower,
            target_r=target_r,
            progress=f"{len(group)} of {settings.v3_forward_min_signals} completed {target_r}R signals",
        )
        recent = group[-20:]
        recent_net = sum(record.net_r for record in recent)
        slippage = [record.actual_slippage_pct for record in group if record.actual_slippage_pct is not None]
        mean_slippage = float(pd.Series(slippage).mean()) if slippage else None
        failures = []
        if len(group) < settings.v3_forward_min_signals:
            failures.append(
                f"needs {settings.v3_forward_min_signals - len(group)} more completed signals"
            )
        if probability < point_floor:
            failures.append(f"measured wins {probability:.1%} below {point_floor:.0%}")
        if lower < conservative_floor:
            failures.append(f"conservative wins {lower:.1%} below {conservative_floor:.0%}")
        if len(recent) == 20 and recent_net <= 0:
            failures.append("the latest 20 trades lost money after costs")
        if mean_slippage is not None and mean_slippage > settings.slippage_pct * 1.5:
            failures.append(
                f"real slippage {mean_slippage:.3f}% materially exceeds the tested assumption"
            )
        if not failures:
            candidate.phase = "live-ready"
            candidate.reason = "historical and forward gates passed"
            return candidate
        candidate.reason = "; ".join(failures)
        if candidate.completed > best.completed:
            best = candidate
    return best


def mark_fill(symbol: str, price: float) -> V3Record:
    records = load()
    matches = [record for record in records if record.symbol == symbol.upper() and record.outcome == "open"]
    if not matches:
        raise ValueError(f"no open V3 record for {symbol.upper()}")
    record = matches[-1]
    record.actual_fill = price
    record.actual_slippage_pct = abs(price / record.entry - 1) * 100 if record.entry else None
    save(records)
    return record
