"""Clean-room, read-only Upstox data capture and parsing.

This module deliberately exposes no order, funds, portfolio, or account mutation API.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from .contracts import MarketBar, QuoteSnapshot, RawObservation, require_aware
from .options import OptionContract, OptionRight, QuotedContract
from .secret_store import read_upstox_analytics_token
from .source_registry import SourceRegistry


SOURCE_ID = "upstox-read-only-market-data-v3"
SOURCE_HASH = "f47a860205a3a9ff922a4338bb1246a94ea56620f34167edf6c9311a5ba86aa1"
API_HOST = "api.upstox.com"
INSTRUMENT_HOST = "assets.upstox.com"
INSTRUMENTS_URL = (
    "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
)


@dataclass(frozen=True)
class UpstoxCredentials:
    access_token: str

    @classmethod
    def from_os_secret_store(cls, reader=None) -> "UpstoxCredentials":
        secret_reader = reader or read_upstox_analytics_token
        return cls(access_token=secret_reader())

    @classmethod
    def from_environment(cls) -> "UpstoxCredentials":
        raise RuntimeError(
            "environment-based credentials are prohibited; use Windows Credential Manager"
        )


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    retrieved_at: datetime


class UpstoxApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Upstox data endpoint returned HTTP {status}: {detail}")


class ReadOnlyTransport(Protocol):
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...


class UpstoxEndpointPolicy:
    """Fail-closed outbound policy for the approved market-data surface only."""

    _API_RULES = (
        ("/v2/option/chain", frozenset({"instrument_key", "expiry_date"})),
        ("/v2/option/contract", frozenset({"instrument_key", "expiry_date"})),
        ("/v3/historical-candle/", frozenset()),
        ("/v3/market-quote/quotes", frozenset({"instrument_key"})),
        ("/v3/feed/market-data-feed/authorize", frozenset()),
    )

    @classmethod
    def require_allowed(cls, url: str, *, method: str) -> None:
        if method != "GET":
            raise PermissionError("only GET is permitted by the Upstox outbound policy")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            raise PermissionError("only canonical HTTPS Upstox endpoints are permitted")
        if parsed.hostname == INSTRUMENT_HOST:
            if parsed.path != "/market-quote/instruments/exchange/NSE.json.gz":
                raise PermissionError("asset path is outside the Upstox outbound policy")
            if parsed.query or parsed.fragment:
                raise PermissionError("instrument-master URL cannot include query or fragment")
            return
        if parsed.hostname != API_HOST or parsed.fragment:
            raise PermissionError("host is outside the Upstox outbound policy")
        query_keys = frozenset(urllib.parse.parse_qs(parsed.query, keep_blank_values=True))
        for prefix, allowed_query in cls._API_RULES:
            if parsed.path == prefix or (
                prefix.endswith("/") and parsed.path.startswith(prefix)
            ):
                if not query_keys.issubset(allowed_query):
                    raise PermissionError("query is outside the Upstox outbound policy")
                return
        raise PermissionError("path is outside the Upstox outbound policy")


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PermissionError("HTTP redirects are denied by the Upstox outbound policy")


class UrllibReadOnlyTransport:
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        UpstoxEndpointPolicy.require_allowed(url, method="GET")
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=30) as response:
                return HttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    retrieved_at=datetime.now(timezone.utc),
                )
        except HTTPError as exc:
            return HttpResponse(
                status=int(exc.code),
                body=exc.read(),
                retrieved_at=datetime.now(timezone.utc),
            )


class ContentAddressedRawStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def put(self, content: bytes, *, suffix: str) -> tuple[str, str]:
        digest = hashlib.sha256(content).hexdigest()
        directory = self.root / SOURCE_ID / digest[:2]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{digest}.{suffix}"
        if path.exists():
            if path.read_bytes() != content:
                raise ValueError("content-addressed storage collision")
        else:
            path.write_bytes(content)
        return digest, str(path.resolve())


@dataclass(frozen=True)
class CapturedPayload:
    observation: RawObservation
    payload: Any


class UpstoxReadOnlyClient:
    def __init__(
        self,
        *,
        credentials: UpstoxCredentials,
        registry: SourceRegistry,
        raw_store: ContentAddressedRawStore,
        transport: ReadOnlyTransport | None = None,
        source_hash: str = SOURCE_HASH,
    ) -> None:
        self.credentials = credentials
        self.registry = registry
        self.raw_store = raw_store
        self.transport = transport or UrllibReadOnlyTransport()
        self.source_hash = source_hash

    def option_contracts(
        self, underlying_key: str, *, expiry: date | None = None
    ) -> CapturedPayload:
        query = {"instrument_key": underlying_key}
        if expiry is not None:
            query["expiry_date"] = expiry.isoformat()
        return self._capture_api("/v2/option/contract", query)

    def option_chain(self, underlying_key: str, expiry: date) -> CapturedPayload:
        return self._capture_api(
            "/v2/option/chain",
            {"instrument_key": underlying_key, "expiry_date": expiry.isoformat()},
        )

    def full_quotes(self, instrument_keys: tuple[str, ...]) -> CapturedPayload:
        if not instrument_keys or len(instrument_keys) > 500:
            raise ValueError("full quote requests require between 1 and 500 instruments")
        return self._capture_api(
            "/v3/market-quote/quotes", {"instrument_key": ",".join(instrument_keys)}
        )

    def historical_candles(
        self,
        instrument_key: str,
        *,
        unit: str,
        interval: int,
        from_date: date,
        to_date: date,
    ) -> CapturedPayload:
        if unit not in {"minutes", "hours", "days", "weeks", "months"}:
            raise ValueError("unsupported historical candle unit")
        if interval <= 0 or from_date > to_date:
            raise ValueError("invalid historical candle range")
        encoded_key = urllib.parse.quote(instrument_key, safe="")
        path = (
            f"/v3/historical-candle/{encoded_key}/{unit}/{interval}/"
            f"{to_date.isoformat()}/{from_date.isoformat()}"
        )
        return self._capture_api(path, {})

    def intraday_candles(
        self, instrument_key: str, *, unit: str = "minutes", interval: int = 15
    ) -> CapturedPayload:
        if unit not in {"minutes", "hours", "days"}:
            raise ValueError("unsupported intraday candle unit")
        if interval <= 0:
            raise ValueError("intraday candle interval must be positive")
        encoded_key = urllib.parse.quote(instrument_key, safe="")
        path = f"/v3/historical-candle/intraday/{encoded_key}/{unit}/{interval}"
        return self._capture_api(path, {})

    def instrument_master(self) -> CapturedPayload:
        self.registry.require_active(SOURCE_ID, self.source_hash)
        response = self.transport.get(
            INSTRUMENTS_URL,
            {"Accept": "application/gzip", "User-Agent": "fno-momentum-paper-research/0.1"},
        )
        if response.status != 200:
            raise RuntimeError(f"Upstox instrument master returned HTTP {response.status}")
        try:
            decoded = gzip.decompress(response.body)
            payload = json.loads(decoded)
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Upstox instrument-master response") from exc
        return self._record(
            response,
            payload,
            suffix="json.gz",
            raw_content=response.body,
            request_metadata={"method": "GET", "endpoint": INSTRUMENTS_URL, "query": {}},
        )

    def _capture_api(self, path: str, query: dict[str, str]) -> CapturedPayload:
        self.registry.require_active(SOURCE_ID, self.source_hash)
        url = urllib.parse.urlunparse(
            ("https", API_HOST, path, "", urllib.parse.urlencode(query), "")
        )
        UpstoxEndpointPolicy.require_allowed(url, method="GET")
        response = self.transport.get(
            url,
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.credentials.access_token}",
                "User-Agent": "fno-momentum-paper-research/0.1",
            },
        )
        if response.status != 200:
            raise UpstoxApiError(response.status, self._safe_error(response.body))
        try:
            payload = json.loads(response.body)
        except json.JSONDecodeError as exc:
            raise ValueError("invalid JSON from Upstox data endpoint") from exc
        if not isinstance(payload, dict) or payload.get("status") != "success":
            raise ValueError("Upstox data response did not report success")
        return self._record(
            response,
            payload,
            suffix="json",
            raw_content=response.body,
            request_metadata={"method": "GET", "endpoint": path, "query": query},
        )

    @staticmethod
    def _safe_error(body: bytes) -> str:
        try:
            value = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return "provider rejected the request"
        if not isinstance(value, dict):
            return "provider rejected the request"
        code = str(value.get("errors", [{}])[0].get("errorCode", "")) if isinstance(
            value.get("errors"), list
        ) and value.get("errors") else str(value.get("errorCode", ""))
        message = str(value.get("message") or value.get("errorMessage") or "request rejected")
        return " ".join(part for part in (code, message) if part).strip()

    def _record(
        self,
        response: HttpResponse,
        payload: Any,
        *,
        suffix: str,
        raw_content: bytes,
        request_metadata: dict[str, Any],
    ) -> CapturedPayload:
        require_aware(response.retrieved_at, "retrieved_at")
        digest, storage_ref = self.raw_store.put(raw_content, suffix=suffix)
        observation = RawObservation(
            observation_id=str(uuid.uuid4()),
            source_id=SOURCE_ID,
            retrieved_at=response.retrieved_at,
            content_sha256=digest,
            parser_version="upstox-clean-room-v1",
            license_tier="private-read-only",
            storage_ref=storage_ref,
        )
        self.registry.record_observation(observation, source_hash=self.source_hash)
        self.registry.ledger.append(
            "source_request_recorded",
            observation.observation_id,
            {
                "source_id": SOURCE_ID,
                "source_hash": self.source_hash,
                "request": request_metadata,
                "response_status": response.status,
                "content_sha256": digest,
            },
            occurred_at=response.retrieved_at,
            decision_at=response.retrieved_at,
            data_cutoff=response.retrieved_at,
            idempotency_key=f"source-request:{observation.observation_id}",
        )
        return CapturedPayload(observation, payload)


def fno_equity_underlyings(instrument_rows: list[dict]) -> tuple[tuple[str, str], ...]:
    """Return the current discovery pool; this does not activate a trading universe."""
    members = {
        (
            str(row["underlying_key"]),
            str(row.get("underlying_symbol") or row.get("name")),
        )
        for row in instrument_rows
        if row.get("segment") == "NSE_FO"
        and row.get("instrument_type") in {"CE", "PE"}
        and row.get("underlying_type") == "EQUITY"
    }
    return tuple(sorted(members))


def parse_historical_bars(
    capture: CapturedPayload,
    *,
    instrument_id: str,
    interval_name: str,
    interval_duration: timedelta,
) -> tuple[MarketBar, ...]:
    candles = capture.payload.get("data", {}).get("candles")
    if not isinstance(candles, list):
        raise ValueError("historical response is missing candles")
    bars = []
    for candle in reversed(candles):
        if not isinstance(candle, list) or len(candle) < 6:
            raise ValueError("malformed historical candle")
        start = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
        bars.append(
            MarketBar(
                instrument_id=instrument_id,
                interval=interval_name,
                interval_start=start,
                interval_end=start + interval_duration,
                # Historical downloads are not backdated as if observed in the past.
                available_at=capture.observation.retrieved_at,
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5]),
                observation_id=capture.observation.observation_id,
            )
        )
    return tuple(sorted(bars, key=lambda bar: bar.interval_start))


def parse_completed_bars(
    capture: CapturedPayload,
    *,
    instrument_id: str,
    interval_name: str,
    interval_duration: timedelta,
) -> tuple[MarketBar, ...]:
    """Parse only bars completed by local receipt time; never admit a live candle."""
    candles = capture.payload.get("data", {}).get("candles")
    if not isinstance(candles, list):
        raise ValueError("candle response is missing candles")
    bars = []
    for candle in reversed(candles):
        if not isinstance(candle, list) or len(candle) < 6:
            raise ValueError("malformed candle")
        start = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
        end = start + interval_duration
        if end > capture.observation.retrieved_at:
            continue
        bars.append(
            MarketBar(
                instrument_id=instrument_id,
                interval=interval_name,
                interval_start=start,
                interval_end=end,
                available_at=capture.observation.retrieved_at,
                open=float(candle[1]),
                high=float(candle[2]),
                low=float(candle[3]),
                close=float(candle[4]),
                volume=float(candle[5]),
                observation_id=capture.observation.observation_id,
            )
        )
    return tuple(sorted(bars, key=lambda bar: bar.interval_start))


def _provider_timestamp(value: Any) -> datetime:
    if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def parse_full_quotes(capture: CapturedPayload) -> dict[str, QuoteSnapshot]:
    """Normalize provider-timestamped top-of-book quotes from Full Quotes V3."""
    rows = capture.payload.get("data")
    if not isinstance(rows, dict):
        raise ValueError("full-quote response is missing data")
    result: dict[str, QuoteSnapshot] = {}
    for row in rows.values():
        if not isinstance(row, dict):
            raise ValueError("malformed full-quote row")
        contract_id = str(row.get("instrument_token", ""))
        if not contract_id:
            raise ValueError("full-quote row is missing instrument_token")
        depth = row.get("depth") or {}
        buys = depth.get("buy") or []
        sells = depth.get("sell") or []
        best_bid = next(
            (item for item in buys if float(item.get("price", 0)) > 0), None
        )
        best_ask = next(
            (item for item in sells if float(item.get("price", 0)) > 0), None
        )
        if best_bid is None or best_ask is None:
            continue
        observed_at = _provider_timestamp(row["timestamp"])
        last_trade = row.get("last_trade_time")
        quote = QuoteSnapshot(
            contract_id=contract_id,
            observed_at=observed_at,
            available_at=capture.observation.retrieved_at,
            bid=float(best_bid["price"]),
            ask=float(best_ask["price"]),
            bid_quantity=int(best_bid.get("quantity", 0)),
            ask_quantity=int(best_ask.get("quantity", 0)),
            open_interest=int(row.get("oi", 0)),
            volume=int(row.get("volume", 0)),
            observation_id=capture.observation.observation_id,
            last_trade_at=_provider_timestamp(last_trade) if last_trade else None,
        )
        result[contract_id] = quote
    return result


def parse_option_contracts(capture: CapturedPayload) -> dict[str, OptionContract]:
    rows = capture.payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("option-contract response is missing data")
    contracts: dict[str, OptionContract] = {}
    for row in rows:
        instrument_type = str(row.get("instrument_type", ""))
        if instrument_type not in {"CE", "PE", "OPTSTK"}:
            continue
        if row.get("underlying_type") not in {None, "EQUITY"}:
            continue
        right_value = str(row.get("option_type", "")).upper()
        if not right_value and instrument_type in {"CE", "PE"}:
            right_value = instrument_type
        right = OptionRight.CALL if right_value in {"CE", "CALL"} else OptionRight.PUT
        contract_id = str(row["instrument_key"])
        contracts[contract_id] = OptionContract(
            contract_id=contract_id,
            underlying_symbol=str(
                row.get("underlying_symbol") or row.get("underlying_key") or row.get("name")
            ),
            right=right,
            strike=float(row["strike_price"]),
            expiry=date.fromisoformat(str(row["expiry"])),
            lot_size=int(row["lot_size"]),
            tick_size=float(row["tick_size"]),
            available_at=capture.observation.retrieved_at,
            observation_id=capture.observation.observation_id,
        )
    return contracts


def parse_option_chain(
    capture: CapturedPayload, contracts: dict[str, OptionContract]
) -> tuple[QuotedContract, ...]:
    rows = capture.payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("option-chain response is missing data")
    result = []
    for row in rows:
        for key in ("call_options", "put_options"):
            option = row.get(key)
            if not isinstance(option, dict):
                continue
            contract_id = str(option.get("instrument_key", ""))
            contract = contracts.get(contract_id)
            if contract is None:
                raise ValueError("option chain contains a contract absent from approved metadata")
            market = option.get("market_data") or {}
            bid, ask = float(market.get("bid_price", 0)), float(market.get("ask_price", 0))
            if bid <= 0 or ask <= 0 or ask < bid:
                continue
            quote = QuoteSnapshot(
                contract_id=contract_id,
                observed_at=capture.observation.retrieved_at,
                available_at=capture.observation.retrieved_at,
                bid=bid,
                ask=ask,
                bid_quantity=int(market.get("bid_qty", 0)),
                ask_quantity=int(market.get("ask_qty", 0)),
                open_interest=int(market.get("oi", 0)),
                volume=int(market.get("volume", 0)),
                observation_id=capture.observation.observation_id,
            )
            result.append(QuotedContract(contract, quote))
    return tuple(result)
