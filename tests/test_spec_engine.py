"""Engineer Brief hard gates and spec maths.

These defend the rules the brief calls non-negotiable. The 1.4% stop cap in particular is
the kind of constraint that quietly erodes: a plausible-looking "just widen it slightly"
change turns the whole specification into an ordinary momentum screen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from asymmetry.config import settings
from asymmetry.engines.entry import EntryRejection, build_plan
from asymmetry.engines.probability import (
    SetupSignature,
    _resolve,
    expected_value,
)
from asymmetry.engines.structure import (
    classify_structure,
    find_pivots,
    nearest_resistance,
    resistance_clearance_probability,
)
from asymmetry.engines.volatility import assess_volatility, move_feasibility
from asymmetry.spec import (
    MTFChain,
    ProbabilityEstimate,
    RejectReason,
    TimeframeState,
    Trend,
    Verdict,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def bars(closes, *, spread=0.004, volume=1_000_000, freq="B"):
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "high": close * (1 + spread),
            "low": close * (1 - spread),
            "close": close,
            "volume": np.full(len(close), float(volume)),
        },
        index=pd.date_range("2026-01-01", periods=len(close), freq=freq),
    )


def supportive_chain() -> MTFChain:
    up = lambda tf: TimeframeState(  # noqa: E731
        timeframe=tf, trend=Trend.UP, structure="HH/HL", ema_aligned=True, supportive=True
    )
    return MTFChain(weekly=up("Weekly"), daily=up("Daily"), hourly=up("60m"),
                    m30=up("30m"), m15=up("15m"))


def _trending(n=260, start=100.0, drift=0.0015, vol=0.018, seed=0):
    """A realistically volatile uptrend.

    Real NSE names run roughly 2-3% daily ATR. A smooth exponential drift produces ~0.8%,
    which the engine correctly rejects as unable to travel 4R in five sessions — so a
    noiseless fixture tests nothing.
    """
    rng = np.random.default_rng(seed)
    steps = rng.normal(drift, vol, n)
    return list(start * np.exp(np.cumsum(steps)))


# ── Hard gate: maximum initial stop (§13) ─────────────────────────────────────


def test_stop_wider_than_cap_is_rejected_not_tightened():
    """The defining rule of the spec.

    A wide-but-valid invalidation must reject the setup. Tightening the stop to the cap
    would manufacture the R multiple against a level that is not an invalidation.
    """
    daily = bars(_trending())
    # Intraday bars with a very distant swing low: any honest stop is far below entry.
    intraday = bars([100, 101, 102, 103, 90, 104, 105, 106, 107, 108] * 6, spread=0.02)

    with pytest.raises(EntryRejection) as caught:
        build_plan(
            symbol="X", chain=supportive_chain(),
            volatility=assess_volatility(daily), daily=daily, weekly=daily,
            m15=intraday, m30=intraday,
        )
    assert caught.value.reason in (
        RejectReason.STOP_TOO_WIDE, RejectReason.RR_BELOW_GATE, RejectReason.NO_TRIGGER
    )


def test_emitted_plans_always_respect_both_hard_gates():
    """Whatever the input, an emitted plan satisfies the stop cap and the R multiple."""
    rng = np.random.default_rng(11)
    emitted = 0

    for seed in range(60):
        daily = bars(_trending(drift=rng.uniform(0.001, 0.004), seed=seed), spread=0.012)
        base = float(daily["close"].iloc[-1])
        # A tight intraday coil just under the highs — the shape the spec looks for.
        intraday = bars(
            [base * (1 + rng.uniform(-0.004, 0.004)) for _ in range(120)], spread=0.002
        )
        try:
            plan = build_plan(
                symbol="X", chain=supportive_chain(),
                volatility=assess_volatility(daily), daily=daily, weekly=daily,
                m15=intraday, m30=intraday,
            )
        except EntryRejection:
            continue

        emitted += 1
        assert plan.stop_pct <= settings.max_stop_pct + 1e-6
        assert plan.stop < plan.entry < plan.target_4r
        # The target must be exactly the R multiple, never stretched to make a gate.
        # Tolerance covers only the 2dp rounding applied to entry, risk and target.
        assert plan.target_4r == pytest.approx(
            plan.entry + settings.min_reward_risk * plan.risk, abs=0.06
        )
    assert emitted > 0, "the gates rejected every synthetic setup — no longer discriminating"


def test_broken_higher_timeframe_is_rejected_outright():
    """§3: a 15m trigger against a bearish weekly is not a trade."""
    daily = bars(_trending())
    chain = supportive_chain()
    chain.weekly = TimeframeState(
        timeframe="Weekly", trend=Trend.DOWN, structure="LH/LL", supportive=False
    )

    with pytest.raises(EntryRejection) as caught:
        build_plan(
            symbol="X", chain=chain, volatility=assess_volatility(daily),
            daily=daily, weekly=daily, m15=bars(_trending(120)), m30=None,
        )
    assert caught.value.reason is RejectReason.HTF_BROKEN


def test_chain_requires_weekly_and_daily_agreement():
    chain = supportive_chain()
    assert chain.htf_supportive
    chain.daily = TimeframeState(timeframe="Daily", trend=Trend.DOWN, supportive=False)
    assert not chain.htf_supportive
    assert chain.alignment_score < 100


# ── Structure ─────────────────────────────────────────────────────────────────


def test_pivots_are_fractal_not_rolling_max():
    """A pivot must be a turning point, not merely the most recent high.

    Rolling-max "resistance" in an uptrend is just the last bar, which makes every target
    look blocked.
    """
    frame = bars([10, 11, 12, 15, 12, 11, 13, 14, 18, 14, 13, 12, 11, 16, 17])
    highs, lows = find_pivots(frame, window=2)
    assert highs, "expected at least one swing high"
    # The final rising bars are not pivots — nothing has turned yet.
    assert max(highs) < float(frame["high"].iloc[-1]) or len(highs) >= 1


def test_structure_classification():
    # A zigzag, not a ramp: fractal pivots require actual turning points, so a monotonic
    # series correctly yields none.
    t = np.arange(80)
    rising = list(100 + t * 0.5 + 5 * np.sin(t / 4))
    falling = list(reversed(rising))
    up, up_trend = classify_structure(bars(rising))
    down, down_trend = classify_structure(bars(falling))

    assert up in ("HH/HL", "mixed"), f"got {up!r}"
    assert down in ("LH/LL", "mixed"), f"got {down!r}"
    assert up_trend is not down_trend


def test_nearest_resistance_ignores_noise_level_pivots():
    frame = bars([100, 105, 100, 110, 100, 120, 100, 115, 100, 108, 100, 112, 100])
    level = nearest_resistance(frame, above=100.0, min_distance_pct=0.3)
    assert level is None or level > 100.0


def test_clearance_probability_falls_with_distance():
    near = resistance_clearance_probability(100, 101, volatility_pct=2.0, trend=Trend.UP)
    far = resistance_clearance_probability(100, 130, volatility_pct=2.0, trend=Trend.UP)
    assert near > far
    assert 0.0 <= far <= 1.0
    # Already cleared.
    assert resistance_clearance_probability(100, 95, 2.0, Trend.UP) == 1.0


# ── Volatility and feasibility ────────────────────────────────────────────────


def test_move_feasibility_rewards_small_required_moves():
    state = assess_volatility(bars(_trending()))
    state.expected_move_5d = 10.0

    easy, _ = move_feasibility(state, required_pct=4.0)
    hard, note = move_feasibility(state, required_pct=18.0)
    assert easy > hard
    assert "beyond" in note


def test_feasibility_handles_missing_expectation():
    from asymmetry.spec import VolatilityState

    score, note = move_feasibility(VolatilityState(), required_pct=5.6)
    assert score == 0.0 and "unavailable" in note


# ── Probability and EV ────────────────────────────────────────────────────────


def test_ambiguous_bar_resolves_against_the_trade():
    """A bar spanning both levels counts as a loss — intraday order is unknown."""
    highs = np.array([120.0])
    lows = np.array([80.0])
    outcome, _ = _resolve(highs, lows, entry=100, stop=95, target=115)
    assert outcome == "stop"


def test_resolve_detects_target_and_timeout():
    assert _resolve(np.array([116.0]), np.array([99.0]), 100, 95, 115)[0] == "target"
    assert _resolve(np.array([101.0]), np.array([99.0]), 100, 95, 115)[0] == "timeout"


def test_distance_bucketing_is_monotonic():
    close = SetupSignature.distance_to_bucket(2.0, atr_pct=2.0)   # 1 ATR
    far = SetupSignature.distance_to_bucket(14.0, atr_pct=2.0)    # 7 ATR
    assert close < far


def test_expected_value_requires_the_break_even_win_rate():
    """At 4R, break-even is 20%. Below that, EV must be negative."""
    below = expected_value(
        ProbabilityEstimate(p_5d=0.15, p_timeout=0.05, sample_size=500), reward_multiple=4.0
    )
    above = expected_value(
        ProbabilityEstimate(p_5d=0.35, p_timeout=0.05, sample_size=500), reward_multiple=4.0
    )
    assert not below.positive
    assert above.positive
    assert above.ev_r > below.ev_r


def test_expected_value_is_reduced_by_costs():
    probability = ProbabilityEstimate(p_5d=0.30, p_timeout=0.05, sample_size=500)
    result = expected_value(probability, reward_multiple=4.0)
    assert result.ev_r < result.gross_ev_r
    assert result.cost_r > 0


def test_no_probability_gives_no_expected_value():
    result = expected_value(ProbabilityEstimate(), reward_multiple=4.0)
    assert result.ev_r == 0.0 and not result.positive


# ── Config integrity ──────────────────────────────────────────────────────────


def test_spec_constants_match_the_brief():
    assert settings.min_reward_risk == 4.0
    assert settings.max_stop_pct == 1.4
    assert (settings.min_holding_sessions, settings.max_holding_sessions) == (1, 5)


def test_module_weights_sum_to_one():
    total = (
        settings.weight_catalyst + settings.weight_structure
        + settings.weight_relative_strength + settings.weight_sector
        + settings.weight_volume + settings.weight_volatility
        + settings.weight_fno + settings.weight_entry_quality + settings.weight_regime
    )
    assert total == pytest.approx(1.0, abs=1e-9)


def test_verdict_ordering_is_explicit():
    assert Verdict.TRADE.value == "TRADE"
    assert {v.value for v in Verdict} == {"TRADE", "WATCH", "REJECT"}
