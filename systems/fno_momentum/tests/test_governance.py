from datetime import datetime, timezone

import pytest

from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.policy import APPROVER_ID


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _proposal(version="1"):
    return ChangeProposal(
        manifest_id="liquidity-sweep-long-5m",
        kind=ManifestKind.PATTERN,
        version=version,
        definition={"anchor_timeframe": ["1d", "1w"], "thresholds": "proposal-only"},
        evidence="No prior evidence is imported; collect a fresh clean-room cohort.",
        expected_benefit="Test anchored sweep behavior.",
        risks="False reclaims and sparse samples.",
        test_plan="Golden fixtures, chronological replay, then untouched validation.",
    )


def _activate(control, proposal):
    content_hash = control.propose(proposal, actor_id="engineer", at=NOW)
    control.approve(
        proposal.manifest_id,
        content_hash,
        actor_id=APPROVER_ID,
        rationale="definition and clean-room test plan approved",
        at=NOW,
    )
    control.activate(proposal.manifest_id, content_hash, actor_id=APPROVER_ID, at=NOW)
    return content_hash


def test_only_exact_approved_manifest_can_activate(tmp_path):
    control = ChangeControl(ImmutableLedger(tmp_path / "events.sqlite"))
    proposal = _proposal()
    content_hash = control.propose(proposal, actor_id="engineer", at=NOW)
    with pytest.raises(ValueError, match="requires approval"):
        control.activate(proposal.manifest_id, content_hash, actor_id=APPROVER_ID, at=NOW)
    with pytest.raises(PermissionError, match="designated approver"):
        control.approve(
            proposal.manifest_id,
            content_hash,
            actor_id="engineer",
            rationale="self approved",
            at=NOW,
        )


def test_active_manifest_is_frozen_during_forward_cohort(tmp_path):
    control = ChangeControl(ImmutableLedger(tmp_path / "events.sqlite"))
    first = _proposal("1")
    first_hash = _activate(control, first)
    control.start_forward_cohort(first.manifest_id, first_hash, cohort_id="cohort-1", at=NOW)

    second = _proposal("2")
    second_hash = control.propose(second, actor_id="engineer", at=NOW)
    control.approve(
        second.manifest_id,
        second_hash,
        actor_id=APPROVER_ID,
        rationale="approved for activation after the current cohort",
        at=NOW,
    )
    with pytest.raises(ValueError, match="frozen"):
        control.activate(second.manifest_id, second_hash, actor_id=APPROVER_ID, at=NOW)
    with pytest.raises(ValueError, match="at least 20"):
        control.complete_forward_cohort(
            first.manifest_id, first_hash, cohort_id="cohort-1", alerts=19, at=NOW
        )

    control.complete_forward_cohort(
        first.manifest_id, first_hash, cohort_id="cohort-1", alerts=20, at=NOW
    )
    control.activate(second.manifest_id, second_hash, actor_id=APPROVER_ID, at=NOW)
    assert control.active_hash(second.manifest_id) == second_hash


def test_prior_delegated_manifest_events_are_not_active(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    control = ChangeControl(ledger)
    proposal = _proposal()
    content_hash = control.propose(proposal, actor_id="engineer", at=NOW)
    ledger.append(
        "manifest_change_approved", proposal.manifest_id,
        {"kind": proposal.kind.value, "content_hash": content_hash,
         "actor_id": "benefactor-authority", "rationale": "legacy delegated event"},
        occurred_at=NOW, idempotency_key="legacy-manifest-approval",
    )
    ledger.append(
        "manifest_activated", proposal.manifest_id,
        {"kind": proposal.kind.value, "content_hash": content_hash,
         "actor_id": "benefactor-authority"},
        occurred_at=NOW, idempotency_key="legacy-manifest-activation",
    )
    assert control.active_hash(proposal.manifest_id) is None
    with pytest.raises(PermissionError, match="not active"):
        control.require_active(proposal.manifest_id, content_hash)
