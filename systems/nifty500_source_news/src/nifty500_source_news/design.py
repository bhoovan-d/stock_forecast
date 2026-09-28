"""Exact-hash governance for the independent Source News system design."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .ledger import NewsLedger, hash_manifest
from .policy import is_authorized_approver


@dataclass(frozen=True)
class NewsDesignProposal:
    design_id: str
    version: str
    universe_policy: dict[str, Any]
    instrument_policy: dict[str, Any]
    source_categories: tuple[str, ...]
    candidate_policy: dict[str, Any]
    model_policy: dict[str, Any]
    timeframe_policy: dict[str, Any]
    entry_policy: dict[str, Any]
    exit_policy: dict[str, Any]
    reporting_policy: dict[str, Any]
    evidence: str
    risks: str
    test_plan: str

    def validate(self) -> None:
        value = asdict(self)
        missing = [key for key, item in value.items() if not item]
        if missing:
            raise ValueError("news design proposal is incomplete: " + ", ".join(missing))

    @property
    def content_hash(self) -> str:
        return hash_manifest(asdict(self))


class NewsDesignControl:
    def __init__(self, ledger: NewsLedger) -> None:
        self.ledger = ledger

    def propose(self, proposal: NewsDesignProposal, *, actor_id: str, at: datetime) -> str:
        proposal.validate()
        payload = {**asdict(proposal), "content_hash": proposal.content_hash, "actor_id": actor_id}
        self.ledger.append(
            "news_design_proposed",
            proposal.design_id,
            payload,
            occurred_at=at,
            idempotency_key=f"news-design-proposal:{proposal.design_id}:{proposal.content_hash}",
        )
        return proposal.content_hash

    def approve(
        self,
        design_id: str,
        content_hash: str,
        *,
        actor_id: str,
        rationale: str,
        at: datetime,
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may approve the news design")
        if not rationale.strip():
            raise ValueError("news design approval rationale is required")
        if self._proposal(design_id, content_hash) is None:
            raise ValueError("the exact news design proposal hash does not exist")
        self.ledger.append(
            "news_design_approved",
            design_id,
            {"content_hash": content_hash, "actor_id": actor_id, "rationale": rationale},
            occurred_at=at,
            idempotency_key=f"news-design-approval:{design_id}:{content_hash}",
        )

    def activate(
        self, design_id: str, content_hash: str, *, actor_id: str, at: datetime
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may activate the news design")
        if not any(
            event.event_type == "news_design_approved"
            and event.payload.get("content_hash") == content_hash
            and is_authorized_approver(str(event.payload.get("actor_id", "")))
            for event in self.ledger.events()
            if event.aggregate_id == design_id
        ):
            raise ValueError("news design activation requires exact-hash approval")
        self.ledger.append(
            "news_design_activated",
            design_id,
            {"content_hash": content_hash, "actor_id": actor_id},
            occurred_at=at,
            idempotency_key=f"news-design-activation:{design_id}:{content_hash}",
        )

    def require_active(self, design_id: str, content_hash: str) -> None:
        active = [
            event.payload["content_hash"]
            for event in self.ledger.events()
            if event.aggregate_id == design_id
            and event.event_type == "news_design_activated"
            and is_authorized_approver(str(event.payload.get("actor_id", "")))
            and any(
                approved.event_type == "news_design_approved"
                and approved.aggregate_id == design_id
                and approved.payload.get("content_hash") == event.payload.get("content_hash")
                and is_authorized_approver(str(approved.payload.get("actor_id", "")))
                for approved in self.ledger.events()
            )
        ]
        if not active or active[-1] != content_hash:
            raise PermissionError("the exact news design is not active")

    def _proposal(self, design_id: str, content_hash: str):
        return next(
            (
                event for event in self.ledger.events()
                if event.aggregate_id == design_id
                and event.event_type == "news_design_proposed"
                and event.payload.get("content_hash") == content_hash
            ),
            None,
        )
