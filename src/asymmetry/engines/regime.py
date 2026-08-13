"""Engine 1 — market regime. "Should I be aggressive today?"

Five inputs, each scored -1/0/+1, summed into a single verdict. The point is to stop
treating every breakout equally: the same setup is worth far more in a negative-gamma,
expanding-volatility, broad-participation tape than in a compressed, positive-gamma one.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..data import DataTier, MarketData
from ..data import yahoo
from ..models import RegimeComponent, RegimeReport, RegimeVerdict
from . import gamma as gamma_mod
from .indicators import ema_stack_score, rolling_percentile


def _trend_component(nifty: pd.DataFrame | None) -> RegimeComponent:
    if nifty is None or nifty.empty:
        return RegimeComponent(name="NIFTY trend", score=0, detail="unavailable")

    close = nifty["close"]
    raw, note = ema_stack_score(close)
    score = 1 if raw > 0.5 else (-1 if raw < -0.5 else 0)
    change_20d = (close.iloc[-1] / close.iloc[-21] - 1) * 100 if len(close) > 21 else np.nan
    detail = f"{note}; 20d {change_20d:+.1f}%" if np.isfinite(change_20d) else note
    return RegimeComponent(name="NIFTY trend", score=score, detail=detail)


def _vix_component(vix: pd.DataFrame | None) -> RegimeComponent:
    """Percentile matters more than level — VIX 14 means different things in different years."""
    if vix is None or vix.empty:
        return RegimeComponent(name="India VIX", score=0, detail="unavailable")

    close = vix["close"]
    level = float(close.iloc[-1])
    pct = rolling_percentile(close, settings.vix_percentile_lookback_days).iloc[-1]
    change_5d = (level / close.iloc[-6] - 1) * 100 if len(close) > 6 else np.nan

    # Low and stable vol supports carrying longs; a sharp spike does not.
    if np.isfinite(pct) and pct < 40 and (not np.isfinite(change_5d) or change_5d < 10):
        score = 1
    elif (np.isfinite(pct) and pct > 75) or (np.isfinite(change_5d) and change_5d > 20):
        score = -1
    else:
        score = 0

    detail = f"{level:.1f}"
    if np.isfinite(pct):
        detail += f" ({pct:.0f}th pct of 1y)"
    if np.isfinite(change_5d):
        detail += f", 5d {change_5d:+.1f}%"
    return RegimeComponent(name="India VIX", score=score, detail=detail)


def _gamma_component(data: MarketData, as_of: date) -> tuple[RegimeComponent, dict]:
    tiered = data.option_chain("NIFTY", day=as_of)
    if not tiered.ok:
        return (
            RegimeComponent(name="Dealer gamma", score=0, detail="chain unavailable"),
            {},
        )
    stats = gamma_mod.compute_gex(tiered.value, as_of)
    score, note = gamma_mod.gamma_regime_score(
        stats.get("net_gex"), stats.get("spot"), stats.get("gamma_flip")
    )
    return RegimeComponent(name="Dealer gamma", score=score, detail=note), stats


def _global_component(macro: dict[str, pd.DataFrame]) -> RegimeComponent:
    """S&P/Nasdaq trend, dollar, and US yields. Risk-on abroad supports Indian longs."""
    votes: list[int] = []
    notes: list[str] = []

    for name, label in (("sp500", "S&P"), ("nasdaq", "Nasdaq")):
        frame = macro.get(name)
        if frame is None or len(frame) < 50:
            continue
        close = frame["close"]
        above_50 = close.iloc[-1] > close.rolling(50).mean().iloc[-1]
        votes.append(1 if above_50 else -1)
        notes.append(f"{label} {'>' if above_50 else '<'}50DMA")

    # A rising dollar and rising US yields both tighten conditions for emerging markets.
    for name, label in (("dxy", "DXY"), ("us10y", "US10Y")):
        frame = macro.get(name)
        if frame is None or len(frame) < 21:
            continue
        close = frame["close"]
        change = (close.iloc[-1] / close.iloc[-21] - 1) * 100
        votes.append(-1 if change > 1.5 else (1 if change < -1.5 else 0))
        notes.append(f"{label} 20d {change:+.1f}%")

    if not votes:
        return RegimeComponent(name="Global risk", score=0, detail="unavailable")
    mean = float(np.mean(votes))
    score = 1 if mean > 0.25 else (-1 if mean < -0.25 else 0)
    return RegimeComponent(name="Global risk", score=score, detail="; ".join(notes))


def _breadth_component(history: pd.DataFrame) -> RegimeComponent:
    """Participation: % above 50DMA plus the day's advance/decline split.

    Computed straight from the bhavcopy cross-section, so it covers the whole market
    rather than a sampled proxy.
    """
    if history is None or history.empty:
        return RegimeComponent(name="Breadth", score=0, detail="unavailable")

    closes = history.pivot_table(index="date", columns="symbol", values="close").sort_index()
    if len(closes) < settings.breadth_ma_period + 1:
        return RegimeComponent(name="Breadth", score=0, detail="insufficient history")

    ma = closes.rolling(settings.breadth_ma_period).mean()
    latest, latest_ma = closes.iloc[-1], ma.iloc[-1]
    valid = latest.notna() & latest_ma.notna()
    pct_above = float((latest[valid] > latest_ma[valid]).mean() * 100)

    prev = closes.iloc[-2]
    both = latest.notna() & prev.notna()
    advancers = int((latest[both] > prev[both]).sum())
    decliners = int((latest[both] < prev[both]).sum())
    ad_ratio = advancers / max(decliners, 1)

    if pct_above > 55 and ad_ratio > 1.0:
        score = 1
    elif pct_above < 40 or ad_ratio < 0.6:
        score = -1
    else:
        score = 0

    return RegimeComponent(
        name="Breadth",
        score=score,
        detail=f"{pct_above:.0f}% above {settings.breadth_ma_period}DMA; "
        f"A/D {advancers}/{decliners} ({ad_ratio:.2f})",
    )


def assess_regime(as_of: date | None = None, data: MarketData | None = None) -> RegimeReport:
    from ..data import nse_archive
    from ..storage import load_history

    data = data or MarketData()
    as_of = as_of or nse_archive.last_trading_day() or date.today()

    # as_of on every fetch: without it a historical regime read is computed from data
    # that did not exist on the day being assessed.
    nifty = yahoo.fetch_chart("^NSEI", range_="2y", interval="1d", as_of=as_of)
    vix = yahoo.fetch_chart("^INDIAVIX", range_="2y", interval="1d", as_of=as_of)
    macro = yahoo.fetch_macro(range_="1y", as_of=as_of)
    history = load_history(days=200, end=as_of)

    gamma_component, gamma_stats = _gamma_component(data, as_of)
    components = [
        _trend_component(nifty),
        _vix_component(vix),
        gamma_component,
        _global_component(macro),
        _breadth_component(history),
    ]

    total = sum(c.score for c in components)
    if total >= 2:
        verdict = RegimeVerdict.AGGRESSIVE
    elif total <= -2:
        verdict = RegimeVerdict.DEFENSIVE
    else:
        verdict = RegimeVerdict.SELECTIVE

    logger.info(f"[regime] {as_of} -> {verdict.value} (score {total:+d})")
    return RegimeReport(
        as_of=as_of,
        verdict=verdict,
        total=total,
        components=components,
        tier=data.session_tier.label,
        net_gex=gamma_stats.get("net_gex"),
        gamma_flip=gamma_stats.get("gamma_flip"),
        spot=gamma_stats.get("spot"),
    )
