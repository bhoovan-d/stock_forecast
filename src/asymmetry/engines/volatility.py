"""Volatility and expected-move engine (Brief §10).

Answers "why should it move *substantially*?" — and, critically, whether the move required
to reach 4R is small relative to what this stock actually does in a few sessions.

That comparison is the point. A 1.4% stop implies a 5.6% target, which is unremarkable for
a stock whose 5-day expected move is 9% and near-impossible for one whose 5-day move is 3%.
Ranking without it rewards stocks that simply cannot travel far enough in time.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..spec import VolatilityState
from .indicators import atr
from .structure import atr_percentile, compression_state


def _narrow_range(frame: pd.DataFrame, n: int) -> bool:
    """NR4/NR7: today's range is the narrowest of the last n bars.

    A narrow range after an advance means supply and demand have reached a temporary
    truce, which is the classic precondition for expansion.
    """
    if len(frame) < n:
        return False
    ranges = (frame["high"] - frame["low"]).tail(n)
    return bool(ranges.iloc[-1] == ranges.min())


def _realized_vol(close: pd.Series, days: int) -> float:
    """Annualisation is deliberately omitted — we want the move over the horizon itself."""
    returns = close.pct_change().dropna().tail(60)
    if len(returns) < 10:
        return 0.0
    return float(returns.std() * np.sqrt(days) * 100)


def assess_volatility(daily: pd.DataFrame, hourly: pd.DataFrame | None = None) -> VolatilityState:
    """Full volatility read for one stock."""
    if daily is None or len(daily) < 30:
        return VolatilityState()

    high, low, close = daily["high"], daily["low"], daily["close"]
    price = float(close.iloc[-1])

    day_atr = float(atr(high, low, close, 14).iloc[-1])
    atr_pct = day_atr / price * 100 if price else 0.0

    bb_percentile, compression_days = compression_state(daily)

    volume = daily.get("volume")
    relative_volume, acceleration = 1.0, 0.0
    if volume is not None and len(volume.dropna()) > 25:
        baseline = float(volume.tail(21).head(20).mean())
        if baseline > 0:
            relative_volume = float(volume.iloc[-1]) / baseline
            recent3 = float(volume.tail(3).mean())
            acceleration = recent3 / baseline - 1

    realized_5d = _realized_vol(close, 5)

    # Expected move blends two independent estimates: ATR (recent realised range) and the
    # standard deviation of returns. They disagree in useful ways — ATR is steadier, the
    # return-based figure reacts faster to a volatility shift.
    def expected(days: int) -> float:
        atr_based = atr_pct * np.sqrt(days)
        vol_based = _realized_vol(close, days)
        return float(round((atr_based + vol_based) / 2, 2))

    percentile = atr_percentile(daily)
    expanding = bool(relative_volume > 1.3 and bb_percentile > 40 and acceleration > 0.2)

    return VolatilityState(
        atr=round(day_atr, 2),
        atr_pct=round(atr_pct, 2),
        atr_percentile=round(percentile, 1),
        bb_width_percentile=round(bb_percentile, 1),
        compression_days=compression_days,
        nr4=_narrow_range(daily, 4),
        nr7=_narrow_range(daily, 7),
        realized_vol_5d=round(realized_5d, 2),
        relative_volume=round(relative_volume, 2),
        volume_acceleration=round(acceleration, 2),
        expanding=expanding,
        expected_move_1d=expected(1),
        expected_move_3d=expected(3),
        expected_move_5d=expected(5),
    )


def move_feasibility(state: VolatilityState, required_pct: float) -> tuple[float, str]:
    """How comfortably the required move fits inside the expected 5-day move.

    Returns (0-100 score, note). The brief asks to *reward* setups where the move needed
    for 4R is small relative to what the stock typically does — this is that term.
    """
    expected = state.expected_move_5d
    if expected <= 0 or required_pct <= 0:
        return 0.0, "expected move unavailable"

    ratio = required_pct / expected
    if ratio <= 0.5:
        score, verdict = 100.0, "well within"
    elif ratio <= 0.8:
        score, verdict = 80.0, "comfortably within"
    elif ratio <= 1.0:
        score, verdict = 60.0, "at the edge of"
    elif ratio <= 1.5:
        score, verdict = 30.0, "beyond"
    else:
        score, verdict = 5.0, "far beyond"

    return score, (
        f"needs {required_pct:.1f}% vs {expected:.1f}% expected over 5d — {verdict} range"
    )


def compression_score(state: VolatilityState) -> float:
    """0-100 for the compression → expansion transition the brief wants to catch."""
    score = 0.0
    # Tight band is the primary tell; a low percentile means coiled.
    score += max(0.0, 100 - state.bb_width_percentile) * 0.45
    if state.nr7:
        score += 20
    elif state.nr4:
        score += 12
    score += min(state.compression_days, 10) * 1.5
    # Volume already turning up while the range is still tight is the transition itself.
    if state.expanding:
        score += 15
    elif state.relative_volume > 1.2:
        score += 8
    return float(np.clip(score, 0, 100))
