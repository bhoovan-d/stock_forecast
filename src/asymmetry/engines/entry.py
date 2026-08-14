"""Entry, stop and target engine — the hard-gate layer (Brief §12, §13, §14, §18).

Three rules from the brief drive everything here, and each exists to stop a specific way of
lying to yourself:

1. **The entry is the last step, not the thesis.** A trigger only becomes a trade when the
   higher timeframes already agreed. That check happens upstream; this module refuses to
   emit anything when the chain says no.

2. **The stop is thesis invalidation, capped at 1.4%.** If the technically valid level sits
   further away, the setup is REJECTED. The brief is explicit — never tighten the stop to
   manufacture the R multiple, because a stop inside the noise is not an invalidation, it
   is a donation.

3. **4R must be real.** The target is entry + 4 × risk, and it is then *validated* against
   overhead resistance and the stock's own expected move. Notably the brief forbids the
   trick this codebase previously used — extending a target to an ATR ceiling simply
   because that produces the required multiple.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import settings
from ..spec import (
    MTFChain,
    RejectReason,
    SetupType,
    TargetPlan,
    Trend,
    VolatilityState,
)
from .indicators import atr
from .structure import (
    find_pivots,
    major_resistance,
    nearest_resistance,
    resistance_clearance_probability,
)


class EntryRejection(Exception):
    """Carries the structured reason a setup was refused."""

    def __init__(self, reason: RejectReason, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(detail or reason.value)


def _trigger_level(m15: pd.DataFrame, m30: pd.DataFrame | None) -> tuple[float, str, SetupType]:
    """The price at which the setup proves itself.

    Preference order follows the brief: a breakout of defined 15m/30m structure, or a
    breakout that has pulled back and is reclaiming. The trigger sits just *through* the
    level so we are not buying into the level itself.
    """
    if m15 is None or len(m15) < 20:
        raise EntryRejection(RejectReason.NO_TRIGGER, "no 15m data")

    recent = m15.tail(26)  # roughly the last session
    level_15 = float(recent["high"].iloc[:-1].max())

    level_30 = None
    if m30 is not None and len(m30) >= 14:
        level_30 = float(m30.tail(14)["high"].iloc[:-1].max())

    # The nearer of the two structures is the one price must clear first.
    candidates = [(level_15, "15m")] + ([(level_30, "30m")] if level_30 else [])
    level, timeframe = min(candidates, key=lambda pair: pair[0])

    last_close = float(m15["close"].iloc[-1])
    if last_close > level:
        # Already through: this is continuation, and the trigger is the current price.
        return last_close, f"continuation above {timeframe} structure {level:,.2f}", SetupType.CONTINUATION

    return level * 1.0005, f"{timeframe} breakout over {level:,.2f}", SetupType.BREAKOUT


def _structural_stop(
    m15: pd.DataFrame, m30: pd.DataFrame | None, daily: pd.DataFrame, entry: float
) -> tuple[float, str]:
    """Tightest genuine invalidation below entry.

    Structure first, ATR only as the fallback the brief permits when no clean level exists.
    """
    candidates: list[tuple[float, str]] = []

    # Most relevant short-term swing lows.
    _, lows_15 = find_pivots(m15.tail(60))
    below_15 = [p for p in lows_15 if p < entry]
    if below_15:
        candidates.append((max(below_15), "15m swing low"))

    if m30 is not None and len(m30) > 20:
        _, lows_30 = find_pivots(m30.tail(40))
        below_30 = [p for p in lows_30 if p < entry]
        if below_30:
            candidates.append((max(below_30), "30m swing low"))

    # The low of the consolidation the trigger breaks out of.
    session_low = float(m15.tail(26)["low"].min())
    if session_low < entry:
        candidates.append((session_low, "session low"))

    day_atr = float(atr(daily["high"], daily["low"], daily["close"], 14).iloc[-1])
    if np.isfinite(day_atr) and day_atr > 0:
        candidates.append((entry - 0.5 * day_atr, "0.5x daily ATR"))

    # A stop must sit far enough below entry to survive ordinary noise, or it is not an
    # invalidation level at all.
    noise_floor = entry - 0.15 * day_atr if np.isfinite(day_atr) else entry * 0.998
    valid = [(lvl, name) for lvl, name in candidates if 0 < lvl <= noise_floor]
    if not valid:
        raise EntryRejection(
            RejectReason.NO_TRIGGER, "no invalidation level below the trigger"
        )

    # Tightest valid = the highest qualifying level.
    return max(valid, key=lambda pair: pair[0])


def build_plan(
    *,
    symbol: str,
    chain: MTFChain,
    volatility: VolatilityState,
    daily: pd.DataFrame,
    weekly: pd.DataFrame | None,
    m15: pd.DataFrame | None,
    m30: pd.DataFrame | None,
    call_oi_wall: float | None = None,
) -> TargetPlan:
    """Produce a spec-compliant plan, or raise EntryRejection with the reason."""
    if not chain.htf_supportive:
        raise EntryRejection(
            RejectReason.HTF_BROKEN,
            "weekly/daily structure does not support a long",
        )
    if daily is None or len(daily) < 60:
        raise EntryRejection(RejectReason.DATA_QUALITY, "insufficient daily history")
    if m15 is None or m15.empty:
        raise EntryRejection(RejectReason.NO_TRIGGER, "no intraday data for a trigger")

    entry, trigger_note, setup_type = _trigger_level(m15, m30)
    stop, invalidation = _structural_stop(m15, m30, daily, entry)

    risk = entry - stop
    stop_pct = risk / entry * 100

    # ── Hard gate 1: maximum initial stop ─────────────────────────────────────
    if stop_pct > settings.max_stop_pct:
        raise EntryRejection(
            RejectReason.STOP_TOO_WIDE,
            f"valid invalidation is {stop_pct:.2f}% away "
            f"(max {settings.max_stop_pct:.1f}%) — rejected rather than tightened",
        )

    target = entry + settings.min_reward_risk * risk
    target_pct = (target / entry - 1) * 100

    # ── Resistance validation (§14, §18) ──────────────────────────────────────
    resistance = major_resistance(weekly, daily, entry)
    # Option writers' heaviest strike is resistance too, when it sits nearer.
    if call_oi_wall and call_oi_wall > entry:
        resistance = min(resistance, call_oi_wall) if resistance else call_oi_wall

    clearance = 1.0
    blocks = False
    if resistance is not None and resistance < target:
        blocks = True
        daily_move = volatility.atr_pct or 1.0
        trend = chain.daily.trend if chain.daily else Trend.SIDEWAYS
        clearance = resistance_clearance_probability(entry, resistance, daily_move, trend)
        if clearance < settings.min_resistance_clearance_prob:
            raise EntryRejection(
                RejectReason.RESISTANCE_BLOCKS,
                f"resistance {resistance:,.2f} sits below the 4R target "
                f"{target:,.2f}, clearance probability {clearance:.0%}",
            )

    # ── Hard gate 2: the move must be reachable in the horizon ────────────────
    # A 4R target the stock cannot travel to within five sessions is arithmetic, not a
    # trade. This is the check the brief adds on top of R:R.
    if volatility.expected_move_5d > 0 and target_pct > volatility.expected_move_5d * 1.6:
        raise EntryRejection(
            RejectReason.MOVE_UNREACHABLE,
            f"4R needs {target_pct:.1f}% but the 5-day expected move is only "
            f"{volatility.expected_move_5d:.1f}%",
        )

    quantity = int(settings.risk_budget_inr // risk) if risk > 0 else 0

    return TargetPlan(
        entry=round(entry, 2),
        stop=round(stop, 2),
        stop_pct=round(stop_pct, 3),
        risk=round(risk, 2),
        target_4r=round(target, 2),
        target_pct=round(target_pct, 2),
        reward_risk=settings.min_reward_risk,
        nearest_resistance=round(resistance, 2) if resistance else None,
        resistance_distance_pct=(
            round((resistance / entry - 1) * 100, 2) if resistance else None
        ),
        resistance_before_target=blocks,
        clearance_probability=clearance,
        quantity=quantity,
        trigger=trigger_note,
        invalidation=invalidation,
        setup_type=setup_type,
    )
