"""Approved read-only FnO universe and option-contract metadata baseline."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import ImmutableLedger, canonical_json, sha256_json
from .policy import APPROVER_ID
from .source_registry import SourceManifest, SourceRegistry
from .upstox import (
    SOURCE_ID,
    ContentAddressedRawStore,
    UpstoxCredentials,
    UpstoxReadOnlyClient,
)

PARSER_VERSION = "fno-universe-foundation-v3"
ENGINEER_ID = "codex-engineer"
IST = timezone(timedelta(hours=5, minutes=30))


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_source_manifest(path: Path) -> SourceManifest:
    value = json.loads(path.read_text(encoding="utf-8"))
    value["fields"] = tuple(value["fields"])
    manifest = SourceManifest(**value)
    manifest.validate()
    if manifest.source_id != SOURCE_ID:
        raise PermissionError("source manifest does not match the Upstox read-only source")
    return manifest


def load_collection_manifest(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "approved_for_metadata_baseline_only":
        raise PermissionError("only the approved metadata-baseline phase may run")
    phase = value.get("active_phase", {})
    allowed = phase.get("allowed_endpoints")
    if allowed != [
        "GET https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    ]:
        raise PermissionError("metadata baseline endpoint boundary changed")
    if phase.get("credential_required") is not False:
        raise PermissionError("metadata baseline must not request a private token")
    boundaries = value.get("hard_boundaries", {})
    prohibited = (
        "continuous_all_contract_option_bbo",
        "strategy_or_hypothesis_activity",
        "indicators",
        "paper_trades_or_pnl",
        "alerts_or_telegram",
        "orders_or_execution",
        "ai_learning_or_scoring",
        "source_news_inputs",
    )
    if any(boundaries.get(name) is not False for name in prohibited):
        raise PermissionError("a prohibited behavior was enabled in the manifest")
    return value


def _schema_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict):
        paths: set[str] = set()
        for key, child in value.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            paths.add(name)
            paths.update(_schema_paths(child, name))
        return paths
    if isinstance(value, list) and value:
        return _schema_paths(value[0], f"{prefix}[]")
    return set()


def _expiry_date(value: Any) -> date:
    """Normalize the documented master-file epoch milliseconds or an ISO date."""
    if isinstance(value, (int, float)) or (
        isinstance(value, str) and value.isdigit()
    ):
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc).astimezone(IST).date()
    return date.fromisoformat(str(value))


def _normalized_expiry(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return _expiry_date(value).isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return str(value)


def inspect_instrument_master(rows: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(rows, list):
        raise ValueError("instrument master is not a list")
    malformed = 0
    option_rows: list[dict[str, Any]] = []
    required = (
        "instrument_key", "underlying_key", "instrument_type", "strike_price",
        "expiry", "lot_size", "tick_size",
    )
    missing_fields: Counter[str] = Counter()
    invalid_expiry = invalid_strike = invalid_lot = invalid_tick = 0
    for row in rows:
        if not isinstance(row, dict):
            malformed += 1
            continue
        if not (
            row.get("segment") == "NSE_FO"
            and row.get("instrument_type") in {"CE", "PE"}
            and row.get("underlying_type") == "EQUITY"
        ):
            continue
        option_rows.append(row)
        for field in required:
            if row.get(field) in (None, ""):
                missing_fields[field] += 1
        try:
            _expiry_date(row.get("expiry"))
        except (TypeError, ValueError, OSError, OverflowError):
            invalid_expiry += 1
        try:
            if float(row.get("strike_price")) < 0:
                invalid_strike += 1
        except (TypeError, ValueError):
            invalid_strike += 1
        try:
            if int(row.get("lot_size")) <= 0:
                invalid_lot += 1
        except (TypeError, ValueError):
            invalid_lot += 1
        try:
            if float(row.get("tick_size")) <= 0:
                invalid_tick += 1
        except (TypeError, ValueError):
            invalid_tick += 1

    contract_ids = [str(row.get("instrument_key", "")) for row in option_rows]
    duplicate_contract_ids = sum(count - 1 for count in Counter(contract_ids).values() if count > 1)
    members = sorted({
        (str(row.get("underlying_key", "")), str(row.get("underlying_symbol") or row.get("name") or ""))
        for row in option_rows
        if row.get("underlying_key")
    })
    contracts = [
        {
            "contract_id": str(row.get("instrument_key", "")),
            "underlying_key": str(row.get("underlying_key", "")),
            "underlying_symbol": str(row.get("underlying_symbol") or row.get("name") or ""),
            "right": str(row.get("instrument_type", "")),
            "strike": row.get("strike_price"),
            "expiry": _normalized_expiry(row.get("expiry")),
            "lot_size": row.get("lot_size"),
            "tick_size": row.get("tick_size"),
        }
        for row in option_rows
    ]
    schema = sorted(_schema_paths(rows[0])) if rows and isinstance(rows[0], dict) else []
    summary = {
        "instrument_master_rows": len(rows),
        "fno_stock_underlying_count": len(members),
        "stock_option_contract_count": len(option_rows),
        "malformed_master_rows": malformed,
        "missing_required_contract_fields": dict(sorted(missing_fields.items())),
        "duplicate_contract_identifiers": duplicate_contract_ids,
        "invalid_expiry": invalid_expiry,
        "invalid_strike": invalid_strike,
        "invalid_lot_size": invalid_lot,
        "invalid_tick_size": invalid_tick,
        "schema_fingerprint": sha256_json(schema),
        "schema_paths": schema,
    }
    snapshot = {
        "members": [
            {"underlying_key": key, "symbol": symbol} for key, symbol in members
        ],
        "contracts": contracts,
    }
    return summary, snapshot


def store_reference_snapshot(
    storage_root: Path, snapshot: dict[str, Any]
) -> tuple[str, str, str]:
    content = canonical_json(snapshot).encode("utf-8")
    content_hash = hashlib.sha256(content).hexdigest()
    compressed = gzip.compress(content, compresslevel=9, mtime=0)
    compressed_hash = hashlib.sha256(compressed).hexdigest()
    path = storage_root / "reference" / content_hash[:2] / f"{content_hash}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != compressed:
            raise ValueError("content-addressed reference snapshot collision")
    else:
        path.write_bytes(compressed)
    return content_hash, compressed_hash, str(path.resolve())


def run_metadata_baseline(
    *, manifest_path: Path, source_manifest_path: Path, ledger_path: Path
) -> dict[str, Any]:
    manifest = load_collection_manifest(manifest_path)
    source = load_source_manifest(source_manifest_path)
    storage_root = Path(manifest["storage"]["root"])
    ledger = ImmutableLedger(ledger_path)
    registry = SourceRegistry(ledger)
    now = datetime.now(timezone.utc)
    source_hash = registry.propose(source, actor_id=ENGINEER_ID, at=now)
    registry.approve(
        source.source_id,
        source_hash,
        actor_id=APPROVER_ID,
        rationale="Aditya Lakhotia authorized the FnO universe-wide data foundation on 2026-09-22; this activation is limited to the public instrument-master metadata baseline.",
        at=now,
    )
    registry.activate(source.source_id, source_hash, actor_id=APPROVER_ID, at=now)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials(access_token=""),
        registry=registry,
        raw_store=ContentAddressedRawStore(storage_root / "raw"),
        source_hash=source_hash,
    )
    manifest_hash = _file_sha256(manifest_path)
    code_hash = _file_sha256(Path(__file__))
    job_id = sha256_json({
        "manifest_hash": manifest_hash,
        "source_hash": source_hash,
        "started_at": now.isoformat(),
    })[:32]
    ledger.append(
        "universe_foundation_baseline_started",
        job_id,
        {
            "manifest_id": manifest["manifest_id"],
            "manifest_sha256": manifest_hash,
            "source_manifest_hash": source_hash,
            "parser_version": PARSER_VERSION,
            "code_sha256": code_hash,
            "credential_used": False,
            "scope": "current_universe_and_contract_metadata_only",
        },
        occurred_at=now,
        idempotency_key=f"universe-foundation-start:{job_id}",
    )
    try:
        capture = client.instrument_master()
        quality, snapshot = inspect_instrument_master(capture.payload)
        reference_hash, reference_file_hash, reference_storage_ref = (
            store_reference_snapshot(storage_root, snapshot)
        )
        captured_at = capture.observation.retrieved_at
        payload = {
            "effective_from": captured_at.isoformat(),
            "prior_membership_reconstructed": False,
            "source_observation_id": capture.observation.observation_id,
            "raw_content_sha256": capture.observation.content_sha256,
            "raw_storage_ref": capture.observation.storage_ref,
            "quality": quality,
            "universe_hash": sha256_json(snapshot["members"]),
            "contract_reference_hash": sha256_json(snapshot["contracts"]),
            "reference_snapshot_hash": reference_hash,
            "reference_file_sha256": reference_file_hash,
            "reference_storage_ref": reference_storage_ref,
            "members": snapshot["members"],
        }
        ledger.append(
            "universe_foundation_metadata_captured",
            job_id,
            payload,
            occurred_at=captured_at,
            decision_at=captured_at,
            data_cutoff=captured_at,
            idempotency_key=f"universe-foundation-metadata:{job_id}",
        )
        finished = datetime.now(timezone.utc)
        result = {
            "job_id": job_id,
            "status": "metadata_baseline_complete",
            "captured_at": captured_at.isoformat(),
            "source_hash": source_hash,
            "manifest_sha256": manifest_hash,
            "raw_content_sha256": capture.observation.content_sha256,
            "universe_hash": payload["universe_hash"],
            "contract_reference_hash": payload["contract_reference_hash"],
            "reference_snapshot_hash": reference_hash,
            "reference_file_sha256": reference_file_hash,
            "reference_storage_ref": reference_storage_ref,
            **{key: value for key, value in quality.items() if key != "schema_paths"},
        }
        ledger.append(
            "universe_foundation_baseline_completed",
            job_id,
            result,
            occurred_at=finished,
            idempotency_key=f"universe-foundation-complete:{job_id}",
        )
        ledger.verify()
        return result
    except Exception as exc:
        failed = datetime.now(timezone.utc)
        ledger.append(
            "data_quality_failure",
            job_id,
            {
                "stage": "universe_foundation_metadata_baseline",
                "error_type": type(exc).__name__,
                "detail": str(exc)[:500],
                "retry_attempted": False,
                "substitution_attempted": False,
            },
            occurred_at=failed,
            idempotency_key=f"universe-foundation-failure:{job_id}:{type(exc).__name__}",
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-manifest", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args()
    result = run_metadata_baseline(
        manifest_path=args.manifest,
        source_manifest_path=args.source_manifest,
        ledger_path=args.ledger,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
