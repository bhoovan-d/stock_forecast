import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from fno_momentum.ledger import ImmutableLedger
from fno_momentum.retention_audit import delete_with_audit, prepare_deletion


NOW = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
RETRIEVED = NOW - timedelta(days=8)


def fixture(tmp_path):
    root = tmp_path / "foundation"
    raw = root / "raw" / "provider"
    raw.mkdir(parents=True)
    target = raw / "exact.json"
    target.write_bytes(b'{"data":"original"}')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "manifest_id": "fixture-foundation", "status": "approved_for_underlying_collection",
        "storage": {"root": str(root), "raw_retention_days": 7},
    }), encoding="utf-8")
    manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    ledger.append("underlying_collection_started", "job", {
        "manifest_id": "fixture-foundation", "manifest_sha256": manifest_hash,
        "parser_version": "v1", "code_sha256": "c" * 64,
    }, occurred_at=RETRIEVED, idempotency_key="start")
    ledger.append("source_observed", "obs-1", {
        "source_id": "upstox", "source_hash": "s" * 64,
        "retrieved_at": RETRIEVED.isoformat(), "storage_ref": str(target),
        "content_sha256": digest,
    }, occurred_at=RETRIEVED, idempotency_key="observed")
    quality = ledger.append("underlying_data_quality_profiled", "job", {
        "source_observation_id": "obs-1", "missing": 0,
    }, occurred_at=RETRIEVED, idempotency_key="quality")
    params = dict(
        raw_root=root / "raw", raw_file=target, observation_id="obs-1",
        quality_event_id=quality.event_id, manifest_path=manifest,
        retention_days=7, at=NOW,
    )
    return ledger, target, params


def test_dry_run_is_read_only_and_reports_exact_evidence(tmp_path):
    ledger, target, params = fixture(tmp_path)
    evidence = prepare_deletion(ledger, **params)
    assert target.exists()
    assert evidence["raw_sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert evidence["quality_event_id"] == params["quality_event_id"]
    assert len(ledger.events()) == 3


def test_deletion_writes_intent_and_completion_without_erasing_audit(tmp_path):
    ledger, target, params = fixture(tmp_path)
    result = delete_with_audit(ledger, **params)
    assert not target.exists()
    assert result["deleted_at"]
    assert [event.event_type for event in ledger.events()][-2:] == [
        "raw_deletion_intended", "raw_deletion_completed",
    ]
    assert ledger.events()[-1].payload["quality_event_hash"]
    ledger.verify()


def test_retention_window_and_hash_mismatch_fail_closed(tmp_path):
    ledger, target, params = fixture(tmp_path)
    with pytest.raises(PermissionError, match="not elapsed"):
        prepare_deletion(ledger, **{**params, "at": RETRIEVED + timedelta(days=6)})
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="raw bytes differ"):
        delete_with_audit(ledger, **params)
    assert target.exists()
    assert not any(e.event_type.startswith("raw_deletion") for e in ledger.events())


def test_outside_root_and_wrong_quality_report_are_rejected(tmp_path):
    ledger, target, params = fixture(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(target.read_bytes())
    with pytest.raises(PermissionError, match="inside the exact raw root"):
        prepare_deletion(ledger, **{**params, "raw_file": outside})
    wrong = ledger.append("underlying_data_quality_profiled", "other-job", {
        "source_observation_id": "other-observation",
    }, occurred_at=NOW, idempotency_key="wrong-quality")
    with pytest.raises(ValueError, match="does not reference"):
        prepare_deletion(ledger, **{**params, "quality_event_id": wrong.event_id})


def test_proposed_manifest_cannot_authorize_deletion(tmp_path):
    ledger, target, params = fixture(tmp_path)
    value = json.loads(params["manifest_path"].read_text(encoding="utf-8"))
    value["status"] = "proposed_inactive"
    params["manifest_path"].write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PermissionError, match="not in an approved"):
        prepare_deletion(ledger, **params)
    assert target.exists()


def test_unresolved_prior_intent_blocks_repeated_delete(tmp_path):
    ledger, target, params = fixture(tmp_path)
    evidence = prepare_deletion(ledger, **params)
    ledger.append(
        "raw_deletion_intended", "obs-1", evidence,
        occurred_at=NOW, idempotency_key="raw-deletion-intent:obs-1",
    )
    with pytest.raises(ValueError, match="manual reconciliation"):
        delete_with_audit(ledger, **params)
    assert target.exists()
