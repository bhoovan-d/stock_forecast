"""Pattern-direction cohort reports without cross-group pooling."""

from __future__ import annotations

from dataclasses import dataclass

from .ledger import ImmutableLedger


@dataclass(frozen=True, order=True)
class CohortKey:
    pattern_family: str
    pattern_variant: str
    direction: str
    entry_timeframe: str


@dataclass(frozen=True)
class CohortMetrics:
    key: CohortKey
    alerts: int
    labeled_outcomes: int
    settled_positions: int
    target_hit_rate: dict[str, float | None]
    option_net_pnl_per_lot: float
    average_normalized_r: float | None
    maximum_option_drawdown_per_lot: float
    outcome_coverage: float
    pilot_historical_ready: bool
    pilot_forward_ready: bool
    statistical_claim: str = "50% is an operational target, not a validated true win rate"


class CohortReporter:
    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def build(self) -> tuple[CohortMetrics, ...]:
        events = self.ledger.events()
        candidates = {
            event.aggregate_id: event
            for event in events if event.event_type == "candidate_created"
        }
        alerts = [event for event in events if event.event_type == "core_paper_alert"]
        outcomes = {
            event.payload["alert_event_id"]: event
            for event in events if event.event_type == "outcome_labeled"
        }
        settlements_by_alert = {
            event.payload["alert_event_id"]: event
            for event in events if event.event_type == "settlement"
        }
        grouped: dict[CohortKey, list] = {}
        for alert in alerts:
            candidate = candidates.get(alert.aggregate_id)
            if candidate is None:
                continue
            key = CohortKey(
                pattern_family=candidate.payload["pattern_family"],
                pattern_variant=candidate.payload["pattern_variant"],
                direction=candidate.payload["direction"],
                entry_timeframe=candidate.payload["entry_timeframe"],
            )
            grouped.setdefault(key, []).append(alert)
        reports = []
        for key in sorted(grouped):
            cohort_alerts = grouped[key]
            cohort_outcomes = [outcomes[a.event_id] for a in cohort_alerts if a.event_id in outcomes]
            cohort_settlements = [
                settlements_by_alert[a.event_id]
                for a in cohort_alerts if a.event_id in settlements_by_alert
            ]
            rates = {
                target: (
                    sum(bool(event.payload["labels"][target]) for event in cohort_outcomes)
                    / len(cohort_outcomes)
                    if cohort_outcomes else None
                )
                for target in ("2R", "3R", "4R", "5R")
            }
            pnl_path = [float(event.payload["net_option_pnl_per_lot"]) for event in cohort_settlements]
            maximum_drawdown = self._maximum_drawdown(pnl_path)
            normalized = [
                float(event.payload["normalized_underlying_r"])
                for event in cohort_settlements
            ]
            reports.append(
                CohortMetrics(
                    key=key,
                    alerts=len(cohort_alerts),
                    labeled_outcomes=len(cohort_outcomes),
                    settled_positions=len(cohort_settlements),
                    target_hit_rate=rates,
                    option_net_pnl_per_lot=sum(pnl_path),
                    average_normalized_r=(sum(normalized) / len(normalized) if normalized else None),
                    maximum_option_drawdown_per_lot=maximum_drawdown,
                    outcome_coverage=(len(cohort_outcomes) / len(cohort_alerts)),
                    pilot_historical_ready=len(cohort_outcomes) >= 25,
                    pilot_forward_ready=len(cohort_alerts) >= 20 and len(cohort_outcomes) == len(cohort_alerts),
                )
            )
        return tuple(reports)

    @staticmethod
    def _maximum_drawdown(pnl_values: list[float]) -> float:
        equity = 0.0
        peak = 0.0
        maximum = 0.0
        for pnl in pnl_values:
            equity += pnl
            peak = max(peak, equity)
            maximum = max(maximum, peak - equity)
        return maximum
