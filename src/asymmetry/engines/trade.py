"""Engine 5 — the trade engine. "Can I make 2R?"

Multi-timeframe: daily structure sets the context and the levels, intraday confirms the
trigger. Entry, stop and target are derived from *structure*, never from a fixed
percentage, because the whole asymmetry argument depends on the stop sitting at a genuine
invalidation point rather than an arbitrary distance.

The R:R gate is the point of this module. A setup that cannot reasonably show 2R is not
downgraded — it is rejected. Preferring tight invalidation with a large measured move is
how you get risk ₹10 / reward ₹25 instead of risk ₹20 / reward ₹25.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..config import settings
from ..models import TradePlan
from .indicators import atr, bollinger_bandwidth, ema, swing_high, swing_low, vwap


def _recent_pivot_high(high: pd.Series, lookback: int = 60, exclude: int = 3) -> float:
    """Highest high of the base, ignoring the last few bars.

    Excluding the most recent bars keeps today's spike from being treated as the
    breakout level it is supposed to be clearing.
    """
    window = high.tail(lookback)
    return float(window.iloc[:-exclude].max()) if len(window) > exclude else float(window.max())


# A swing target beyond roughly six daily ATRs is not reachable in this system's holding
# period. Without this ceiling, a stock far below its 52-week high produces a fantasy
# target and an R:R of 1:12, which would silently defeat the gate this engine exists for.
_MAX_TARGET_ATR = 6.0
# The consolidation base, not the whole trend. Using a 60-day range on a trending stock
# measures the advance itself and inflates every target.
_BASE_WINDOW = 30


def _overhead_supply(high: pd.Series, entry: float, day_atr: float) -> list[float]:
    """Prior swing highs above entry — where trapped supply actually sits.

    A pivot must be a local maximum over +/-5 bars to count, and must sit at least one ATR
    above entry: resistance inside a single day's range is noise, not a level a move
    stalls at.
    """
    window = high.tail(252)
    values = window.to_numpy()
    pivots = []
    for i in range(5, len(values) - 5):
        level = values[i]
        if level == max(values[i - 5 : i + 6]) and level >= entry + day_atr:
            pivots.append(float(level))
    return sorted(set(pivots))


def _target_from_structure(
    daily: pd.DataFrame, entry: float, day_atr: float
) -> tuple[float, str]:
    """Target = the nearest genuine overhead supply, else a projected move in blue sky.

    Nearest overhead supply, not the furthest candidate: the first real supply zone is
    where the move stalls and where profit is actually taken. Picking the most generous
    candidate would make R:R a measure of optimism rather than structure.

    When a stock is breaking to new highs there is no overhead supply at all, so the
    target is projected from the base height or an ATR extension — whichever is larger,
    since a tight base should not cap the upside of a clean breakout.
    """
    high, low = daily["high"], daily["low"]
    ceiling = entry + _MAX_TARGET_ATR * day_atr

    supply = [level for level in _overhead_supply(high, entry, day_atr) if level <= ceiling]
    if supply:
        return supply[0], "nearest overhead supply"

    # Blue sky: nothing overhead within reach.
    base_high = float(high.tail(_BASE_WINDOW).max())
    base_low = float(low.tail(_BASE_WINDOW).min())
    measured = entry + max(base_high - base_low, 0.0)
    projected = max(measured, entry + 3.0 * day_atr)

    if projected <= ceiling:
        label = (
            f"measured move of {_BASE_WINDOW}d base"
            if measured >= entry + 3.0 * day_atr
            else "3x ATR extension"
        )
        return projected, label
    return ceiling, f"{_MAX_TARGET_ATR:g}x ATR ceiling"


# A structural stop further than this is too wide to be worth taking; fall back to ATR.
_MAX_STOP_ATR = 2.5


def _stop_from_structure(
    daily: pd.DataFrame, intraday: pd.DataFrame | None, entry: float
) -> tuple[float, str]:
    """Tightest *valid structural* invalidation, with the ATR stop only as a fallback.

    Structural levels are preferred over an ATR distance because an ATR stop is an
    arbitrary distance, not a point at which the trade thesis is actually wrong. The base
    low matters most here: for a breakout from a consolidation, the floor of that
    consolidation is the real invalidation, and it is usually tighter than the 60-day
    swing low that a generic implementation would reach for.

    Tighter is better for asymmetry, but only when the level is real, so candidates inside
    normal daily noise are discarded rather than used to manufacture a flattering R.
    """
    high, low, close = daily["high"], daily["low"], daily["close"]
    day_atr = float(atr(high, low, close, settings.atr_period).iloc[-1])

    structural: list[tuple[float, str]] = [
        (float(low.tail(_BASE_WINDOW).min()), f"{_BASE_WINDOW}-day base low"),
        (float(low.tail(20).min()), "20-day base low"),
        (swing_low(low, 10), "10-day swing low"),
        (float(ema(close, 21).iloc[-1]), "21 EMA"),
    ]
    if intraday is not None and not intraday.empty and "volume" in intraday:
        session_vwap = vwap(intraday)
        if not session_vwap.empty and np.isfinite(session_vwap.iloc[-1]):
            structural.append((float(session_vwap.iloc[-1]), "session VWAP"))

    # Valid = far enough to survive noise (0.4 ATR), near enough to be worth taking.
    near_bound = entry - 0.4 * day_atr
    far_bound = entry - _MAX_STOP_ATR * day_atr
    valid = [
        (level, label) for level, label in structural if far_bound <= level <= near_bound
    ]
    if valid:
        # Tightest valid structural stop = the highest qualifying level.
        return max(valid, key=lambda pair: pair[0])

    return (
        entry - settings.atr_stop_multiple * day_atr,
        f"{settings.atr_stop_multiple}x ATR (no structural level in range)",
    )


def _entry_from_structure(daily: pd.DataFrame, last_price: float) -> tuple[float, str]:
    """Breakout over the base, or a reclaim if price is already through it."""
    base_high = _recent_pivot_high(daily["high"])
    if last_price > base_high:
        return last_price, "continuation above base"
    # Trigger just through the pivot, so we are not buying into the level itself.
    return base_high * 1.001, f"breakout over {base_high:,.2f}"


def build_plan(
    daily: pd.DataFrame,
    *,
    intraday: pd.DataFrame | None = None,
    last_price: float | None = None,
) -> tuple[TradePlan | None, str]:
    """Build a trade plan, or return (None, reason) if it fails the gate.

    Returning the rejection reason lets the caller surface near-misses on a watchlist
    without ever presenting them as trades.
    """
    if daily is None or len(daily) < 60:
        return None, "insufficient daily history"

    price = float(last_price if last_price is not None else daily["close"].iloc[-1])
    if not np.isfinite(price) or price <= 0:
        return None, "no valid price"

    day_atr = float(atr(daily["high"], daily["low"], daily["close"], settings.atr_period).iloc[-1])
    if not np.isfinite(day_atr) or day_atr <= 0:
        return None, "no valid ATR"

    entry, setup = _entry_from_structure(daily, price)
    stop, invalidation = _stop_from_structure(daily, intraday, entry)

    risk = entry - stop
    if risk <= 0:
        return None, "stop is not below entry"
    # Guard against a hair-thin stop producing a spectacular but unrealistic R.
    if risk / entry < 0.005:
        return None, "invalidation too tight to be real (<0.5%)"

    target, target_label = _target_from_structure(daily, entry, day_atr)
    reward_risk = (target - entry) / risk

    # The screening gate, deliberately separate from the specification's 4R. See
    # config.screen_min_reward_risk for why the two must not share a constant.
    if reward_risk < settings.screen_min_reward_risk:
        return None, (
            f"R:R 1:{reward_risk:.1f} below {settings.screen_min_reward_risk:.1f} gate"
        )

    quantity = int(settings.risk_budget_inr // risk)

    return (
        TradePlan(
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(target, 2),
            reward_risk=round(reward_risk, 2),
            quantity=quantity,
            setup=f"{setup}; target = {target_label}",
            invalidation=invalidation,
        ),
        "",
    )


def intraday_trigger_note(intraday: pd.DataFrame | None, entry: float) -> str:
    """Describe where price sits versus VWAP and the trigger, for the 5m/3m layer."""
    if intraday is None or intraday.empty:
        return "no intraday data"
    last = float(intraday["close"].iloc[-1])
    session_vwap = vwap(intraday)
    above_vwap = (
        last > float(session_vwap.iloc[-1])
        if not session_vwap.empty and np.isfinite(session_vwap.iloc[-1])
        else None
    )
    bits = [f"last {last:,.2f}"]
    if above_vwap is not None:
        bits.append("above VWAP" if above_vwap else "below VWAP")
    bits.append("trigger armed" if last >= entry else f"needs {entry - last:,.2f} to trigger")
    return "; ".join(bits)


def compression_score(daily: pd.DataFrame, window: int = 120) -> float:
    """0-100: how compressed the range is versus its own recent history.

    High score = tight base. Compression before a catalyst is what produces the large
    move against a small stop.
    """
    if len(daily) < 40:
        return 50.0
    bandwidth = bollinger_bandwidth(daily["close"])
    recent = bandwidth.tail(window).dropna()
    if recent.empty or not np.isfinite(recent.iloc[-1]):
        return 50.0
    # Invert: a narrow band should score high.
    percentile = (recent < recent.iloc[-1]).sum() / max(len(recent) - 1, 1) * 100
    return float(100 - percentile)
