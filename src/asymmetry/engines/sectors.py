"""Sector composites built from the universe itself.

Only 8 of the 20 NSE ``Industry`` labels map to a tradeable sector index, which left 212 of
500 stocks — including all 63 Capital Goods names — with no sector benchmark at all. Their
relative strength silently fell back to index-relative only, so a stock being carried by a
hot sector looked identical to one genuinely outperforming its peers.

The fix is to compute an equal-weight composite from the constituents we already store.
Equal weight rather than cap weight is deliberate: without float-adjusted market caps a
cap-weighted composite would be dominated by two or three mega caps and stop describing the
sector. Equal weight answers the question we actually ask — "how is the median name in this
sector doing?"

Composites are computed from stored bhavcopy, so they are point-in-time by construction.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
from loguru import logger

# Minimum constituents for a composite to mean anything. Below this, one stock's move is
# the "sector".
MIN_CONSTITUENTS = 4


def build_sector_composites(
    history: pd.DataFrame, sector_map: dict[str, str]
) -> pd.DataFrame:
    """Equal-weight total-return index per sector, normalised to 100 at the window start.

    ``history`` is stored daily bars; ``sector_map`` maps symbol -> sector label.
    Returns a date x sector frame.
    """
    if history.empty:
        return pd.DataFrame()

    closes = history.pivot_table(index="date", columns="symbol", values="close").sort_index()
    # Daily returns per stock, then the cross-sectional mean per sector: this handles
    # stocks entering or leaving the window without distorting the level.
    returns = closes.pct_change()

    frames: dict[str, pd.Series] = {}
    for sector in sorted(set(sector_map.values())):
        members = [s for s, sec in sector_map.items() if sec == sector and s in returns.columns]
        if len(members) < MIN_CONSTITUENTS:
            continue
        # min_periods guards against a day where almost nothing traded.
        mean_return = returns[members].mean(axis=1, skipna=True)
        composite = (1 + mean_return.fillna(0)).cumprod() * 100
        frames[sector] = composite

    if not frames:
        return pd.DataFrame()
    logger.debug(f"[sectors] built {len(frames)} composites")
    return pd.DataFrame(frames)


def composite_returns(
    composites: pd.DataFrame, horizons: list[int]
) -> dict[str, float]:
    """Percentage return of each composite over each horizon, keyed "<sector>:<periods>".

    Matches the key format the selection engine already uses for traded sector indices, so
    composites and real indices are interchangeable downstream.
    """
    out: dict[str, float] = {}
    for sector in composites.columns:
        series = composites[sector].dropna()
        for periods in horizons:
            if len(series) <= periods:
                continue
            past, now = series.iloc[-periods - 1], series.iloc[-1]
            if past > 0:
                out[f"{sector}:{periods}"] = (now / past - 1) * 100
    return out


def sector_strength_table(
    composites: pd.DataFrame, periods: int = 20
) -> pd.DataFrame:
    """Sectors ranked by recent performance — useful context in the brief."""
    rows = []
    for sector in composites.columns:
        series = composites[sector].dropna()
        if len(series) <= periods:
            continue
        change = (series.iloc[-1] / series.iloc[-periods - 1] - 1) * 100
        rows.append({"sector": sector, f"return_{periods}d": change})
    if not rows:
        return pd.DataFrame()
    return (
        pd.DataFrame(rows)
        .sort_values(f"return_{periods}d", ascending=False)
        .reset_index(drop=True)
    )
