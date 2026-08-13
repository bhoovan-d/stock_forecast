"""Market data provider with explicit tiering.

The user chose live Upstox data with graceful fallback. The risk in any fallback design is
that the user acts on delayed data believing it was live, so the tier is not an internal
detail: every call returns the tier that served it, and the daily brief prints the worst
tier used in its header.

Tier order: LIVE (Upstox) -> ARCHIVE (official NSE EOD) -> DELAYED (Yahoo, ~15min).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import IntEnum
from typing import Generic, TypeVar

import pandas as pd
from loguru import logger

from . import nse_archive, upstox, yahoo

T = TypeVar("T")


class DataTier(IntEnum):
    """Ordered worst-to-best so ``min()`` yields the weakest tier used in a run."""

    UNAVAILABLE = 0
    DELAYED = 1
    ARCHIVE = 2
    LIVE = 3

    @property
    def label(self) -> str:
        return {
            DataTier.LIVE: "LIVE (Upstox)",
            DataTier.ARCHIVE: "ARCHIVE (NSE EOD)",
            DataTier.DELAYED: "DELAYED (~15min)",
            DataTier.UNAVAILABLE: "UNAVAILABLE",
        }[self]


@dataclass
class Tiered(Generic[T]):
    """A value together with the tier that produced it."""

    value: T
    tier: DataTier

    @property
    def ok(self) -> bool:
        return self.value is not None and self.tier > DataTier.UNAVAILABLE


class MarketData:
    """Facade over the three tiers. Engines depend on this, never on a raw client."""

    def __init__(self) -> None:
        self._live = upstox.UpstoxClient()
        self._tiers_used: list[DataTier] = []

    # ── status ────────────────────────────────────────────────────────────────

    @property
    def live_available(self) -> bool:
        return self._live.authenticated

    @property
    def session_tier(self) -> DataTier:
        """Worst tier used so far — what the brief header reports."""
        return min(self._tiers_used) if self._tiers_used else DataTier.UNAVAILABLE

    def _record(self, tier: DataTier) -> DataTier:
        self._tiers_used.append(tier)
        return tier

    # ── quotes ────────────────────────────────────────────────────────────────

    def quote(self, symbol: str) -> Tiered[float | None]:
        """Last traded price."""
        if self._live.authenticated:
            price = self._live.ltp(symbol)
            if price is not None:
                return Tiered(price, self._record(DataTier.LIVE))

        frame = yahoo.fetch_chart(yahoo.to_yahoo_symbol(symbol), range_="1d", interval="5m")
        if frame is not None and not frame.empty:
            return Tiered(float(frame["close"].iloc[-1]), self._record(DataTier.DELAYED))
        return Tiered(None, self._record(DataTier.UNAVAILABLE))

    # ── intraday bars (Engine 5 entry triggers) ───────────────────────────────

    def intraday(
        self, symbol: str, *, interval: str = "5m", days: int = 5
    ) -> Tiered[pd.DataFrame | None]:
        if self._live.authenticated:
            bars = self._live.intraday(symbol, interval=interval, days=days)
            if bars is not None and not bars.empty:
                return Tiered(bars, self._record(DataTier.LIVE))

        frame = yahoo.fetch_chart(
            yahoo.to_yahoo_symbol(symbol), range_=f"{days}d", interval=interval
        )
        if frame is not None and not frame.empty:
            return Tiered(frame, self._record(DataTier.DELAYED))
        return Tiered(None, self._record(DataTier.UNAVAILABLE))

    # ── daily bars ────────────────────────────────────────────────────────────

    def daily(self, symbol: str, *, range_: str = "1y") -> Tiered[pd.DataFrame | None]:
        """Daily bars. Archive history is assembled by storage; this is the direct path."""
        frame = yahoo.fetch_chart(yahoo.to_yahoo_symbol(symbol), range_=range_, interval="1d")
        if frame is not None and not frame.empty:
            return Tiered(frame, self._record(DataTier.DELAYED))
        return Tiered(None, self._record(DataTier.UNAVAILABLE))

    # ── option chain (Engine 1 dealer gamma) ──────────────────────────────────

    def option_chain(
        self, underlying: str = "NIFTY", day: date | None = None
    ) -> Tiered[pd.DataFrame | None]:
        """Options with open interest.

        Live gives an intraday chain; the archive gives the settled EOD chain. Either is
        acceptable for regime context — the brief treats gamma as context, not a trigger.
        """
        if self._live.authenticated and day is None:
            chain = self._live.option_chain(underlying)
            if chain is not None and not chain.empty:
                return Tiered(chain, self._record(DataTier.LIVE))

        target = day or nse_archive.last_trading_day()
        if target is not None:
            chain = nse_archive.fetch_option_chain(target, underlying)
            if chain is not None and not chain.empty:
                return Tiered(chain, self._record(DataTier.ARCHIVE))

        logger.warning(f"[data] no option chain for {underlying}")
        return Tiered(None, self._record(DataTier.UNAVAILABLE))

    # ── EOD cross-section ─────────────────────────────────────────────────────

    def bhavcopy(self, day: date) -> Tiered[pd.DataFrame | None]:
        frame = nse_archive.fetch_cm_bhavcopy(day)
        if frame is not None:
            return Tiered(frame, self._record(DataTier.ARCHIVE))
        return Tiered(None, self._record(DataTier.UNAVAILABLE))

    def delivery(self, day: date) -> Tiered[pd.DataFrame | None]:
        frame = nse_archive.fetch_delivery(day)
        if frame is not None:
            return Tiered(frame, self._record(DataTier.ARCHIVE))
        return Tiered(None, self._record(DataTier.UNAVAILABLE))
