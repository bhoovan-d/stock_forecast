"""Point-in-time source content contract for the independent news track."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


@dataclass(frozen=True)
class SourceObservation:
    observation_id: str
    source_id: str
    published_at: datetime
    retrieved_at: datetime
    available_at: datetime
    content_sha256: str
    parser_version: str
    model_version: str | None
    storage_ref: str

    def __post_init__(self) -> None:
        for name in ("published_at", "retrieved_at", "available_at"):
            require_aware(getattr(self, name), name)
        if self.available_at < self.published_at:
            raise ValueError("content cannot be available before publication")
        if len(self.content_sha256) != 64:
            raise ValueError("content_sha256 must be a full SHA-256 hex digest")

    def require_available(self, decision_at: datetime) -> None:
        require_aware(decision_at, "decision_at")
        if self.available_at > decision_at:
            raise ValueError("future source content attempted to enter a decision")
