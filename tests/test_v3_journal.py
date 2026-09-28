from __future__ import annotations

import pandas as pd

from asymmetry.engines.carry import CarryState
from asymmetry.engines.setups import SetupSignal
from asymmetry.engines.v3 import V3Plan
from asymmetry.engines.v3_scan import V3Candidate, V3Scan
from asymmetry.spec import SetupType


def _scan() -> V3Scan:
    plan = V3Plan(
        direction="long", entry=100, stop=99, stop_pct=1.0, risk=1, target=104,
        target_pct=4, quantity=100, invalidation="swing", setup=SetupType.RECLAIM,
        trigger_bar=pd.Timestamp("2026-01-02 09:15"), position_value_inr=10_000,
    )
    candidate = V3Candidate(
        symbol="TEST", sector="Tech", direction="long", plan=plan, score=80,
        setup=SetupSignal(kind=SetupType.RECLAIM, found=True, quality=80),
        carry=CarryState(score=70, passes=True), catalyst_note="results", catalyst_score=75,
    )
    return V3Scan(as_of="2026-01-02", trades=[candidate], cleared_floor=1)


def test_record_is_idempotent_and_freezes_target(monkeypatch, tmp_path):
    import asymmetry.v3_journal as journal

    monkeypatch.setattr(journal, "WATCH_PATH", tmp_path / "watch.jsonl")
    scan = _scan()
    assert journal.record(scan) == 1
    assert journal.record(scan) == 0
    record = journal.load()[0]
    assert record.target == 104
    assert record.reward_risk == 4


def test_record_freezes_probability_selected_3r_target(monkeypatch, tmp_path):
    import asymmetry.v3_journal as journal
    from types import SimpleNamespace

    monkeypatch.setattr(journal, "WATCH_PATH", tmp_path / "watch.jsonl")
    scan = _scan()
    candidate = scan.trades[0]
    candidate.recommended_reward_risk = 3
    candidate.recommended_target = 103
    candidate.probability_3r = SimpleNamespace(probability=0.40, n=120)
    candidate.probability_4r = SimpleNamespace(probability=0.31, n=115)
    assert journal.record(scan) == 1
    record = journal.load()[0]
    assert record.target == 103
    assert record.reward_risk == 3
    assert record.probability_3r == 0.40
    assert record.probability_4r_n == 115


def test_settlement_matches_public_resolver_and_ambiguous_bar_loses(monkeypatch, tmp_path):
    import asymmetry.v3_journal as journal
    from asymmetry.v3_backtest import Trade, resolve_forward

    monkeypatch.setattr(journal, "WATCH_PATH", tmp_path / "watch.jsonl")
    journal.record(_scan())
    bars = pd.DataFrame(
        {"open": [100, 100], "high": [100, 105], "low": [100, 98], "close": [100, 103],
         "volume": [1, 1]},
        index=pd.to_datetime(["2026-01-02 09:15", "2026-01-02 09:30"]),
    )
    monkeypatch.setattr("asymmetry.data.yahoo.fetch_chart", lambda *_a, **_k: bars)
    assert journal.settle() == 1
    stored = journal.load()[0]

    direct = Trade("TEST", "long", "reclaim", bars.index[0], 100, 99, 104, 1.0)
    resolve_forward(bars, 0, direct, 125)
    assert stored.outcome == direct.outcome == "stop"
    assert stored.realised_r == direct.realised_r == -1


def test_forward_rollout_needs_30_signals_and_the_conservative_floor():
    from asymmetry.v3_journal import V3Record, rollout_status

    records = [
        V3Record(
            symbol=f"T{i}", prediction_qualified=True, reward_risk=2,
            outcome="target" if i < 25 else "stop",
            realised_r=2 if i < 25 else -1, cost_r=0.1,
        )
        for i in range(30)
    ]
    passed = rollout_status(records)
    assert passed.phase == "live-ready"
    assert passed.probability >= 0.70 and passed.lower >= 0.60

    too_few = rollout_status(records[:29])
    assert too_few.phase == "paper"

    weak = [
        V3Record(
            symbol=f"W{i}", prediction_qualified=True, reward_risk=2,
            outcome="target" if i < 21 else "stop",
            realised_r=2 if i < 21 else -1, cost_r=0.1,
        )
        for i in range(30)
    ]
    assert rollout_status(weak).phase == "paper"
