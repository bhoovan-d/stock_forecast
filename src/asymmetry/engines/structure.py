"""Multi-timeframe structure analysis (Brief §3, §9).

Each timeframe answers a different question, and the answers are combined in one direction
only — downward. Weekly and daily set the context, 60m confirms, 30m forms the setup, 15m
triggers. A 15-minute breakout against a bearish weekly is not a trade; the brief says such
a setup must be severely penalised or rejected, and ``MTFChain.htf_supportive`` is what
enforces that downstream.

Swing detection is deliberately fractal-based (a pivot must be the extreme of a window
centred on it) rather than rolling-max based. A rolling max identifies "the highest price
recently", which is usually just the latest bar in an uptrend; a fractal identifies a level
the market actually turned at, which is what support and resistance mean.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..spec import SetupType, TimeframeState, Trend
from .indicators import atr, bollinger_bandwidth, ema

# A pivot must be the extreme across this many bars either side.
_FRACTAL_WINDOW = 3


def find_pivots(frame: pd.DataFrame, window: int = _FRACTAL_WINDOW) -> tuple[list[float], list[float]]:
    """(swing highs, swing lows) as price levels, oldest first."""
    highs, lows = frame["high"].to_numpy(), frame["low"].to_numpy()
    pivot_highs, pivot_lows = [], []
    for i in range(window, len(frame) - window):
        segment_h = highs[i - window : i + window + 1]
        segment_l = lows[i - window : i + window + 1]
        if highs[i] == segment_h.max() and (segment_h.argmax() == window):
            pivot_highs.append(float(highs[i]))
        if lows[i] == segment_l.min() and (segment_l.argmin() == window):
            pivot_lows.append(float(lows[i]))
    return pivot_highs, pivot_lows


def classify_structure(frame: pd.DataFrame) -> tuple[str, Trend]:
    """Higher-highs/higher-lows vs lower-highs/lower-lows, from the last two swings."""
    pivot_highs, pivot_lows = find_pivots(frame)
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return "", Trend.SIDEWAYS

    higher_high = pivot_highs[-1] > pivot_highs[-2]
    higher_low = pivot_lows[-1] > pivot_lows[-2]

    if higher_high and higher_low:
        return "HH/HL", Trend.UP
    if not higher_high and not higher_low:
        return "LH/LL", Trend.DOWN
    return "mixed", Trend.SIDEWAYS


def nearest_resistance(
    frame: pd.DataFrame, above: float, *, min_distance_pct: float = 0.3
) -> float | None:
    """Lowest swing high meaningfully above ``above``.

    The minimum distance filters out pivots inside the current bar's noise, which would
    otherwise report resistance a few paise overhead and make every target look blocked.
    """
    pivot_highs, _ = find_pivots(frame)
    floor = above * (1 + min_distance_pct / 100)
    candidates = [p for p in pivot_highs if p >= floor]
    return min(candidates) if candidates else None


def nearest_support(frame: pd.DataFrame, below: float) -> float | None:
    _, pivot_lows = find_pivots(frame)
    candidates = [p for p in pivot_lows if p <= below]
    return max(candidates) if candidates else None


def _ema_state(close: pd.Series, spans: tuple[int, ...]) -> tuple[bool, str]:
    """Whether the EMA stack is bullish, plus a readable description."""
    usable = [s for s in spans if len(close) >= s]
    if not usable:
        return False, "insufficient history"
    values = [float(ema(close, s).iloc[-1]) for s in usable]
    price = float(close.iloc[-1])

    stacked = all(values[i] > values[i + 1] for i in range(len(values) - 1))
    above_all = price > max(values)

    labels = "/".join(str(s) for s in usable)
    if above_all and stacked:
        return True, f"price > {labels} EMA, stacked"
    if above_all:
        return True, f"price above all EMAs ({labels}), order mixed"
    below = sum(price < v for v in values)
    return False, f"price below {below}/{len(values)} EMAs ({labels})"


def _base_quality(frame: pd.DataFrame, lookback: int = 20) -> tuple[SetupType, str]:
    """Detect the setup shape from recent range behaviour."""
    if len(frame) < lookback + 5:
        return SetupType.NONE, "insufficient history"

    window = frame.tail(lookback)
    high, low, close = window["high"], window["low"], window["close"]
    top, bottom = float(high.max()), float(low.min())
    price = float(close.iloc[-1])
    depth_pct = (top - bottom) / top * 100 if top else 0.0

    prior_high = float(frame["high"].iloc[-(lookback + 5) : -lookback].max())

    # Breaking out of the base.
    if price > top * 0.999:
        return SetupType.BREAKOUT, f"clearing {lookback}-bar base ({depth_pct:.1f}% deep)"

    # Recently broke out and is pulling back into the level.
    if prior_high and price > prior_high and price < top * 0.995:
        return SetupType.BREAKOUT_RETEST, f"retesting prior breakout near {prior_high:,.2f}"

    if depth_pct < 8:
        rising_lows = float(low.tail(5).min()) > float(low.head(5).min())
        if rising_lows:
            return SetupType.ASCENDING_BASE, f"tight ascending base ({depth_pct:.1f}%)"
        return SetupType.FLAT_BASE, f"tight flat base ({depth_pct:.1f}%)"

    if price > float(close.rolling(10).mean().iloc[-1]):
        return SetupType.CONTINUATION, "holding above short-term mean"

    return SetupType.NONE, f"no clean structure ({depth_pct:.1f}% range)"


def analyse_timeframe(
    frame: pd.DataFrame,
    label: str,
    *,
    ema_spans: tuple[int, ...] = (20, 50),
    detect_setup: bool = True,
) -> TimeframeState:
    """Full structural read of one timeframe."""
    if frame is None or len(frame) < 25:
        return TimeframeState(
            timeframe=label, note="insufficient history", supportive=False
        )

    close = frame["close"]
    price = float(close.iloc[-1])
    structure, trend = classify_structure(frame)
    aligned, ema_note = _ema_state(close, ema_spans)

    setup, setup_note = (
        _base_quality(frame) if detect_setup else (SetupType.NONE, "")
    )

    support = nearest_support(frame, price)
    resistance = nearest_resistance(frame, price)

    window = frame.tail(60)
    span_high, span_low = float(window["high"].max()), float(window["low"].min())
    location = (
        (price - span_low) / (span_high - span_low) * 100
        if span_high > span_low
        else 50.0
    )

    # A timeframe supports a long when it is not in a confirmed downtrend and price is not
    # buried at the bottom of its own range.
    supportive = trend is not Trend.DOWN and location > 25 and aligned

    return TimeframeState(
        timeframe=label,
        trend=trend,
        structure=structure,
        ema_aligned=aligned,
        ema_note=ema_note,
        price_location=f"{location:.0f}% of {len(window)}-bar range",
        setup=setup,
        support=support,
        resistance=resistance,
        note=setup_note,
        supportive=supportive,
    )


def major_resistance(weekly: pd.DataFrame, daily: pd.DataFrame, above: float) -> float | None:
    """Nearest resistance that matters, taken from weekly first then daily.

    Weekly levels are the ones that stop multi-day moves, so they take precedence; the
    daily level is used only when no weekly pivot sits overhead.
    """
    candidates = []
    for frame in (weekly, daily):
        if frame is not None and len(frame) > 10:
            level = nearest_resistance(frame, above)
            if level is not None:
                candidates.append(level)
    return min(candidates) if candidates else None


def resistance_clearance_probability(
    price: float, resistance: float, volatility_pct: float, trend: Trend
) -> float:
    """Rough probability of clearing a level within the horizon.

    Deliberately simple and slightly pessimistic: it is a gate, not a forecast. The inputs
    that matter are how far the level is in volatility terms and whether the trend is
    already pushing into it.
    """
    if resistance <= price:
        return 1.0
    if volatility_pct <= 0:
        return 0.0

    distance_in_vol = (resistance / price - 1) * 100 / volatility_pct
    # A level one daily move away is often cleared; four moves away rarely is.
    base = float(np.clip(1.0 - distance_in_vol / 4.0, 0.0, 0.9))
    if trend is Trend.UP:
        base = min(0.95, base * 1.25)
    elif trend is Trend.DOWN:
        base *= 0.5
    return round(base, 3)


def compression_state(frame: pd.DataFrame) -> tuple[float, int]:
    """(Bollinger bandwidth percentile, consecutive bars compressed)."""
    if len(frame) < 40:
        return 50.0, 0
    width = bollinger_bandwidth(frame["close"]).dropna()
    if width.empty:
        return 50.0, 0

    recent = width.tail(120)
    percentile = float((recent < recent.iloc[-1]).sum() / max(len(recent) - 1, 1) * 100)

    threshold = float(recent.quantile(0.30))
    days = 0
    for value in reversed(width.tolist()):
        if value <= threshold:
            days += 1
        else:
            break
    return percentile, days


def atr_percentile(frame: pd.DataFrame, period: int = 14, window: int = 252) -> float:
    """Where current ATR sits within its own recent distribution."""
    if len(frame) < period + 20:
        return 50.0
    series = atr(frame["high"], frame["low"], frame["close"], period).dropna()
    if series.empty:
        return 50.0
    recent = series.tail(window)
    return float((recent < recent.iloc[-1]).sum() / max(len(recent) - 1, 1) * 100)
