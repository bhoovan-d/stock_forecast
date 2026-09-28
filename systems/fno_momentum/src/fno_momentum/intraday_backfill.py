"""Resumable, research-only intraday capture for the active dated universe."""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone

from .ledger import ImmutableLedger, sha256_json
from .source_registry import SourceRegistry
from .upstox import ContentAddressedRawStore, UpstoxCredentials, UpstoxReadOnlyClient


def monthly_windows(start: date, end: date) -> tuple[tuple[date, date], ...]:
    if start > end:
        raise ValueError("intraday start date cannot follow end date")
    windows = []
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=27), end)
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


class IntradayHistoryBackfill:
    def __init__(self, ledger: ImmutableLedger, client: UpstoxReadOnlyClient) -> None:
        self.ledger = ledger
        self.client = client

    def run(
        self,
        *,
        from_date: date,
        to_date: date,
        interval_minutes: int,
        minimum_interval_seconds: float = 0.25,
    ) -> dict[str, int]:
        if interval_minutes not in {5, 15}:
            raise ValueError("research entry timeframes are restricted to 5m and 15m")
        universe = self._active_universe()
        windows = monthly_windows(from_date, to_date)
        job_id = sha256_json(
            {
                "kind": "research_intraday_history",
                "universe_hash": universe.payload["universe_hash"],
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "interval_minutes": interval_minutes,
            }
        )[:32]
        now = datetime.now(timezone.utc)
        self.ledger.append(
            "research_intraday_backfill_started",
            job_id,
            {
                "universe_hash": universe.payload["universe_hash"],
                "member_count": universe.payload["member_count"],
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "interval_minutes": interval_minutes,
                "window_count": len(windows),
                "purpose": "pattern_threshold_research_only_no_alert_publication",
            },
            occurred_at=now,
            idempotency_key=f"intraday-backfill-start:{job_id}",
        )
        completed_requests = self._completed_requests(job_id)
        completed = skipped = failed = 0
        for member in universe.payload["members"]:
            underlying_key, symbol = member["underlying_key"], member["symbol"]
            for window_start, window_end in windows:
                request_key = (underlying_key, window_start.isoformat(), window_end.isoformat())
                if request_key in completed_requests:
                    skipped += 1
                    continue
                started = time.monotonic()
                try:
                    capture = self.client.historical_candles(
                        underlying_key,
                        unit="minutes",
                        interval=interval_minutes,
                        from_date=window_start,
                        to_date=window_end,
                    )
                    candles = capture.payload.get("data", {}).get("candles", [])
                    self.ledger.append(
                        "research_intraday_captured",
                        job_id,
                        {
                            "underlying_key": underlying_key,
                            "symbol": symbol,
                            "window_start": window_start.isoformat(),
                            "window_end": window_end.isoformat(),
                            "interval_minutes": interval_minutes,
                            "observation_id": capture.observation.observation_id,
                            "content_sha256": capture.observation.content_sha256,
                            "candle_count": len(candles) if isinstance(candles, list) else 0,
                        },
                        occurred_at=capture.observation.retrieved_at,
                        idempotency_key=(
                            f"intraday-captured:{job_id}:{underlying_key}:"
                            f"{window_start.isoformat()}:{window_end.isoformat()}"
                        ),
                    )
                    completed += 1
                except Exception as exc:
                    detail = str(exc)[:300]
                    self.ledger.append(
                        "data_quality_failure",
                        job_id,
                        {
                            "underlying_key": underlying_key,
                            "symbol": symbol,
                            "window_start": window_start.isoformat(),
                            "window_end": window_end.isoformat(),
                            "interval_minutes": interval_minutes,
                            "stage": "research_intraday_capture",
                            "error_type": type(exc).__name__,
                            "detail": detail,
                        },
                        occurred_at=datetime.now(timezone.utc),
                        idempotency_key=(
                            f"intraday-failure:{job_id}:{underlying_key}:"
                            f"{window_start.isoformat()}:{window_end.isoformat()}:"
                            f"{sha256_json({'type': type(exc).__name__, 'detail': detail})[:16]}"
                        ),
                    )
                    failed += 1
                elapsed = time.monotonic() - started
                if elapsed < minimum_interval_seconds:
                    time.sleep(minimum_interval_seconds - elapsed)
        result = {"completed": completed, "skipped": skipped, "failed": failed}
        finished_at = datetime.now(timezone.utc)
        self.ledger.append(
            "research_intraday_backfill_run_completed",
            job_id,
            result,
            occurred_at=finished_at,
            idempotency_key=f"intraday-run:{job_id}:{finished_at.isoformat()}",
        )
        return result

    def _active_universe(self):
        events = [event for event in self.ledger.events() if event.event_type == "universe_snapshot_created"]
        if not events:
            raise ValueError("an active dated universe snapshot is required")
        return events[-1]

    def _completed_requests(self, job_id: str) -> set[tuple[str, str, str]]:
        return {
            (
                str(event.payload["underlying_key"]),
                str(event.payload["window_start"]),
                str(event.payload["window_end"]),
            )
            for event in self.ledger.events(job_id)
            if event.event_type == "research_intraday_captured"
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--raw-store", required=True)
    parser.add_argument("--from-date", required=True, type=date.fromisoformat)
    parser.add_argument("--to-date", required=True, type=date.fromisoformat)
    parser.add_argument("--interval", required=True, type=int, choices=(5, 15))
    args = parser.parse_args()
    ledger = ImmutableLedger(args.ledger)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials.from_os_secret_store(),
        registry=SourceRegistry(ledger),
        raw_store=ContentAddressedRawStore(args.raw_store),
    )
    result = IntradayHistoryBackfill(ledger, client).run(
        from_date=args.from_date,
        to_date=args.to_date,
        interval_minutes=args.interval,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
