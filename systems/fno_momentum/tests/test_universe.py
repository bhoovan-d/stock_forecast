from datetime import datetime, timezone

import pytest

from fno_momentum.contracts import RawObservation
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID
from fno_momentum.universe import UniverseBuilder


NOW = datetime(2026, 9, 16, 4, tzinfo=timezone.utc)


def test_universe_is_stock_only_dated_and_exact_policy_gated(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    changes = ChangeControl(ledger)
    proposal = ChangeProposal(
        manifest_id="fno-universe-v1",
        kind=ManifestKind.UNIVERSE_POLICY,
        version="1",
        definition=UniverseBuilder.REQUIRED_POLICY,
        evidence="binding stock-only scope",
        expected_benefit="date-versioned FnO universe",
        risks="current contracts do not reconstruct past membership",
        test_plan="stock/index exclusion fixture",
    )
    value = changes.propose(proposal, actor_id="engineer", at=NOW)
    changes.approve(
        proposal.manifest_id,
        value,
        actor_id=APPROVER_ID,
        rationale="test approval",
        at=NOW,
    )
    changes.activate(
        proposal.manifest_id, value, actor_id=APPROVER_ID, at=NOW
    )
    observation = RawObservation(
        observation_id="instruments-1",
        source_id="upstox-read-only-market-data-v3",
        retrieved_at=NOW,
        content_sha256="a" * 64,
        parser_version="fixture",
        license_tier="fixture",
        storage_ref="fixture://instruments",
    )
    rows = [
        {
            "segment": "NSE_FO", "instrument_type": "CE",
            "underlying_type": "EQUITY", "underlying_key": "NSE_EQ|ABC",
            "underlying_symbol": "ABC",
        },
        {
            "segment": "NSE_FO", "instrument_type": "PE",
            "underlying_type": "EQUITY", "underlying_key": "NSE_EQ|ABC",
            "underlying_symbol": "ABC",
        },
        {
            "segment": "NSE_FO", "instrument_type": "CE",
            "underlying_type": "INDEX", "underlying_key": "NSE_INDEX|Nifty 50",
            "underlying_symbol": "NIFTY",
        },
    ]
    event_id = UniverseBuilder(ledger, changes).build_and_record(
        rows,
        observation=observation,
        policy_manifest_id=proposal.manifest_id,
        policy_manifest_hash=value,
    )
    event = next(event for event in ledger.events() if event.event_id == event_id)
    assert event.payload["member_count"] == 1
    assert event.payload["contract_count"] == 2
    assert event.payload["members"] == [
        {"underlying_key": "NSE_EQ|ABC", "symbol": "ABC"}
    ]

    with pytest.raises(PermissionError, match="not active"):
        UniverseBuilder(ledger, changes).build_and_record(
            rows,
            observation=observation,
            policy_manifest_id=proposal.manifest_id,
            policy_manifest_hash="b" * 64,
        )


def test_production_universe_proposal_hash_is_frozen():
    import json
    from pathlib import Path

    path = Path(__file__).parents[2] / "proposals" / "fno_universe_policy_v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["kind"] = ManifestKind(value["kind"])
    proposal = ChangeProposal(**value)
    proposal.validate()
    assert proposal.content_hash == (
        "9ea8663c50d7c80e7a3bc2b2adfc43cc87260b16b84f962226d7df25916fcf9c"
    )
