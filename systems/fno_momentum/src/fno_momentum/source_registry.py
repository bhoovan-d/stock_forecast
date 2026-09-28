"""Approval-gated source registry for FnO Momentum."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .contracts import RawObservation, require_aware
from .ledger import ImmutableLedger, sha256_json
from .policy import is_authorized_approver


@dataclass(frozen=True)
class SourceManifest:
    source_id: str
    name: str
    source_type: str
    lawful_access_basis: str
    terms_reference: str
    fields: tuple[str, ...]
    latency: str
    monthly_cost_inr: int
    collection_method: str
    retention_policy: str
    version: str

    @property
    def content_hash(self) -> str:
        return sha256_json(asdict(self))

    def validate(self) -> None:
        required = {
            "source_id": self.source_id,
            "name": self.name,
            "source_type": self.source_type,
            "lawful_access_basis": self.lawful_access_basis,
            "terms_reference": self.terms_reference,
            "latency": self.latency,
            "collection_method": self.collection_method,
            "retention_policy": self.retention_policy,
            "version": self.version,
        }
        missing = [name for name, value in required.items() if not value.strip()]
        if missing or not self.fields:
            raise ValueError("source manifest is incomplete: " + ", ".join(missing or ["fields"]))
        if self.monthly_cost_inr < 0:
            raise ValueError("monthly source cost cannot be negative")


class SourceRegistry:
    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def propose(self, manifest: SourceManifest, *, actor_id: str, at: datetime) -> str:
        manifest.validate()
        payload = {**asdict(manifest), "content_hash": manifest.content_hash, "actor_id": actor_id}
        self.ledger.append(
            "source_registry_proposed",
            manifest.source_id,
            payload,
            occurred_at=at,
            idempotency_key=f"source-proposal:{manifest.source_id}:{manifest.content_hash}",
        )
        return manifest.content_hash

    def approve(
        self, source_id: str, content_hash: str, *, actor_id: str, rationale: str, at: datetime
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may approve a source")
        if not rationale.strip():
            raise ValueError("approval rationale is required")
        if self._proposal(source_id, content_hash) is None:
            raise ValueError("the exact source proposal hash does not exist")
        self.ledger.append(
            "source_registry_approved",
            source_id,
            {
                "content_hash": content_hash,
                "approval_actor_id": actor_id,
                "rationale": rationale,
            },
            occurred_at=at,
            idempotency_key=f"source-approval:{source_id}:{content_hash}",
        )

    def activate(self, source_id: str, content_hash: str, *, actor_id: str, at: datetime) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may activate a source")
        if not self._approved(source_id, content_hash):
            raise ValueError("source activation requires approval of the exact content hash")
        self.ledger.append(
            "source_registry_activated",
            source_id,
            {"content_hash": content_hash, "activation_actor_id": actor_id},
            occurred_at=at,
            idempotency_key=f"source-activation:{source_id}:{content_hash}",
        )

    def is_active(self, source_id: str, content_hash: str) -> bool:
        events = self.ledger.events(source_id)
        approved = any(
            event.event_type == "source_registry_approved"
            and event.payload.get("content_hash") == content_hash
            and is_authorized_approver(str(event.payload.get("approval_actor_id", "")))
            for event in events
        )
        activated = any(
            event.event_type == "source_registry_activated"
            and event.payload.get("content_hash") == content_hash
            and is_authorized_approver(str(event.payload.get("activation_actor_id", "")))
            for event in events
        )
        return approved and activated

    def require_active(self, source_id: str, content_hash: str) -> None:
        if not self.is_active(source_id, content_hash):
            raise PermissionError("collection from an unapproved or inactive source is forbidden")

    def record_observation(
        self,
        observation: RawObservation,
        *,
        source_hash: str,
    ) -> str:
        """Record raw evidence only after its exact source version is active."""
        if observation.source_id.strip() == "":
            raise ValueError("observation source_id is required")
        self.require_active(observation.source_id, source_hash)
        event = self.ledger.append(
            "source_observed",
            observation.observation_id,
            {
                "source_id": observation.source_id,
                "source_hash": source_hash,
                "retrieved_at": observation.retrieved_at.isoformat(),
                "published_at": (
                    observation.published_at.isoformat()
                    if observation.published_at is not None else None
                ),
                "content_sha256": observation.content_sha256,
                "parser_version": observation.parser_version,
                "license_tier": observation.license_tier,
                "storage_ref": observation.storage_ref,
            },
            occurred_at=observation.retrieved_at,
            decision_at=observation.retrieved_at,
            data_cutoff=observation.retrieved_at,
            idempotency_key=f"source-observed:{observation.observation_id}",
        )
        return event.event_id

    def require_observation(
        self,
        observation_id: str,
        *,
        decision_at: datetime,
        allowed_versions: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        require_aware(decision_at, "decision_at")
        event = next(
            (
                event for event in self.ledger.events(observation_id)
                if event.event_type == "source_observed"
            ),
            None,
        )
        if event is None:
            raise ValueError(f"raw source observation is missing: {observation_id}")
        if datetime.fromisoformat(event.payload["retrieved_at"]) > decision_at:
            raise ValueError("future source observation attempted to enter a decision")
        source_version = (event.payload["source_id"], event.payload["source_hash"])
        if allowed_versions is not None and source_version not in allowed_versions:
            raise PermissionError("observation does not belong to an allowed source version")
        self.require_active(*source_version)

    def _proposal(self, source_id: str, content_hash: str):
        return next(
            (
                event
                for event in self.ledger.events(source_id)
                if event.event_type == "source_registry_proposed"
                and event.payload.get("content_hash") == content_hash
            ),
            None,
        )

    def _approved(self, source_id: str, content_hash: str) -> bool:
        return any(
            event.event_type == "source_registry_approved"
            and event.payload.get("content_hash") == content_hash
            and is_authorized_approver(str(event.payload.get("approval_actor_id", "")))
            for event in self.ledger.events(source_id)
        )
