"""Immutable 2R through 5R outcome labels from point-in-time underlying paths."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .contracts import Direction, MarketBar, available_only, require_aware
from .governance import ChangeControl
from .ledger import ImmutableLedger, LedgerEvent
from .policy import MAX_HOLDING_SESSIONS


@dataclass(frozen=True)
class OutcomeLabels:
    reached_2r: bool
    reached_3r: bool
    reached_4r: bool
    reached_5r: bool
    stop_reached: bool
    terminal_reason: str

    def as_dict(self) -> dict[str, bool]:
        return {
            "2R": self.reached_2r,
            "3R": self.reached_3r,
            "4R": self.reached_4r,
            "5R": self.reached_5r,
        }


class OutcomeLabeler:
    REQUIRED_DEFINITION = {
        "targets_r": [2, 3, 4, 5],
        "horizon_completed_sessions": 3,
        "both_touch_policy": "stop_first_without_lower_timeframe_evidence",
    }

    def __init__(self, ledger: ImmutableLedger, changes: ChangeControl) -> None:
        self.ledger = ledger
        self.changes = changes

    def label_and_record(
        self,
        alert_event_id: str,
        *,
        bars: Iterable[MarketBar],
        completed_sessions: int,
        outcome_at: datetime,
        formula_manifest_id: str,
        formula_manifest_hash: str,
    ) -> str:
        require_aware(outcome_at, "outcome_at")
        definition = self.changes.active_definition(formula_manifest_id, formula_manifest_hash)
        if definition != self.REQUIRED_DEFINITION:
            raise ValueError("outcome formula does not match the implemented binding label policy")
        if completed_sessions < MAX_HOLDING_SESSIONS:
            raise ValueError("final labels require the complete three-session horizon")
        alert = self._alert(alert_event_id)
        path = list(bars)
        if path != sorted(path, key=lambda bar: (bar.interval_start, bar.observation_id)):
            raise ValueError("outcome bars must be supplied in chronological order")
        available_only(path, outcome_at)
        if not path:
            raise ValueError("outcome labeling requires an underlying path")
        labels = self._label(
            Direction(alert.payload["direction"]),
            float(alert.payload["underlying_entry"]),
            float(alert.payload["underlying_stop"]),
            path,
        )
        event = self.ledger.append(
            "outcome_labeled",
            alert_event_id,
            {
                "alert_event_id": alert_event_id,
                "candidate_id": alert.aggregate_id,
                "labels": labels.as_dict(),
                "stop_reached": labels.stop_reached,
                "terminal_reason": labels.terminal_reason,
                "completed_sessions": completed_sessions,
                "bar_observation_ids": [bar.observation_id for bar in path],
                "formula_manifest": [formula_manifest_id, formula_manifest_hash],
            },
            occurred_at=outcome_at,
            decision_at=outcome_at,
            data_cutoff=max(bar.available_at for bar in path),
            idempotency_key=f"outcome-label:{alert_event_id}:{formula_manifest_hash}",
        )
        return event.event_id

    @staticmethod
    def _label(
        direction: Direction, entry: float, stop: float, bars: list[MarketBar]
    ) -> OutcomeLabels:
        risk = abs(entry - stop)
        reached = {target: False for target in (2, 3, 4, 5)}
        stop_reached = False
        for bar in bars:
            stop_hit = bar.low <= stop if direction is Direction.BULLISH else bar.high >= stop
            target_hits = {
                target: (
                    bar.high >= entry + risk * target
                    if direction is Direction.BULLISH
                    else bar.low <= entry - risk * target
                )
                for target in reached
            }
            if stop_hit:
                # Same-bar target/stop paths are unknowable at this resolution and fail closed.
                stop_reached = True
                break
            for target, hit in target_hits.items():
                reached[target] = reached[target] or hit
        return OutcomeLabels(
            reached_2r=reached[2],
            reached_3r=reached[3],
            reached_4r=reached[4],
            reached_5r=reached[5],
            stop_reached=stop_reached,
            terminal_reason="stop" if stop_reached else "three_session_expiry",
        )

    def _alert(self, event_id: str) -> LedgerEvent:
        event = next((event for event in self.ledger.events() if event.event_id == event_id), None)
        if event is None or event.event_type != "core_paper_alert":
            raise ValueError("core paper alert does not exist")
        return event
