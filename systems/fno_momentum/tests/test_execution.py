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
from fno_momentum.execution import ExitReason, PaperExecution
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID
from fno_momentum.reconciliation import DashboardProjection, Reconciler
from fno_momentum.source_registry import SourceManifest, SourceRegistry


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _activate(changes, manifest_id, kind, definition):
    proposal = ChangeProposal(
        manifest_id=manifest_id,
        kind=kind,
        version="test-1",
        definition=definition,
        evidence="clean-room unit fixture",
        expected_benefit="exercise deterministic paper lifecycle",
        risks="fixture only",
        test_plan="unit test",
    )
    value = changes.propose(proposal, actor_id="engineer", at=NOW)
    changes.approve(
        manifest_id,
        value,
        actor_id=APPROVER_ID,
        rationale="test-only exact-hash approval",
        at=NOW,
    )
    changes.activate(manifest_id, value, actor_id=APPROVER_ID, at=NOW)
    return value


def _quote(at, *, bid=10.0, ask=10.5, observation_id="quote"):
    return QuoteSnapshot(
        contract_id="NSE_FO|EXAMPLE-CALL",
        observed_at=at,
        available_at=at,
        bid=bid,
        ask=ask,
        bid_quantity=100,
        ask_quantity=100,
        open_interest=1000,
        volume=500,
        observation_id=observation_id,
    )


def _bar(
    at,
    *,
    open_price=100,
    high=101,
    low=99.5,
    close=100.5,
    interval="5m",
    observation_id=None,
):
    return MarketBar(
        instrument_id="NSE_EQ|EXAMPLE",
        interval=interval,
        interval_start=at - timedelta(minutes=5),
        interval_end=at,
        available_at=at,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=1000,
        observation_id=observation_id or f"bar-{at.isoformat()}-{interval}",
    )


def _record(ledger, source_hash, observation_id, at):
    ledger.append(
        "source_observed",
        observation_id,
        {
            "source_id": "source-1",
            "source_hash": source_hash,
            "retrieved_at": at.isoformat(),
            "published_at": None,
            "content_sha256": "c" * 64,
            "parser_version": "fixture-1",
            "license_tier": "fixture",
            "storage_ref": f"fixture://{observation_id}",
        },
        occurred_at=at,
        decision_at=at,
        data_cutoff=at,
        idempotency_key=f"source-observed:{observation_id}",
    )


def _setup(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    changes = ChangeControl(ledger)
    sources = SourceRegistry(ledger)
    source = SourceManifest(
        source_id="source-1",
        name="Test market source",
        source_type="market_data",
        lawful_access_basis="fixture license",
        terms_reference="https://example.test/terms",
        fields=("ohlcv", "bbo"),
        latency="fixture",
        monthly_cost_inr=0,
        collection_method="unit fixture",
        retention_policy="test lifetime",
        version="test-1",
    )
    source_hash = sources.propose(source, actor_id="engineer", at=NOW)
    sources.approve(
        source.source_id,
        source_hash,
        actor_id=APPROVER_ID,
        rationale="test-only source",
        at=NOW,
    )
    sources.activate(source.source_id, source_hash, actor_id=APPROVER_ID, at=NOW)
    observation = RawObservation(
        observation_id="raw-1",
        source_id="source-1",
        retrieved_at=NOW,
        content_sha256="a" * 64,
        parser_version="fixture-1",
        license_tier="fixture",
        storage_ref="fixture://raw-1",
    )
    sources.record_observation(observation, source_hash=source_hash)
    _record(ledger, source_hash, "quote", NOW)

    pattern_hash = _activate(
        changes, "pattern-1", ManifestKind.PATTERN, {"fixture": "approved"}
    )
    model_hash = _activate(
        changes,
        "model-1",
        ManifestKind.MODEL,
        {"fixture": "approved", "minimum_calibrated_2r_probability": 0.6},
    )
    feature_hash = _activate(
        changes, "feature-1", ManifestKind.FEATURE_SCHEMA, {"fixture": "approved"}
    )
    execution_definition = {
        "fill_rule": "buy_at_observable_ask_sell_at_next_observable_bid",
        "both_touch_fallback": "stop_first",
        "contract_roll_policy": "no_roll",
        "maximum_exit_quote_age_seconds": 5,
    }
    execution_hash = _activate(
        changes, "execution-1", ManifestKind.LIVE_BEHAVIOR, execution_definition
    )
    cost_definition = {
        "brokerage_per_order_inr": 20,
        "other_fixed_costs_per_lot_inr": 1,
        "sell_turnover_cost_bps": 0,
        "slippage_policy": "observable_bbo_no_additional_slippage",
    }
    cost_hash = _activate(changes, "cost-1", ManifestKind.COST_POLICY, cost_definition)

    lifecycle = CandidateLifecycle(ledger, changes, sources)
    draft = CandidateDraft(
        symbol="EXAMPLE",
        direction=Direction.BULLISH,
        pattern_family=PatternFamily.LIQUIDITY_SWEEP,
        pattern_variant="reversal_reclaim",
        entry_timeframe=EntryTimeframe.FIVE_MINUTE,
        context=DecisionContext(NOW, NOW, ("raw-1",)),
        pattern_manifest_id="pattern-1",
        pattern_manifest_hash=pattern_hash,
        source_versions=(("source-1", source_hash),),
    )
    candidate_id = lifecycle.create(draft, [_bar(NOW, observation_id="raw-1")])
    alert = CorePaperAlert(
        candidate_id=candidate_id,
        symbol="EXAMPLE",
        direction=Direction.BULLISH,
        option_contract_id="NSE_FO|EXAMPLE-CALL",
        option_position="long_call",
        entry_rule="approved-test-entry",
        underlying_entry=100,
        underlying_stop=99,
        selected_target_r=3,
        predicted_move_pct=6.5,
        target_probabilities=((2, 0.72), (3, 0.58), (4, 0.4), (5, 0.22)),
        expected_net_r=0.4,
        quote=_quote(NOW),
        model_version="model-1",
        feature_schema_version="feature-1",
        source_hashes=(source_hash,),
        reason_codes=("TEST_APPROVED",),
    )
    alert_id = lifecycle.publish(
        alert,
        context=draft.context,
        active_manifests=(("model-1", model_hash), ("feature-1", feature_hash)),
    )
    execution = PaperExecution(ledger, changes, sources)
    order_id = execution.create_order(
        alert_id,
        created_at=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(minutes=30),
        execution_manifest_id="execution-1",
        execution_manifest_hash=execution_hash,
    )
    _record(ledger, source_hash, "entry-quote", NOW + timedelta(seconds=2))
    execution.fill(
        order_id,
        quote=_quote(NOW + timedelta(seconds=2), observation_id="entry-quote"),
        lot_size=100,
        filled_at=NOW + timedelta(seconds=2),
    )
    return (
        ledger, sources, source_hash, execution, order_id, alert_id,
        execution_hash, cost_hash,
    )


def test_target_uses_ask_entry_next_bid_exit_and_reconciles(tmp_path):
    (
        ledger, _, source_hash, execution, position_id, alert_id,
        execution_hash, cost_hash,
    ) = _setup(tmp_path)
    trigger_at = NOW + timedelta(minutes=5)
    trigger_bar = _bar(trigger_at, high=103.2, low=100, close=103)
    trigger_quote = _quote(
        trigger_at, bid=13.8, ask=14.0, observation_id="trigger-quote"
    )
    _record(ledger, source_hash, trigger_bar.observation_id, trigger_at)
    _record(ledger, source_hash, trigger_quote.observation_id, trigger_at)
    trigger = execution.evaluate_exit(
        position_id,
        underlying_bar=trigger_bar,
        option_quote=trigger_quote,
        completed_sessions=0,
        decision_at=trigger_at,
    )
    assert trigger is not None and trigger.reason is ExitReason.TARGET
    exit_at = trigger_at + timedelta(seconds=1)
    _record(ledger, source_hash, "exit-quote", exit_at)
    settlement_id = execution.settle(
        position_id,
        exit_quote=_quote(exit_at, bid=14, ask=14.2, observation_id="exit-quote"),
        settled_at=exit_at,
        execution_manifest_id="execution-1",
        execution_manifest_hash=execution_hash,
        cost_manifest_id="cost-1",
        cost_manifest_hash=cost_hash,
    )
    settlement = next(event for event in ledger.events() if event.event_id == settlement_id)
    assert settlement.payload["entry_ask"] == 10.5
    assert settlement.payload["exit_bid"] == 14
    assert settlement.payload["gross_option_pnl_per_lot"] == 350
    assert settlement.payload["total_cost_per_lot"] == 41
    assert settlement.payload["net_option_pnl_per_lot"] == 309
    assert settlement.payload["normalized_underlying_r"] == pytest.approx(3)
    report = Reconciler(ledger).run()
    assert report.balanced and not report.open_positions
    evidence = DashboardProjection(ledger).alert_evidence(alert_id)
    assert len(evidence["source_observations"]) == 1
    assert any(event["event_type"] == "settlement" for event in evidence["lifecycle"])


def test_both_touch_uses_lower_timeframe_and_falls_back_to_stop(tmp_path):
    ledger, _, source_hash, execution, position_id, _, _, _ = _setup(tmp_path)
    at = NOW + timedelta(minutes=15)
    target_first = _bar(
        at - timedelta(minutes=5), open_price=100.5, high=103.1, low=100, close=102.8
    )
    both_bar = _bar(at, open_price=100, high=103.5, low=98.5, close=100)
    both_quote = _quote(at, observation_id="both-touch-quote")
    for observation_id, observed_at in (
        (target_first.observation_id, target_first.available_at),
        (both_bar.observation_id, at),
        (both_quote.observation_id, at),
    ):
        _record(ledger, source_hash, observation_id, observed_at)
    trigger = execution.evaluate_exit(
        position_id,
        underlying_bar=both_bar,
        option_quote=both_quote,
        completed_sessions=0,
        decision_at=at,
        lower_timeframe_bars=(target_first,),
    )
    assert trigger is not None and trigger.reason is ExitReason.TARGET

    ledger2, _, source_hash2, execution2, position_id2, _, _, _ = _setup(tmp_path / "fallback")
    fallback_bar = _bar(at, open_price=100, high=103.5, low=98.5, close=100)
    fallback_quote = _quote(at, observation_id="ambiguous-quote")
    _record(ledger2, source_hash2, fallback_bar.observation_id, at)
    _record(ledger2, source_hash2, fallback_quote.observation_id, at)
    trigger2 = execution2.evaluate_exit(
        position_id2,
        underlying_bar=fallback_bar,
        option_quote=fallback_quote,
        completed_sessions=0,
        decision_at=at,
    )
    assert trigger2 is not None and trigger2.reason is ExitReason.STOP


def test_premium_guard_and_next_quote_requirement(tmp_path):
    ledger, _, source_hash, execution, position_id, _, execution_hash, cost_hash = _setup(tmp_path)
    at = NOW + timedelta(minutes=5)
    guard_bar = _bar(at)
    guard_quote = _quote(at, bid=5.0, ask=5.2, observation_id="guard-quote")
    _record(ledger, source_hash, guard_bar.observation_id, at)
    _record(ledger, source_hash, guard_quote.observation_id, at)
    trigger = execution.evaluate_exit(
        position_id,
        underlying_bar=guard_bar,
        option_quote=guard_quote,
        completed_sessions=1,
        decision_at=at,
    )
    assert trigger is not None and trigger.reason is ExitReason.PREMIUM_GUARD
    _record(ledger, source_hash, "same-time-quote", at)
    with pytest.raises(ValueError, match="next observable bid"):
        execution.settle(
            position_id,
            exit_quote=_quote(at, bid=4.9, ask=5.1, observation_id="same-time-quote"),
            settled_at=at,
            execution_manifest_id="execution-1",
            execution_manifest_hash=execution_hash,
            cost_manifest_id="cost-1",
            cost_manifest_hash=cost_hash,
        )


def test_unfilled_order_is_terminal(tmp_path):
    ledger, sources, _, _, _, alert_id, execution_hash, _ = _setup(tmp_path)
    changes = ChangeControl(ledger)
    execution = PaperExecution(ledger, changes, sources)
    order_id = execution.create_order(
        alert_id,
        created_at=NOW + timedelta(minutes=1),
        expires_at=NOW + timedelta(minutes=2),
        execution_manifest_id="execution-1",
        execution_manifest_hash=execution_hash,
    )
    execution.mark_unfilled(order_id, reason_code="ENTRY_RULE_NOT_FILLED", at=NOW + timedelta(minutes=2))
    with pytest.raises(ValueError, match="terminal order state"):
        execution.fill(
            order_id,
            quote=_quote(NOW + timedelta(minutes=2), observation_id="late-entry"),
            lot_size=100,
            filled_at=NOW + timedelta(minutes=2),
        )
