"""Controlled raw acquisition for the approved full FnO underlying foundation."""

from __future__ import annotations

import argparse
import ctypes
from contextlib import contextmanager
import gzip
import hashlib
import json
import msvcrt
import os
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .ledger import ImmutableLedger
from .secret_store import read_upstox_analytics_token
from .universe_foundation import inspect_instrument_master
from .upstox import INSTRUMENTS_URL, UpstoxEndpointPolicy, _NoRedirectHandler

IST = timezone(timedelta(hours=5, minutes=30), "Asia/Kolkata")
PROPOSALS = Path(__file__).parents[3] / "proposals"
MANIFEST = PROPOSALS / "fno_full_underlying_data_collection_v7.json"
BASELINE = Path("data/fno_momentum/upstox/universe_foundation_v1/reference")


def utc() -> datetime:
    return datetime.now(timezone.utc)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def used_bytes(root: Path) -> int:
    # DirectoryEntry caches metadata returned by Windows enumeration. Count the
    # same complete tree without issuing multiple stat calls for every file.
    total = 0
    pending = [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(entry.path)
                elif entry.is_file():
                    total += entry.stat().st_size
    return total


def master_retry_delay(now_ist: datetime) -> timedelta:
    """Retry a failed current-day master promptly near the opening quote slot."""
    if now_ist.tzinfo is None:
        raise ValueError("master retry time must be timezone-aware")
    cutoff = now_ist.replace(hour=10, minute=0, second=0, microsecond=0)
    return timedelta(minutes=1 if now_ist < cutoff else 5)


def collection_awake_window(now_ist: datetime) -> bool:
    """Keep the host awake only while the current session's capture is due."""
    if now_ist.tzinfo is None:
        raise ValueError("power-control time must be timezone-aware")
    if now_ist.weekday() >= 5:
        return False
    start = now_ist.replace(hour=9, minute=10, second=0, microsecond=0)
    end = now_ist.replace(hour=17, minute=30, second=0, microsecond=0)
    return start <= now_ist < end


def set_system_awake(enabled: bool) -> bool:
    """Set or clear a thread-local Windows system power request, not display power."""
    flags = 0x80000000 | (0x00000001 if enabled else 0)
    result = ctypes.windll.kernel32.SetThreadExecutionState(flags)
    return result != 0


class StorageLimitError(RuntimeError):
    pass


class StaleCycleError(RuntimeError):
    pass


class Collector:
    def __init__(self, manifest_path: Path = MANIFEST) -> None:
        self.manifest_path = manifest_path.resolve()
        self.manifest_bytes = self.manifest_path.read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        if self.manifest["status"] != "authorized_data_acquisition_2026-09-23":
            raise PermissionError("full underlying collection is inactive")
        self.root = Path(self.manifest["storage"]["root"])
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ImmutableLedger(self.root / "events.sqlite")
        self.manifest_hash = digest(self.manifest_bytes)
        self.source_path = PROPOSALS / self.manifest["proposed_source_manifest"]
        self.source_bytes = self.source_path.read_bytes()
        self.source_hash = digest(self.source_bytes)
        self.source_id = json.loads(self.source_bytes)["source_id"]
        self.code_hash = digest(Path(__file__).read_bytes())
        self.cap = int(self.manifest["storage"]["maximum_total_bytes"])
        self.batch_cap = int(self.manifest["storage"]["maximum_batch_increment_bytes"])
        self.historical_soft_cap = int(self.manifest["storage"]["historical_soft_cap_bytes"])
        self.reserve = 32 * 1024 * 1024
        self._bytes_used = used_bytes(self.root)
        self._writes = 0
        self.token: str | None = None

    @contextmanager
    def write_lock(self):
        with (self.root / "storage.lock").open("a+b") as lock:
            lock.seek(0)
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def event(self, kind: str, key: str, payload: dict) -> None:
        at = utc()
        with self.write_lock():
            projected = len(json.dumps(payload, allow_nan=False).encode("utf-8")) + 1048576
            if used_bytes(self.root) + projected > self.cap:
                raise StorageLimitError("3 GiB ceiling leaves insufficient room for audit ledger")
            self.ledger.append(kind, key, {"manifest_sha256": self.manifest_hash,
                "source_manifest_sha256": self.source_hash, "source_id": self.source_id,
                "collector_code_sha256": self.code_hash, **payload},
                               occurred_at=at, idempotency_key=f"{kind}:{key}:{at.isoformat()}")

    def write_capped(self, path: Path, content: bytes, *, historical: bool = False) -> None:
        with self.write_lock():
            current = used_bytes(self.root)
            if current + len(content) + self.reserve > self.cap:
                raise StorageLimitError("3 GiB total storage ceiling; 32 MiB audit reserve")
            if historical and current + len(content) > self.historical_soft_cap:
                raise StorageLimitError("2 GiB historical allocation reached; older windows deferred")
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_bytes(content)
            self._bytes_used = used_bytes(self.root)

    def members(self) -> list[dict]:
        files = list(BASELINE.glob("*/*.json.gz"))
        if not files:
            raise RuntimeError("approved FnO universe baseline is absent")
        newest = max(files, key=lambda p: p.stat().st_mtime)
        snapshot = json.loads(gzip.decompress(newest.read_bytes()))
        members = snapshot["members"]
        if len(members) != self.manifest["accepted_baseline"]["active_underlying_count"]:
            raise RuntimeError("approved baseline member count changed")
        return sorted(members, key=lambda x: x["underlying_key"])

    def active_members(self, day: date) -> list[dict]:
        events = [e for e in self.ledger.events(f"master:{day}")
                  if e.event_type == "universe_reference_observed"]
        if not events:
            raise RuntimeError(f"no verified instrument master for {day}")
        path = Path(events[-1].payload["reference_path"])
        snapshot = json.loads(gzip.decompress(path.read_bytes()))
        return sorted(snapshot["members"], key=lambda x: x["underlying_key"])

    def fetch(self, url: str, key: str, *, slot: datetime | None = None,
              historical: bool = False) -> dict | list | None:
        UpstoxEndpointPolicy.require_allowed(url, method="GET")
        if url != INSTRUMENTS_URL and self.token is None:
            self.token = read_upstox_analytics_token()
        headers = {"Accept": "application/json", "User-Agent": "fno-underlying-acquisition/1"}
        if url != INSTRUMENTS_URL:
            headers["Authorization"] = f"Bearer {self.token}"
        started = utc()
        if slot is not None and (started - slot).total_seconds() >= 300:
            raise StaleCycleError("scheduled quote slot expired before request start")
        tick = time.monotonic()
        try:
            request = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.build_opener(_NoRedirectHandler()).open(request, timeout=30) as response:
                body = response.read()
                status = response.status
            ended = utc()
            if status != 200:
                raise RuntimeError(f"provider HTTP {status}")
            data = json.loads(gzip.decompress(body) if url == INSTRUMENTS_URL else body)
            if url != INSTRUMENTS_URL and (not isinstance(data, dict) or data.get("status") != "success"):
                raise ValueError("provider response has no success status")
            raw = gzip.compress(body, compresslevel=6, mtime=0)
            sha = digest(body)
            path = self.root / "raw" / sha[:2] / f"{sha}.http.gz"
            self.write_capped(path, raw, historical=historical)
            self.event("raw_response_captured", key, {
                "url_path": urllib.parse.urlsplit(url).path,
                "request_start_utc": started.isoformat(), "response_end_utc": ended.isoformat(),
                "elapsed_ms": round((time.monotonic() - tick) * 1000, 3),
                "scheduled_slot_utc": slot.isoformat() if slot else None,
                "request_lateness_ms": round((started-slot).total_seconds()*1000, 3) if slot else None,
                "completion_delay_ms": round((ended-slot).total_seconds()*1000, 3) if slot else None,
                "raw_sha256": sha, "raw_path": str(path.resolve()), "raw_bytes": len(raw),
            })
            return data
        except Exception as exc:
            if isinstance(exc, StorageLimitError):
                self.event("collection_blocked", key, {"reason": str(exc)})
                raise
            self.event("source_failure", key, {"url_path": urllib.parse.urlsplit(url).path,
                "request_start_utc": started.isoformat(), "failed_at_utc": utc().isoformat(),
                "error_type": type(exc).__name__, "http_status": exc.code if isinstance(exc, HTTPError) else None,
                "error": str(exc)[:200]})
            if isinstance(exc, HTTPError) and exc.code in {401, 403, 429}:
                self.event("collection_blocked", key, {"reason": f"provider_http_{exc.code}"})
                raise
            return None

    def normalized(self, key: str, kind: str, rows: list | dict, *, historical: bool = False) -> str:
        content = json.dumps({"key": key, "kind": kind, "rows": rows}, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        sha = digest(content)
        compressed = gzip.compress(content, compresslevel=6, mtime=0)
        path = self.root / "normalized" / kind / sha[:2] / f"{sha}.json.gz"
        self.write_capped(path, compressed, historical=historical)
        self.event("normalized_partition_recorded", key, {"kind": kind, "content_sha256": sha,
            "path": str(path.resolve()), "compressed_bytes": len(compressed)})
        return sha

    def refresh_master(self) -> list[dict] | None:
        data = self.fetch(INSTRUMENTS_URL, f"master:{utc().date()}")
        if data is None:
            return None
        quality, snapshot = inspect_instrument_master(data)
        content = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ref_hash = digest(content)
        compressed = gzip.compress(content, compresslevel=9, mtime=0)
        file_hash = digest(compressed)
        path = self.root / "reference" / ref_hash[:2] / f"{ref_hash}.json.gz"
        self.write_capped(path, compressed)
        self.event("universe_reference_observed", f"master:{utc().date()}", {
            "effective_from_utc": utc().isoformat(), "member_count": len(snapshot["members"]),
            "contract_count": len(snapshot["contracts"]), "quality": quality,
            "reference_sha256": ref_hash, "reference_file_sha256": file_hash,
            "reference_path": str(path.resolve()), "historical_eligibility_known": False})
        return snapshot["members"]

    def backfill_batch(self, offset: int) -> dict:
        if self.manifest["activation"].get("historical_bulk_job_enabled") is False:
            raise PermissionError("all-universe historical backfill is disabled; use on-demand windows")
        members = self.members()
        size = self.manifest["storage"]["historical_batch_size_underlyings"]
        selected = members[offset:offset+size]
        if not selected:
            raise ValueError("batch offset is beyond the universe")
        before = used_bytes(self.root)
        self._bytes_used = before
        if before + self.batch_cap + self.reserve > self.cap or before >= self.historical_soft_cap:
            self.event("batch_blocked", f"backfill:{offset}", {"reason": "3 GiB total or 2 GiB historical allocation", "bytes_used": before,
                "remaining_members": len(members)-offset})
            return {"blocked": True, "offset": offset}
        requested = completed = failed = 0
        today = utc().astimezone(IST).date()
        for item in selected:
            symbol, instrument = item["symbol"], item["underlying_key"]
            for spec in self.manifest["scope"]["historical_ohlcv"]:
                interval = spec["interval"]
                unit, n = ("days", 1) if interval == "1 day" else ("minutes", 15 if interval == "15 minutes" else 5)
                start = date.fromisoformat(spec["from_date_inclusive"])
                end = min(date.fromisoformat(spec["to_date_inclusive"]), today-timedelta(days=1))
                step_days = 3650 if unit == "days" else 28
                cursor = start
                while cursor <= end:
                    finish = min(end, cursor+timedelta(days=step_days-1))
                    key = f"{symbol}:{n}{unit}:{cursor}:{finish}"
                    if any(e.event_type in {"history_window_recorded", "source_failure", "collection_blocked"}
                           for e in self.ledger.events(key)):
                        cursor = finish+timedelta(days=1)
                        continue
                    path = f"/v3/historical-candle/{urllib.parse.quote(instrument,safe='')}/{unit}/{n}/{finish}/{cursor}"
                    requested += 1
                    try:
                        result = self.fetch(f"https://api.upstox.com{path}", key, historical=True)
                    except StorageLimitError:
                        self.event("partial_batch", f"backfill:{offset}", {"reason": "storage_ceiling",
                            "blocked_window": key, "requested": requested, "completed": completed, "failed": failed})
                        return {"offset": offset, "blocked": True, "blocked_window": key}
                    if result is None:
                        failed += 1
                    else:
                        bars = result.get("data", {}).get("candles", [])
                        try:
                            self.normalized(key, "ohlcv", bars, historical=True)
                        except StorageLimitError:
                            self.event("partial_batch", f"backfill:{offset}", {"reason": "storage_ceiling",
                                "blocked_window": key, "requested": requested, "completed": completed, "failed": failed})
                            return {"offset": offset, "blocked": True, "blocked_window": key}
                        invalid = []
                        observed_by_day: dict[date, set[str]] = {}
                        for row in bars:
                            try:
                                stamp = datetime.fromisoformat(str(row[0]))
                                if stamp.tzinfo is None or stamp.year < 2000 or stamp > utc()+timedelta(days=1):
                                    raise ValueError("invalid timestamp")
                                if len(row) < 6 or any(not isinstance(v, (int, float)) for v in row[1:6]):
                                    raise ValueError("invalid OHLCV types")
                                op, hi, lo, close, vol = row[1:6]
                                if min(op, hi, lo, close) <= 0 or vol < 0 or hi < max(op, close) or lo > min(op, close):
                                    raise ValueError("invalid OHLCV values")
                                local = stamp.astimezone(IST)
                                observed_by_day.setdefault(local.date(), set()).add(local.isoformat())
                            except (ValueError, TypeError, IndexError) as exc:
                                invalid.append({"timestamp": row[0] if row else None, "reason": str(exc)})
                        missing = []
                        if unit == "minutes":
                            for day, observed in observed_by_day.items():
                                session_open = datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=9, minutes=15)
                                for index in range(375//n):
                                    expected = (session_open + timedelta(minutes=n*index)).isoformat()
                                    if expected not in observed:
                                        missing.append(expected)
                        self.event("history_window_recorded", key, {"underlying_key": instrument,
                            "interval": interval, "from_date": str(cursor), "to_date": str(finish),
                            "row_count": len(bars), "invalid_timestamps": invalid,
                            "missing_bar_starts_on_observed_sessions": missing,
                            "missing_history_status": "unresolved_without_historical_membership_or_calendar"})
                        completed += 1
                    if self._bytes_used-before > self.batch_cap:
                        self.event("partial_batch", f"backfill:{offset}", {"requested": requested, "completed": completed,
                            "failed": failed, "reason": "512 MiB batch increment reached"})
                        return {"offset": offset, "partial": True, "requested": requested, "completed": completed, "failed": failed}
                    time.sleep(0.6)
                    cursor = finish+timedelta(days=1)
        self.event("backfill_batch_completed", f"backfill:{offset}", {"members": len(selected),
            "requested": requested, "completed": completed, "failed": failed, "bytes_added": used_bytes(self.root)-before})
        return {"offset": offset, "requested": requested, "completed": completed, "failed": failed}

    def backfill_all(self, start_offset: int = 0) -> None:
        if self.manifest["activation"].get("historical_bulk_job_enabled") is False:
            raise PermissionError("all-universe historical backfill is disabled")
        with (self.root / "backfill.lock").open("a+b") as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return
            count = len(self.members())
            step = self.manifest["storage"]["historical_batch_size_underlyings"]
            for offset in range(start_offset, count, step):
                while True:
                    result = self.backfill_batch(offset)
                    print(json.dumps(result), flush=True)
                    if result.get("blocked"):
                        return
                    if not result.get("partial"):
                        break

    def history_on_demand(self, *, symbol: str, interval: str,
                          from_date: date, to_date: date, reason: str) -> dict:
        if not reason.strip():
            raise ValueError("on-demand request requires a recorded purpose")
        choices = {"daily": ("days", 1, date(2000, 1, 1), 3650),
                   "15m": ("minutes", 15, date(2022, 1, 1), 28),
                   "5m": ("minutes", 5, date(2022, 1, 1), 28)}
        if interval not in choices:
            raise ValueError("interval must be daily, 15m or 5m")
        unit, n, earliest, max_days = choices[interval]
        if from_date < earliest or to_date < from_date or (to_date-from_date).days >= max_days:
            raise ValueError("requested range exceeds the approved provider window")
        if to_date >= utc().astimezone(IST).date():
            raise ValueError("on-demand history is limited to completed prior dates")
        member = next((m for m in self.members() if m["symbol"].upper() == symbol.upper()), None)
        if member is None:
            raise ValueError("symbol is absent from the accepted FnO baseline")
        key = f"on-demand:{symbol.upper()}:{interval}:{from_date}:{to_date}"
        self.event("on_demand_history_requested", key, {"underlying_key": member["underlying_key"],
            "interval": interval, "from_date": str(from_date), "to_date": str(to_date),
            "purpose": reason.strip()})
        path = f"/v3/historical-candle/{urllib.parse.quote(member['underlying_key'],safe='')}/{unit}/{n}/{to_date}/{from_date}"
        result = self.fetch(f"https://api.upstox.com{path}", key, historical=True)
        if result is None:
            return {"status": "source_failure", "key": key}
        bars = result.get("data", {}).get("candles", [])
        self.normalized(key, "ohlcv", bars, historical=True)
        observed: dict[date, set[str]] = {}
        invalid = []
        for row in bars:
            try:
                stamp = datetime.fromisoformat(str(row[0]))
                if stamp.tzinfo is None or len(row) < 6:
                    raise ValueError("invalid timestamp or row")
                op, hi, lo, close, volume = row[1:6]
                if any(not isinstance(v, (int, float)) for v in (op, hi, lo, close, volume)):
                    raise ValueError("invalid OHLCV type")
                if min(op, hi, lo, close) <= 0 or hi < max(op, close) or lo > min(op, close) or volume < 0:
                    raise ValueError("invalid OHLCV value")
                local = stamp.astimezone(IST)
                observed.setdefault(local.date(), set()).add(local.isoformat())
            except (ValueError, TypeError, IndexError) as exc:
                invalid.append({"timestamp": row[0] if row else None, "reason": str(exc)})
        missing = []
        if unit == "minutes":
            for day, stamps in observed.items():
                start = datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=9, minutes=15)
                for i in range(375//n):
                    expected = (start+timedelta(minutes=n*i)).isoformat()
                    if expected not in stamps:
                        missing.append(expected)
        self.event("on_demand_history_recorded", key, {"underlying_key": member["underlying_key"],
            "interval": interval, "from_date": str(from_date), "to_date": str(to_date),
            "returned_rows": len(bars), "invalid_rows": invalid,
            "missing_bar_starts_on_observed_sessions": missing,
            "unrequested_history_status": "UNFETCHED"})
        return {"status": "recorded", "key": key, "rows": len(bars),
                "invalid": len(invalid), "missing_on_observed_sessions": len(missing)}

    def quote_cycle(self, slot: datetime) -> dict:
        if (utc() - slot).total_seconds() >= 300:
            self.event("scheduled_cycle_missed", f"quote:{slot.isoformat()}", {
                "scheduled_slot_utc": slot.isoformat(), "reason": "request_start_after_slot_deadline",
                "missing_underlyings": len(self.active_members(slot.astimezone(IST).date()))})
            return {"requested": 0, "returned": 0, "missed": True}
        members = self.active_members(slot.astimezone(IST).date())
        keys = [m["underlying_key"] for m in members]
        requested = len(keys)
        returned = 0
        for i in range(0, len(keys), 500):
            batch = keys[i:i+500]
            query = urllib.parse.urlencode({"instrument_key": ",".join(batch)})
            try:
                data = self.fetch(f"https://api.upstox.com/v3/market-quote/quotes?{query}",
                                  f"quote:{slot.isoformat()}:{i//500}", slot=slot)
            except StaleCycleError:
                self.event("scheduled_cycle_missed", f"quote:{slot.isoformat()}", {
                    "scheduled_slot_utc": slot.isoformat(), "reason": "request_start_after_slot_deadline",
                    "missing_underlyings": len(members)})
                return {"requested": requested, "returned": returned, "missed": True}
            rows = data.get("data", {}) if data else {}
            if rows:
                slim = {str(v.get("instrument_token", k)): {
                    "price": v.get("last_price"), "volume": v.get("volume"),
                    "provider_timestamp": v.get("timestamp"),
                    "last_trade_time": v.get("last_trade_time")}
                    for k, v in rows.items() if isinstance(v, dict)}
                self.normalized(f"quote:{slot.isoformat()}:{i//500}", "quote", slim)
            returned += len(rows)
            found = {str(v.get("instrument_token", "")) for v in rows.values() if isinstance(v, dict)}
            missing = sorted(set(batch)-found)
            received = utc()
            invalid = []
            provider_lags = []
            trade_lags = []
            for row in rows.values():
                if not isinstance(row, dict):
                    invalid.append("malformed_quote")
                    continue
                instrument = str(row.get("instrument_token", ""))
                if instrument not in batch or not isinstance(row.get("last_price"), (int, float)) or row["last_price"] <= 0:
                    invalid.append(instrument or "unknown_instrument")
                if not isinstance(row.get("volume"), (int, float)) or row["volume"] < 0:
                    invalid.append(instrument + ":invalid_volume")
                try:
                    provider = datetime.fromisoformat(row["timestamp"]).astimezone(timezone.utc)
                    provider_lags.append(round((received-provider).total_seconds()*1000, 3))
                except (KeyError, TypeError, ValueError):
                    invalid.append(instrument + ":invalid_provider_timestamp")
                try:
                    trade = datetime.fromtimestamp(int(row["last_trade_time"])/1000, timezone.utc)
                    trade_lags.append(round((received-trade).total_seconds()*1000, 3))
                except (KeyError, TypeError, ValueError):
                    invalid.append(instrument + ":invalid_last_trade_timestamp")
            self.event("quote_batch_quality", f"quote:{slot.isoformat()}:{i//500}", {
                "requested_keys": batch, "returned_count": len(rows), "missing_keys": missing,
                "invalid_rows": invalid, "provider_lag_ms": provider_lags,
                "last_trade_lag_ms": trade_lags,
                "per_instrument_network_arrival_skew_observable": False})
        self.event("quote_cycle_completed", f"quote:{slot.isoformat()}", {
            "scheduled_slot_utc": slot.isoformat(), "requested": requested, "returned": returned,
            "missing": requested-returned, "collection_delay_ms": round((utc()-slot).total_seconds()*1000)})
        return {"requested": requested, "returned": returned}

    def session_candles(self, session: date) -> dict:
        members = self.active_members(session)
        completed = failed = 0
        starts = {
            5: {(datetime.combine(session, datetime.min.time(), IST) +
                 timedelta(hours=9, minutes=15+5*i)).isoformat() for i in range(75)},
            15: {(datetime.combine(session, datetime.min.time(), IST) +
                  timedelta(hours=9, minutes=15+15*i)).isoformat() for i in range(25)},
        }
        for member in members:
            instrument = member["underlying_key"]
            for unit, n in (("minutes", 5), ("minutes", 15), ("days", 1)):
                key = f"session:{session}:{instrument}:{n}{unit}"
                if any(e.event_type in {"session_candles_recorded", "source_failure", "collection_blocked"}
                       for e in self.ledger.events(key)):
                    continue
                path = f"/v3/historical-candle/intraday/{urllib.parse.quote(instrument,safe='')}/{unit}/{n}"
                result = self.fetch(f"https://api.upstox.com{path}", key)
                if result is None:
                    failed += 1
                    continue
                bars = result.get("data", {}).get("candles", [])
                self.normalized(key, "ohlcv", bars)
                observed = set()
                invalid = []
                for bar in bars:
                    try:
                        stamp = datetime.fromisoformat(str(bar[0]))
                        if stamp.tzinfo is None:
                            raise ValueError("naive timestamp")
                        local = stamp.astimezone(IST)
                        if local.date() != session:
                            continue
                        if len(bar) < 6 or any(not isinstance(v, (int, float)) for v in bar[1:6]):
                            raise ValueError("invalid OHLCV")
                        op, hi, lo, close, vol = bar[1:6]
                        if min(op, hi, lo, close) <= 0 or vol < 0 or hi < max(op, close) or lo > min(op, close):
                            raise ValueError("invalid OHLCV")
                        observed.add(local.isoformat())
                    except (ValueError, TypeError, IndexError) as exc:
                        invalid.append({"timestamp": bar[0] if bar else None, "reason": str(exc)})
                missing = sorted(starts[n]-observed) if n in starts and observed else []
                coverage_status = ("observed_session" if observed else
                                   "target_session_not_returned_by_undated_intraday_endpoint")
                self.event("session_candles_recorded", key, {"session": str(session),
                    "underlying_key": instrument, "interval": f"{n}{unit}",
                    "returned_rows": len(bars), "observed_session_rows": len(observed),
                    "missing_bar_starts": missing, "invalid_rows": invalid,
                    "daily_missing": n == 1 and not observed,
                    "coverage_status": coverage_status})
                completed += 1
                time.sleep(0.6)
        return {"completed": completed, "failed": failed}

    def retention(self, *, execute: bool = False) -> dict:
        """Audit exact raw paths and evidence before any seven-day deletion."""
        all_events = self.ledger.events()
        raw_events = [e for e in all_events if e.event_type == "raw_response_captured"]
        quality_types = {"history_window_recorded", "on_demand_history_recorded",
                         "quote_batch_quality", "universe_reference_observed",
                         "session_candles_recorded"}
        by_path: dict[str, list] = {}
        for event in raw_events:
            by_path.setdefault(event.payload["raw_path"], []).append(event)
        eligible = blocked = deleted = normalized_deleted = 0
        cutoff = utc() - timedelta(days=self.manifest["storage"]["raw_retention_days"])
        for path_text, observations in by_path.items():
            path = Path(path_text)
            if not path.is_file():
                continue
            if any(datetime.fromisoformat(e.payload["response_end_utc"]) > cutoff for e in observations):
                continue
            quality_ids = []
            for observation in observations:
                candidates = [e for e in all_events if e.aggregate_id == observation.aggregate_id
                              and e.event_type in quality_types]
                if not candidates:
                    quality_ids = []
                    break
                quality_ids.append(candidates[-1].event_id)
            if not quality_ids:
                blocked += 1
                if execute:
                    self.event("raw_deletion_blocked", path.name, {"reason": "missing_linked_quality_event",
                        "raw_path": path_text})
                continue
            if path.resolve().parent.parent != (self.root / "raw").resolve():
                raise ValueError("raw path escapes approved storage root")
            raw = gzip.decompress(path.read_bytes())
            if digest(raw) != observations[0].payload["raw_sha256"]:
                raise ValueError("raw payload hash mismatch")
            eligible += 1
            if execute:
                evidence = {"raw_path": path_text, "raw_sha256": digest(raw),
                    "compressed_bytes": path.stat().st_size, "observation_event_ids": [e.event_id for e in observations],
                    "quality_event_ids": quality_ids, "cutoff_utc": cutoff.isoformat()}
                self.event("raw_deletion_intended", path.name, evidence)
                try:
                    path.unlink()
                except OSError as exc:
                    self.event("raw_deletion_failed", path.name, {**evidence, "error": str(exc)[:200]})
                    raise
                self.event("raw_deletion_completed", path.name, {**evidence, "deleted_at_utc": utc().isoformat()})
                deleted += 1
        normalized_cutoff = utc() - timedelta(days=self.manifest["storage"]["forward_normalized_retention_days"])
        for observation in (e for e in all_events if e.event_type == "normalized_partition_recorded"):
            if not observation.aggregate_id.startswith(("quote:", "session:")):
                continue
            if datetime.fromisoformat(observation.occurred_at) > normalized_cutoff:
                continue
            path = Path(observation.payload["path"])
            if not path.is_file():
                continue
            if not any(e.aggregate_id == observation.aggregate_id and e.event_type in quality_types for e in all_events):
                blocked += 1
                continue
            if (self.root / "normalized").resolve() not in path.resolve().parents:
                raise ValueError("normalized path escapes approved storage root")
            if digest(gzip.decompress(path.read_bytes())) != observation.payload["content_sha256"]:
                raise ValueError("normalized partition hash mismatch")
            eligible += 1
            if execute:
                evidence = {"path": str(path.resolve()), "content_sha256": observation.payload["content_sha256"],
                    "partition_event_id": observation.event_id, "retention_days": self.manifest["storage"]["forward_normalized_retention_days"]}
                self.event("normalized_deletion_intended", path.name, evidence)
                try:
                    path.unlink()
                except OSError as exc:
                    self.event("normalized_deletion_failed", path.name, {**evidence, "error": str(exc)[:200]})
                    raise
                self.event("normalized_deletion_completed", path.name, {**evidence, "deleted_at_utc": utc().isoformat()})
                normalized_deleted += 1
        return {"eligible": eligible, "blocked": blocked, "raw_deleted": deleted,
                "normalized_deleted": normalized_deleted, "mode": "execute" if execute else "dry_run"}

    def audit_initial_history(self) -> dict:
        """Record exact in-session gaps for windows acquired before gap details were added."""
        events = self.ledger.events()
        observations = {e.aggregate_id: e for e in events if e.event_type == "raw_response_captured"}
        audited = {e.aggregate_id for e in events if e.event_type == "history_window_gap_audited"}
        count = 0
        for event in events:
            if event.event_type != "history_window_recorded" or "missing_bar_starts_on_observed_sessions" in event.payload:
                continue
            if event.aggregate_id in audited:
                continue
            observation = observations.get(event.aggregate_id)
            if observation is None:
                continue
            payload = json.loads(gzip.decompress(Path(observation.payload["raw_path"]).read_bytes()))
            bars = payload.get("data", {}).get("candles", [])
            interval = event.payload["interval"]
            n = 5 if interval == "5 minutes" else 15 if interval == "15 minutes" else 0
            observed_by_day: dict[date, set[str]] = {}
            invalid = []
            for row in bars:
                try:
                    stamp = datetime.fromisoformat(str(row[0]))
                    if stamp.tzinfo is None or len(row) < 6:
                        raise ValueError("invalid row or timestamp")
                    local = stamp.astimezone(IST)
                    observed_by_day.setdefault(local.date(), set()).add(local.isoformat())
                except (ValueError, TypeError, IndexError):
                    invalid.append(row[0] if row else None)
            missing = []
            if n:
                for day, observed in observed_by_day.items():
                    session_open = datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=9, minutes=15)
                    for i in range(375//n):
                        expected = (session_open + timedelta(minutes=n*i)).isoformat()
                        if expected not in observed:
                            missing.append(expected)
            self.event("history_window_gap_audited", event.aggregate_id, {
                "interval": interval, "missing_bar_starts_on_observed_sessions": missing,
                "invalid_timestamps": invalid, "source_event_id": event.event_id,
                "expectations_outside_observed_sessions": "unknown"})
            count += 1
        return {"windows_audited": count}

    def audit_session_rollover(self, session: date) -> dict:
        events = self.ledger.events()
        corrected = {e.aggregate_id for e in events
                     if e.event_type == "session_response_classification_corrected"}
        count = 0
        for event in events:
            if event.event_type != "session_candles_recorded" or event.payload.get("session") != str(session):
                continue
            if event.aggregate_id in corrected or event.payload.get("observed_session_rows") != 0:
                continue
            self.event("session_response_classification_corrected", event.aggregate_id, {
                "source_event_id": event.event_id,
                "prior_missing_bar_claim_count": len(event.payload.get("missing_bar_starts", [])),
                "corrected_missing_bar_claim_count": 0,
                "coverage_status": "target_session_not_returned_by_undated_intraday_endpoint",
                "reason": "Upstox intraday endpoint is documented for the current trading day; an empty response after rollover is unavailable coverage, not proof that all session bars were absent."})
            count += 1
        return {"corrected_windows": count}

    def serve(self) -> None:
        """Run quote slots during the session and completed candles after close."""
        lock_path = self.root / "ongoing.lock"
        with lock_path.open("a+b") as lock:
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return
            self._serve_locked()

    def _serve_locked(self) -> None:
        self.event("ongoing_lane_started", "ongoing", {"started_at_utc": utc().isoformat()})
        awake = False
        power_failure_logged_day = None
        while True:
            now = utc().astimezone(IST)
            day = now.date()
            want_awake = collection_awake_window(now)
            if want_awake != awake:
                if set_system_awake(want_awake):
                    awake = want_awake
                    self.event("system_awake_request_changed", f"power:{day}", {
                        "enabled": awake, "at_utc": utc().isoformat(),
                        "reason": "prevent_idle_sleep_during_controlled_collection"})
                elif power_failure_logged_day != day:
                    power_failure_logged_day = day
                    self.event("system_awake_request_failed", f"power:{day}", {
                        "requested_enabled": want_awake, "at_utc": utc().isoformat()})
            if now.weekday() < 5:
                open_at = datetime.combine(day, datetime.min.time(), IST) + timedelta(hours=9, minutes=15)
                prepare_at = open_at - timedelta(minutes=5)
                close_at = open_at + timedelta(minutes=375)
                if prepare_at <= now <= close_at:
                    master_events = self.ledger.events(f"master:{day}")
                    verified_master = any(e.event_type == "universe_reference_observed"
                                          for e in master_events)
                    failures = [e for e in master_events if e.event_type == "master_refresh_failed"]
                    retry_delay = master_retry_delay(now)
                    retry_due = (not failures or
                                 utc() - datetime.fromisoformat(failures[-1].occurred_at) >= retry_delay)
                    if not verified_master and len(failures) < 120 and retry_due:
                        if self.refresh_master() is None:
                            self.event("master_refresh_failed", f"master:{day}", {
                                "session": str(day), "attempt": len(failures)+1,
                                "next_attempt_not_before_utc": (utc()+master_retry_delay(utc().astimezone(IST))).isoformat()})
                if now >= open_at:
                    slots = [open_at + timedelta(minutes=5*i) for i in range(76)]
                    for slot_local in slots:
                        if slot_local > now:
                            break
                        slot = slot_local.astimezone(timezone.utc)
                        key = f"quote:{slot.isoformat()}"
                        prior = self.ledger.events(key)
                        if any(e.event_type in {"quote_cycle_completed", "scheduled_cycle_missed"} for e in prior):
                            continue
                        verified = any(e.event_type == "universe_reference_observed"
                                       for e in self.ledger.events(f"master:{day}"))
                        if not verified:
                            self.event("scheduled_cycle_missed", key, {"scheduled_slot_utc": slot.isoformat(),
                                "reason": "unverified_daily_membership", "missing_underlyings": None})
                        elif (now-slot_local).total_seconds() >= 300 or now > close_at:
                            self.event("scheduled_cycle_missed", key, {"scheduled_slot_utc": slot.isoformat(),
                                "reason": "collector_not_running_or_late", "missing_underlyings": len(self.active_members(day))})
                        else:
                            self.quote_cycle(slot)
                if now >= close_at + timedelta(minutes=10):
                    key = f"session-finished:{day}"
                    if (any(e.event_type == "universe_reference_observed" for e in self.ledger.events(f"master:{day}"))
                        and not any(e.event_type == "session_collection_completed" for e in self.ledger.events(key))):
                        result = self.session_candles(day)
                        self.event("session_collection_completed", key, result)
                if now >= close_at + timedelta(hours=1, minutes=30):
                    key = f"retention:{day}"
                    if not any(e.event_type == "retention_run_completed" for e in self.ledger.events(key)):
                        result = self.retention(execute=True)
                        self.event("retention_run_completed", key, result)
            time.sleep(15)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lane", choices=["master", "backfill", "backfill-all", "backfill-follow", "history-on-demand", "quote", "session", "retention", "audit-initial", "audit-session-rollover", "serve"])
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--session-date", type=date.fromisoformat)
    parser.add_argument("--symbol")
    parser.add_argument("--interval", choices=["daily", "15m", "5m"])
    parser.add_argument("--from-date", type=date.fromisoformat)
    parser.add_argument("--to-date", type=date.fromisoformat)
    parser.add_argument("--reason")
    args = parser.parse_args()
    collector = Collector()
    if args.lane == "serve":
        collector.serve()
    elif args.lane == "master":
        result = collector.refresh_master()
        print(json.dumps({"members": len(result) if result else None}))
    elif args.lane == "backfill":
        print(json.dumps(collector.backfill_batch(args.offset)))
    elif args.lane == "backfill-all":
        collector.backfill_all(args.offset)
    elif args.lane == "backfill-follow":
        while not any(e.event_type == "backfill_batch_completed" for e in collector.ledger.events("backfill:0")):
            time.sleep(30)
        collector.backfill_all(10)
    elif args.lane == "history-on-demand":
        if not all((args.symbol, args.interval, args.from_date, args.to_date, args.reason)):
            raise ValueError("history-on-demand requires symbol, interval, from-date, to-date and reason")
        print(json.dumps(collector.history_on_demand(symbol=args.symbol, interval=args.interval,
            from_date=args.from_date, to_date=args.to_date, reason=args.reason)))
    elif args.lane == "session":
        if args.session_date is None:
            raise ValueError("session lane requires --session-date")
        print(json.dumps(collector.session_candles(args.session_date)))
    elif args.lane == "retention":
        print(json.dumps(collector.retention(execute=False)))
    elif args.lane == "audit-initial":
        print(json.dumps(collector.audit_initial_history()))
    elif args.lane == "audit-session-rollover":
        if args.session_date is None:
            raise ValueError("audit-session-rollover requires --session-date")
        print(json.dumps(collector.audit_session_rollover(args.session_date)))
    else:
        now = utc().astimezone(IST)
        minute = now.minute-now.minute%5
        slot = now.replace(minute=minute, second=0, microsecond=0).astimezone(timezone.utc)
        print(json.dumps(collector.quote_cycle(slot)))


if __name__ == "__main__":
    main()
