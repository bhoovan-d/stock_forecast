from datetime import date, datetime, timedelta, timezone

import pytest

from fno_momentum.contracts import Direction, QuoteSnapshot
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.options import OptionContract, OptionRight, OptionSelector, QuotedContract
from fno_momentum.policy import APPROVER_ID


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _policy(tmp_path):
    control = ChangeControl(ImmutableLedger(tmp_path / "events.sqlite"))
    proposal = ChangeProposal(
        manifest_id="contract-policy-1", kind=ManifestKind.CONTRACT_POLICY, version="1",
        definition={
            "maximum_spread_bps": 500,
            "maximum_quote_age_seconds": 30,
            "minimum_volume": 100,
            "minimum_open_interest": 500,
            "ranking": ["spread_bps_asc", "open_interest_desc", "volume_desc",
                        "dte_asc", "contract_id_asc"],
        },
        evidence="Synthetic fixtures only.", expected_benefit="Test the quote envelope.",
        risks="Thresholds are not market validated.", test_plan="Golden and rejection fixtures.",
    )
    value = control.propose(proposal, actor_id="engineer", at=NOW)
    control.approve(proposal.manifest_id, value, actor_id=APPROVER_ID,
                    rationale="fixture policy approved for tests", at=NOW)
    control.activate(proposal.manifest_id, value, actor_id=APPROVER_ID, at=NOW)
    return control, proposal.manifest_id, value


def _quoted(strike, *, right=OptionRight.CALL, spread=0.2, age=0, dte=14, oi=1000, volume=500):
    contract_id = f"{right.value}-{strike}-{dte}"
    contract = OptionContract(
        contract_id=contract_id, underlying_symbol="EXAMPLE", right=right,
        strike=strike, expiry=NOW.date() + timedelta(days=dte), lot_size=100,
        tick_size=0.05, available_at=NOW, observation_id=f"contract-{strike}",
    )
    quote = QuoteSnapshot(
        contract_id=contract_id, observed_at=NOW - timedelta(seconds=age),
        available_at=NOW - timedelta(seconds=age), bid=10, ask=10 + spread,
        bid_quantity=100, ask_quantity=100, open_interest=oi, volume=volume,
        observation_id=f"quote-{strike}",
    )
    return QuotedContract(contract, quote)


def test_bullish_selection_uses_only_atm_and_one_step_itm(tmp_path):
    control, manifest_id, manifest_hash = _policy(tmp_path)
    selection = OptionSelector(control).select(
        [_quoted(95, spread=0.3), _quoted(100, spread=0.2), _quoted(105, spread=0.01)],
        underlying_symbol="EXAMPLE", underlying_price=101, direction=Direction.BULLISH,
        decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert selection.selected.contract.strike == 100
    assert selection.rejection_counts == {"OUTSIDE_ATM_ONE_ITM_ENVELOPE": 1}


def test_bearish_selection_uses_put_and_one_step_higher_strike(tmp_path):
    control, manifest_id, manifest_hash = _policy(tmp_path)
    selection = OptionSelector(control).select(
        [_quoted(100, right=OptionRight.PUT, spread=0.3),
         _quoted(105, right=OptionRight.PUT, spread=0.1),
         _quoted(110, right=OptionRight.PUT, spread=0.01),
         _quoted(105, right=OptionRight.CALL, spread=0.001)],
        underlying_symbol="EXAMPLE", underlying_price=102, direction=Direction.BEARISH,
        decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert selection.selected.contract.right is OptionRight.PUT
    assert selection.selected.contract.strike == 105


def test_stale_illiquid_and_wrong_dte_contracts_are_rejected(tmp_path):
    control, manifest_id, manifest_hash = _policy(tmp_path)
    selection = OptionSelector(control).select(
        [_quoted(100, age=31), _quoted(100, dte=6), _quoted(100, oi=499),
         _quoted(100, volume=99)],
        underlying_symbol="EXAMPLE", underlying_price=100, direction=Direction.BULLISH,
        decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert selection.selected is None
    assert selection.rejection_counts == {
        "STALE_QUOTE": 1,
        "DTE_OUTSIDE_ENVELOPE": 1,
        "INSUFFICIENT_OPEN_INTEREST": 1,
        "INSUFFICIENT_VOLUME": 1,
    }


def test_unapproved_contract_policy_cannot_select(tmp_path):
    control, manifest_id, _ = _policy(tmp_path)
    with pytest.raises(PermissionError, match="not active"):
        OptionSelector(control).select(
            [_quoted(100)], underlying_symbol="EXAMPLE", underlying_price=100,
            direction=Direction.BULLISH, decision_at=NOW,
            manifest_id=manifest_id, manifest_hash="0" * 64,
        )
