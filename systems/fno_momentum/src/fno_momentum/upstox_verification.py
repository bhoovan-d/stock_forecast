"""One-shot Upstox market-data connectivity and schema verification.

This command is intentionally not a collector. It examines one NSE equity underlying and
one current call option, writes a sanitized Markdown report, and exits. Raw provider
payloads and the credential are not persisted.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
import urllib.parse

from .secret_store import UPSTOX_CREDENTIAL_TARGET
from .upstox import (
    API_HOST,
    INSTRUMENTS_URL,
    HttpResponse,
    UpstoxCredentials,
    UpstoxEndpointPolicy,
    UrllibReadOnlyTransport,
)


UNDERLYING_KEY = "NSE_EQ|INE002A01018"
UNDERLYING_SYMBOL = "RELIANCE"
REPORT_PATH = (
    Path(__file__).resolve().parents[4]
    / "reports"
    / "2026-09-19-upstox-tiny-verification-report.md"
)


@dataclass(frozen=True)
class Contact:
    method: str
    host: str
    path: str
    query_keys: tuple[str, ...]
    status: int
    received_at: datetime


class AuditedTransport:
    def __init__(self) -> None:
        self._transport = UrllibReadOnlyTransport()
        self.contacts: list[Contact] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        UpstoxEndpointPolicy.require_allowed(url, method="GET")
        response = self._transport.get(url, headers)
        parsed = urllib.parse.urlsplit(url)
        self.contacts.append(
            Contact(
                method="GET",
                host=parsed.hostname or "",
                path=parsed.path,
                query_keys=tuple(sorted(urllib.parse.parse_qs(parsed.query))),
                status=response.status,
                received_at=response.retrieved_at,
            )
        )
        return response


def _json_response(response: HttpResponse) -> dict:
    if response.status != 200:
        raise RuntimeError(f"allowlisted Upstox endpoint returned HTTP {response.status}")
    value = json.loads(response.body)
    if not isinstance(value, dict) or value.get("status") != "success":
        raise RuntimeError("Upstox response did not report success")
    return value


def _first_quote(payload: dict) -> dict:
    rows = payload.get("data")
    if not isinstance(rows, dict) or len(rows) != 1:
        raise RuntimeError("expected exactly one quote row")
    row = next(iter(rows.values()))
    if not isinstance(row, dict):
        raise RuntimeError("quote row is malformed")
    return row


def _expiry_date(value) -> date:
    text = str(value)
    if text.isdigit():
        return datetime.fromtimestamp(float(text) / 1000, timezone.utc).date()
    return date.fromisoformat(text)


def _select_one_option(rows: list[dict], underlying_price: float) -> dict:
    today = date.today()
    candidates = [
        row
        for row in rows
        if row.get("segment") == "NSE_FO"
        and row.get("instrument_type") == "CE"
        and row.get("underlying_type") == "EQUITY"
        and row.get("underlying_key") == UNDERLYING_KEY
        and row.get("expiry")
        and _expiry_date(row["expiry"]) >= today
    ]
    if not candidates:
        raise RuntimeError("no current RELIANCE call option found in instrument master")
    nearest_expiry = min(_expiry_date(row["expiry"]) for row in candidates)
    expiry_rows = [
        row for row in candidates if _expiry_date(row["expiry"]) == nearest_expiry
    ]
    return min(
        expiry_rows,
        key=lambda row: (abs(float(row["strike_price"]) - underlying_price), str(row["instrument_key"])),
    )


def _depth(row: dict, side: str) -> dict | None:
    values = (row.get("depth") or {}).get(side) or []
    for value in values:
        if isinstance(value, dict) and float(value.get("price", 0) or 0) > 0:
            return value
    return None


def _schema(value, prefix: str = "") -> list[str]:
    result: list[str] = []
    if isinstance(value, dict):
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            result.append(path)
            if isinstance(value[key], (dict, list)):
                result.extend(_schema(value[key], path))
    elif isinstance(value, list) and value:
        result.extend(_schema(value[0], f"{prefix}[]"))
    return result


def _provider_time(value) -> str:
    if value is None or value == "":
        return "missing"
    if isinstance(value, (int, float)) or str(value).isdigit():
        return datetime.fromtimestamp(float(value) / 1000, timezone.utc).isoformat()
    return str(value)


def run(report_path: Path = REPORT_PATH) -> Path:
    credentials = UpstoxCredentials.from_os_secret_store()
    transport = AuditedTransport()
    common = {"Accept": "application/json", "User-Agent": "fno-momentum-verification/1"}
    auth = {**common, "Authorization": f"Bearer {credentials.access_token}"}

    master_response = transport.get(
        INSTRUMENTS_URL,
        {"Accept": "application/gzip", "User-Agent": "fno-momentum-verification/1"},
    )
    if master_response.status != 200:
        raise RuntimeError(f"instrument master returned HTTP {master_response.status}")
    master = json.loads(gzip.decompress(master_response.body))
    if not isinstance(master, list):
        raise RuntimeError("instrument master is malformed")

    underlying_url = "https://api.upstox.com/v3/market-quote/quotes?" + urllib.parse.urlencode(
        {"instrument_key": UNDERLYING_KEY}
    )
    underlying_response = transport.get(underlying_url, auth)
    underlying_payload = _json_response(underlying_response)
    underlying_row = _first_quote(underlying_payload)
    underlying_price = float(underlying_row.get("last_price", 0) or 0)
    if underlying_price <= 0:
        raise RuntimeError("underlying quote has no positive last price")

    option = _select_one_option(master, underlying_price)
    option_key = str(option["instrument_key"])
    option_url = "https://api.upstox.com/v3/market-quote/quotes?" + urllib.parse.urlencode(
        {"instrument_key": option_key}
    )
    option_response = transport.get(option_url, auth)
    option_payload = _json_response(option_response)
    option_row = _first_quote(option_payload)
    bid = _depth(option_row, "buy")
    ask = _depth(option_row, "sell")

    failures = []
    if bid is None:
        failures.append("No positive best bid was present.")
    if ask is None:
        failures.append("No positive best ask was present.")
    if bid and ask and float(ask["price"]) < float(bid["price"]):
        failures.append("Best ask was below best bid.")
    for field in ("timestamp", "last_trade_time", "oi", "volume"):
        if option_row.get(field) is None:
            failures.append(f"Option quote field `{field}` was missing.")
    if bid and int(bid.get("quantity", 0) or 0) <= 0:
        failures.append("Best-bid quantity was not positive.")
    if ask and int(ask.get("quantity", 0) or 0) <= 0:
        failures.append("Best-ask quantity was not positive.")
    if int(option_row.get("oi", 0) or 0) < 0:
        failures.append("Open interest was negative.")
    if int(option_row.get("volume", 0) or 0) < 0:
        failures.append("Volume was negative.")
    last_trade_value = option_row.get("last_trade_time")
    if last_trade_value is not None:
        if isinstance(last_trade_value, (int, float)) or str(last_trade_value).isdigit():
            last_trade_at = datetime.fromtimestamp(
                float(last_trade_value) / 1000, timezone.utc
            )
            age = option_response.retrieved_at - last_trade_at
            if age.total_seconds() > 15 * 60:
                failures.append(
                    "Last trade was more than 15 minutes old; this out-of-hours response "
                    "does not establish a fresh live BBO."
                )

    contact_lines = "\n".join(
        f"| {item.method} | `{item.host}` | `{item.path}` | "
        f"{', '.join(item.query_keys) or 'none'} | {item.status} | {item.received_at.isoformat()} |"
        for item in transport.contacts
    )
    schema_lines = "\n".join(f"- `{name}`" for name in _schema(option_payload))
    failure_lines = (
        "\n".join(f"- {failure}" for failure in failures)
        if failures
        else "- None in this single observation."
    )
    report = f"""# Upstox Tiny Connectivity and Data-Quality Verification

**Run time:** {datetime.now(timezone.utc).isoformat()}  
**Scope:** One underlying ({UNDERLYING_SYMBOL}) and one option contract only  
**Result:** {'Completed with data-quality failures' if failures else 'Completed'}  
**Routine collection:** Not started; remains blocked

## Boundary verification

- Every network request passed the in-process HTTPS host/method/path/query allowlist.
- Only `GET` requests were permitted. Redirects were denied.
- No static IP was configured.
- No account, portfolio, holdings, funds, order, trade-history, P&L or mutation route was contacted.
- The token was read in-process from Windows Credential Manager target
  `{UPSTOX_CREDENTIAL_TARGET}`. Its value was not written to this report, source, an
  environment variable, `.env`, log, payload or repository artifact.
- Provider response bodies were held in memory only. This report retains selected observed
  values and schema names, not raw payloads.

| Method | Host | Path | Query keys only | HTTP | Local receipt time (UTC) |
|---|---|---|---|---:|---|
{contact_lines}

## Verified instruments

| Role | Instrument | Expiry | Strike | Type |
|---|---|---|---:|---|
| Underlying | `{UNDERLYING_KEY}` ({UNDERLYING_SYMBOL}) | n/a | n/a | NSE equity |
| Option | `{option_key}` | {_expiry_date(option['expiry']).isoformat()} | {option['strike_price']} | CE |

## Observed option market fields

| Field | Observed value |
|---|---:|
| Provider general response timestamp (`timestamp`) | {_provider_time(option_row.get('timestamp'))} |
| Provider last-trade timestamp | {_provider_time(option_row.get('last_trade_time'))} |
| Local receipt timestamp | {option_response.retrieved_at.isoformat()} |
| Best bid | {bid.get('price') if bid else 'missing'} |
| Best-bid quantity | {bid.get('quantity') if bid else 'missing'} |
| Best ask | {ask.get('price') if ask else 'missing'} |
| Best-ask quantity | {ask.get('quantity') if ask else 'missing'} |
| Open interest | {option_row.get('oi', 'missing')} |
| Volume | {option_row.get('volume', 'missing')} |

## Observed response schema

{schema_lines}

## Data-quality failures

{failure_lines}

## What this proves

This one-shot run proves only that the OS-held Analytics Token authenticated against the
listed allowlisted market-data route at the recorded time, the instrument master could be
read, one underlying and one option quote could be returned, and the displayed fields had
the values shown. It also proves which routes this process attempted.

## What remains unproven

It does not prove long-run completeness, uptime, latency, quote freshness during market
hours, exchange-time synchronization of BBO changes (no distinct BBO-change timestamp was
observed), correction behavior, historical
option BBO, retention rights, corporate-action treatment, executable fills, liquidity,
slippage, costs, stops, targets, holding periods, P&L, any hypothesis, or any trading edge.

## Exact separate approval required before routine collection

> Aditya Lakhotia approves the exact-hash Upstox routine-collection manifest, including
> its instrument universe, REST/WebSocket endpoint allowlist, fields, cadence, operating
> window, local storage layout, raw and derived retention period, deletion obligations,
> data-quality checks, failure behavior and security controls. This approval authorizes
> read-only routine data collection and quality monitoring only. It does not authorize a
> research hypothesis, candidates, historical option P&L, alerts, AI, Telegram, execution,
> trading, or any connection to NIFTY500 Source News.
"""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8")
    return report_path.resolve()


if __name__ == "__main__":
    print(run())
