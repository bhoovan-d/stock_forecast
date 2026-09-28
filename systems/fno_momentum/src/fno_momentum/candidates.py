"""Auditable candidate lifecycle for the clean-room FnO track."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .contracts import (
    DecisionContext,
    Direction,
    EntryTimeframe,
    MarketBar,
    PatternFamily,
    QuoteSnapshot,
    available_only,
)
from .governance import ChangeControl
from .ledger import ImmutableLedger, sha256_json
from .policy import MAX_CORE_ALERTS_PER_SESSION, MAX_STOP_PCT, MIN_STOP_PCT
from .source_registry import SourceRegistry


TERMINAL_CANDIDATE_EVENTS = {"candidate_rejected", "core_paper_alert"}


@dataclass(frozen=True)
class CandidateDraft:
    symbol: str
    direction: Direction
    pattern_family: PatternFamily
    pattern_variant: str
    entry_timeframe: EntryTimeframe
    context: DecisionContext
    pattern_manifest_id: str
    pattern_manifest_hash: str
    source_versions: tuple[tuple[str, str], ...]

    @property
    def candidate_id(self) -> str:
        return sha256_json(
            {
                "symbol": self.symbol.upper(),
                "direction": self.direction.value,
                "pattern_family": self.pattern_family.value,
                "pattern_variant": self.pattern_variant,
                "entry_timeframe": self.entry_timeframe.value,
                "decision_at": self.context.decision_at.isoformat(),
                "pattern_manifest_hash": self.pattern_manifest_hash,
            }
        )[:32]


@dataclass(frozen=True)
class CorePaperAlert:
    candidate_id: str
    symbol: str
    direction: Direction
    option_contract_id: str
    option_position: str
    entry_rule: str
    underlying_entry: float
    underlying_stop: float
    selected_target_r: int
    predicted_move_pct: float
    target_probabilities: tuple[tuple[int, float], ...]
    expected_net_r: float
    quote: QuoteSnapshot
    model_version: str
    feature_schema_version: str
    source_hashes: tuple[str, ...]
    reason_codes: tuple[str, ...]

    @property
    def stop_pct(self) -> float:
        if self.direction is Direction.BULLISH:
            return (self.underlying_entry - self.underlying_stop) / self.underlying_entry * 100
        return (self.underlying_stop - self.underlying_entry) / self.underlying_entry * 100

    def validate(self, decision_at: datetime) -> None:
        expected_position = "long_call" if self.direction is Direction.BULLISH else "long_put"
        if self.option_position != expected_position:
            raise ValueError(f"{self.direction.value} candidates require {expected_position}")
        if self.underlying_entry <= 0:
            raise ValueError("underlying entry must be positive")
        if not MIN_STOP_PCT <= self.stop_pct <= MAX_STOP_PCT:
            raise ValueError(f"structural stop must be between {MIN_STOP_PCT}% and {MAX_STOP_PCT}%")
        if self.selected_target_r not in (2, 3, 4, 5):
            raise ValueError("selected target must be one of 2R, 3R, 4R, or 5R")
        if not 6.0 <= self.predicted_move_pct <= 8.0:
            raise ValueError("core publication requires a 6% to 8% predicted underlying move")
        probabilities = dict(self.target_probabilities)
        if set(probabilities) != {2, 3, 4, 5}:
            raise ValueError("probabilities for 2R, 3R, 4R, and 5R are required")
        if any(not 0 <= value <= 1 for value in probabilities.values()):
            raise ValueError("target probabilities must be between zero and one")
        if not all(probabilities[target] >= probabilities[target + 1] for target in (2, 3, 4)):
            raise ValueError("higher-R target probabilities cannot exceed lower-R probabilities")
        if self.expected_net_r <= 0:
            raise ValueError("core publication requires positive expected net R")
        if not self.entry_rule.strip() or not self.model_version.strip() or not self.feature_schema_version.strip():
            raise ValueError("entry rule and model/feature versions are required")
        if not self.source_hashes or not self.reason_codes:
            raise ValueError("source hashes and machine-readable reason codes are required")
        available_only([self.quote], decision_at)


class CandidateLifecycle:
    def __init__(
        self,
        ledger: ImmutableLedger,
        changes: ChangeControl,
        sources: SourceRegistry,
    ) -> None:
        self.ledger = ledger
        self.changes = changes
        self.sources = sources

    def create(self, draft: CandidateDraft, inputs: Iterable[MarketBar]) -> str:
        materialized_inputs = available_only(inputs, draft.context.decision_at)
        self.changes.require_active(draft.pattern_manifest_id, draft.pattern_manifest_hash)
        if not draft.source_versions:
            raise ValueError("candidate must reference at least one approved source")
        for source_id, content_hash in draft.source_versions:
            self.sources.require_active(source_id, content_hash)
        input_observations = {item.observation_id for item in materialized_inputs}
        declared_observations = set(draft.context.source_observation_ids)
        if not input_observations or not input_observations.issubset(declared_observations):
            raise ValueError("every candidate input must reference declared raw source evidence")
        for observation_id in declared_observations:
            self.sources.require_observation(
                observation_id,
                decision_at=draft.context.decision_at,
                allowed_versions=draft.source_versions,
            )
        payload = {
            "symbol": draft.symbol.upper(),
            "direction": draft.direction.value,
            "pattern_family": draft.pattern_family.value,
            "pattern_variant": draft.pattern_variant,
            "entry_timeframe": draft.entry_timeframe.value,
            "pattern_manifest_id": draft.pattern_manifest_id,
            "pattern_manifest_hash": draft.pattern_manifest_hash,
            "source_versions": [list(item) for item in draft.source_versions],
            "source_observation_ids": list(draft.context.source_observation_ids),
        }
        self.ledger.append(
            "candidate_created",
            draft.candidate_id,
            payload,
            occurred_at=draft.context.decision_at,
            decision_at=draft.context.decision_at,
            data_cutoff=draft.context.data_cutoff,
            idempotency_key=f"candidate-created:{draft.candidate_id}",
        )
        return draft.candidate_id

    def reject(
        self,
        candidate_id: str,
        *,
        reason_code: str,
        detail: str,
        at: datetime,
    ) -> None:
        self._require_open(candidate_id)
        if not reason_code.strip() or not detail.strip():
            raise ValueError("rejection reason code and detail are required")
        self.ledger.append(
            "candidate_rejected",
            candidate_id,
            {"reason_code": reason_code, "detail": detail},
            occurred_at=at,
            idempotency_key=f"candidate-rejected:{candidate_id}",
        )

    def publish(
        self,
        alert: CorePaperAlert,
        *,
        context: DecisionContext,
        active_manifests: tuple[tuple[str, str], ...],
    ) -> str:
        self._require_open(alert.candidate_id)
        alert.validate(context.decision_at)
        quote_allowed_versions = tuple(
            (
                event.payload["source_id"],
                event.payload["source_hash"],
            )
            for event in self.ledger.events(alert.quote.observation_id)
            if event.event_type == "source_observed"
            and event.payload.get("source_hash") in alert.source_hashes
        )
        if not quote_allowed_versions:
            raise ValueError("option quote lacks raw evidence from an alert source hash")
        self.sources.require_observation(
            alert.quote.observation_id,
            decision_at=context.decision_at,
            allowed_versions=quote_allowed_versions,
        )
        for manifest_id, content_hash in active_manifests:
            self.changes.require_active(manifest_id, content_hash)
        active = dict(active_manifests)
        if alert.model_version not in active:
            raise ValueError("the alert model version must be supplied as an active manifest")
        if alert.feature_schema_version not in active:
            raise ValueError("the alert feature schema must be supplied as an active manifest")
        model_definition = self.changes.active_definition(
            alert.model_version, active[alert.model_version]
        )
        if "minimum_calibrated_2r_probability" not in model_definition:
            raise ValueError("active model manifest lacks its publication probability threshold")
        threshold = float(model_definition["minimum_calibrated_2r_probability"])
        if not 0 <= threshold <= 1:
            raise ValueError("model publication probability threshold must be between zero and one")
        if dict(alert.target_probabilities)[2] < threshold:
            raise ValueError("candidate does not clear the active calibrated 2R threshold")
        session = context.decision_at.date().isoformat()
        published = [
            event for event in self.ledger.events()
            if event.event_type == "core_paper_alert"
            and event.decision_at is not None
            and event.decision_at[:10] == session
        ]
        if len(published) >= MAX_CORE_ALERTS_PER_SESSION:
            raise ValueError("the per-session Core Paper Alert limit has been reached")
        payload = {
            "symbol": alert.symbol.upper(),
            "direction": alert.direction.value,
            "option_contract": alert.option_contract_id,
            "option_position": alert.option_position,
            "entry_rule": alert.entry_rule,
            "underlying_entry": alert.underlying_entry,
            "underlying_stop": alert.underlying_stop,
            "structural_stop_pct": alert.stop_pct,
            "selected_target_r": alert.selected_target_r,
            "predicted_move_pct": alert.predicted_move_pct,
            "target_probabilities": {
                str(target): probability for target, probability in alert.target_probabilities
            },
            "expected_net_r": alert.expected_net_r,
            "calibrated_2r_threshold": threshold,
            "option_bid": alert.quote.bid,
            "option_ask": alert.quote.ask,
            "quote_available_at": alert.quote.available_at.isoformat(),
            "model_version": alert.model_version,
            "feature_schema_version": alert.feature_schema_version,
            "source_hashes": list(alert.source_hashes),
            "reason_codes": list(alert.reason_codes),
            "active_manifests": [list(item) for item in active_manifests],
        }
        event = self.ledger.append(
            "core_paper_alert",
            alert.candidate_id,
            payload,
            occurred_at=context.decision_at,
            decision_at=context.decision_at,
            data_cutoff=context.data_cutoff,
            idempotency_key=f"core-paper-alert:{alert.candidate_id}",
        )
        return event.event_id

    def unreconciled(self) -> list[str]:
        events = self.ledger.events()
        created = {event.aggregate_id for event in events if event.event_type == "candidate_created"}
        terminal = {
            event.aggregate_id for event in events
            if event.event_type in TERMINAL_CANDIDATE_EVENTS
        }
        return sorted(created - terminal)

    def _require_open(self, candidate_id: str) -> None:
        events = self.ledger.events(candidate_id)
        if not any(event.event_type == "candidate_created" for event in events):
            raise ValueError("candidate does not exist")
        if any(event.event_type in TERMINAL_CANDIDATE_EVENTS for event in events):
            raise ValueError("candidate already has a terminal decision")
