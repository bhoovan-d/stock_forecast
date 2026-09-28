from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from nifty500_source_news.contracts import SourceObservation
from nifty500_source_news.ledger import NewsLedger
from nifty500_source_news.policy import APPROVER_ID, TRACK_ID
from nifty500_source_news.source_registry import NewsSourceProposal, NewsSourceRegistry


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def test_source_observation_is_point_in_time():
    observation = SourceObservation(
        observation_id="obs-1",
        source_id="source-1",
        published_at=NOW - timedelta(minutes=2),
        retrieved_at=NOW,
        available_at=NOW,
        content_sha256="a" * 64,
        parser_version="1",
        model_version=None,
        storage_ref="source-news/aa/observation",
    )
    observation.require_available(NOW)
    with pytest.raises(ValueError, match="future source content"):
        observation.require_available(NOW - timedelta(seconds=1))


def test_news_ledger_is_independent_and_immutable(tmp_path):
    ledger = NewsLedger(tmp_path / "news.sqlite")
    event = ledger.append(
        "source_observed",
        "obs-1",
        {"source_id": "source-1"},
        occurred_at=NOW,
        idempotency_key="obs-1",
    )
    assert event.track_id == TRACK_ID
    ledger.verify()
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM news_event WHERE sequence=1")


def test_news_source_activation_requires_designated_approval(tmp_path):
    registry = NewsSourceRegistry(NewsLedger(tmp_path / "news.sqlite"))
    proposal = NewsSourceProposal(
        source_id="example-news",
        source_category="company_filing",
        lawful_access_basis="public company filing",
        terms_reference="https://example.test/terms",
        data_fields=("headline", "published_at", "body"),
        collection_method="documented API",
        retention_policy="seven years",
        monthly_cost_inr=0,
        version="1",
    )
    content_hash = registry.propose(proposal, actor_id="engineer", at=NOW)
    with pytest.raises(PermissionError, match="designated approver"):
        registry.approve_and_activate(
            proposal.source_id,
            content_hash,
            actor_id="engineer",
            rationale="reviewed",
            at=NOW,
        )
    registry.approve_and_activate(
        proposal.source_id,
        content_hash,
        actor_id=APPROVER_ID,
        rationale="lawful source approved",
        at=NOW,
    )
    assert [event.event_type for event in registry.ledger.events()][-2:] == [
        "news_source_approved",
        "news_source_activated",
    ]
