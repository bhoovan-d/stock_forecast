"""Resumable research-only underlying history capture from approved Upstox data."""

from __future__ import annotations

import argparse
import gzip
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path

from .ledger import ImmutableLedger, sha256_json
from .source_registry import SourceRegistry
from .upstox import (
    ContentAddressedRawStore,
    UpstoxCredentials,
    UpstoxReadOnlyClient,
    fno_equity_underlyings,
)


class UnderlyingHistoryBackfill:
    def __init__(self, ledger: ImmutableLedger, client: UpstoxReadOnlyClient) -> None:
        self.ledger = ledger
        self.client = client

    def run(
        self,
        *,
        from_date: date,
        to_date: date,
        minimum_interval_seconds: float = 0.25,
    ) -> dict[str, int]:
        rows, master_observation_id = self._latest_instrument_master()
        underlyings = fno_equity_underlyings(rows)
        if not underlyings:
            raise ValueError("instrument master has no equity FnO discovery members")
        job_id = sha256_json(
            {
                "kind": "research_underlying_daily_history",
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "instrument_master_observation_id": master_observation_id,
            }
        )[:32]
        now = datetime.now(timezone.utc)
        self.ledger.append(
            "research_backfill_started",
            job_id,
            {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "discovery_member_count": len(underlyings),
                "instrument_master_observation_id": master_observation_id,
                "purpose": "threshold_research_only_no_alert_publication",
            },
            occurred_at=now,
            idempotency_key=f"research-backfill-start:{job_id}",
        )
        completed_keys = self._completed_keys(job_id)
        completed = skipped = failed = 0
        for underlying_key, symbol in underlyings:
            if underlying_key in completed_keys:
                skipped += 1
                continue
            started = time.monotonic()
            try:
                capture = self.client.historical_candles(
                    underlying_key,
                    unit="days",
                    interval=1,
                    from_date=from_date,
                    to_date=to_date,
                )
                candle_count = len(capture.payload.get("data", {}).get("candles", []))
                at = capture.observation.retrieved_at
                self.ledger.append(
                    "research_history_captured",
                    job_id,
                    {
                        "underlying_key": underlying_key,
                        "symbol": symbol,
                        "observation_id": capture.observation.observation_id,
                        "content_sha256": capture.observation.content_sha256,
                        "candle_count": candle_count,
                    },
                    occurred_at=at,
                    idempotency_key=f"research-history:{job_id}:{underlying_key}",
                )
                completed += 1
            except Exception as exc:  # each failure is evidence; the batch remains resumable
                at = datetime.now(timezone.utc)
                detail = str(exc)[:300]
                self.ledger.append(
                    "data_quality_failure",
                    job_id,
                    {
                        "underlying_key": underlying_key,
                        "symbol": symbol,
                        "stage": "research_history_capture",
                        "error_type": type(exc).__name__,
                        "detail": detail,
                    },
                    occurred_at=at,
                    idempotency_key=(
                        f"research-history-failure:{job_id}:{underlying_key}:"
                        f"{sha256_json({'type': type(exc).__name__, 'detail': detail})[:16]}"
                    ),
                )
                failed += 1
            elapsed = time.monotonic() - started
            if elapsed < minimum_interval_seconds:
                time.sleep(minimum_interval_seconds - elapsed)
        final = {"completed": completed, "skipped": skipped, "failed": failed}
        completed_at = datetime.now(timezone.utc)
        self.ledger.append(
            "research_backfill_run_completed",
            job_id,
            final,
            occurred_at=completed_at,
            idempotency_key=f"research-backfill-run:{job_id}:{completed_at.isoformat()}",
        )
        return final

    def _latest_instrument_master(self) -> tuple[list[dict], str]:
        events = [
            event for event in self.ledger.events()
            if event.event_type == "source_request_recorded"
            and event.payload.get("request", {}).get("endpoint", "").endswith("NSE.json.gz")
        ]
        if not events:
            raise ValueError("capture an approved Upstox instrument master first")
        observation_id = events[-1].aggregate_id
        observed = next(
            event for event in self.ledger.events(observation_id)
            if event.event_type == "source_observed"
        )
        content = Path(observed.payload["storage_ref"]).read_bytes()
        rows = json.loads(gzip.decompress(content))
        if not isinstance(rows, list):
            raise ValueError("instrument master payload is not a list")
        return rows, observation_id

    def _completed_keys(self, job_id: str) -> set[str]:
        return {
            str(event.payload["underlying_key"])
            for event in self.ledger.events(job_id)
            if event.event_type == "research_history_captured"
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--raw-store", required=True)
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--to-date", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    ledger = ImmutableLedger(args.ledger)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials.from_os_secret_store(),
        registry=SourceRegistry(ledger),
        raw_store=ContentAddressedRawStore(args.raw_store),
    )
    result = UnderlyingHistoryBackfill(ledger, client).run(
        from_date=args.from_date, to_date=args.to_date
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
