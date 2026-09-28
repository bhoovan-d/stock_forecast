from datetime import datetime, timedelta, timezone

import pytest

from fno_momentum.contracts import Direction, EntryTimeframe, MarketBar
from fno_momentum.governance import ChangeControl, ChangeProposal, ManifestKind
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.liquidity_sweep import (
    AnchorKind,
    LiquidityAnchor,
    LiquiditySweepDetector,
)
from fno_momentum.policy import APPROVER_ID


NOW = datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)


def _active_rule(tmp_path, *, touches=2, direction=None, entry_timeframe=None):
    control = ChangeControl(ImmutableLedger(tmp_path / "events.sqlite"))
    proposal = ChangeProposal(
        manifest_id="sweep-reclaim-5m-v1",
        kind=ManifestKind.PATTERN,
        version="1",
        definition={
            "minimum_prior_touches": touches,
            "minimum_sweep_bps": 10,
            "minimum_reclaim_bps": 5,
            "confirmation_bars": 3,
            "allowed_anchor_kinds": ["daily_swing", "weekly_swing", "prior_session"],
            "variant": "reversal_reclaim",
            **({"direction": direction} if direction else {}),
            **({"entry_timeframe": entry_timeframe} if entry_timeframe else {}),
        },
        evidence="Synthetic clean-room fixture only; no performance claim.",
        expected_benefit="Validate the reference lifecycle.",
        risks="Rule is not supported by market evidence.",
        test_plan="Directional golden and rejection fixtures.",
    )
    value = control.propose(proposal, actor_id="engineer", at=NOW)
    control.approve(
        proposal.manifest_id, value, actor_id=APPROVER_ID,
        rationale="fixture rule approved for tests only", at=NOW,
    )
    control.activate(proposal.manifest_id, value, actor_id=APPROVER_ID, at=NOW)
    return control, proposal.manifest_id, value


def _anchor(kind=AnchorKind.DAILY_SWING, available=NOW - timedelta(hours=1)):
    return LiquidityAnchor(
        anchor_id="anchor-1", kind=kind, price=100, touches=2,
        formed_at=NOW - timedelta(days=2), available_at=available,
        observation_ids=("daily-1", "daily-2"),
    )


def _bar(position, *, low, high, close, interval="5m"):
    end = NOW - timedelta(minutes=10 - position * 5)
    return MarketBar(
        instrument_id="NSE_EQ|EXAMPLE", interval=interval,
        interval_start=end - timedelta(minutes=5), interval_end=end, available_at=end,
        open=100, high=high, low=low, close=close, volume=1000,
        observation_id=f"bar-{position}",
    )


def test_long_daily_sweep_and_reclaim_is_confirmed(tmp_path):
    control, manifest_id, manifest_hash = _active_rule(tmp_path)
    result = LiquiditySweepDetector(control).detect(
        [_bar(0, low=100, high=101, close=100),
         _bar(1, low=99.8, high=100.4, close=99.9),
         _bar(2, low=99.9, high=100.5, close=100.2)],
        _anchor(), direction=Direction.BULLISH,
        entry_timeframe=EntryTimeframe.FIVE_MINUTE, decision_at=NOW,
        manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert result.matched
    assert result.reason_code == "LIQUIDITY_SWEEP_RECLAIM_CONFIRMED"
    assert result.sweep_distance_bps == pytest.approx(20)
    assert result.reclaim_distance_bps == pytest.approx(20)


def test_short_weekly_sweep_is_directional_mirror(tmp_path):
    control, manifest_id, manifest_hash = _active_rule(tmp_path)
    result = LiquiditySweepDetector(control).detect(
        [_bar(0, low=99, high=100, close=100),
         _bar(1, low=99.7, high=100.2, close=100.1),
         _bar(2, low=99.5, high=100.1, close=99.8)],
        _anchor(AnchorKind.WEEKLY_SWING), direction=Direction.BEARISH,
        entry_timeframe=EntryTimeframe.FIVE_MINUTE, decision_at=NOW,
        manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert result.matched
    assert result.sweep_distance_bps == pytest.approx(20)
    assert result.reclaim_distance_bps == pytest.approx(20)


def test_sweep_without_reclaim_is_explicit_rejection(tmp_path):
    control, manifest_id, manifest_hash = _active_rule(tmp_path)
    result = LiquiditySweepDetector(control).detect(
        [_bar(0, low=100, high=101, close=100),
         _bar(1, low=99.8, high=100.1, close=99.9),
         _bar(2, low=99.7, high=100, close=99.8)],
        _anchor(), direction=Direction.BULLISH,
        entry_timeframe=EntryTimeframe.FIVE_MINUTE, decision_at=NOW,
        manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert not result.matched
    assert result.reason_code == "SWEEP_NOT_RECLAIMED"
    assert result.sweep_at is not None


def test_future_anchor_and_inactive_rule_fail_closed(tmp_path):
    control, manifest_id, manifest_hash = _active_rule(tmp_path)
    detector = LiquiditySweepDetector(control)
    with pytest.raises(ValueError, match="future observation"):
        detector.detect(
            [_bar(2, low=99.8, high=100.2, close=100.1)],
            _anchor(available=NOW + timedelta(seconds=1)),
            direction=Direction.BULLISH, entry_timeframe=EntryTimeframe.FIVE_MINUTE,
            decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
        )
    with pytest.raises(PermissionError, match="not active"):
        detector.detect(
            [_bar(2, low=99.8, high=100.2, close=100.1)], _anchor(),
            direction=Direction.BULLISH, entry_timeframe=EntryTimeframe.FIVE_MINUTE,
            decision_at=NOW, manifest_id=manifest_id, manifest_hash="0" * 64,
        )


def test_frozen_pattern_pair_rejects_other_direction_and_timeframe(tmp_path):
    control, manifest_id, manifest_hash = _active_rule(
        tmp_path, direction="bearish", entry_timeframe="15m"
    )
    detector = LiquiditySweepDetector(control)
    wrong_direction = detector.detect(
        [_bar(2, low=99.8, high=100.2, close=100.1)], _anchor(),
        direction=Direction.BULLISH, entry_timeframe=EntryTimeframe.FIFTEEN_MINUTE,
        decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert wrong_direction.reason_code == "DIRECTION_NOT_APPROVED"
    wrong_timeframe = detector.detect(
        [_bar(2, low=99.8, high=100.2, close=100.1)], _anchor(),
        direction=Direction.BEARISH, entry_timeframe=EntryTimeframe.FIVE_MINUTE,
        decision_at=NOW, manifest_id=manifest_id, manifest_hash=manifest_hash,
    )
    assert wrong_timeframe.reason_code == "ENTRY_TIMEFRAME_NOT_APPROVED"


def test_first_pattern_proposal_hash_is_frozen():
    import json
    from pathlib import Path

    path = (
        Path(__file__).parents[2]
        / "proposals"
        / "fno_pattern_prior_session_high_bearish_reclaim_v1.json"
    )
    value = json.loads(path.read_text(encoding="utf-8"))
    value["kind"] = ManifestKind(value["kind"])
    proposal = ChangeProposal(**value)
    proposal.validate()
    assert proposal.content_hash == (
        "79d6da6704d846112f98b3cc97c28bf9903f09523e855f61bdc06a1a93da0883"
    )
