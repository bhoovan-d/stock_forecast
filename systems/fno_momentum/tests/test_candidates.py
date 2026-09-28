from datetime import datetime, timedelta, timezone

import pytest

from fno_momentum.candidates import CandidateDraft, CandidateLifecycle, CorePaperAlert
from fno_momentum.contracts import (
    DecisionContext,
    Direction,
    EntryTimeframe,
    MarketBar,
    PatternFamily,
    QuoteSnapshot,
    RawObservation,
)
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID
from fno_momentum.source_registry import SourceManifest, SourceRegistry


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _approved_source(registry):
    manifest = SourceManifest(
        source_id="source-1", name="Test source", source_type="market_data",
        lawful_access_basis="test license", terms_reference="https://example.test/terms",
        fields=("ohlcv",), latency="real-time", monthly_cost_inr=0,
        collection_method="fixture", retention_policy="test lifetime", version="1",
    )
    value = registry.propose(manifest, actor_id="engineer", at=NOW)
    registry.approve("source-1", value, actor_id=APPROVER_ID, rationale="test approval", at=NOW)
    registry.activate("source-1", value, actor_id=APPROVER_ID, at=NOW)
    for observation_id in ("observation-1", "quote-1"):
        registry.record_observation(
            RawObservation(
                observation_id=observation_id,
                source_id="source-1",
                retrieved_at=NOW,
                content_sha256=("a" if observation_id == "observation-1" else "b") * 64,
                parser_version="fixture-1",
                license_tier="fixture",
                storage_ref=f"fixture://{observation_id}",
            ),
            source_hash=value,
        )
    return value


def _approved_manifest(changes, manifest_id, kind):
    definition = {"fixture": True}
    if kind is ManifestKind.MODEL:
        definition["minimum_calibrated_2r_probability"] = 0.6
    proposal = ChangeProposal(
        manifest_id=manifest_id, kind=kind, version="1", definition=definition,
        evidence="clean-room fixture", expected_benefit="exercise lifecycle",
        risks="fixture only", test_plan="unit test",
    )
    value = changes.propose(proposal, actor_id="engineer", at=NOW)
    changes.approve(manifest_id, value, actor_id=APPROVER_ID, rationale="test approval", at=NOW)
    changes.activate(manifest_id, value, actor_id=APPROVER_ID, at=NOW)
    return value


def _bar(available=NOW):
    return MarketBar(
        instrument_id="NSE_EQ|EXAMPLE", interval="5m",
        interval_start=NOW - timedelta(minutes=5), interval_end=NOW,
        available_at=available, open=100, high=101, low=99, close=100.5,
        volume=1000, observation_id="observation-1",
    )


def _setup(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    changes, sources = ChangeControl(ledger), SourceRegistry(ledger)
    source_hash = _approved_source(sources)
    pattern_hash = _approved_manifest(changes, "pattern-1", ManifestKind.PATTERN)
    lifecycle = CandidateLifecycle(ledger, changes, sources)
    draft = CandidateDraft(
        symbol="EXAMPLE", direction=Direction.BULLISH,
        pattern_family=PatternFamily.LIQUIDITY_SWEEP, pattern_variant="reversal_reclaim",
        entry_timeframe=EntryTimeframe.FIVE_MINUTE,
        context=DecisionContext(NOW, NOW, ("observation-1",)),
        pattern_manifest_id="pattern-1", pattern_manifest_hash=pattern_hash,
        source_versions=(("source-1", source_hash),),
    )
    return ledger, changes, lifecycle, draft


def test_candidate_rejects_future_input_and_reconciles_terminal_state(tmp_path):
    _, _, lifecycle, draft = _setup(tmp_path)
    with pytest.raises(ValueError, match="future observation"):
        lifecycle.create(draft, [_bar(NOW + timedelta(seconds=1))])
    candidate_id = lifecycle.create(draft, [_bar()])
    assert lifecycle.unreconciled() == [candidate_id]
    lifecycle.reject(candidate_id, reason_code="NO_OPTION_QUOTE", detail="No fresh ask.", at=NOW)
    assert lifecycle.unreconciled() == []


def test_core_alert_enforces_fixed_controls_and_daily_cap(tmp_path):
    _, changes, lifecycle, draft = _setup(tmp_path)
    candidate_id = lifecycle.create(draft, [_bar()])
    model_hash = _approved_manifest(changes, "model-1", ManifestKind.MODEL)
    feature_hash = _approved_manifest(changes, "features-1", ManifestKind.FEATURE_SCHEMA)
    quote = QuoteSnapshot(
        contract_id="NSE_FO|EXAMPLE", observed_at=NOW, available_at=NOW,
        bid=10, ask=10.5, bid_quantity=100, ask_quantity=100,
        open_interest=1000, volume=500, observation_id="quote-1",
    )
    alert = CorePaperAlert(
        candidate_id=candidate_id, symbol="EXAMPLE", direction=Direction.BULLISH,
        option_contract_id=quote.contract_id, option_position="long_call",
        entry_rule="approved-rule-v1", underlying_entry=100, underlying_stop=99,
        selected_target_r=3, predicted_move_pct=6.5, quote=quote,
        target_probabilities=((2, 0.7), (3, 0.55), (4, 0.4), (5, 0.25)),
        expected_net_r=0.35,
        model_version="model-1", feature_schema_version="features-1",
        source_hashes=(draft.source_versions[0][1],), reason_codes=("PATTERN_APPROVED",),
    )
    event_id = lifecycle.publish(
        alert,
        context=draft.context,
        active_manifests=(("model-1", model_hash), ("features-1", feature_hash)),
    )
    assert event_id
    assert lifecycle.unreconciled() == []

    with pytest.raises(ValueError, match="terminal decision"):
        lifecycle.publish(alert, context=draft.context, active_manifests=(("model-1", model_hash),))


def test_core_alert_refuses_stop_outside_binding_band(tmp_path):
    _, _, lifecycle, draft = _setup(tmp_path)
    candidate_id = lifecycle.create(draft, [_bar()])
    quote = QuoteSnapshot(
        contract_id="NSE_FO|EXAMPLE", observed_at=NOW, available_at=NOW,
        bid=10, ask=10.5, bid_quantity=1, ask_quantity=1, open_interest=1,
        volume=1, observation_id="quote-1",
    )
    alert = CorePaperAlert(
        candidate_id=candidate_id, symbol="EXAMPLE", direction=Direction.BULLISH,
        option_contract_id=quote.contract_id, option_position="long_call",
        entry_rule="rule", underlying_entry=100, underlying_stop=96,
        selected_target_r=2, predicted_move_pct=6, quote=quote,
        target_probabilities=((2, 0.7), (3, 0.5), (4, 0.3), (5, 0.2)),
        expected_net_r=0.2,
        model_version="model", feature_schema_version="features",
        source_hashes=("a" * 64,), reason_codes=("TEST",),
    )
    with pytest.raises(ValueError, match="structural stop"):
        alert.validate(NOW)
