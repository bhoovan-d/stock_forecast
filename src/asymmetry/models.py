"""Storage tables and the contracts engines pass between each other."""

from __future__ import annotations

# `date` is a field name on several tables, which would shadow the type in annotations.
import datetime as dt
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field as PydField
from sqlmodel import Field, SQLModel


# ── Persisted tables ──────────────────────────────────────────────────────────


class DailyBar(SQLModel, table=True):
    """One stock, one day. Delivery is joined in from the MTO file."""

    __tablename__ = "daily_bar"

    id: int | None = Field(default=None, primary_key=True)
    date: dt.date = Field(index=True)
    symbol: str = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    prev_close: float | None = None
    volume: float
    turnover: float
    trades: float | None = None
    delivery_pct: float | None = None


class CatalystRecord(SQLModel, table=True):
    """An LLM-scored news item bound to a ticker."""

    __tablename__ = "catalyst"

    id: int | None = Field(default=None, primary_key=True)
    published: dt.datetime = Field(index=True)
    symbol: str = Field(index=True)
    headline: str
    url: str = ""
    source: str = ""
    catalyst_type: str = "none"
    expectation_delta: int = 0
    materiality: int = 0
    durability: int = 0
    already_priced: bool = False
    confidence: int = 0
    score: float = 0.0
    rationale: str = ""
    provider: str = ""


# ── Engine contracts ──────────────────────────────────────────────────────────


class CatalystType(str, Enum):
    EARNINGS_SURPRISE = "earnings_surprise"
    ORDER_WIN = "order_win"
    POLICY = "policy"
    REGULATORY = "regulatory"
    MERGER_ACQUISITION = "m&a"
    GUIDANCE = "guidance"
    CAPACITY_EXPANSION = "capacity_expansion"
    PROMOTER_INSTITUTIONAL = "promoter_institutional"
    SECTOR_MACRO = "sector_macro"
    NONE = "none"


class CatalystExtraction(BaseModel):
    """What the LLM must return for each news item.

    The whole point of this contract is ``expectation_delta``: it asks whether the item
    changes the company's future earnings/value expectation, which is a different and much
    stricter question than whether the news is positive. Positive-toned news with no
    change to forward expectations scores zero.
    """

    catalyst_type: CatalystType = CatalystType.NONE
    expectation_delta: int = PydField(
        0, ge=-3, le=3, description="Change to forward earnings/value expectation."
    )
    materiality: int = PydField(0, ge=0, le=3, description="Size of impact vs the company.")
    durability: int = PydField(
        0, ge=0, le=3, description="0=one-day pop, 3=multi-quarter re-rating."
    )
    already_priced: bool = False
    confidence: int = PydField(0, ge=0, le=3)
    rationale: str = ""

    def score(self) -> float:
        """0-100, centred on 50 = no catalyst.

        Neutral must be 50, not 0. Returning 0 would rank a company with no news below one
        with actively bad news, so an ordinary quiet stock would be penalised for nothing.
        """
        if self.expectation_delta == 0 or self.catalyst_type == CatalystType.NONE:
            return 50.0

        # Magnitude of the expectation change is the primary term; materiality, durability
        # and confidence scale conviction without ever being able to zero it out on their
        # own (hence the 0.4 floor).
        magnitude = abs(self.expectation_delta) / 3
        quality = 0.4 + 0.6 * (self.materiality + self.durability + self.confidence) / 9
        strength = magnitude * quality
        if self.already_priced:
            strength *= 0.4

        signed = strength if self.expectation_delta > 0 else -strength
        # Map [-1, 1] onto [0, 100]: 50 = no catalyst, below 50 = forward numbers cut.
        # Clamped because a score outside this range would distort the weighted total in
        # the selection engine, where every other factor is a 0-100 percentile.
        return round(max(0.0, min(100.0, 50 + 50 * signed)), 2)


class RegimeVerdict(str, Enum):
    AGGRESSIVE = "aggressive"
    SELECTIVE = "selective"
    DEFENSIVE = "defensive"

    @property
    def emoji(self) -> str:
        return {
            RegimeVerdict.AGGRESSIVE: "🟢",
            RegimeVerdict.SELECTIVE: "🟡",
            RegimeVerdict.DEFENSIVE: "🔴",
        }[self]

    @property
    def headline(self) -> str:
        return {
            RegimeVerdict.AGGRESSIVE: "Aggressive long environment",
            RegimeVerdict.SELECTIVE: "Selective",
            RegimeVerdict.DEFENSIVE: "Avoid longs / defensive",
        }[self]


class RegimeComponent(BaseModel):
    name: str
    score: int = PydField(0, ge=-1, le=1)
    detail: str = ""


class RegimeReport(BaseModel):
    as_of: date
    verdict: RegimeVerdict
    total: int
    components: list[RegimeComponent] = []
    tier: str = ""
    # Gamma is regime context only — never an entry signal.
    net_gex: float | None = None
    gamma_flip: float | None = None
    spot: float | None = None


class FactorScores(BaseModel):
    relative_strength: float = 0.0
    volume: float = 0.0
    price_structure: float = 0.0
    catalyst: float = 50.0
    liquidity: float = 0.0


class MacroGap(BaseModel):
    """Engine 4 output. ``reliable`` gates whether this may influence ranking at all."""

    fair_value: float | None = None
    gap_pct: float | None = None
    r_squared: float = 0.0
    reliable: bool = False
    top_drivers: list[tuple[str, float]] = []


class TradePlan(BaseModel):
    entry: float
    stop: float
    target: float
    reward_risk: float
    quantity: int = 0
    setup: str = ""
    invalidation: str = ""

    @property
    def risk_per_share(self) -> float:
        return self.entry - self.stop


class Candidate(BaseModel):
    symbol: str
    company: str = ""
    sector: str = ""
    close: float = 0.0
    factors: FactorScores = FactorScores()
    total_score: float = 0.0
    catalyst_note: str = ""
    macro: MacroGap = MacroGap()
    plan: TradePlan | None = None
    # Earnings proximity. An imminent result is a *risk* flag, not a catalyst: a gap can
    # open straight through the stop, so the computed R:R no longer holds.
    earnings_flag: str = ""
    # Set when the setup is real but fails the R:R gate — surfaced as a watch item, never
    # as a trade.
    rejected_reason: str = ""


class ScanResult(BaseModel):
    as_of: date
    regime: RegimeReport
    candidates: list[Candidate] = []
    watchlist: list[Candidate] = []
    universe_size: int = 0
    liquid_size: int = 0
    tier: str = ""
