"""Contracts for the Engineer Brief specification.

Kept separate from ``models.py`` so the brief's vocabulary — timeframe states, setup types,
TRADE/WATCH/REJECT, probability and EV — reads as one coherent thing rather than being
scattered through the older screening types.

The brief's central discipline is that a candidate is *refused*, not downgraded, when a
hard gate fails. Every rejection therefore carries an explicit reason, and the reason is
surfaced rather than swallowed: knowing why 470 stocks failed is as informative as knowing
why two passed.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    TRADE = "TRADE"
    WATCH = "WATCH"
    REJECT = "REJECT"

    @property
    def emoji(self) -> str:
        return {"TRADE": "🟢", "WATCH": "🟡", "REJECT": "🔴"}[self.value]


class RejectReason(str, Enum):
    """Brief §18. Ordered roughly by how early the check runs."""

    LIQUIDITY = "poor liquidity or slippage risk"
    DATA_QUALITY = "data quality failure or missing critical inputs"
    HTF_BROKEN = "broken higher-timeframe structure"
    NO_TRIGGER = "no valid 15m/30m trigger"
    STOP_TOO_WIDE = "valid invalidation exceeds the maximum initial stop"
    RR_BELOW_GATE = "reward:risk below the minimum"
    # Distinct from RR_BELOW_GATE: the arithmetic works, but the stock does not travel far
    # enough in five sessions for the target to be realistic.
    MOVE_UNREACHABLE = "4R target exceeds the stock's expected move over the horizon"
    RESISTANCE_BLOCKS = "major resistance blocks the target with low clearance probability"
    NEGATIVE_EV = "expected value is not positive after costs"
    STALE_CATALYST = "catalyst stale or unclear with no exceptional setup"
    NONE = ""


class Trend(str, Enum):
    UP = "up"
    DOWN = "down"
    SIDEWAYS = "sideways"


class SetupType(str, Enum):
    BREAKOUT = "breakout"
    BREAKOUT_RETEST = "breakout-retest"
    CONTINUATION = "continuation"
    RECLAIM = "reclaim"
    # Distinct from BREAKOUT on purpose: that value is the generic "price cleared a base"
    # shape `structure._base_quality` reports for any timeframe. This one is a V3 setup and
    # additionally requires the volume surge, which is what makes it selective.
    BASE_BREAKOUT = "base-breakout"
    FLAT_BASE = "flat base"
    ASCENDING_BASE = "ascending base"
    FAILED_BREAKOUT = "failed breakout"
    NONE = "none"


class TimeframeState(BaseModel):
    """One rung of the Weekly → Daily → 60m → 30m → 15m chain (Brief §3)."""

    timeframe: str
    trend: Trend = Trend.SIDEWAYS
    structure: str = ""            # e.g. "HH/HL" or "LH/LL"
    ema_aligned: bool = False
    ema_note: str = ""
    price_location: str = ""       # where price sits within the range
    setup: SetupType = SetupType.NONE
    support: float | None = None
    resistance: float | None = None
    note: str = ""
    supportive: bool = True        # does this rung support a long?

    @property
    def summary(self) -> str:
        bits = [self.trend.value]
        if self.structure:
            bits.append(self.structure)
        if self.setup != SetupType.NONE:
            bits.append(self.setup.value)
        return ", ".join(bits)


class MTFChain(BaseModel):
    """The full context chain, plus whether the higher timeframes permit a long.

    The brief's rule: a lower-timeframe trigger is valid only when the higher timeframes
    are supportive, and a 15m breakout against a bearish weekly/daily must be severely
    penalised or rejected outright.
    """

    weekly: TimeframeState | None = None
    daily: TimeframeState | None = None
    hourly: TimeframeState | None = None
    m30: TimeframeState | None = None
    m15: TimeframeState | None = None

    @property
    def rungs(self) -> list[TimeframeState]:
        return [tf for tf in (self.weekly, self.daily, self.hourly, self.m30, self.m15) if tf]

    @property
    def htf_supportive(self) -> bool:
        """Weekly and daily must both permit a long."""
        return all(tf.supportive for tf in (self.weekly, self.daily) if tf is not None)

    @property
    def alignment_score(self) -> float:
        """0-100: how much of the chain agrees."""
        rungs = self.rungs
        if not rungs:
            return 0.0
        return sum(tf.supportive for tf in rungs) / len(rungs) * 100

    @property
    def chain_text(self) -> str:
        return "  →  ".join(f"{tf.timeframe}: {tf.summary}" for tf in self.rungs)


class VolatilityState(BaseModel):
    """Brief §10 — is this stock capable of the required move?"""

    atr: float = 0.0
    atr_pct: float = 0.0               # ATR as % of price
    atr_percentile: float = 50.0       # within its own history
    bb_width_percentile: float = 50.0  # low = compressed
    compression_days: int = 0
    nr4: bool = False
    nr7: bool = False
    realized_vol_5d: float = 0.0
    relative_volume: float = 1.0
    volume_acceleration: float = 0.0
    expanding: bool = False

    # Expected move over the horizon, from ATR and realized volatility.
    expected_move_1d: float = 0.0
    expected_move_3d: float = 0.0
    expected_move_5d: float = 0.0

    @property
    def compressed(self) -> bool:
        return self.bb_width_percentile <= 30 or self.nr7


class FnOState(BaseModel):
    """Brief §11. Confirmation only — never a standalone entry signal."""

    has_fno: bool = False
    futures_oi: float | None = None
    oi_change_pct: float | None = None
    basis_pct: float | None = None
    buildup: str = ""               # long buildup / short covering / etc.
    atm_iv: float | None = None
    iv_percentile: float | None = None
    call_oi_wall: float | None = None   # option-implied resistance
    put_oi_support: float | None = None
    genuine_participation: bool = False
    note: str = ""


class ProbabilityEstimate(BaseModel):
    """Brief §15 — P(target before stop), by horizon."""

    p_1d: float = 0.0
    p_3d: float = 0.0
    p_5d: float = 0.0
    p_timeout: float = 0.0
    sample_size: int = 0
    calibrated: bool = False
    note: str = ""

    @property
    def primary(self) -> float:
        """The brief specifies the 5-day probability as the primary trade score."""
        return self.p_5d


class ExpectedValue(BaseModel):
    """Brief §16. EV is the ranking layer *after* the hard gates, never a substitute."""

    ev_r: float = 0.0            # expected R after costs and timeouts
    gross_ev_r: float = 0.0      # before costs, for comparison
    cost_r: float = 0.0
    positive: bool = False
    note: str = ""


class TargetPlan(BaseModel):
    """Brief §13-14 — a true 4R target, resistance-aware."""

    entry: float = 0.0
    stop: float = 0.0
    stop_pct: float = 0.0
    risk: float = 0.0
    target_4r: float = 0.0
    target_pct: float = 0.0
    reward_risk: float = 0.0
    nearest_resistance: float | None = None
    resistance_distance_pct: float | None = None
    resistance_before_target: bool = False
    clearance_probability: float = 1.0
    quantity: int = 0
    trigger: str = ""
    invalidation: str = ""
    setup_type: SetupType = SetupType.NONE


class SpecCandidate(BaseModel):
    """One row of the Brief §19 ranking output."""

    rank: int = 0
    symbol: str
    company: str = ""
    sector: str = ""
    instrument: str = "equity"        # "futures" when F&O is available
    direction: str = "long"
    close: float = 0.0

    verdict: Verdict = Verdict.REJECT
    reject_reason: RejectReason = RejectReason.NONE
    reject_detail: str = ""

    chain: MTFChain = Field(default_factory=MTFChain)
    volatility: VolatilityState = Field(default_factory=VolatilityState)
    fno: FnOState = Field(default_factory=FnOState)
    plan: TargetPlan | None = None
    probability: ProbabilityEstimate = Field(default_factory=ProbabilityEstimate)
    expected_value: ExpectedValue = Field(default_factory=ExpectedValue)

    # Why now.
    catalyst_note: str = ""
    catalyst_score: float = 50.0
    catalyst_age_hours: float | None = None
    earnings_note: str = ""

    # Leadership.
    rs_nifty: dict[str, float] = Field(default_factory=dict)   # "5"/"20"/"60" -> percentile
    rs_sector: dict[str, float] = Field(default_factory=dict)
    rs_acceleration: float = 0.0
    sector_quadrant: str = ""     # e.g. "strong market / strong sector"

    # Participation.
    delivery_pct: float | None = None
    participation_note: str = ""

    module_scores: dict[str, float] = Field(default_factory=dict)
    score: float = 0.0

    @property
    def why_now(self) -> str:
        return self.catalyst_note or self.earnings_note or "no fresh catalyst identified"


class SpecScan(BaseModel):
    """A full run of the specification engine."""

    as_of: str
    tier: str = ""
    universe_size: int = 0
    liquid_size: int = 0
    evaluated: int = 0
    market_regime: str = ""
    market_note: str = ""

    trades: list[SpecCandidate] = Field(default_factory=list)
    watch: list[SpecCandidate] = Field(default_factory=list)
    # Kept as counts rather than full rows: 400+ rejection objects are noise, but the
    # distribution of *why* they failed is genuinely informative.
    reject_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def total_rejected(self) -> int:
        return sum(self.reject_counts.values())
