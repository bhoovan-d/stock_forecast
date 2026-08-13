"""Shared technical primitives.

Kept dependency-free (pandas/numpy only) so every engine computes indicators identically —
a divergence between, say, the ATR used for stop placement and the ATR used for structure
scoring would make the R:R numbers quietly wrong.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=max(2, window // 2)).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return true_range(high, low, close).ewm(alpha=1 / period, adjust=False).mean()


def bollinger_bandwidth(close: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.Series:
    """(upper - lower) / mid. Low values mean compression — the pre-breakout state."""
    mid = close.rolling(window, min_periods=window // 2).mean()
    std = close.rolling(window, min_periods=window // 2).std()
    return (2 * num_std * std) / mid


def vwap(frame: pd.DataFrame) -> pd.Series:
    """Session-anchored VWAP. Resets each trading day, as the intraday reference should."""
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    notional = typical * frame["volume"]
    day = pd.Series(frame.index.date, index=frame.index)
    return notional.groupby(day).cumsum() / frame["volume"].groupby(day).cumsum()


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank of the latest value within its trailing window (0-100).

    Used for VIX and Bollinger bandwidth, where the level only means something relative to
    the recent regime.
    """
    return series.rolling(window, min_periods=max(20, window // 4)).apply(
        lambda w: (w < w[-1]).sum() / max(1, len(w) - 1) * 100, raw=True
    )


def pct_change_over(series: pd.Series, periods: int) -> float:
    """Total return over the last ``periods`` bars, as a percentage."""
    clean = series.dropna()
    if len(clean) <= periods:
        return float("nan")
    past, now = clean.iloc[-periods - 1], clean.iloc[-1]
    return float("nan") if past == 0 else (now / past - 1) * 100


def swing_low(low: pd.Series, lookback: int = 10) -> float:
    return float(low.tail(lookback).min())


def swing_high(high: pd.Series, lookback: int = 10) -> float:
    return float(high.tail(lookback).max())


def zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=window // 2).mean()
    std = series.rolling(window, min_periods=window // 2).std()
    return (series - mean) / std.replace(0, np.nan)


def normalise_rank(values: pd.Series) -> pd.Series:
    """Cross-sectional percentile rank -> 0-100.

    Ranking rather than z-scoring keeps a single outlier from compressing every other
    stock's factor score into a narrow band.
    """
    return values.rank(pct=True, na_option="keep") * 100


def ema_stack_score(close: pd.Series) -> tuple[float, str]:
    """+1 for a clean bullish EMA stack, -1 for bearish, 0 for mixed."""
    if len(close) < 60:
        return 0.0, "insufficient history"
    e20, e50 = ema(close, 20).iloc[-1], ema(close, 50).iloc[-1]
    e200 = ema(close, 200).iloc[-1] if len(close) >= 200 else e50
    price = close.iloc[-1]
    if price > e20 > e50 > e200:
        return 1.0, "clean stack: price > 20 > 50 > 200 EMA"
    if price < e20 < e50 < e200:
        return -1.0, "clean downtrend: price < 20 < 50 < 200 EMA"
    # Price location still carries the signal; only the EMA ordering is unresolved.
    above = sum(price > level for level in (e20, e50, e200))
    return (above - 1.5) / 1.5, f"price above {above}/3 EMAs, ordering unresolved"
