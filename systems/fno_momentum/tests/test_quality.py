import hashlib
import json
from datetime import datetime, timezone

from fno_momentum.ledger import ImmutableLedger
from fno_momentum.quality import HistoryQualityProfiler, IntradayQualityProfiler


NOW = datetime(2026, 9, 16, 4, tzinfo=timezone.utc)


def test_quality_profile_verifies_hash_and_reports_without_thresholds(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    content = json.dumps(
        {
            "status": "success",
            "data": {
                "candles": [
                    ["2026-09-15T00:00:00+05:30", 100, 102, 99, 101, 1000],
                    ["2026-09-14T00:00:00+05:30", 99, 101, 98, 100, 900],
                ]
            },
        }
    ).encode()
    raw = tmp_path / "raw.json"
    raw.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    ledger.append(
        "source_observed",
        "obs-1",
        {"storage_ref": str(raw), "content_sha256": digest},
        occurred_at=NOW,
        idempotency_key="observed",
    )
    ledger.append(
        "research_history_captured",
        "job-1",
        {
            "underlying_key": "NSE_EQ|ABC",
            "symbol": "ABC",
            "observation_id": "obs-1",
            "content_sha256": digest,
            "candle_count": 2,
        },
        occurred_at=NOW,
        idempotency_key="captured",
    )
    event_id = HistoryQualityProfiler(ledger).profile_and_record("job-1")
    event = next(event for event in ledger.events() if event.event_id == event_id)
    assert event.payload["captured_symbols"] == 1
    assert event.payload["totals"]["hash_failures"] == 0
    assert event.payload["totals"]["invalid_ohlc_candles"] == 0
    assert event.payload["interpretation"] == "descriptive_only_no_quality_threshold_activated"


def test_intraday_quality_profile_reports_windows_and_resolved_failures(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    content = json.dumps(
        {"data": {"candles": [["2026-09-15T09:15:00+05:30", 100, 102, 99, 101, 10]]}}
    ).encode()
    raw = tmp_path / "intraday.json"
    raw.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    ledger.append(
        "source_observed",
        "obs-intraday",
        {"storage_ref": str(raw), "content_sha256": digest},
        occurred_at=NOW,
        idempotency_key="intraday-observed",
    )
    request = {
        "underlying_key": "NSE_EQ|ABC",
        "symbol": "ABC",
        "window_start": "2026-09-01",
        "window_end": "2026-09-16",
        "interval_minutes": 15,
    }
    ledger.append(
        "data_quality_failure",
        "job-intraday",
        {**request, "stage": "research_intraday_capture"},
        occurred_at=NOW,
        idempotency_key="intraday-failed-once",
    )
    ledger.append(
        "research_intraday_captured",
        "job-intraday",
        {**request, "observation_id": "obs-intraday", "content_sha256": digest, "candle_count": 1},
        occurred_at=NOW,
        idempotency_key="intraday-captured",
    )

    event_id = IntradayQualityProfiler(ledger).profile_and_record("job-intraday")
    event = next(event for event in ledger.events() if event.event_id == event_id)
    assert event.payload["captured_windows"] == 1
    assert event.payload["captured_symbols"] == 1
    assert event.payload["unresolved_capture_failures"] == 0
    assert event.payload["totals"]["candles"] == 1
    assert event.payload["totals"]["hash_failures"] == 0
