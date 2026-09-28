"""Approval-driven stock-option envelope and executable-quote selection."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Iterable

from .contracts import Direction, QuoteSnapshot, available_only, require_aware
from .governance import ChangeControl
from .policy import MAX_OPTION_DTE, MIN_OPTION_DTE


class OptionRight(StrEnum):
    CALL = "call"
    PUT = "put"


@dataclass(frozen=True)
class OptionContract:
    contract_id: str
    underlying_symbol: str
    right: OptionRight
    strike: float
    expiry: date
    lot_size: int
    tick_size: float
    available_at: datetime
    observation_id: str

    def __post_init__(self) -> None:
        require_aware(self.available_at, "available_at")
        if self.strike <= 0 or self.lot_size <= 0 or self.tick_size <= 0:
            raise ValueError("contract strike, lot size, and tick size must be positive")


@dataclass(frozen=True)
class QuotedContract:
    contract: OptionContract
    quote: QuoteSnapshot

    def __post_init__(self) -> None:
        if self.contract.contract_id != self.quote.contract_id:
            raise ValueError("quote and contract identifiers do not match")


@dataclass(frozen=True)
class ContractPolicy:
    maximum_spread_bps: float
    maximum_quote_age_seconds: int
    minimum_volume: int
    minimum_open_interest: int
    ranking: tuple[str, ...]

    REQUIRED_RANKING = (
        "spread_bps_asc",
        "open_interest_desc",
        "volume_desc",
        "dte_asc",
        "contract_id_asc",
    )

    @classmethod
    def from_definition(cls, value: dict) -> "ContractPolicy":
        required = {
            "maximum_spread_bps",
            "maximum_quote_age_seconds",
            "minimum_volume",
            "minimum_open_interest",
            "ranking",
        }
        missing = required - set(value)
        if missing:
            raise ValueError("active contract policy is incomplete: " + ", ".join(sorted(missing)))
        policy = cls(
            maximum_spread_bps=float(value["maximum_spread_bps"]),
            maximum_quote_age_seconds=int(value["maximum_quote_age_seconds"]),
            minimum_volume=int(value["minimum_volume"]),
            minimum_open_interest=int(value["minimum_open_interest"]),
            ranking=tuple(value["ranking"]),
        )
        if min(
            policy.maximum_spread_bps,
            policy.maximum_quote_age_seconds,
            policy.minimum_volume,
            policy.minimum_open_interest,
        ) < 0:
            raise ValueError("contract liquidity thresholds cannot be negative")
        if policy.ranking != cls.REQUIRED_RANKING:
            raise ValueError("contract ranking does not match the implemented approved policy")
        return policy


@dataclass(frozen=True)
class OptionSelection:
    selected: QuotedContract | None
    eligible_contract_ids: tuple[str, ...] = ()
    rejection_counts: dict[str, int] = field(default_factory=dict)


class OptionSelector:
    def __init__(self, changes: ChangeControl) -> None:
        self.changes = changes

    def select(
        self,
        contracts: Iterable[QuotedContract],
        *,
        underlying_symbol: str,
        underlying_price: float,
        direction: Direction,
        decision_at: datetime,
        manifest_id: str,
        manifest_hash: str,
    ) -> OptionSelection:
        require_aware(decision_at, "decision_at")
        if underlying_price <= 0:
            raise ValueError("underlying price must be positive")
        policy = ContractPolicy.from_definition(
            self.changes.active_definition(manifest_id, manifest_hash)
        )
        items = list(contracts)
        available_only([item.contract for item in items], decision_at)
        available_only([item.quote for item in items], decision_at)
        expected_right = OptionRight.CALL if direction is Direction.BULLISH else OptionRight.PUT
        same_side = [
            item for item in items
            if item.contract.underlying_symbol.upper() == underlying_symbol.upper()
            and item.contract.right is expected_right
        ]
        envelope_ids = self._envelope_ids(same_side, underlying_price, direction)
        rejections: dict[str, int] = {}
        eligible: list[tuple[QuotedContract, float, int]] = []
        for item in same_side:
            contract, quote = item.contract, item.quote
            dte = (contract.expiry - decision_at.date()).days
            reason = None
            if item.contract.contract_id not in envelope_ids:
                reason = "OUTSIDE_ATM_ONE_ITM_ENVELOPE"
            elif not MIN_OPTION_DTE <= dte <= MAX_OPTION_DTE:
                reason = "DTE_OUTSIDE_ENVELOPE"
            elif (decision_at - quote.available_at).total_seconds() > policy.maximum_quote_age_seconds:
                reason = "STALE_QUOTE"
            elif quote.volume < policy.minimum_volume:
                reason = "INSUFFICIENT_VOLUME"
            elif quote.open_interest < policy.minimum_open_interest:
                reason = "INSUFFICIENT_OPEN_INTEREST"
            else:
                spread_bps = self._spread_bps(quote)
                if spread_bps > policy.maximum_spread_bps:
                    reason = "SPREAD_TOO_WIDE"
            if reason:
                rejections[reason] = rejections.get(reason, 0) + 1
                continue
            eligible.append((item, self._spread_bps(quote), dte))
        eligible.sort(
            key=lambda row: (
                row[1], -row[0].quote.open_interest, -row[0].quote.volume,
                row[2], row[0].contract.contract_id,
            )
        )
        return OptionSelection(
            selected=eligible[0][0] if eligible else None,
            eligible_contract_ids=tuple(row[0].contract.contract_id for row in eligible),
            rejection_counts=rejections,
        )

    @staticmethod
    def _spread_bps(quote: QuoteSnapshot) -> float:
        midpoint = (quote.ask + quote.bid) / 2
        return (quote.ask - quote.bid) / midpoint * 10_000

    @staticmethod
    def _envelope_ids(
        items: list[QuotedContract], underlying_price: float, direction: Direction
    ) -> set[str]:
        result: set[str] = set()
        expiries = sorted({item.contract.expiry for item in items})
        for expiry in expiries:
            expiry_items = [item for item in items if item.contract.expiry == expiry]
            strikes = sorted({item.contract.strike for item in expiry_items})
            if not strikes:
                continue
            atm = min(strikes, key=lambda strike: (abs(strike - underlying_price), strike))
            if direction is Direction.BULLISH:
                itm_candidates = [strike for strike in strikes if strike < atm]
                itm = max(itm_candidates) if itm_candidates else None
            else:
                itm_candidates = [strike for strike in strikes if strike > atm]
                itm = min(itm_candidates) if itm_candidates else None
            allowed = {atm, itm} if itm is not None else {atm}
            result.update(
                item.contract.contract_id
                for item in expiry_items
                if item.contract.strike in allowed
            )
        return result
