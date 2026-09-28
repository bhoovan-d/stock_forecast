"""Clean-room, higher-timeframe-anchored Liquidity Sweep reference slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from .contracts import Direction, EntryTimeframe, MarketBar, available_only, require_aware
from .governance import ChangeControl


class AnchorKind(StrEnum):
    DAILY_SWING = "daily_swing"
    WEEKLY_SWING = "weekly_swing"
    DAILY_SHELF = "daily_shelf"
    WEEKLY_SHELF = "weekly_shelf"
    PRIOR_SESSION = "prior_session"


class SweepVariant(StrEnum):
    REVERSAL_RECLAIM = "reversal_reclaim"
    CONTINUATION = "continuation"


@dataclass(frozen=True)
class LiquidityAnchor:
    anchor_id: str
    kind: AnchorKind
    price: float
    touches: int
    formed_at: datetime
    available_at: datetime
    observation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        require_aware(self.formed_at, "formed_at")
        require_aware(self.available_at, "available_at")
        if self.price <= 0:
            raise ValueError("anchor price must be positive")
        if self.touches < 1:
            raise ValueError("anchor must have at least one prior touch")
        if self.available_at < self.formed_at:
            raise ValueError("anchor cannot be available before it forms")
        if not self.observation_ids:
            raise ValueError("anchor must retain its source observations")


@dataclass(frozen=True)
class SweepResult:
    matched: bool
    reason_code: str
    detail: str
    anchor_id: str
    anchor_kind: AnchorKind
    anchor_price: float
    direction: Direction
    variant: SweepVariant
    entry_timeframe: EntryTimeframe
    sweep_at: datetime | None = None
    confirmation_at: datetime | None = None
    sweep_distance_bps: float = 0.0
    reclaim_distance_bps: float = 0.0


@dataclass(frozen=True)
class SweepRule:
    minimum_prior_touches: int
    minimum_sweep_bps: float
    minimum_reclaim_bps: float
    confirmation_bars: int
    allowed_anchor_kinds: tuple[AnchorKind, ...]
    variant: SweepVariant
    direction: Direction | None = None
    entry_timeframe: EntryTimeframe | None = None

    @classmethod
    def from_definition(cls, value: dict) -> "SweepRule":
        required = {
            "minimum_prior_touches",
            "minimum_sweep_bps",
            "minimum_reclaim_bps",
            "confirmation_bars",
            "allowed_anchor_kinds",
            "variant",
        }
        missing = required - set(value)
        if missing:
            raise ValueError("active sweep definition is incomplete: " + ", ".join(sorted(missing)))
        rule = cls(
            minimum_prior_touches=int(value["minimum_prior_touches"]),
            minimum_sweep_bps=float(value["minimum_sweep_bps"]),
            minimum_reclaim_bps=float(value["minimum_reclaim_bps"]),
            confirmation_bars=int(value["confirmation_bars"]),
            allowed_anchor_kinds=tuple(AnchorKind(item) for item in value["allowed_anchor_kinds"]),
            variant=SweepVariant(value["variant"]),
            direction=Direction(value["direction"]) if value.get("direction") else None,
            entry_timeframe=(
                EntryTimeframe(value["entry_timeframe"])
                if value.get("entry_timeframe") else None
            ),
        )
        if rule.minimum_prior_touches < 1 or rule.confirmation_bars < 1:
            raise ValueError("touch and confirmation counts must be positive")
        if rule.minimum_sweep_bps < 0 or rule.minimum_reclaim_bps < 0:
            raise ValueError("sweep and reclaim distances cannot be negative")
        return rule


class LiquiditySweepDetector:
    def __init__(self, changes: ChangeControl) -> None:
        self.changes = changes

    def detect(
        self,
        bars: Iterable[MarketBar],
        anchor: LiquidityAnchor,
        *,
        direction: Direction,
        entry_timeframe: EntryTimeframe,
        decision_at: datetime,
        manifest_id: str,
        manifest_hash: str,
    ) -> SweepResult:
        require_aware(decision_at, "decision_at")
        available_only([anchor], decision_at)
        ordered = sorted(available_only(bars, decision_at), key=lambda bar: bar.interval_start)
        definition = self.changes.active_definition(manifest_id, manifest_hash)
        rule = SweepRule.from_definition(definition)
        if rule.variant is not SweepVariant.REVERSAL_RECLAIM:
            raise NotImplementedError("the first vertical slice implements reversal/reclaim only")
        if rule.direction is not None and direction is not rule.direction:
            return self._reject("DIRECTION_NOT_APPROVED", "Direction is not approved for this rule.",
                                anchor, direction, entry_timeframe, rule.variant)
        if rule.entry_timeframe is not None and entry_timeframe is not rule.entry_timeframe:
            return self._reject("ENTRY_TIMEFRAME_NOT_APPROVED",
                                "Entry timeframe is not approved for this rule.", anchor,
                                direction, entry_timeframe, rule.variant)
        if anchor.kind not in rule.allowed_anchor_kinds:
            return self._reject("ANCHOR_KIND_NOT_APPROVED", "Anchor kind is not approved.", anchor,
                                direction, entry_timeframe, rule.variant)
        if anchor.touches < rule.minimum_prior_touches:
            return self._reject("INSUFFICIENT_ANCHOR_TOUCHES", "Anchor has too few prior touches.",
                                anchor, direction, entry_timeframe, rule.variant)
        if not ordered:
            return self._reject("NO_COMPLETED_ENTRY_BARS", "No completed entry bars are available.",
                                anchor, direction, entry_timeframe, rule.variant)
        if any(bar.interval != entry_timeframe.value for bar in ordered):
            return self._reject("ENTRY_TIMEFRAME_MISMATCH", "Bars do not match the approved entry timeframe.",
                                anchor, direction, entry_timeframe, rule.variant)

        window = ordered[-rule.confirmation_bars:]
        sweep_index = None
        sweep_distance = 0.0
        for index, bar in enumerate(window):
            distance = self._sweep_distance_bps(bar, anchor.price, direction)
            if distance >= rule.minimum_sweep_bps:
                sweep_index, sweep_distance = index, distance
                break
        if sweep_index is None:
            return self._reject("NO_LIQUIDITY_SWEEP", "Price did not sweep the approved anchor.",
                                anchor, direction, entry_timeframe, rule.variant)

        confirmation = None
        reclaim_distance = 0.0
        for bar in window[sweep_index:]:
            distance = self._reclaim_distance_bps(bar, anchor.price, direction)
            if distance >= rule.minimum_reclaim_bps:
                confirmation, reclaim_distance = bar, distance
                break
        if confirmation is None:
            return SweepResult(
                matched=False,
                reason_code="SWEEP_NOT_RECLAIMED",
                detail="The anchor was swept but no approved reclaim closed afterward.",
                anchor_id=anchor.anchor_id,
                anchor_kind=anchor.kind,
                anchor_price=anchor.price,
                direction=direction,
                variant=rule.variant,
                entry_timeframe=entry_timeframe,
                sweep_at=window[sweep_index].interval_end,
                sweep_distance_bps=round(sweep_distance, 4),
            )
        return SweepResult(
            matched=True,
            reason_code="LIQUIDITY_SWEEP_RECLAIM_CONFIRMED",
            detail="Approved higher-timeframe anchor was swept and reclaimed on a completed bar.",
            anchor_id=anchor.anchor_id,
            anchor_kind=anchor.kind,
            anchor_price=anchor.price,
            direction=direction,
            variant=rule.variant,
            entry_timeframe=entry_timeframe,
            sweep_at=window[sweep_index].interval_end,
            confirmation_at=confirmation.interval_end,
            sweep_distance_bps=round(sweep_distance, 4),
            reclaim_distance_bps=round(reclaim_distance, 4),
        )

    @staticmethod
    def _sweep_distance_bps(bar: MarketBar, level: float, direction: Direction) -> float:
        extreme = bar.low if direction is Direction.BULLISH else bar.high
        return ((level - extreme) / level if direction is Direction.BULLISH
                else (extreme - level) / level) * 10_000

    @staticmethod
    def _reclaim_distance_bps(bar: MarketBar, level: float, direction: Direction) -> float:
        return ((bar.close - level) / level if direction is Direction.BULLISH
                else (level - bar.close) / level) * 10_000

    @staticmethod
    def _reject(code, detail, anchor, direction, timeframe, variant) -> SweepResult:
        return SweepResult(
            matched=False,
            reason_code=code,
            detail=detail,
            anchor_id=anchor.anchor_id,
            anchor_kind=anchor.kind,
            anchor_price=anchor.price,
            direction=direction,
            variant=variant,
            entry_timeframe=timeframe,
        )
