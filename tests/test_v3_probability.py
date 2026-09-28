from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from asymmetry.v3_backtest import Trade, resolve_forward
from asymmetry.v3_probability import (
    cost_adjusted_break_even,
    fit,
    load,
    market_is_open,
    save,
    trigger_is_fresh,
    wilson_interval,
)


def _history() -> list[Trade]:
    """A large, stable exact cohort that legitimately clears the configured gates."""
    days = pd.bdate_range("2023-01-02", periods=550)
    trades: list[Trade] = []
    for day_number, day in enumerate(days):
        for slot in range(5):
            entered = day + pd.Timedelta(hours=9, minutes=30)
            hit_2r = slot < 4       # 80% every date block
            hit_3r = slot < 3       # 60%
            hit_4r = slot < 3       # 60%
            trades.append(Trade(
                symbol=f"T{(day_number * 5 + slot) % 40}",
                direction="long", setup="reclaim", signal_at=entered - pd.Timedelta(minutes=15),
                entered_at=entered, entry=100, stop=99, target=104, stop_pct=1.0,
                outcome="target" if hit_4r else "stop", resolved_at=entered + pd.Timedelta(hours=1),
                realised_r=4 if hit_4r else -1, hit_2r=hit_2r, hit_3r=hit_3r, hit_4r=hit_4r,
                carry_passed=True, carry_applies=False,
                regime=("aggressive", "selective", "defensive")[day_number % 3],
                setup_quality=80, adr_pct=4, atr_pct=3,
                sector=f"Sector{slot}",
            ))
    return trades


def test_wilson_and_cost_adjusted_break_even_are_conservative():
    lower, upper = wilson_interval(40, 100)
    assert lower < 0.40 < upper
    assert cost_adjusted_break_even(4, 0.17) == pytest.approx(0.234)


def test_same_bar_stop_beats_every_target_touch():
    bars = pd.DataFrame(
        {"high": [100, 104.5], "low": [100, 98.5], "close": [100, 102]},
        index=pd.date_range("2026-01-05 09:15", periods=2, freq="15min"),
    )
    trade = Trade("TEST", "long", "reclaim", bars.index[0], 100, 99, 104, 1.0)
    resolve_forward(bars, 0, trade, 5)
    assert trade.outcome == "stop"
    assert not trade.hit_2r and not trade.hit_3r and not trade.hit_4r


def test_2r_and_3r_touch_before_later_stop_are_exact_wins():
    bars = pd.DataFrame(
        {"high": [100, 103.1, 101], "low": [100, 99.5, 98.5], "close": [100, 102, 99]},
        index=pd.date_range("2026-01-05 09:15", periods=3, freq="15min"),
    )
    trade = Trade("TEST", "long", "reclaim", bars.index[0], 100, 99, 104, 1.0)
    resolve_forward(bars, 0, trade, 5)
    assert trade.outcome == "stop"
    assert trade.hit_2r and trade.hit_3r and not trade.hit_4r
    assert trade.reached_3r_at == bars.index[1]


def test_fit_uses_embargoes_final_test_and_exact_groups(tmp_path):
    model = fit(_history(), embargo_sessions=5)
    assert model.development_end < model.validation_start < model.validation_end
    assert model.validation_end < model.final_start <= model.final_end
    assert model.approved

    segment = "selective|medium|open"
    features = np.array([1, 1 / 1.5, 0, .8, .8, .6, .04, 0, 0])
    two = model.predict(
        setup="reclaim", admitted=True, stop_pct=1.0, target_r=2,
        features=features, segment=segment,
    )
    assert two.accepted
    assert two.n >= 100 and two.lower >= 0.60

    destination = tmp_path / "model.json"
    save(model, destination)
    restored = load(destination)
    assert restored.strategies["reclaim:2"].final.approved


def test_unrelated_setup_is_refused_but_research_segment_does_not_veto_fixed_rule():
    model = fit(_history(), embargo_sessions=5)
    unseen_setup = model.predict(
        setup="base-breakout", admitted=True, stop_pct=1.0, target_r=4,
        segment="selective|medium|open",
    )
    unseen_group = model.predict(
        setup="reclaim", admitted=True, stop_pct=1.0, target_r=2,
        segment="aggressive|medium|open",
    )
    assert not unseen_setup.accepted
    assert "no independently tested model" in unseen_setup.reason
    assert unseen_group.accepted


def test_old_probability_model_is_refused(tmp_path):
    path = tmp_path / "old.json"
    path.write_text('{"version": 2}', encoding="utf-8")
    with pytest.raises(ValueError, match="recalibration"):
        load(path)


def test_only_one_candidate_is_published_using_best_conservative_profit(monkeypatch):
    from types import SimpleNamespace

    import asymmetry.v3_probability as probability
    from asymmetry.engines.carry import CarryState
    from asymmetry.engines.setups import SetupSignal
    from asymmetry.engines.v3 import V3Plan
    from asymmetry.engines.v3_scan import V3Candidate, V3Scan
    from asymmetry.spec import SetupType

    def candidate(symbol, values):
        plan = V3Plan(
            direction="long", entry=100, stop=99, stop_pct=1, risk=1, target=104,
            target_pct=4, quantity=10, invalidation="fixed", setup=SetupType.RECLAIM,
            trigger_bar=pd.Timestamp("2026-09-10 10:00"), entry_min=99, entry_max=101,
        )
        item = V3Candidate(
            symbol=symbol, direction="long", plan=plan, score=80,
            setup=SetupSignal(kind=SetupType.RECLAIM, found=True, quality=80),
            carry=CarryState(passes=True),
        )
        for target_r, conservative in zip((2, 3, 4), values):
            setattr(item, f"probability_{target_r}r", SimpleNamespace(
                target_r=target_r, accepted=True, conservative_net_r=conservative,
                reason="passed",
            ))
        return item

    strongest = candidate("BEST", (0.50, 0.90, 0.70))
    other = candidate("OTHER", (0.40, 0.60, 0.55))
    scan = V3Scan(as_of="2026-09-10", trades=[other, strongest])
    monkeypatch.setattr(probability, "annotate_candidate", lambda *_args, **_kwargs: None)
    probability.apply_to_scan(scan, probability.ProbabilityModel(), strict=True)
    assert [item.symbol for item in scan.trades] == ["BEST"]
    assert strongest.recommended_reward_risk == 2
    assert strongest.recommended_target == 102
    assert other.rejected_by == "daily cap"


def test_market_hours_are_explicitly_ist():
    assert market_is_open(datetime.fromisoformat("2026-09-10T10:00:00+05:30"))
    assert not market_is_open(datetime.fromisoformat("2026-09-10T16:00:00+05:30"))
    assert not market_is_open(datetime.fromisoformat("2026-09-13T10:00:00+05:30"))


def test_trigger_must_be_recent_and_from_today():
    now = datetime.fromisoformat("2026-09-10T11:00:00+05:30")
    assert trigger_is_fresh(pd.Timestamp("2026-09-10T10:45:00+05:30"), now)
    assert not trigger_is_fresh(pd.Timestamp("2026-09-10T10:15:00+05:30"), now)
    assert not trigger_is_fresh(pd.Timestamp("2026-09-09T15:15:00+05:30"), now)
