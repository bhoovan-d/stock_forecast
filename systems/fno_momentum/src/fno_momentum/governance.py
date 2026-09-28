"""Versioned change control for rules, models, costs, contracts, and live behavior."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from .ledger import ImmutableLedger, sha256_json
from .policy import is_authorized_approver


class ManifestKind(StrEnum):
    UNIVERSE_POLICY = "universe_policy"
    PATTERN = "pattern"
    FEATURE_SCHEMA = "feature_schema"
    MODEL = "model"
    COST_POLICY = "cost_policy"
    CONTRACT_POLICY = "contract_policy"
    PORTFOLIO_POLICY = "portfolio_policy"
    LIVE_BEHAVIOR = "live_behavior"


@dataclass(frozen=True)
class ChangeProposal:
    manifest_id: str
    kind: ManifestKind
    version: str
    definition: dict[str, Any]
    evidence: str
    expected_benefit: str
    risks: str
    test_plan: str

    def validate(self) -> None:
        strings = {
            "manifest_id": self.manifest_id,
            "version": self.version,
            "evidence": self.evidence,
            "expected_benefit": self.expected_benefit,
            "risks": self.risks,
            "test_plan": self.test_plan,
        }
        missing = [name for name, value in strings.items() if not value.strip()]
        if missing or not self.definition:
            raise ValueError("change proposal is incomplete: " + ", ".join(missing or ["definition"]))

    @property
    def content_hash(self) -> str:
        body = asdict(self)
        body["kind"] = self.kind.value
        return sha256_json(body)


class ChangeControl:
    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def propose(self, proposal: ChangeProposal, *, actor_id: str, at: datetime) -> str:
        proposal.validate()
        payload = asdict(proposal)
        payload["kind"] = proposal.kind.value
        payload.update(content_hash=proposal.content_hash, actor_id=actor_id)
        self.ledger.append(
            self._event_name(proposal.kind, "proposed"),
            proposal.manifest_id,
            payload,
            occurred_at=at,
            idempotency_key=f"proposal:{proposal.manifest_id}:{proposal.content_hash}",
        )
        return proposal.content_hash

    def approve(
        self,
        manifest_id: str,
        content_hash: str,
        *,
        actor_id: str,
        rationale: str,
        at: datetime,
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may approve a change")
        if not rationale.strip():
            raise ValueError("approval rationale is required")
        proposal = self._proposal(manifest_id, content_hash)
        if proposal is None:
            raise ValueError("the exact change proposal hash does not exist")
        kind = ManifestKind(proposal.payload["kind"])
        self.ledger.append(
            self._event_name(kind, "approved"),
            manifest_id,
            {"kind": kind.value, "content_hash": content_hash, "actor_id": actor_id,
             "rationale": rationale},
            occurred_at=at,
            idempotency_key=f"approval:{manifest_id}:{content_hash}",
        )

    def reject(
        self,
        manifest_id: str,
        content_hash: str,
        *,
        actor_id: str,
        rationale: str,
        at: datetime,
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may reject a change")
        proposal = self._proposal(manifest_id, content_hash)
        if proposal is None:
            raise ValueError("the exact change proposal hash does not exist")
        kind = ManifestKind(proposal.payload["kind"])
        self.ledger.append(
            self._event_name(kind, "rejected"),
            manifest_id,
            {"kind": kind.value, "content_hash": content_hash, "actor_id": actor_id,
             "rationale": rationale},
            occurred_at=at,
            idempotency_key=f"rejection:{manifest_id}:{content_hash}",
        )

    def activate(self, manifest_id: str, content_hash: str, *, actor_id: str, at: datetime) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may activate a change")
        proposal = self._proposal(manifest_id, content_hash)
        if proposal is None or not self._is_approved(manifest_id, content_hash):
            raise ValueError("activation requires approval of the exact proposal hash")
        active = self.active_hash(manifest_id)
        if active and active != content_hash and self._has_open_forward_cohort(manifest_id, active):
            raise ValueError("active manifest is frozen until its forward cohort completes")
        kind = ManifestKind(proposal.payload["kind"])
        self.ledger.append(
            "manifest_activated",
            manifest_id,
            {"kind": kind.value, "content_hash": content_hash, "actor_id": actor_id},
            occurred_at=at,
            idempotency_key=f"activation:{manifest_id}:{content_hash}",
        )

    def start_forward_cohort(
        self, manifest_id: str, content_hash: str, *, cohort_id: str, at: datetime
    ) -> None:
        self.require_active(manifest_id, content_hash)
        self.ledger.append(
            "forward_cohort_started",
            manifest_id,
            {"content_hash": content_hash, "cohort_id": cohort_id, "target_alerts": 20},
            occurred_at=at,
            idempotency_key=f"cohort-start:{manifest_id}:{cohort_id}",
        )

    def complete_forward_cohort(
        self, manifest_id: str, content_hash: str, *, cohort_id: str, alerts: int, at: datetime
    ) -> None:
        if alerts < 20:
            raise ValueError("a forward cohort requires at least 20 immutable paper alerts")
        self.ledger.append(
            "forward_cohort_completed",
            manifest_id,
            {"content_hash": content_hash, "cohort_id": cohort_id, "alerts": alerts},
            occurred_at=at,
            idempotency_key=f"cohort-complete:{manifest_id}:{cohort_id}",
        )

    def active_hash(self, manifest_id: str) -> str | None:
        active = [
            event.payload["content_hash"]
            for event in self.ledger.events(manifest_id)
            if event.event_type == "manifest_activated"
            and is_authorized_approver(str(event.payload.get("actor_id", "")))
            and self._is_approved(manifest_id, str(event.payload.get("content_hash", "")))
        ]
        return active[-1] if active else None

    def require_active(self, manifest_id: str, content_hash: str) -> None:
        if self.active_hash(manifest_id) != content_hash:
            raise PermissionError("the exact manifest version is not active")

    def active_definition(self, manifest_id: str, content_hash: str) -> dict[str, Any]:
        """Return a copy of an approved active definition, never an unapproved draft."""
        self.require_active(manifest_id, content_hash)
        proposal = self._proposal(manifest_id, content_hash)
        if proposal is None:
            raise ValueError("active manifest has no matching proposal event")
        return dict(proposal.payload["definition"])

    def _proposal(self, manifest_id: str, content_hash: str):
        return next(
            (event for event in self.ledger.events(manifest_id)
             if event.event_type.endswith("_proposed")
             and event.payload.get("content_hash") == content_hash),
            None,
        )

    def _is_approved(self, manifest_id: str, content_hash: str) -> bool:
        return any(
            event.event_type.endswith("_approved")
            and event.payload.get("content_hash") == content_hash
            and is_authorized_approver(str(event.payload.get("actor_id", "")))
            for event in self.ledger.events(manifest_id)
        )

    def _has_open_forward_cohort(self, manifest_id: str, content_hash: str) -> bool:
        events = self.ledger.events(manifest_id)
        starts = {
            event.payload["cohort_id"]
            for event in events
            if event.event_type == "forward_cohort_started"
            and event.payload.get("content_hash") == content_hash
        }
        completed = {
            event.payload["cohort_id"]
            for event in events
            if event.event_type == "forward_cohort_completed"
            and event.payload.get("content_hash") == content_hash
        }
        return bool(starts - completed)

    @staticmethod
    def _event_name(kind: ManifestKind, state: str) -> str:
        return f"model_change_{state}" if kind is ManifestKind.MODEL else f"manifest_change_{state}"
