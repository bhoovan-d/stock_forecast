"""Approved one-hour, read-only RELIANCE market-data quality pilot."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from collections.abc import Callable, Iterable
from datetime import datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Any

from .ledger import ImmutableLedger, sha256_json
from .source_registry import SourceRegistry
from .upstox import (
    SOURCE_ID,
    ContentAddressedRawStore,
    UpstoxCredentials,
    UpstoxReadOnlyClient,
    _provider_timestamp,
    fno_equity_underlyings,
)


APPROVED_MANIFEST_SHA256 = (
    "0c1e762106239009e3604c0b1ad1a5cfa89c38f63a611132ccf25d6b675a4989"
)
APPROVED_SOURCE_HASH = "63405f858a21982c328002f70918423b542ba27f0d776621bb1f4221c1d5c040"
IST = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
SESSION_OPEN = wall_time(9, 15)
SESSION_CLOSE = wall_time(15, 30)


class StorageQuotaExceeded(RuntimeError):
    """Raised before a raw write would exceed the approved pilot quota."""


class QuotaRawStore(ContentAddressedRawStore):
    def __init__(self, root: str | Path, *, maximum_total_bytes: int) -> None:
        super().__init__(root)
        if maximum_total_bytes <= 0:
            raise ValueError("maximum_total_bytes must be positive")
        self.maximum_total_bytes = maximum_total_bytes

    def put(self, content: bytes, *, suffix: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        path = self.root / SOURCE_ID / digest[:2] / f"{digest}.{suffix}"
        additional = 0 if path.exists() else len(content)
        current = sum(
            item.stat().st_size for item in self.root.rglob("*") if item.is_file()
        ) if self.root.exists() else 0
        if current + additional > self.maximum_total_bytes:
            raise StorageQuotaExceeded(
                "approved 250 MB pilot storage limit would be exceeded"
            )
        return super().put(content, suffix=suffix)


def _chunks(values: tuple[str, ...], size: int = 500) -> Iterable[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _distribution(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"minimum": None, "median": None, "maximum": None}
    return {
        "minimum": min(values),
        "median": statistics.median(values),
        "maximum": max(values),
    }


def _market_window_allows(
    started_at: datetime, *, snapshot_count: int, interval_seconds: int
) -> bool:
    local = started_at.astimezone(IST)
    finish = local + timedelta(seconds=(snapshot_count - 1) * interval_seconds)
    return (
        local.weekday() < 5
        and local.date() == finish.date()
        and local.time() >= SESSION_OPEN
        and finish.time() <= SESSION_CLOSE
    )


def _approved_manifest(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != APPROVED_MANIFEST_SHA256:
        raise PermissionError("pilot manifest differs from the approved exact version")
    value = json.loads(content)
    if value.get("status") != "approved_not_yet_run":
        raise PermissionError("pilot manifest is not approved for a first run")
    return value


def _discover_reliance(rows: list[dict[str, Any]]) -> tuple[str, tuple[str, ...]]:
    underlyings = {
        key for key, symbol in fno_equity_underlyings(rows)
        if symbol.strip().upper() == "RELIANCE"
    }
    if len(underlyings) != 1:
        raise ValueError("expected exactly one RELIANCE equity underlying")
    underlying_key = next(iter(underlyings))
    contracts = tuple(sorted({
        str(row["instrument_key"])
        for row in rows
        if row.get("segment") == "NSE_FO"
        and row.get("instrument_type") in {"CE", "PE"}
        and row.get("underlying_type") == "EQUITY"
        and str(row.get("underlying_key", "")) == underlying_key
        and row.get("instrument_key")
    }))
    if not contracts:
        raise ValueError("RELIANCE has no current stock-option contracts")
    return underlying_key, contracts


def _inspect_rows(capture, requested: tuple[str, ...]) -> dict[str, Any]:
    rows = capture.payload.get("data")
    if not isinstance(rows, dict):
        raise ValueError("full-quote response is missing data")
    returned: set[str] = set()
    empty_markets = crossed_markets = malformed_rows = 0
    provider_lag_ms: list[float] = []
    last_trade_age_seconds: list[float] = []
    for row in rows.values():
        try:
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            instrument_key = str(row["instrument_token"])
            returned.add(instrument_key)
            provider_at = _provider_timestamp(row["timestamp"])
            provider_lag_ms.append(
                (capture.observation.retrieved_at - provider_at).total_seconds() * 1000
            )
            last_trade = row.get("last_trade_time")
            if last_trade:
                last_trade_age_seconds.append(
                    (capture.observation.retrieved_at - _provider_timestamp(last_trade))
                    .total_seconds()
                )
            depth = row.get("depth") or {}
            buys = depth.get("buy") or []
            sells = depth.get("sell") or []
            bid = next((float(x.get("price", 0)) for x in buys if float(x.get("price", 0)) > 0), None)
            ask = next((float(x.get("price", 0)) for x in sells if float(x.get("price", 0)) > 0), None)
            if bid is None or ask is None:
                empty_markets += 1
            elif ask < bid:
                crossed_markets += 1
        except (KeyError, TypeError, ValueError, OverflowError):
            malformed_rows += 1
    requested_set = set(requested)
    return {
        "requested_instruments": len(requested_set),
        "returned_instruments": len(returned),
        "missing_instruments": len(requested_set - returned),
        "unexpected_instruments": len(returned - requested_set),
        "empty_markets": empty_markets,
        "crossed_markets": crossed_markets,
        "malformed_rows": malformed_rows,
        "provider_to_local_lag_ms": _distribution(provider_lag_ms),
        "last_trade_age_seconds": _distribution(last_trade_age_seconds),
        "observation_id": capture.observation.observation_id,
        "content_sha256": capture.observation.content_sha256,
        "retrieved_at": capture.observation.retrieved_at.isoformat(),
    }


class RelianceQualityPilot:
    def __init__(
        self,
        ledger: ImmutableLedger,
        client: UpstoxReadOnlyClient,
        *,
        now: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.client = client
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.monotonic = monotonic or time.monotonic
        self.sleeper = sleeper or time.sleep

    def run(
        self,
        *,
        manifest_hash: str,
        snapshot_count: int = 60,
        interval_seconds: int = 60,
    ) -> dict[str, Any]:
        started_at = self.now()
        if not _market_window_allows(
            started_at,
            snapshot_count=snapshot_count,
            interval_seconds=interval_seconds,
        ):
            raise RuntimeError(
                "pilot must start early enough to finish within 09:15-15:30 Asia/Kolkata "
                "on a weekday NSE session"
            )
        master = self.client.instrument_master()
        if not isinstance(master.payload, list):
            raise ValueError("instrument master payload is not a list")
        underlying_key, contracts = _discover_reliance(master.payload)
        instruments = (underlying_key, *contracts)
        job_id = sha256_json({
            "manifest_hash": manifest_hash,
            "started_at": started_at.isoformat(),
            "underlying_key": underlying_key,
            "contracts": contracts,
        })[:32]
        self.ledger.append(
            "reliance_quality_pilot_started",
            job_id,
            {
                "manifest_hash": manifest_hash,
                "underlying_key": underlying_key,
                "contract_count": len(contracts),
                "snapshot_count": snapshot_count,
                "interval_seconds": interval_seconds,
                "instrument_master_observation_id": master.observation.observation_id,
                "purpose": "data_quality_only_no_strategy_or_pnl",
            },
            occurred_at=started_at,
            idempotency_key=f"reliance-quality-pilot-start:{job_id}",
        )
        completed = failures = gaps = 0
        expected_start = self.monotonic()
        for snapshot_number in range(1, snapshot_count + 1):
            actual_start = self.monotonic()
            if snapshot_number > 1 and actual_start - expected_start >= interval_seconds:
                gaps += 1
            batch_results: list[dict[str, Any]] = []
            try:
                for batch_number, batch in enumerate(_chunks(instruments), start=1):
                    capture = self.client.full_quotes(batch)
                    batch_results.append({
                        "batch_number": batch_number,
                        **_inspect_rows(capture, batch),
                    })
                captured_at = self.now()
                self.ledger.append(
                    "reliance_quality_snapshot_captured",
                    job_id,
                    {
                        "snapshot_number": snapshot_number,
                        "batch_count": len(batch_results),
                        "batches": batch_results,
                    },
                    occurred_at=captured_at,
                    decision_at=captured_at,
                    data_cutoff=captured_at,
                    idempotency_key=f"reliance-quality-snapshot:{job_id}:{snapshot_number}",
                )
                completed += 1
            except Exception as exc:
                failed_at = self.now()
                self.ledger.append(
                    "data_quality_failure",
                    job_id,
                    {
                        "stage": "reliance_quality_snapshot",
                        "snapshot_number": snapshot_number,
                        "error_type": type(exc).__name__,
                        "detail": str(exc)[:300],
                    },
                    occurred_at=failed_at,
                    idempotency_key=(
                        f"reliance-quality-failure:{job_id}:{snapshot_number}:"
                        f"{type(exc).__name__}"
                    ),
                )
                failures += 1
                if isinstance(exc, StorageQuotaExceeded):
                    break
            if snapshot_number < snapshot_count:
                expected_start += interval_seconds
                delay = expected_start - self.monotonic()
                if delay > 0:
                    self.sleeper(delay)

        finished_at = self.now()
        summary = {
            "job_id": job_id,
            "underlying": "RELIANCE",
            "contract_count": len(contracts),
            "planned_snapshots": snapshot_count,
            "completed_snapshots": completed,
            "failed_snapshots": failures,
            "collection_gaps": gaps,
            "status": "complete" if completed == snapshot_count and failures == 0 else "incomplete",
        }
        self.ledger.append(
            "reliance_quality_pilot_completed",
            job_id,
            summary,
            occurred_at=finished_at,
            idempotency_key=f"reliance-quality-pilot-complete:{job_id}",
        )
        return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    args = parser.parse_args()
    manifest = _approved_manifest(args.manifest)
    storage = manifest["storage"]
    root = Path(storage["root"])
    root.mkdir(parents=True, exist_ok=True)
    ledger = ImmutableLedger(args.ledger)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials.from_os_secret_store(),
        registry=SourceRegistry(ledger),
        raw_store=QuotaRawStore(
            root / "raw",
            maximum_total_bytes=int(storage["maximum_total_bytes"]),
        ),
        source_hash=APPROVED_SOURCE_HASH,
    )
    result = RelianceQualityPilot(ledger, client).run(
        manifest_hash=APPROVED_MANIFEST_SHA256,
        snapshot_count=int(manifest["scope"]["snapshot_count"]),
        interval_seconds=int(manifest["scope"]["snapshot_interval_seconds"]),
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()




