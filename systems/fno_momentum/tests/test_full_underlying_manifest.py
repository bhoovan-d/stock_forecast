import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import fno_momentum.full_underlying_collection as collection
from fno_momentum.full_underlying_collection import collection_awake_window, master_retry_delay
from fno_momentum.source_registry import SourceManifest


PROPOSALS = Path(__file__).parents[2] / "proposals"


def test_full_underlying_manifest_is_authorized_and_excludes_option_bbo():
    value = json.loads(
        (PROPOSALS / "fno_full_underlying_data_collection_v7.json")
        .read_text(encoding="utf-8")
    )
    assert value["status"] == "authorized_data_acquisition_2026-09-23"
    assert value["activation"]["permitted_now"] is True
    assert value["activation"]["scheduled_job_enabled"] is True
    assert value["activation"]["token_access_permitted_now"] is True
    assert value["activation"]["historical_bulk_job_enabled"] is False
    assert "09:10 IST" in value["quality_and_failure_controls"]["master_recovery"]
    assert "every minute" in value["quality_and_failure_controls"]["master_recovery"]
    assert "maximum 120 attempts" in value["quality_and_failure_controls"]["master_recovery"]
    assert "17:30 IST" in value["scope"]["ongoing"]["host_awake_control"]
    assert value["storage"]["raw_retention_days"] == 7
    assert value["storage"]["maximum_total_bytes"] == 3221225472
    assert value["storage"]["historical_batch_size_underlyings"] == 5
    assert value["scope"]["ongoing"]["weekly_derivation"]["version"] == "nse-session-daily-to-weekly-v1"
    assert len(value["scope"]["historical_ohlcv"]) == 3
    assert all("NSE_EQ" in endpoint or "instruments/exchange/NSE" in endpoint
               for endpoint in value["provider"]["proposed_endpoint_allowlist"])
    assert all(item["status"] == "identified_not_approved"
               for item in value["external_reference_sources"])


def test_proposed_source_manifest_has_valid_registry_fields():
    value = json.loads(
        (PROPOSALS / "fno_source_upstox_underlying_v1.json")
        .read_text(encoding="utf-8")
    )
    value["fields"] = tuple(value["fields"])
    SourceManifest(**value).validate()


def test_master_retry_schedule_is_faster_near_open():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert master_retry_delay(datetime(2026, 9, 25, 9, 10, tzinfo=ist)) == timedelta(minutes=1)
    assert master_retry_delay(datetime(2026, 9, 25, 9, 59, tzinfo=ist)) == timedelta(minutes=1)
    assert master_retry_delay(datetime(2026, 9, 25, 10, 0, tzinfo=ist)) == timedelta(minutes=5)


def test_collection_awake_window_is_limited_to_weekday_capture():
    ist = timezone(timedelta(hours=5, minutes=30))
    assert not collection_awake_window(datetime(2026, 9, 25, 9, 9, tzinfo=ist))
    assert collection_awake_window(datetime(2026, 9, 25, 9, 10, tzinfo=ist))
    assert collection_awake_window(datetime(2026, 9, 25, 17, 29, tzinfo=ist))
    assert not collection_awake_window(datetime(2026, 9, 25, 17, 30, tzinfo=ist))
    assert not collection_awake_window(datetime(2026, 9, 26, 12, 0, tzinfo=ist))


def test_collector_attempts_current_day_master_before_open(monkeypatch):
    class StopLoop(Exception):
        pass

    class EmptyLedger:
        def events(self, _key):
            return []

    ist = timezone(timedelta(hours=5, minutes=30))
    at = datetime(2026, 9, 25, 9, 10, tzinfo=ist).astimezone(timezone.utc)
    monkeypatch.setattr(collection, "utc", lambda: at)
    monkeypatch.setattr(collection, "set_system_awake", lambda _enabled: True)
    monkeypatch.setattr(collection.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopLoop()))
    collector = collection.Collector.__new__(collection.Collector)
    collector.ledger = EmptyLedger()
    events = []
    collector.event = lambda kind, key, payload: events.append((kind, key, payload))
    collector.refresh_master = lambda: None

    with pytest.raises(StopLoop):
        collector._serve_locked()

    assert [kind for kind, _, _ in events] == ["ongoing_lane_started", "system_awake_request_changed", "master_refresh_failed"]
    assert events[-1][2]["attempt"] == 1
