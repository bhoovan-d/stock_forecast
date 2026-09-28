"""Source proposals for the news track; activation requires an exact approval hash."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

from .ledger import NewsLedger, hash_manifest
from .policy import is_authorized_approver


@dataclass(frozen=True)
class NewsSourceProposal:
    source_id: str
    source_category: str
    lawful_access_basis: str
    terms_reference: str
    data_fields: tuple[str, ...]
    collection_method: str
    retention_policy: str
    monthly_cost_inr: int
    version: str

    @property
    def content_hash(self) -> str:
        return hash_manifest(asdict(self))

    def validate(self) -> None:
        values = asdict(self)
        missing = [key for key, value in values.items() if key != "monthly_cost_inr" and not value]
        if missing:
            raise ValueError("news source proposal is incomplete: " + ", ".join(missing))
        if self.monthly_cost_inr < 0:
            raise ValueError("monthly source cost cannot be negative")


class NewsSourceRegistry:
    def __init__(self, ledger: NewsLedger) -> None:
        self.ledger = ledger

    def propose(self, proposal: NewsSourceProposal, *, actor_id: str, at: datetime) -> str:
        proposal.validate()
        payload = {**asdict(proposal), "content_hash": proposal.content_hash, "actor_id": actor_id}
        self.ledger.append(
            "news_source_proposed",
            proposal.source_id,
            payload,
            occurred_at=at,
            idempotency_key=f"news-source-proposal:{proposal.source_id}:{proposal.content_hash}",
        )
        return proposal.content_hash

    def approve_and_activate(
        self,
        source_id: str,
        content_hash: str,
        *,
        actor_id: str,
        rationale: str,
        at: datetime,
    ) -> None:
        if not is_authorized_approver(actor_id):
            raise PermissionError("only the designated approver may activate a news source")
        if not rationale.strip():
            raise ValueError("approval rationale is required")
        proposed = any(
            event.event_type == "news_source_proposed"
            and event.aggregate_id == source_id
            and event.payload.get("content_hash") == content_hash
            for event in self.ledger.events()
        )
        if not proposed:
            raise ValueError("the exact news source proposal hash does not exist")
        self.ledger.append(
            "news_source_approved",
            source_id,
            {"content_hash": content_hash, "actor_id": actor_id, "rationale": rationale},
            occurred_at=at,
            idempotency_key=f"news-source-approval:{source_id}:{content_hash}",
        )
        self.ledger.append(
            "news_source_activated",
            source_id,
            {"content_hash": content_hash, "actor_id": actor_id},
            occurred_at=at,
            idempotency_key=f"news-source-activation:{source_id}:{content_hash}",
        )
