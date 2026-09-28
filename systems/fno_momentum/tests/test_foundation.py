from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from fno_momentum.contracts import MarketBar, RawObservation, available_only
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID, TRACK_ID
from fno_momentum.source_registry import SourceManifest, SourceRegistry


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _bar(available_at=NOW):
    return MarketBar(
        instrument_id="NSE_EQ|EXAMPLE",
        interval="5m",
        interval_start=NOW - timedelta(minutes=5),
        interval_end=NOW,
        available_at=available_at,
        open=100,
        high=102,
        low=99,
        close=101,
        volume=1000,
        observation_id="observation-1",
    )


def _manifest(version="1"):
    return SourceManifest(
        source_id="example",
        name="Example proposed source",
        source_type="market_data",
        lawful_access_basis="licensed account",
        terms_reference="https://example.test/terms",
        fields=("open", "high", "low", "close"),
        latency="real-time",
        monthly_cost_inr=1000,
        collection_method="authenticated API",
        retention_policy="retain raw observations for seven years",
        version=version,
    )


def test_future_data_fails_closed():
    assert available_only([_bar()], NOW) == [_bar()]
    with pytest.raises(ValueError, match="future observation"):
        available_only([_bar(NOW + timedelta(seconds=1))], NOW)


def test_ledger_is_hash_chained_idempotent_and_immutable(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    first = ledger.append(
        "candidate_created",
        "candidate-1",
        {"symbol": "EXAMPLE"},
        occurred_at=NOW,
        idempotency_key="candidate-1:create",
        decision_at=NOW,
        data_cutoff=NOW,
    )
    duplicate = ledger.append(
        "candidate_created",
        "candidate-1",
        {"symbol": "EXAMPLE"},
        occurred_at=NOW,
        idempotency_key="candidate-1:create",
        decision_at=NOW,
        data_cutoff=NOW,
    )
    second = ledger.append(
        "candidate_rejected",
        "candidate-1",
        {"reason": "source inactive"},
        occurred_at=NOW,
        idempotency_key="candidate-1:reject",
    )
    assert duplicate.event_id == first.event_id
    assert second.previous_hash == first.event_hash
    assert all(event.track_id == TRACK_ID for event in ledger.events())
    ledger.verify()

    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("UPDATE ledger_event SET event_type='rewritten' WHERE sequence=1")
    with sqlite3.connect(ledger.path) as db, pytest.raises(sqlite3.IntegrityError):
        db.execute("DELETE FROM ledger_event WHERE sequence=1")


def test_source_cannot_activate_without_exact_approval(tmp_path):
    registry = SourceRegistry(ImmutableLedger(tmp_path / "events.sqlite"))
    manifest = _manifest()
    content_hash = registry.propose(manifest, actor_id="engineer", at=NOW)

    with pytest.raises(ValueError, match="requires approval"):
        registry.activate(manifest.source_id, content_hash, actor_id=APPROVER_ID, at=NOW)
    with pytest.raises(PermissionError, match="designated approver"):
        registry.approve(
            manifest.source_id, content_hash, actor_id="engineer", rationale="looks good", at=NOW
        )

    registry.approve(
        manifest.source_id,
        content_hash,
        actor_id=APPROVER_ID,
        rationale="lawful basis and retention reviewed",
        at=NOW,
    )
    registry.activate(manifest.source_id, content_hash, actor_id=APPROVER_ID, at=NOW)
    registry.require_active(manifest.source_id, content_hash)

    changed_hash = _manifest(version="2").content_hash
    with pytest.raises(PermissionError, match="unapproved or inactive"):
        registry.require_active(manifest.source_id, changed_hash)


def test_raw_observation_is_recorded_only_for_active_exact_source(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    registry = SourceRegistry(ledger)
    manifest = _manifest()
    content_hash = registry.propose(manifest, actor_id="engineer", at=NOW)
    observation = RawObservation(
        observation_id="raw-1",
        source_id=manifest.source_id,
        retrieved_at=NOW,
        content_sha256="b" * 64,
        parser_version="fixture-1",
        license_tier="test",
        storage_ref="fixture://raw-1",
    )
    with pytest.raises(PermissionError, match="inactive source"):
        registry.record_observation(observation, source_hash=content_hash)
    registry.approve(
        manifest.source_id,
        content_hash,
        actor_id=APPROVER_ID,
        rationale="test approval",
        at=NOW,
    )
    registry.activate(manifest.source_id, content_hash, actor_id=APPROVER_ID, at=NOW)
    event_id = registry.record_observation(observation, source_hash=content_hash)
    event = next(event for event in ledger.events() if event.event_id == event_id)
    assert event.event_type == "source_observed"
    assert event.payload["content_sha256"] == "b" * 64
