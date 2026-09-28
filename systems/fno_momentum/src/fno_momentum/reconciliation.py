"""Cross-event reconciliation and dashboard-oriented evidence projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .ledger import ImmutableLedger, LedgerEvent


@dataclass(frozen=True)
class ReconciliationReport:
    balanced: bool
    errors: tuple[str, ...]
    open_candidates: tuple[str, ...]
    open_orders: tuple[str, ...]
    open_positions: tuple[str, ...]


class Reconciler:
    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def run(self) -> ReconciliationReport:
        self.ledger.verify()
        events = self.ledger.events()
        by_id = {event.event_id: event for event in events}
        errors: list[str] = []
        created_candidates = {
            event.aggregate_id for event in events if event.event_type == "candidate_created"
        }
        candidate_terminals = {
            event.aggregate_id for event in events
            if event.event_type in {"candidate_rejected", "core_paper_alert"}
        }
        orders = {event.aggregate_id: event for event in events if event.event_type == "order_created"}
        order_terminals = {
            event.aggregate_id for event in events
            if event.event_type in {"order_unfilled", "paper_fill"}
        }
        fills = {event.aggregate_id: event for event in events if event.event_type == "paper_fill"}
        settlements = {
            event.aggregate_id: event for event in events if event.event_type == "settlement"
        }
        triggers = {
            event.aggregate_id: event for event in events
            if event.event_type in {
                "paper_stop", "paper_target", "paper_expiry", "paper_premium_guard"
            }
        }
        source_observations = {
            event.aggregate_id for event in events if event.event_type == "source_observed"
        }

        for event in events:
            if event.event_type == "core_paper_alert" and event.aggregate_id not in created_candidates:
                errors.append(f"alert {event.event_id} has no candidate")
            if event.event_type == "order_created":
                alert_id = event.payload.get("alert_event_id")
                if alert_id not in by_id or by_id[alert_id].event_type != "core_paper_alert":
                    errors.append(f"order {event.aggregate_id} has no alert")
            if event.event_type == "paper_fill" and event.aggregate_id not in orders:
                errors.append(f"fill {event.aggregate_id} has no order")
            if event.event_type == "settlement":
                if event.aggregate_id not in fills:
                    errors.append(f"settlement {event.aggregate_id} has no fill")
                trigger_id = event.payload.get("exit_trigger_event_id")
                if trigger_id not in by_id or by_id[trigger_id].aggregate_id != event.aggregate_id:
                    errors.append(f"settlement {event.aggregate_id} has no matching exit trigger")
        for candidate_id in created_candidates:
            created = next(
                event for event in events
                if event.event_type == "candidate_created" and event.aggregate_id == candidate_id
            )
            for observation_id in created.payload.get("source_observation_ids", []):
                if observation_id not in source_observations:
                    errors.append(
                        f"candidate {candidate_id} references missing source observation {observation_id}"
                    )
        for position_id in settlements:
            if position_id not in triggers:
                errors.append(f"settlement {position_id} has no exit trigger")

        open_candidates = tuple(sorted(created_candidates - candidate_terminals))
        open_orders = tuple(sorted(set(orders) - order_terminals))
        open_positions = tuple(sorted(set(fills) - set(settlements)))
        return ReconciliationReport(
            balanced=not errors,
            errors=tuple(sorted(set(errors))),
            open_candidates=open_candidates,
            open_orders=open_orders,
            open_positions=open_positions,
        )


class DashboardProjection:
    """Read-only view; immutable ledger remains the system of record."""

    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def snapshot(self) -> dict[str, Any]:
        report = Reconciler(self.ledger).run()
        events = self.ledger.events()
        counts: dict[str, int] = {}
        for event in events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        settlements = [
            {
                "position_id": event.aggregate_id,
                "exit_reason": event.payload["exit_reason"],
                "net_option_pnl_per_lot": event.payload["net_option_pnl_per_lot"],
                "normalized_underlying_r": event.payload["normalized_underlying_r"],
                "event_id": event.event_id,
                "event_hash": event.event_hash,
            }
            for event in events if event.event_type == "settlement"
        ]
        return {
            "ledger_balanced": report.balanced,
            "reconciliation_errors": list(report.errors),
            "event_counts": counts,
            "open_candidates": list(report.open_candidates),
            "open_orders": list(report.open_orders),
            "open_positions": list(report.open_positions),
            "settlements": settlements,
        }

    def alert_evidence(self, alert_event_id: str) -> dict[str, Any]:
        events = self.ledger.events()
        by_id = {event.event_id: event for event in events}
        alert = by_id.get(alert_event_id)
        if alert is None or alert.event_type != "core_paper_alert":
            raise ValueError("core paper alert does not exist")
        candidate = next(
            (
                event for event in events
                if event.event_type == "candidate_created"
                and event.aggregate_id == alert.aggregate_id
            ),
            None,
        )
        observation_ids = candidate.payload.get("source_observation_ids", []) if candidate else []
        source_events = [
            self._event_view(event) for event in events
            if event.event_type == "source_observed" and event.aggregate_id in observation_ids
        ]
        linked = [
            self._event_view(event) for event in events
            if event.event_id == alert_event_id
            or event.payload.get("alert_event_id") == alert_event_id
        ]
        return {
            "alert": self._event_view(alert),
            "candidate": self._event_view(candidate) if candidate else None,
            "source_observations": source_events,
            "lifecycle": linked,
        }

    @staticmethod
    def _event_view(event: LedgerEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "event_type": event.event_type,
            "aggregate_id": event.aggregate_id,
            "occurred_at": event.occurred_at,
            "decision_at": event.decision_at,
            "data_cutoff": event.data_cutoff,
            "payload": event.payload,
            "payload_hash": event.payload_hash,
            "event_hash": event.event_hash,
            "previous_hash": event.previous_hash,
        }
