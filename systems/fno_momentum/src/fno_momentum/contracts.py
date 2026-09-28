"""Point-in-time contracts for the clean-room FnO track."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Iterable


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class PatternFamily(StrEnum):
    CONTINUATION_BREAKOUT = "continuation_breakout"
    EPISODIC_PIVOT = "episodic_pivot"
    PARABOLIC_REVERSAL = "parabolic_reversal"
    LIQUIDITY_SWEEP = "liquidity_sweep"


class EntryTimeframe(StrEnum):
    FIVE_MINUTE = "5m"
    FIFTEEN_MINUTE = "15m"


@dataclass(frozen=True)
class RawObservation:
    observation_id: str
    source_id: str
    retrieved_at: datetime
    content_sha256: str
    parser_version: str
    license_tier: str
    storage_ref: str
    published_at: datetime | None = None

    def __post_init__(self) -> None:
        require_aware(self.retrieved_at, "retrieved_at")
        if self.published_at is not None:
            require_aware(self.published_at, "published_at")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a full SHA-256 hex digest")


@dataclass(frozen=True)
class MarketBar:
    instrument_id: str
    interval: str
    interval_start: datetime
    interval_end: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    observation_id: str

    def __post_init__(self) -> None:
        for name in ("interval_start", "interval_end", "available_at"):
            require_aware(getattr(self, name), name)
        if not self.interval_start < self.interval_end:
            raise ValueError("interval_start must precede interval_end")
        if self.available_at < self.interval_end:
            raise ValueError("a bar cannot be available before it ends")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.volume < 0:
            raise ValueError("bar prices must be positive and volume non-negative")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("bar high violates OHLC ordering")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("bar low violates OHLC ordering")


@dataclass(frozen=True)
class QuoteSnapshot:
    contract_id: str
    observed_at: datetime
    available_at: datetime
    bid: float
    ask: float
    bid_quantity: int
    ask_quantity: int
    open_interest: int
    volume: int
    observation_id: str
    last_trade_at: datetime | None = None

    def __post_init__(self) -> None:
        require_aware(self.observed_at, "observed_at")
        require_aware(self.available_at, "available_at")
        if self.last_trade_at is not None:
            require_aware(self.last_trade_at, "last_trade_at")
        if self.available_at < self.observed_at:
            raise ValueError("quote cannot be available before it is observed")
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("quote must have positive, non-crossed bid and ask")
        if min(self.bid_quantity, self.ask_quantity, self.open_interest, self.volume) < 0:
            raise ValueError("quote quantities cannot be negative")


@dataclass(frozen=True)
class DecisionContext:
    decision_at: datetime
    data_cutoff: datetime
    source_observation_ids: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        require_aware(self.decision_at, "decision_at")
        require_aware(self.data_cutoff, "data_cutoff")
        if self.data_cutoff > self.decision_at:
            raise ValueError("data_cutoff cannot be after decision_at")


def available_only(items: Iterable[Any], decision_at: datetime) -> list[Any]:
    """Fail closed if any supplied input was unavailable at the decision time."""
    require_aware(decision_at, "decision_at")
    result = []
    for item in items:
        available_at = getattr(item, "available_at", None)
        if not isinstance(available_at, datetime):
            raise ValueError("decision input is missing an available_at timestamp")
        require_aware(available_at, "available_at")
        if available_at > decision_at:
            raise ValueError("future observation attempted to enter a decision")
        result.append(item)
    return result
