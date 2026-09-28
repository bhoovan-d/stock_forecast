"""Read-only replay of captured FnO source data and audit records."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import gzip
import hashlib
import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "systems" / "fno_momentum" / "src"))

from fno_momentum.full_underlying_collection import Collector, IST  # noqa: E402
from fno_momentum.universe_foundation import inspect_instrument_master  # noqa: E402


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    collector = Collector()
    collector.ledger.verify()
    events = collector.ledger.events()
    raw_by_key = {event.aggregate_id: event for event in events
                  if event.event_type == "raw_response_captured"}
    checked_paths: dict[str, str | None] = {}
    failures: list[str] = []
    counts: Counter[str] = Counter()

    for event in events:
        if event.event_type == "raw_response_captured":
            path = Path(event.payload["raw_path"])
            if str(path) not in checked_paths:
                checked_paths[str(path)] = (digest(gzip.decompress(path.read_bytes()))
                                            if path.is_file() else None)
            if checked_paths[str(path)] != event.payload["raw_sha256"]:
                failures.append(f"raw integrity: {event.aggregate_id}")
            counts["raw_observations"] += 1
        elif event.event_type == "normalized_partition_recorded":
            path = Path(event.payload["path"])
            if not path.is_file() or digest(gzip.decompress(path.read_bytes())) != event.payload["content_sha256"]:
                failures.append(f"normalized integrity: {event.aggregate_id}")
            counts["normalized_observations"] += 1

    for event in events:
        if event.event_type not in {"universe_reference_observed", "master_diagnostic_validated"}:
            continue
        raw_event = raw_by_key.get(event.aggregate_id)
        if raw_event is None:
            failures.append(f"master has no raw payload: {event.aggregate_id}")
            continue
        encoded = gzip.decompress(Path(raw_event.payload["raw_path"]).read_bytes())
        source_rows = json.loads(gzip.decompress(encoded))
        quality, snapshot = inspect_instrument_master(source_rows)
        if len(snapshot["members"]) != event.payload.get("member_count", event.payload.get("members")):
            failures.append(f"master member count: {event.aggregate_id}")
        if len(snapshot["contracts"]) != event.payload.get("contract_count", event.payload.get("contracts")):
            failures.append(f"master contract count: {event.aggregate_id}")
        if quality != event.payload["quality"]:
            failures.append(f"master quality: {event.aggregate_id}")
        if event.event_type == "universe_reference_observed":
            ref = Path(event.payload["reference_path"]).read_bytes()
            if digest(ref) != event.payload["reference_file_sha256"] or digest(gzip.decompress(ref)) != event.payload["reference_sha256"]:
                failures.append(f"reference integrity: {event.aggregate_id}")
        counts["masters_replayed"] += 1

    for event in events:
        if event.event_type not in {"quote_batch_quality", "session_candles_recorded"}:
            continue
        raw_event = raw_by_key.get(event.aggregate_id)
        if raw_event is None:
            failures.append(f"quality event has no raw payload: {event.aggregate_id}")
            continue
        response = json.loads(gzip.decompress(Path(raw_event.payload["raw_path"]).read_bytes()))
        if event.event_type == "quote_batch_quality":
            if len(response.get("data", {})) != event.payload["returned_count"]:
                failures.append(f"quote count: {event.aggregate_id}")
            counts["quote_batches_replayed"] += 1
            continue

        bars = response.get("data", {}).get("candles", [])
        if len(bars) != event.payload["returned_rows"]:
            failures.append(f"candle row count: {event.aggregate_id}")
        valid_session_rows = 0
        for row in bars:
            try:
                stamp = datetime.fromisoformat(str(row[0]))
                if stamp.tzinfo is None or len(row) < 6:
                    continue
                if str(stamp.astimezone(IST).date()) != event.payload["session"]:
                    continue
                op, hi, lo, close, vol = row[1:6]
                if any(not isinstance(v, (int, float)) for v in (op, hi, lo, close, vol)):
                    continue
                if min(op, hi, lo, close) <= 0 or vol < 0 or hi < max(op, close) or lo > min(op, close):
                    continue
                valid_session_rows += 1
            except (ValueError, TypeError, IndexError):
                continue
        if valid_session_rows != event.payload["observed_session_rows"]:
            failures.append(f"candle target-session count: {event.aggregate_id}")
        counts["candle_windows_replayed"] += 1

    result = {"ledger_events_verified": len(events), **dict(counts),
              "integrity_failures": failures[:20], "integrity_failure_count": len(failures)}
    print(json.dumps(result, sort_keys=True))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
