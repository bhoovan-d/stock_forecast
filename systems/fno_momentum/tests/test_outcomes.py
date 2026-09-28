from datetime import datetime, timedelta, timezone

from fno_momentum.contracts import MarketBar
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.outcomes import OutcomeLabeler
from fno_momentum.policy import APPROVER_ID


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _bar(at, high, low):
    return MarketBar(
        instrument_id="NSE_EQ|EXAMPLE",
        interval="15m",
        interval_start=at - timedelta(minutes=15),
        interval_end=at,
        available_at=at,
        open=100,
        high=high,
        low=low,
        close=100,
        volume=100,
        observation_id=at.isoformat(),
    )


def test_outcome_labels_are_exact_hash_gated_and_stop_first_on_ambiguity(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    changes = ChangeControl(ledger)
    proposal = ChangeProposal(
        manifest_id="labels-v1",
        kind=ManifestKind.LIVE_BEHAVIOR,
        version="1",
        definition=OutcomeLabeler.REQUIRED_DEFINITION,
        evidence="binding specification",
        expected_benefit="immutable comparable target labels",
        risks="bar ambiguity is conservative",
        test_plan="golden path fixtures",
    )
    value = changes.propose(proposal, actor_id="engineer", at=NOW)
    changes.approve(
        proposal.manifest_id,
        value,
        actor_id=APPROVER_ID,
        rationale="test-only approval",
        at=NOW,
    )
    changes.activate(proposal.manifest_id, value, actor_id=APPROVER_ID, at=NOW)
    alert = ledger.append(
        "core_paper_alert",
        "candidate-1",
        {
            "direction": "bullish",
            "underlying_entry": 100,
            "underlying_stop": 99,
        },
        occurred_at=NOW,
        decision_at=NOW,
        data_cutoff=NOW,
        idempotency_key="fixture-alert",
    )
    bars = [
        _bar(NOW + timedelta(minutes=15), 102.2, 99.5),
        _bar(NOW + timedelta(minutes=30), 104.2, 100),
        _bar(NOW + timedelta(minutes=45), 105.2, 98.8),
    ]
    labeler = OutcomeLabeler(ledger, changes)
    event_id = labeler.label_and_record(
        alert.event_id,
        bars=bars,
        completed_sessions=3,
        outcome_at=NOW + timedelta(hours=1),
        formula_manifest_id=proposal.manifest_id,
        formula_manifest_hash=value,
    )
    event = next(event for event in ledger.events() if event.event_id == event_id)
    assert event.payload["labels"] == {
        "2R": True, "3R": True, "4R": True, "5R": False
    }
    assert event.payload["stop_reached"] is True
