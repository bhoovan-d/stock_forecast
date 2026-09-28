import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fno_momentum.contracts import RawObservation
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.reliance_quality_pilot import (
    APPROVED_MANIFEST_SHA256,
    QuotaRawStore,
    RelianceQualityPilot,
    StorageQuotaExceeded,
    _approved_manifest,
    _discover_reliance,
)
from fno_momentum.upstox import CapturedPayload


START = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)  # 09:30 IST


def _observation(name, at=START):
    return RawObservation(
        observation_id=name,
        source_id="upstox-read-only-market-data-v3",
        retrieved_at=at,
        content_sha256="a" * 64,
        parser_version="fixture",
        license_tier="fixture",
        storage_ref=f"fixture://{name}",
    )


def _instrument_rows():
    return [
        {
            "segment": "NSE_FO",
            "instrument_type": "CE",
            "underlying_type": "EQUITY",
            "underlying_key": "NSE_EQ|RELIANCE",
            "underlying_symbol": "RELIANCE",
            "instrument_key": "NSE_FO|REL-CE-1",
            "expiry": "2026-09-24",
        },
        {
            "segment": "NSE_FO",
            "instrument_type": "PE",
            "underlying_type": "EQUITY",
            "underlying_key": "NSE_EQ|RELIANCE",
            "underlying_symbol": "RELIANCE",
            "instrument_key": "NSE_FO|REL-PE-2",
            "expiry": "2026-10-29",
        },
        {
            "segment": "NSE_FO",
            "instrument_type": "CE",
            "underlying_type": "INDEX",
            "underlying_key": "NSE_INDEX|NIFTY",
            "underlying_symbol": "NIFTY",
            "instrument_key": "NSE_FO|NIFTY-CE",
        },
    ]


class FakeClock:
    def __init__(self):
        self.elapsed = 0.0

    def now(self):
        return START + timedelta(seconds=self.elapsed)

    def monotonic(self):
        return self.elapsed

    def sleep(self, seconds):
        self.elapsed += seconds


class FakeClient:
    def __init__(self, clock):
        self.clock = clock
        self.requests = []

    def instrument_master(self):
        return CapturedPayload(_observation("master", self.clock.now()), _instrument_rows())

    def full_quotes(self, instrument_keys):
        self.requests.append(instrument_keys)
        rows = {}
        for index, key in enumerate(instrument_keys):
            rows[str(index)] = {
                "instrument_token": key,
                "timestamp": self.clock.now().isoformat(),
                "last_trade_time": self.clock.now().isoformat(),
                "depth": {
                    "buy": [{"price": 10, "quantity": 5}],
                    "sell": [{"price": 10.2, "quantity": 6}],
                },
                "oi": 100,
                "volume": 50,
            }
        return CapturedPayload(
            _observation(f"quote-{len(self.requests)}", self.clock.now()),
            {"status": "success", "data": rows},
        )


def test_discovery_is_reliance_equity_and_all_listed_expiries():
    underlying, contracts = _discover_reliance(_instrument_rows())
    assert underlying == "NSE_EQ|RELIANCE"
    assert contracts == ("NSE_FO|REL-CE-1", "NSE_FO|REL-PE-2")


def test_pilot_captures_fixed_snapshots_without_strategy_outputs(tmp_path):
    clock = FakeClock()
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    client = FakeClient(clock)
    result = RelianceQualityPilot(
        ledger,
        client,
        now=clock.now,
        monotonic=clock.monotonic,
        sleeper=clock.sleep,
    ).run(manifest_hash=APPROVED_MANIFEST_SHA256, snapshot_count=2, interval_seconds=60)
    assert result["completed_snapshots"] == 2
    assert result["failed_snapshots"] == 0
    assert len(client.requests) == 2
    assert all(len(request) == 3 for request in client.requests)
    event_types = [event.event_type for event in ledger.events(result["job_id"])]
    assert event_types == [
        "reliance_quality_pilot_started",
        "reliance_quality_snapshot_captured",
        "reliance_quality_snapshot_captured",
        "reliance_quality_pilot_completed",
    ]


def test_pilot_rejects_run_outside_approved_market_window(tmp_path):
    clock = FakeClock()
    clock.elapsed = 8 * 60 * 60
    client = FakeClient(clock)
    with pytest.raises(RuntimeError, match="09:15-15:30"):
        RelianceQualityPilot(
            ImmutableLedger(tmp_path / "events.sqlite"),
            client,
            now=clock.now,
            monotonic=clock.monotonic,
            sleeper=clock.sleep,
        ).run(manifest_hash=APPROVED_MANIFEST_SHA256, snapshot_count=60, interval_seconds=60)
    assert client.requests == []


def test_quota_store_rejects_write_before_limit_is_exceeded(tmp_path):
    store = QuotaRawStore(tmp_path, maximum_total_bytes=3)
    store.put(b"abc", suffix="json")
    with pytest.raises(StorageQuotaExceeded):
        store.put(b"defg", suffix="json")
    assert sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file()) == 3


def test_checked_in_manifest_is_the_exact_approved_version():
    path = Path(__file__).parents[2] / "proposals" / "fno_upstox_reliance_quality_pilot_v1.json"
    value = _approved_manifest(path)
    assert value["scope"]["snapshot_count"] == 20
    assert value["scope"]["snapshot_interval_seconds"] == 60
    assert value["storage"]["maximum_total_bytes"] == 262144000
    assert json.loads(path.read_text(encoding="utf-8"))["hard_boundaries"]["orders"] is False

