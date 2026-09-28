"""Fail-closed, per-file raw-retention deletion with permanent ledger evidence.

No scheduler calls this module. A future approved operator must supply one exact
raw file, its source observation, and its quality event; dry-run is the default.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import ImmutableLedger


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _event(ledger: ImmutableLedger, event_id: str):
    return next((entry for entry in ledger.events() if entry.event_id == event_id), None)


def prepare_deletion(
    ledger: ImmutableLedger,
    *,
    raw_root: Path,
    raw_file: Path,
    observation_id: str,
    quality_event_id: str,
    manifest_path: Path,
    retention_days: int,
    at: datetime,
) -> dict[str, Any]:
    """Verify exact provenance, quality evidence, location, hash and age."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("deletion time must include a timezone")
    if retention_days <= 0:
        raise ValueError("retention days must be positive")
    if not manifest_path.is_file():
        raise FileNotFoundError("approved manifest is missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("status") not in {
        "approved_for_metadata_baseline_only", "approved_for_underlying_collection"
    }:
        raise PermissionError("manifest is not in an approved collection state")
    if int(manifest.get("storage", {}).get("raw_retention_days", -1)) != retention_days:
        raise PermissionError("retention period differs from the manifest")
    manifest_hash = _file_hash(manifest_path)
    root = raw_root.resolve(strict=True)
    configured_root = Path(manifest["storage"]["root"]).resolve(strict=True) / "raw"
    if root != configured_root:
        raise PermissionError("raw root differs from the approved manifest")
    target = raw_file.resolve(strict=True)
    if raw_root.is_symlink() or raw_file.is_symlink() or root not in target.parents:
        raise PermissionError("raw file must be a regular file inside the exact raw root")
    if not target.is_file():
        raise ValueError("raw target is not a file")
    observation = next(
        (entry for entry in ledger.events(observation_id) if entry.event_type == "source_observed"),
        None,
    )
    if observation is None:
        raise ValueError("raw file has no source-observed ledger event")
    if Path(observation.payload["storage_ref"]).resolve() != target:
        raise ValueError("raw path does not match source observation")
    recorded_hash = str(observation.payload["content_sha256"])
    if _file_hash(target) != recorded_hash:
        raise ValueError("raw bytes differ from the immutable observation hash")
    retrieved_at = datetime.fromisoformat(observation.payload["retrieved_at"])
    if at < retrieved_at + timedelta(days=retention_days):
        raise PermissionError("raw retention window has not elapsed")
    quality = _event(ledger, quality_event_id)
    if quality is None or quality.event_type not in {
        "universe_foundation_metadata_captured",
        "historical_data_quality_profiled",
        "intraday_data_quality_profiled",
        "underlying_data_quality_profiled",
    }:
        raise ValueError("an immutable quality report event is required")
    quality_text = json.dumps(quality.payload, sort_keys=True)
    if observation_id not in quality_text and recorded_hash not in quality_text:
        raise ValueError("quality report does not reference this raw observation")
    starts = [
        entry for entry in ledger.events()
        if entry.event_type in {
            "universe_foundation_baseline_started",
            "underlying_collection_started",
        }
        and entry.payload.get("manifest_sha256") == manifest_hash
    ]
    if not starts:
        raise ValueError("no collection-start event references this exact manifest")
    started = starts[-1]
    if not started.payload.get("parser_version") or not started.payload.get("code_sha256"):
        raise ValueError("parser and code version evidence is incomplete")
    prior = [
        entry for entry in ledger.events()
        if entry.event_type == "raw_deletion_completed"
        and entry.payload.get("source_observation_id") == observation_id
    ]
    if prior:
        raise ValueError("raw observation already has a completed deletion event")
    pending = [
        entry for entry in ledger.events()
        if entry.event_type == "raw_deletion_intended"
        and entry.aggregate_id == observation_id
    ]
    if pending:
        raise ValueError("unresolved deletion intent requires manual reconciliation")
    return {
        "raw_path": str(target),
        "raw_bytes": target.stat().st_size,
        "raw_sha256": recorded_hash,
        "source_observation_id": observation_id,
        "source_id": observation.payload["source_id"],
        "source_hash": observation.payload["source_hash"],
        "retrieved_at": observation.payload["retrieved_at"],
        "quality_event_id": quality.event_id,
        "quality_event_hash": quality.event_hash,
        "manifest_id": started.payload["manifest_id"],
        "manifest_sha256": manifest_hash,
        "parser_version": started.payload["parser_version"],
        "code_sha256": started.payload["code_sha256"],
        "retention_rule": f"raw payload retained at least {retention_days} days from retrieval",
        "reason": "approved raw-retention window elapsed",
        "requested_at": at.isoformat(),
    }


def delete_with_audit(
    ledger: ImmutableLedger,
    *,
    raw_root: Path,
    raw_file: Path,
    observation_id: str,
    quality_event_id: str,
    manifest_path: Path,
    retention_days: int,
    at: datetime,
) -> dict[str, Any]:
    """Record intent first, delete one verified file, then record completion.

    An interrupted deletion leaves an intent event for reconciliation. It never
    claims completion if the file removal fails.
    """
    evidence = prepare_deletion(
        ledger,
        raw_root=raw_root,
        raw_file=raw_file,
        observation_id=observation_id,
        quality_event_id=quality_event_id,
        manifest_path=manifest_path,
        retention_days=retention_days,
        at=at,
    )
    ledger.append(
        "raw_deletion_intended", observation_id, evidence,
        occurred_at=at, idempotency_key=f"raw-deletion-intent:{observation_id}",
    )
    target = Path(evidence["raw_path"])
    try:
        if _file_hash(target) != evidence["raw_sha256"]:
            raise ValueError("raw bytes changed after deletion intent")
        target.unlink()
    except Exception as exc:
        failed_at = max(datetime.now(timezone.utc), at)
        ledger.append(
            "raw_deletion_failed", observation_id,
            {**evidence, "failure_type": type(exc).__name__, "failure_at": failed_at.isoformat()},
            occurred_at=failed_at,
            idempotency_key=f"raw-deletion-failed:{observation_id}:{failed_at.isoformat()}",
        )
        raise
    deleted_at = max(datetime.now(timezone.utc), at)
    completion = {**evidence, "deleted_at": deleted_at.isoformat()}
    ledger.append(
        "raw_deletion_completed", observation_id, completion,
        occurred_at=deleted_at, idempotency_key=f"raw-deletion-complete:{observation_id}",
    )
    ledger.verify()
    return completion


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit one exact raw-retention deletion")
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--raw-root", required=True, type=Path)
    parser.add_argument("--raw-file", required=True, type=Path)
    parser.add_argument("--observation-id", required=True)
    parser.add_argument("--quality-event-id", required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--retention-days", required=True, type=int)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    ledger = ImmutableLedger(args.ledger)
    ledger.verify()
    params = dict(
        raw_root=args.raw_root, raw_file=args.raw_file,
        observation_id=args.observation_id, quality_event_id=args.quality_event_id,
        manifest_path=args.manifest, retention_days=args.retention_days,
        at=datetime.now(timezone.utc),
    )
    result = (
        delete_with_audit(ledger, **params) if args.execute
        else prepare_deletion(ledger, **params)
    )
    print(json.dumps({"mode": "execute" if args.execute else "dry_run", **result}, sort_keys=True))


if __name__ == "__main__":
    main()
