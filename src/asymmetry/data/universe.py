"""Tradeable universe: NIFTY 500 constituents, sector mapping, and the liquidity gate.

Liquidity is both a scored factor and a hard gate. A stock that cannot be entered and
exited efficiently is not a candidate no matter how good its catalyst looks, so it is
removed before ranking rather than merely penalised.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from loguru import logger

from ..config import settings
from . import nse_archive
from .yahoo import SECTOR_INDEX_SYMBOLS


@dataclass(frozen=True)
class Stock:
    symbol: str
    company: str
    sector: str
    isin: str

    @property
    def sector_index(self) -> str | None:
        """Yahoo symbol of the sector index, when NSE's label maps to a traded index."""
        return SECTOR_INDEX_SYMBOLS.get(self.sector)


def load_universe(index: str | None = None) -> dict[str, Stock]:
    frame = nse_archive.fetch_index_constituents(index or settings.universe_index)
    if frame is None:
        logger.error("[universe] constituent list unavailable")
        return {}
    return {
        row.symbol: Stock(row.symbol, row.company, row.sector, row.isin)
        for row in frame.itertuples()
    }


def liquidity_table(history: pd.DataFrame, lookback: int | None = None) -> pd.DataFrame:
    """Median turnover/volume per symbol over the recent window.

    ``history`` is stacked bhavcopy rows (date, symbol, turnover, volume, ...).
    """
    lookback = lookback or settings.liquidity_lookback_days
    recent_days = sorted(history["date"].unique())[-lookback:]
    window = history[history["date"].isin(recent_days)]

    stats = (
        window.groupby("symbol")
        .agg(
            median_turnover=("turnover", "median"),
            median_volume=("volume", "median"),
            days_traded=("close", "size"),
        )
        .reset_index()
    )
    stats["liquid"] = (
        (stats["median_turnover"] >= settings.min_median_turnover_inr)
        & (stats["median_volume"] >= settings.min_median_volume)
        # Guard against thinly/intermittently traded names passing on a few busy days.
        & (stats["days_traded"] >= max(1, int(0.8 * len(recent_days))))
    )
    return stats


def apply_liquidity_gate(
    universe: dict[str, Stock], history: pd.DataFrame
) -> tuple[dict[str, Stock], pd.DataFrame]:
    """Drop illiquid names. Returns the surviving universe and the liquidity stats."""
    stats = liquidity_table(history)
    passing = set(stats.loc[stats["liquid"], "symbol"])
    kept = {s: st for s, st in universe.items() if s in passing}
    logger.info(
        f"[universe] liquidity gate: {len(kept)}/{len(universe)} passed "
        f"(min turnover ₹{settings.min_median_turnover_inr:,.0f})"
    )
    return kept, stats


def build_history(days: list[date]) -> pd.DataFrame:
    """Stack bhavcopies for the given days into one frame."""
    frames = []
    for day in days:
        cm = nse_archive.fetch_cm_bhavcopy(day)
        if cm is not None:
            frames.append(cm)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def pivot_closes(history: pd.DataFrame) -> pd.DataFrame:
    """Wide date x symbol close-price matrix, the input to most factor maths."""
    return (
        history.pivot_table(index="date", columns="symbol", values="close")
        .sort_index()
    )
