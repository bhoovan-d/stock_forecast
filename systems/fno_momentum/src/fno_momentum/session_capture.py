"""Resumable, read-only session capture for the clean-room FnO research track."""

from __future__ import annotations

import argparse
import gzip
import json
import time
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .governance import ChangeControl
from .ledger import ImmutableLedger, LedgerEvent, sha256_json
from .policy import MAX_OPTION_DTE, MIN_OPTION_DTE
from .source_registry import SourceRegistry
from .universe import UniverseBuilder
from .upstox import (
    CapturedPayload,
    ContentAddressedRawStore,
    UpstoxCredentials,
    UpstoxApiError,
    UpstoxReadOnlyClient,
    parse_completed_bars,
    parse_full_quotes,
    parse_option_chain,
    parse_option_contracts,
)


def _chunks(values: tuple[str, ...], size: int = 500) -> Iterable[tuple[str, ...]]:
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _option_reference(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if (
            row.get("segment") != "NSE_FO"
            or row.get("instrument_type") not in {"CE", "PE"}
            or row.get("underlying_type") != "EQUITY"
        ):
            continue
        instrument_key = str(row.get("instrument_key", ""))
        if not instrument_key:
            continue
        result[instrument_key] = {
            "underlying_key": str(row.get("underlying_key", "")),
            "underlying_symbol": str(row.get("underlying_symbol") or row.get("name") or ""),
            "instrument_type": str(row.get("instrument_type", "")),
            "expiry": str(row.get("expiry", "")),
            "strike_price": row.get("strike_price"),
            "lot_size": row.get("lot_size"),
            "tick_size": row.get("tick_size"),
        }
    return result


class SessionDataCapture:
    """Capture current-session evidence without activating any trading behavior."""

    def __init__(
        self,
        ledger: ImmutableLedger,
        client: UpstoxReadOnlyClient,
        changes: ChangeControl,
    ) -> None:
        self.ledger = ledger
        self.client = client
        self.changes = changes

    def run(
        self,
        *,
        session_date: date,
        interval_minutes: int = 15,
        symbols: tuple[str, ...] = (),
        maximum_underlyings: int | None = None,
        include_options: bool = True,
        minimum_request_interval_seconds: float = 0.25,
    ) -> dict[str, Any]:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be positive")
        if maximum_underlyings is not None and maximum_underlyings <= 0:
            raise ValueError("maximum_underlyings must be positive")
        prior_universe = self._latest_universe()
        master = self.client.instrument_master()
        if not isinstance(master.payload, list):
            raise ValueError("instrument master payload is not a list")
        policy_id, policy_hash = prior_universe.payload["policy_manifest"]
        universe_event_id = UniverseBuilder(self.ledger, self.changes).build_and_record(
            master.payload,
            observation=master.observation,
            policy_manifest_id=str(policy_id),
            policy_manifest_hash=str(policy_hash),
        )
        universe = next(
            event for event in self.ledger.events() if event.event_id == universe_event_id
        )
        self._record_reference_comparison(prior_universe, universe, master.payload)

        wanted = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
        members = [
            (str(item["underlying_key"]), str(item["symbol"]))
            for item in universe.payload["members"]
            if not wanted or str(item["symbol"]).upper() in wanted
        ]
        if wanted:
            found = {symbol.upper() for _, symbol in members}
            missing = sorted(wanted - found)
            if missing:
                raise ValueError("symbols are absent from the active FnO universe: " + ", ".join(missing))
        if maximum_underlyings is not None:
            members = members[:maximum_underlyings]
        if not members:
            raise ValueError("session capture has no selected underlyings")

        job_id = sha256_json(
            {
                "kind": "session_market_data_capture_v1",
                "session_date": session_date.isoformat(),
                "interval_minutes": interval_minutes,
                "symbols": sorted(symbol for _, symbol in members),
                "include_options": include_options,
            }
        )[:32]
        now = datetime.now(timezone.utc)
        self.ledger.append(
            "session_capture_started",
            job_id,
            {
                "session_date": session_date.isoformat(),
                "interval_minutes": interval_minutes,
                "member_count": len(members),
                "include_options": include_options,
                "universe_event_id": universe.event_id,
                "universe_hash": universe.payload["universe_hash"],
                "purpose": "data_foundation_only_no_alert_publication",
            },
            occurred_at=now,
            idempotency_key=f"session-capture-start:{job_id}:{universe.payload['universe_hash']}",
        )

        self._capture_underlying_quotes(job_id, members)
        already_complete = {
            str(event.payload["symbol"])
            for event in self.ledger.events(job_id)
            if event.event_type == "session_member_capture_completed"
        }
        completed = skipped = failed = 0
        for underlying_key, symbol in members:
            if symbol in already_complete:
                skipped += 1
                continue
            started = time.monotonic()
            try:
                self._capture_member(
                    job_id=job_id,
                    session_date=session_date,
                    interval_minutes=interval_minutes,
                    underlying_key=underlying_key,
                    symbol=symbol,
                    include_options=include_options,
                )
                completed += 1
            except Exception as exc:
                self._record_failure(
                    job_id,
                    stage="session_member_capture",
                    exc=exc,
                    underlying_key=underlying_key,
                    symbol=symbol,
                )
                failed += 1
            elapsed = time.monotonic() - started
            if elapsed < minimum_request_interval_seconds:
                time.sleep(minimum_request_interval_seconds - elapsed)

        summary = {
            "job_id": job_id,
            "session_date": session_date.isoformat(),
            "selected_members": len(members),
            "completed": completed,
            "skipped": skipped,
            "failed": failed,
        }
        completed_at = datetime.now(timezone.utc)
        self.ledger.append(
            "session_capture_run_completed",
            job_id,
            summary,
            occurred_at=completed_at,
            idempotency_key=f"session-capture-run:{job_id}:{completed_at.isoformat()}",
        )
        return summary

    def _capture_underlying_quotes(
        self, job_id: str, members: list[tuple[str, str]]
    ) -> None:
        keys = tuple(key for key, _ in members)
        for batch_number, batch in enumerate(_chunks(keys), start=1):
            try:
                capture = self.client.full_quotes(batch)
                rows = capture.payload.get("data", {})
                parsed = parse_full_quotes(capture)
                timestamps = [quote.observed_at for quote in parsed.values()]
                self.ledger.append(
                    "session_underlying_quotes_captured",
                    job_id,
                    {
                        "batch_number": batch_number,
                        "requested_instruments": len(batch),
                        "returned_instruments": len(rows) if isinstance(rows, dict) else 0,
                        "two_sided_bbo_instruments": len(parsed),
                        "provider_timestamp_min": min(timestamps).isoformat() if timestamps else None,
                        "provider_timestamp_max": max(timestamps).isoformat() if timestamps else None,
                        "observation_id": capture.observation.observation_id,
                        "content_sha256": capture.observation.content_sha256,
                    },
                    occurred_at=capture.observation.retrieved_at,
                    data_cutoff=max(timestamps) if timestamps else capture.observation.retrieved_at,
                    decision_at=capture.observation.retrieved_at,
                    idempotency_key=(
                        f"session-underlying-quotes:{job_id}:{batch_number}:"
                        f"{capture.observation.content_sha256}"
                    ),
                )
            except Exception as exc:
                self._record_failure(job_id, stage="underlying_full_quotes", exc=exc)
                if isinstance(exc, UpstoxApiError) and exc.status in {401, 403}:
                    at = datetime.now(timezone.utc)
                    self.ledger.append(
                        "session_capture_blocked",
                        job_id,
                        {
                            "stage": "underlying_full_quotes",
                            "reason": "market_data_authentication_failed",
                            "provider_status": exc.status,
                            "detail": exc.detail,
                        },
                        occurred_at=at,
                        idempotency_key=(
                            f"session-capture-blocked:{job_id}:market-data-auth:"
                            f"{sha256_json({'status': exc.status, 'detail': exc.detail})[:16]}"
                        ),
                    )
                    raise

    def _capture_member(
        self,
        *,
        job_id: str,
        session_date: date,
        interval_minutes: int,
        underlying_key: str,
        symbol: str,
        include_options: bool,
    ) -> None:
        candle_capture = self.client.intraday_candles(
            underlying_key, unit="minutes", interval=interval_minutes
        )
        bars = parse_completed_bars(
            candle_capture,
            instrument_id=underlying_key,
            interval_name=f"{interval_minutes}m",
            interval_duration=timedelta(minutes=interval_minutes),
        )
        same_session = tuple(bar for bar in bars if bar.interval_start.date() == session_date)
        self.ledger.append(
            "session_underlying_candles_captured",
            job_id,
            {
                "underlying_key": underlying_key,
                "symbol": symbol,
                "session_date": session_date.isoformat(),
                "interval_minutes": interval_minutes,
                "completed_candle_count": len(same_session),
                "latest_completed_bar_end": (
                    same_session[-1].interval_end.isoformat() if same_session else None
                ),
                "observation_id": candle_capture.observation.observation_id,
                "content_sha256": candle_capture.observation.content_sha256,
            },
            occurred_at=candle_capture.observation.retrieved_at,
            decision_at=candle_capture.observation.retrieved_at,
            data_cutoff=(
                same_session[-1].interval_end if same_session
                else candle_capture.observation.retrieved_at
            ),
            idempotency_key=(
                f"session-underlying-candles:{job_id}:{underlying_key}:"
                f"{candle_capture.observation.content_sha256}"
            ),
        )
        if not same_session:
            raise ValueError("no completed candles returned for the requested session")

        option_summary = {
            "contract_count": 0,
            "eligible_contract_count": 0,
            "chain_expiries": 0,
            "chain_two_sided_quotes": 0,
            "provider_timestamped_bbo": 0,
        }
        if include_options:
            option_summary = self._capture_options(
                job_id=job_id,
                session_date=session_date,
                underlying_key=underlying_key,
                symbol=symbol,
            )
        at = datetime.now(timezone.utc)
        self.ledger.append(
            "session_member_capture_completed",
            job_id,
            {
                "underlying_key": underlying_key,
                "symbol": symbol,
                "completed_candle_count": len(same_session),
                **option_summary,
            },
            occurred_at=at,
            idempotency_key=f"session-member-complete:{job_id}:{underlying_key}",
        )

    def _capture_options(
        self, *, job_id: str, session_date: date, underlying_key: str, symbol: str
    ) -> dict[str, int]:
        contract_capture = self.client.option_contracts(underlying_key)
        contracts = parse_option_contracts(contract_capture)
        eligible = {
            key: contract for key, contract in contracts.items()
            if MIN_OPTION_DTE <= (contract.expiry - session_date).days <= MAX_OPTION_DTE
        }
        expiries = sorted({contract.expiry for contract in eligible.values()})
        chain_quotes = 0
        for expiry in expiries:
            capture = self.client.option_chain(underlying_key, expiry)
            quoted = parse_option_chain(capture, contracts)
            chain_quotes += len(quoted)
            self.ledger.append(
                "session_option_chain_captured",
                job_id,
                {
                    "underlying_key": underlying_key,
                    "symbol": symbol,
                    "expiry": expiry.isoformat(),
                    "two_sided_quote_count": len(quoted),
                    "observation_id": capture.observation.observation_id,
                    "content_sha256": capture.observation.content_sha256,
                    "freshness_basis": "local_receipt_time_option_chain_has_no_quote_timestamp",
                },
                occurred_at=capture.observation.retrieved_at,
                decision_at=capture.observation.retrieved_at,
                data_cutoff=capture.observation.retrieved_at,
                idempotency_key=(
                    f"session-option-chain:{job_id}:{underlying_key}:{expiry}:"
                    f"{capture.observation.content_sha256}"
                ),
            )

        provider_quotes = 0
        for batch_number, batch in enumerate(_chunks(tuple(sorted(eligible))), start=1):
            capture = self.client.full_quotes(batch)
            quotes = parse_full_quotes(capture)
            provider_quotes += len(quotes)
            observed = [quote.observed_at for quote in quotes.values()]
            last_trades = [
                quote.last_trade_at for quote in quotes.values()
                if quote.last_trade_at is not None
            ]
            self.ledger.append(
                "session_option_bbo_captured",
                job_id,
                {
                    "underlying_key": underlying_key,
                    "symbol": symbol,
                    "batch_number": batch_number,
                    "requested_contracts": len(batch),
                    "provider_timestamped_two_sided_bbo": len(quotes),
                    "provider_timestamp_min": min(observed).isoformat() if observed else None,
                    "provider_timestamp_max": max(observed).isoformat() if observed else None,
                    "last_trade_timestamp_max": max(last_trades).isoformat() if last_trades else None,
                    "observation_id": capture.observation.observation_id,
                    "content_sha256": capture.observation.content_sha256,
                },
                occurred_at=capture.observation.retrieved_at,
                decision_at=capture.observation.retrieved_at,
                data_cutoff=max(observed) if observed else capture.observation.retrieved_at,
                idempotency_key=(
                    f"session-option-bbo:{job_id}:{underlying_key}:{batch_number}:"
                    f"{capture.observation.content_sha256}"
                ),
            )
        if not eligible:
            raise ValueError("no option contracts fall inside the binding DTE envelope")
        if provider_quotes == 0:
            raise ValueError("no provider-timestamped two-sided option BBO was captured")
        return {
            "contract_count": len(contracts),
            "eligible_contract_count": len(eligible),
            "chain_expiries": len(expiries),
            "chain_two_sided_quotes": chain_quotes,
            "provider_timestamped_bbo": provider_quotes,
        }

    def _latest_universe(self) -> LedgerEvent:
        events = [
            event for event in self.ledger.events()
            if event.event_type == "universe_snapshot_created"
        ]
        if not events:
            raise ValueError("an approved universe snapshot must exist before session capture")
        return events[-1]

    def _record_reference_comparison(
        self,
        prior_universe: LedgerEvent,
        current_universe: LedgerEvent,
        current_rows: list[dict[str, Any]],
    ) -> None:
        prior_rows = self._instrument_rows(str(prior_universe.payload["source_observation_id"]))
        before, after = _option_reference(prior_rows), _option_reference(current_rows)
        before_keys, after_keys = set(before), set(after)
        changed = sorted(key for key in before_keys & after_keys if before[key] != after[key])
        added, removed = sorted(after_keys - before_keys), sorted(before_keys - after_keys)
        prior_members = {item["underlying_key"] for item in prior_universe.payload["members"]}
        current_members = {item["underlying_key"] for item in current_universe.payload["members"]}
        payload = {
            "previous_universe_event_id": prior_universe.event_id,
            "current_universe_event_id": current_universe.event_id,
            "previous_contract_reference_hash": sha256_json(before),
            "current_contract_reference_hash": sha256_json(after),
            "added_contract_count": len(added),
            "removed_contract_count": len(removed),
            "changed_contract_count": len(changed),
            "added_underlyings": sorted(current_members - prior_members),
            "removed_underlyings": sorted(prior_members - current_members),
            "added_contract_sample": added[:100],
            "removed_contract_sample": removed[:100],
            "changed_contract_sample": changed[:100],
            "samples_truncated": any(len(items) > 100 for items in (added, removed, changed)),
            "interpretation": (
                "reference_change_requires_review_not_attributed_to_corporate_action"
                if added or removed or changed or prior_members != current_members
                else "reference_unchanged"
            ),
        }
        at = datetime.now(timezone.utc)
        self.ledger.append(
            "instrument_reference_compared",
            current_universe.aggregate_id,
            payload,
            occurred_at=at,
            decision_at=at,
            data_cutoff=at,
            idempotency_key=(
                f"instrument-reference-comparison:{prior_universe.event_id}:"
                f"{current_universe.event_id}"
            ),
        )

    def _instrument_rows(self, observation_id: str) -> list[dict[str, Any]]:
        observed = next(
            event for event in self.ledger.events(observation_id)
            if event.event_type == "source_observed"
        )
        content = Path(observed.payload["storage_ref"]).read_bytes()
        rows = json.loads(gzip.decompress(content))
        if not isinstance(rows, list):
            raise ValueError("instrument-master observation is not a list")
        return rows

    def _record_failure(
        self,
        job_id: str,
        *,
        stage: str,
        exc: Exception,
        underlying_key: str | None = None,
        symbol: str | None = None,
    ) -> None:
        detail = str(exc)[:300]
        payload = {
            "stage": stage,
            "error_type": type(exc).__name__,
            "detail": detail,
            "underlying_key": underlying_key,
            "symbol": symbol,
        }
        at = datetime.now(timezone.utc)
        self.ledger.append(
            "data_quality_failure",
            job_id,
            payload,
            occurred_at=at,
            idempotency_key=(
                f"session-capture-failure:{job_id}:{stage}:{underlying_key or 'batch'}:"
                f"{sha256_json(payload)[:16]}"
            ),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--raw-store", required=True)
    parser.add_argument("--session-date", required=True, type=date.fromisoformat)
    parser.add_argument("--interval-minutes", type=int, default=15)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--maximum-underlyings", type=int)
    parser.add_argument("--without-options", action="store_true")
    parser.add_argument("--minimum-request-interval-seconds", type=float, default=0.25)
    args = parser.parse_args()
    ledger = ImmutableLedger(args.ledger)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials.from_os_secret_store(),
        registry=SourceRegistry(ledger),
        raw_store=ContentAddressedRawStore(args.raw_store),
    )
    try:
        result = SessionDataCapture(ledger, client, ChangeControl(ledger)).run(
            session_date=args.session_date,
            interval_minutes=args.interval_minutes,
            symbols=tuple(part.strip() for part in args.symbols.split(",") if part.strip()),
            maximum_underlyings=args.maximum_underlyings,
            include_options=not args.without_options,
            minimum_request_interval_seconds=args.minimum_request_interval_seconds,
        )
    except UpstoxApiError as exc:
        print(json.dumps({
            "status": "blocked",
            "reason": "market_data_authentication_failed",
            "provider_status": exc.status,
            "detail": exc.detail,
        }, sort_keys=True))
        raise SystemExit(2) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
