import gzip
import hashlib
import json
from datetime import date, datetime, timezone

from fno_momentum.contracts import RawObservation
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID
from fno_momentum.session_capture import SessionDataCapture
from fno_momentum.universe import UniverseBuilder
from fno_momentum.upstox import CapturedPayload


NOW = datetime(2026, 9, 16, 10, tzinfo=timezone.utc)
EXPIRY = date(2026, 9, 30)


def _observation(name, digest="a" * 64):
    return RawObservation(
        observation_id=name,
        source_id="upstox-read-only-market-data-v3",
        retrieved_at=NOW,
        content_sha256=digest,
        parser_version="fixture",
        license_tier="fixture",
        storage_ref=f"fixture://{name}",
    )


def _rows():
    common = {
        "segment": "NSE_FO",
        "underlying_type": "EQUITY",
        "underlying_key": "NSE_EQ|ABC",
        "underlying_symbol": "ABC",
        "expiry": EXPIRY.isoformat(),
        "lot_size": 50,
        "tick_size": 0.05,
    }
    return [
        {**common, "instrument_type": "CE", "instrument_key": "NSE_FO|CE", "strike_price": 100},
        {**common, "instrument_type": "PE", "instrument_key": "NSE_FO|PE", "strike_price": 100},
    ]


class FakeClient:
    def instrument_master(self):
        return CapturedPayload(_observation("master-current"), _rows())

    def intraday_candles(self, instrument_key, *, unit, interval):
        return CapturedPayload(
            _observation("candles"),
            {
                "status": "success",
                "data": {
                    "candles": [
                        ["2026-09-16T15:15:00+05:30", 100, 102, 99, 101, 1000],
                        ["2026-09-16T15:00:00+05:30", 99, 101, 98, 100, 900],
                    ]
                },
            },
        )

    def option_contracts(self, underlying_key):
        return CapturedPayload(
            _observation("contracts"), {"status": "success", "data": _rows()}
        )

    def option_chain(self, underlying_key, expiry):
        def option(key):
            return {
                "instrument_key": key,
                "market_data": {
                    "bid_price": 10,
                    "ask_price": 10.2,
                    "bid_qty": 50,
                    "ask_qty": 50,
                    "oi": 1000,
                    "volume": 500,
                },
            }
        return CapturedPayload(
            _observation("chain"),
            {"status": "success", "data": [{"call_options": option("NSE_FO|CE"), "put_options": option("NSE_FO|PE")}]},
        )

    def full_quotes(self, instrument_keys):
        data = {}
        for number, key in enumerate(instrument_keys):
            data[f"ROW:{number}"] = {
                "instrument_token": key,
                "timestamp": "2026-09-16T15:29:59+05:30",
                "last_trade_time": "1789552798000",
                "depth": {
                    "buy": [{"price": 10, "quantity": 50}],
                    "sell": [{"price": 10.2, "quantity": 50}],
                },
                "oi": 1000,
                "volume": 500,
            }
        return CapturedPayload(_observation("quotes-" + str(len(instrument_keys))), {"status": "success", "data": data})


def test_session_capture_records_completed_bars_reference_diff_and_bbo(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    changes = ChangeControl(ledger)
    proposal = ChangeProposal(
        manifest_id="fno-universe-v1",
        kind=ManifestKind.UNIVERSE_POLICY,
        version="1",
        definition=UniverseBuilder.REQUIRED_POLICY,
        evidence="fixture",
        expected_benefit="fixture",
        risks="fixture",
        test_plan="fixture",
    )
    value = changes.propose(proposal, actor_id="engineer", at=NOW)
    changes.approve(
        proposal.manifest_id, value, actor_id=APPROVER_ID,
        rationale="fixture", at=NOW,
    )
    changes.activate(proposal.manifest_id, value, actor_id=APPROVER_ID, at=NOW)

    raw = gzip.compress(json.dumps(_rows()).encode())
    raw_path = tmp_path / "master.json.gz"
    raw_path.write_bytes(raw)
    digest = hashlib.sha256(raw).hexdigest()
    prior_observation = _observation("master-prior", digest)
    ledger.append(
        "source_observed",
        prior_observation.observation_id,
        {"storage_ref": str(raw_path), "content_sha256": digest},
        occurred_at=NOW,
        idempotency_key="prior-master-observed",
    )
    UniverseBuilder(ledger, changes).build_and_record(
        _rows(), observation=prior_observation,
        policy_manifest_id=proposal.manifest_id, policy_manifest_hash=value,
    )

    result = SessionDataCapture(ledger, FakeClient(), changes).run(
        session_date=date(2026, 9, 16), minimum_request_interval_seconds=0,
    )
    assert result["completed"] == 1
    assert result["failed"] == 0
    event_types = [event.event_type for event in ledger.events()]
    assert "instrument_reference_compared" in event_types
    assert "session_underlying_candles_captured" in event_types
    assert "session_option_chain_captured" in event_types
    assert "session_option_bbo_captured" in event_types
    member = next(
        event for event in ledger.events()
        if event.event_type == "session_member_capture_completed"
    )
    assert member.payload["completed_candle_count"] == 2
    assert member.payload["provider_timestamped_bbo"] == 2
