"""Evidence-only profiling of captured research history; no inferred thresholds."""

from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .ledger import ImmutableLedger, sha256_json


@dataclass(frozen=True)
class SymbolQuality:
    underlying_key: str
    symbol: str
    candle_count: int
    earliest_timestamp: str | None
    latest_timestamp: str | None
    duplicate_timestamps: int
    malformed_candles: int
    invalid_ohlc_candles: int
    content_hash_verified: bool


class HistoryQualityProfiler:
    def __init__(self, ledger: ImmutableLedger) -> None:
        self.ledger = ledger

    def profile_and_record(self, job_id: str) -> str:
        events = self.ledger.events(job_id)
        captures = [event for event in events if event.event_type == "research_history_captured"]
        if not captures:
            raise ValueError("research backfill has no captured histories")
        profiles = [self._profile(event.payload) for event in captures]
        failures = [event for event in events if event.event_type == "data_quality_failure"]
        counts = [profile.candle_count for profile in profiles]
        payload: dict[str, Any] = {
            "job_id": job_id,
            "captured_symbols": len(profiles),
            "capture_failures": len(failures),
            "candle_count_distribution": {
                "minimum": min(counts),
                "median": statistics.median(counts),
                "maximum": max(counts),
            },
            "totals": {
                "candles": sum(counts),
                "duplicate_timestamps": sum(p.duplicate_timestamps for p in profiles),
                "malformed_candles": sum(p.malformed_candles for p in profiles),
                "invalid_ohlc_candles": sum(p.invalid_ohlc_candles for p in profiles),
                "hash_failures": sum(not p.content_hash_verified for p in profiles),
            },
            "symbols": [profile.__dict__ for profile in sorted(profiles, key=lambda p: p.symbol)],
            "interpretation": "descriptive_only_no_quality_threshold_activated",
        }
        profile_hash = sha256_json(payload)
        at = datetime.now(timezone.utc)
        event = self.ledger.append(
            "data_quality_profiled",
            job_id,
            {**payload, "profile_hash": profile_hash},
            occurred_at=at,
            idempotency_key=f"data-quality-profile:{job_id}:{profile_hash}",
        )
        return event.event_id

    def _profile(self, capture: dict[str, Any]) -> SymbolQuality:
        observation_id = str(capture["observation_id"])
        observed = next(
            event for event in self.ledger.events(observation_id)
            if event.event_type == "source_observed"
        )
        content = Path(observed.payload["storage_ref"]).read_bytes()
        verified = hashlib.sha256(content).hexdigest() == observed.payload["content_sha256"]
        value = json.loads(content)
        candles = value.get("data", {}).get("candles", [])
        if not isinstance(candles, list):
            candles = []
        timestamps: list[str] = []
        malformed = invalid_ohlc = 0
        for candle in candles:
            if not isinstance(candle, list) or len(candle) < 6:
                malformed += 1
                continue
            timestamp = str(candle[0])
            timestamps.append(timestamp)
            try:
                open_price, high, low, close = map(float, candle[1:5])
                if min(open_price, high, low, close) <= 0:
                    invalid_ohlc += 1
                elif high < max(open_price, close, low) or low > min(open_price, close, high):
                    invalid_ohlc += 1
            except (TypeError, ValueError):
                malformed += 1
        return SymbolQuality(
            underlying_key=str(capture["underlying_key"]),
            symbol=str(capture["symbol"]),
            candle_count=len(candles),
            earliest_timestamp=min(timestamps) if timestamps else None,
            latest_timestamp=max(timestamps) if timestamps else None,
            duplicate_timestamps=len(timestamps) - len(set(timestamps)),
            malformed_candles=malformed,
            invalid_ohlc_candles=invalid_ohlc,
            content_hash_verified=verified,
        )


class IntradayQualityProfiler(HistoryQualityProfiler):
    """Profile every captured intraday request window and record one audit event."""

    def profile_and_record(self, job_id: str) -> str:
        events = self.ledger.events(job_id)
        captures = [event for event in events if event.event_type == "research_intraday_captured"]
        if not captures:
            raise ValueError("research intraday backfill has no captured windows")
        profiles = [self._profile(event.payload) for event in captures]
        counts = [profile.candle_count for profile in profiles]
        captured_keys = {
            (
                str(event.payload["underlying_key"]),
                str(event.payload["window_start"]),
                str(event.payload["window_end"]),
            )
            for event in captures
        }
        failed_keys = {
            (
                str(event.payload["underlying_key"]),
                str(event.payload["window_start"]),
                str(event.payload["window_end"]),
            )
            for event in events
            if event.event_type == "data_quality_failure"
            and event.payload.get("stage") == "research_intraday_capture"
        }
        payload: dict[str, Any] = {
            "job_id": job_id,
            "captured_windows": len(profiles),
            "captured_symbols": len({profile.underlying_key for profile in profiles}),
            "unresolved_capture_failures": len(failed_keys - captured_keys),
            "candle_count_distribution_per_window": {
                "minimum": min(counts),
                "median": statistics.median(counts),
                "maximum": max(counts),
            },
            "totals": {
                "candles": sum(counts),
                "duplicate_timestamps_within_windows": sum(
                    p.duplicate_timestamps for p in profiles
                ),
                "malformed_candles": sum(p.malformed_candles for p in profiles),
                "invalid_ohlc_candles": sum(p.invalid_ohlc_candles for p in profiles),
                "hash_failures": sum(not p.content_hash_verified for p in profiles),
                "zero_candle_windows": sum(p.candle_count == 0 for p in profiles),
            },
            "interpretation": "descriptive_only_no_quality_threshold_activated",
        }
        profile_hash = sha256_json(payload)
        at = datetime.now(timezone.utc)
        event = self.ledger.append(
            "intraday_data_quality_profiled",
            job_id,
            {**payload, "profile_hash": profile_hash},
            occurred_at=at,
            idempotency_key=f"intraday-data-quality-profile:{job_id}:{profile_hash}",
        )
        return event.event_id
