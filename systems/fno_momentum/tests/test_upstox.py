import gzip
import json
from datetime import datetime, timedelta, timezone

import pytest

from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID
from fno_momentum.secret_store import _decode_secret, _encode_secret
from fno_momentum.source_registry import SourceManifest, SourceRegistry
from fno_momentum.upstox import (
    ContentAddressedRawStore,
    HttpResponse,
    SOURCE_HASH,
    CapturedPayload,
    UpstoxCredentials,
    UpstoxEndpointPolicy,
    UpstoxReadOnlyClient,
    parse_completed_bars,
    parse_full_quotes,
    parse_option_chain,
    parse_option_contracts,
    fno_equity_underlyings,
)
from fno_momentum.upstox_verification import _expiry_date
from fno_momentum.contracts import RawObservation


NOW = datetime(2026, 9, 16, 4, tzinfo=timezone.utc)


class FakeTransport:
    def __init__(self, payload, *, compressed=False):
        content = json.dumps(payload).encode()
        self.body = gzip.compress(content) if compressed else content
        self.urls = []

    def get(self, url, headers):
        self.urls.append(url)
        assert headers.get("Authorization") != "Bearer " or "assets.upstox.com" in url
        return HttpResponse(200, self.body, NOW)


def _registry(tmp_path, *, activate):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    registry = SourceRegistry(ledger)
    manifest = SourceManifest(
        source_id="upstox-read-only-market-data-v3",
        name="Upstox Read-Only Market Data APIs v3",
        source_type="authenticated_free_broker_market_data",
        lawful_access_basis="test authority",
        terms_reference="https://upstox.test/terms",
        fields=("bbo",),
        latency="test",
        monthly_cost_inr=0,
        collection_method="read only fixture",
        retention_policy="test lifetime",
        version="test",
    )
    # Use the production hash in the event fixture to exercise the connector gate.
    ledger.append(
        "source_registry_proposed",
        manifest.source_id,
        {"content_hash": SOURCE_HASH},
        occurred_at=NOW,
        idempotency_key="source-proposal",
    )
    if activate:
        ledger.append(
            "source_registry_approved",
            manifest.source_id,
            {"content_hash": SOURCE_HASH, "approval_actor_id": APPROVER_ID},
            occurred_at=NOW,
            idempotency_key="source-approval",
        )
        ledger.append(
            "source_registry_activated",
            manifest.source_id,
            {"content_hash": SOURCE_HASH, "activation_actor_id": APPROVER_ID},
            occurred_at=NOW,
            idempotency_key="source-activation",
        )
    return ledger, registry


def test_read_only_client_fails_closed_before_source_activation(tmp_path):
    _, registry = _registry(tmp_path, activate=False)
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials("secret-test-token"),
        registry=registry,
        raw_store=ContentAddressedRawStore(tmp_path / "raw"),
        transport=FakeTransport({"status": "success", "data": []}),
    )
    with pytest.raises(PermissionError, match="inactive source"):
        client.option_contracts("NSE_EQ|TEST")


def test_contract_and_chain_capture_are_raw_hashed_and_parsed(tmp_path):
    ledger, registry = _registry(tmp_path, activate=True)
    contract_payload = {
        "status": "success",
        "data": [{
            "instrument_type": "CE",
            "underlying_type": "EQUITY",
            "instrument_key": "NSE_FO|123",
            "underlying_key": "NSE_EQ|TEST",
            "strike_price": 100,
            "expiry": "2026-09-30",
            "lot_size": 50,
            "tick_size": 0.05,
        }],
    }
    client = UpstoxReadOnlyClient(
        credentials=UpstoxCredentials("secret-test-token"),
        registry=registry,
        raw_store=ContentAddressedRawStore(tmp_path / "raw"),
        transport=FakeTransport(contract_payload),
    )
    capture = client.option_contracts("NSE_EQ|TEST")
    contracts = parse_option_contracts(capture)
    assert contracts["NSE_FO|123"].lot_size == 50
    assert capture.observation.content_sha256
    assert any(event.event_type == "source_observed" for event in ledger.events())

    chain_payload = {
        "status": "success",
        "data": [{
            "call_options": {
                "instrument_key": "NSE_FO|123",
                "market_data": {
                    "bid_price": 10,
                    "ask_price": 10.2,
                    "bid_qty": 50,
                    "ask_qty": 100,
                    "oi": 1000,
                    "volume": 500,
                },
            },
            "put_options": None,
        }],
    }
    client.transport = FakeTransport(chain_payload)
    chain = client.option_chain("NSE_EQ|TEST", contracts["NSE_FO|123"].expiry)
    quoted = parse_option_chain(chain, contracts)
    assert quoted[0].quote.bid == 10
    assert quoted[0].quote.ask == 10.2
    assert "option/chain" in client.transport.urls[0]


def test_discovery_underlyings_exclude_index_and_deduplicate():
    rows = [
        {"segment": "NSE_FO", "instrument_type": "CE", "underlying_type": "EQUITY",
         "underlying_key": "NSE_EQ|ABC", "underlying_symbol": "ABC"},
        {"segment": "NSE_FO", "instrument_type": "PE", "underlying_type": "EQUITY",
         "underlying_key": "NSE_EQ|ABC", "underlying_symbol": "ABC"},
        {"segment": "NSE_FO", "instrument_type": "CE", "underlying_type": "INDEX",
         "underlying_key": "NSE_INDEX|Nifty 50", "underlying_symbol": "NIFTY"},
    ]
    assert fno_equity_underlyings(rows) == (("NSE_EQ|ABC", "ABC"),)


def test_credentials_are_os_secret_store_only(monkeypatch):
    monkeypatch.setenv("UPSTOX_ANALYTICS_TOKEN", "must-not-be-used")
    with pytest.raises(RuntimeError, match="environment-based credentials are prohibited"):
        UpstoxCredentials.from_environment()
    credentials = UpstoxCredentials.from_os_secret_store(
        reader=lambda: "read-only-configured"
    )
    assert credentials.access_token == "read-only-configured"


def test_secret_store_uses_compact_utf8_for_long_analytics_token():
    token = "a" * 1800
    encoded = _encode_secret(token)
    assert len(encoded) < 2560
    assert _decode_secret(encoded) == token


def test_secret_store_rejects_true_credential_manager_overflow():
    with pytest.raises(ValueError, match="2560-byte limit"):
        _encode_secret("a" * 2560)


def test_verifier_accepts_iso_and_epoch_millisecond_expiries():
    assert _expiry_date("2026-10-27").isoformat() == "2026-10-27"
    assert _expiry_date("1793125799000").isoformat() == "2026-10-27"


@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("https://api.upstox.com/v2/order/place", "GET"),
        ("https://api.upstox.com/v2/user/profile", "GET"),
        ("https://api.upstox.com/v3/market-quote/quotes?instrument_key=x&order_id=y", "GET"),
        ("https://evil.example/v3/market-quote/quotes?instrument_key=x", "GET"),
        ("http://api.upstox.com/v3/market-quote/quotes?instrument_key=x", "GET"),
        ("https://api.upstox.com/v3/market-quote/quotes?instrument_key=x", "POST"),
    ],
)
def test_endpoint_policy_denies_non_market_data_routes(url, method):
    with pytest.raises(PermissionError):
        UpstoxEndpointPolicy.require_allowed(url, method=method)


@pytest.mark.parametrize(
    "url",
    [
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz",
        "https://api.upstox.com/v3/market-quote/quotes?instrument_key=NSE_EQ%7CTEST",
        "https://api.upstox.com/v2/option/contract?instrument_key=NSE_EQ%7CTEST",
        "https://api.upstox.com/v2/option/chain?instrument_key=NSE_EQ%7CTEST&expiry_date=2026-09-24",
        "https://api.upstox.com/v3/historical-candle/NSE_EQ%257CTEST/minutes/1/2026-09-17/2026-09-17",
        "https://api.upstox.com/v3/feed/market-data-feed/authorize",
    ],
)
def test_endpoint_policy_allows_only_approved_market_data_routes(url):
    UpstoxEndpointPolicy.require_allowed(url, method="GET")


def test_prior_delegated_source_events_are_quarantined(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    registry = SourceRegistry(ledger)
    source_id = "upstox-read-only-market-data-v3"
    ledger.append(
        "source_registry_proposed", source_id, {"content_hash": SOURCE_HASH},
        occurred_at=NOW, idempotency_key="legacy-source-proposal",
    )
    ledger.append(
        "source_registry_approved", source_id,
        {"content_hash": SOURCE_HASH, "approval_actor_id": "benefactor-authority"},
        occurred_at=NOW, idempotency_key="legacy-source-approval",
    )
    ledger.append(
        "source_registry_activated", source_id,
        {"content_hash": SOURCE_HASH, "activation_actor_id": "benefactor-authority"},
        occurred_at=NOW, idempotency_key="legacy-source-activation",
    )
    assert not registry.is_active(source_id, SOURCE_HASH)
    with pytest.raises(PermissionError, match="inactive source"):
        registry.require_active(source_id, SOURCE_HASH)


def _capture(payload, *, retrieved_at=NOW):
    return CapturedPayload(
        RawObservation(
            observation_id="obs-1",
            source_id="upstox-read-only-market-data-v3",
            retrieved_at=retrieved_at,
            content_sha256="a" * 64,
            parser_version="test",
            license_tier="test",
            storage_ref="test.json",
        ),
        payload,
    )


def test_completed_bar_parser_drops_live_candle():
    capture = _capture(
        {
            "data": {
                "candles": [
                    ["2026-09-16T09:45:00+00:00", 101, 102, 100, 101, 10],
                    ["2026-09-16T09:30:00+00:00", 100, 102, 99, 101, 20],
                ]
            }
        },
        retrieved_at=datetime(2026, 9, 16, 9, 50, tzinfo=timezone.utc),
    )
    bars = parse_completed_bars(
        capture,
        instrument_id="NSE_EQ|TEST",
        interval_name="15m",
        interval_duration=timedelta(minutes=15),
    )
    assert [bar.interval_start.minute for bar in bars] == [30]


def test_full_quote_parser_uses_provider_time_and_top_of_book():
    capture = _capture(
        {
            "status": "success",
            "data": {
                "NSE_FO:TEST": {
                    "instrument_token": "NSE_FO|123",
                    "timestamp": "2026-09-16T09:59:59+00:00",
                    "last_trade_time": "1789552798000",
                    "depth": {
                        "buy": [
                            {"price": 10.0, "quantity": 50},
                            {"price": 9.9, "quantity": 100},
                        ],
                        "sell": [{"price": 10.2, "quantity": 75}],
                    },
                    "oi": 1000,
                    "volume": 500,
                }
            },
        },
        retrieved_at=datetime(2026, 9, 16, 10, tzinfo=timezone.utc),
    )
    quote = parse_full_quotes(capture)["NSE_FO|123"]
    assert quote.bid == 10.0
    assert quote.ask == 10.2
    assert quote.observed_at == datetime(2026, 9, 16, 9, 59, 59, tzinfo=timezone.utc)
    assert quote.last_trade_at is not None
