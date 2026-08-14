"""The R:R gate is the system's central discipline — these tests defend it.

An earlier implementation targeted the *furthest* candidate level and measured the base
over 60 days, which produced targets like "the 52-week high, 48% away" and an R:R of
1:12 on a stock in a downtrend. The gate passed everything and meant nothing. These tests
exist so that regression cannot come back silently.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from asymmetry.config import settings
from asymmetry.engines.trade import build_plan


def make_daily(closes: list[float], *, spread: float = 0.01) -> pd.DataFrame:
    # Build from numpy arrays, not Series: passing Series with a RangeIndex alongside a
    # DatetimeIndex makes pandas align them and silently produce an all-NaN frame.
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "high": close * (1 + spread),
            "low": close * (1 - spread),
            "close": close,
        },
        index=pd.date_range("2025-01-01", periods=len(close), freq="B"),
    )


def make_base_breakout(
    rng: np.random.Generator, *, trend_pct: float, base_width: float
) -> pd.DataFrame:
    """An uptrend into a tight consolidation — the setup this engine is built to find.

    Pure random walks almost never produce one (price sits far below its 60-day high while
    the structural stop sits far below that), so a suite built only on random walks would
    emit nothing and prove nothing.
    """
    trend = list(100 * np.exp(np.linspace(0, trend_pct, 200)))
    peak = trend[-1]
    base = [peak * (1 + rng.uniform(-base_width, base_width)) for _ in range(60)]
    return make_daily(trend + base, spread=0.004)


def test_no_plan_below_gate_is_ever_emitted():
    """Whatever the shape of the data, an emitted plan always clears the gate."""
    rng = np.random.default_rng(42)
    emitted = 0
    frames = []

    for _ in range(150):
        drift = rng.normal(0, 0.002)
        steps = rng.normal(drift, rng.uniform(0.005, 0.04), 260)
        frames.append(make_daily(list(100 * np.exp(np.cumsum(steps)))))
    for _ in range(150):
        frames.append(
            make_base_breakout(
                rng, trend_pct=rng.uniform(0.15, 0.6), base_width=rng.uniform(0.01, 0.06)
            )
        )

    for daily in frames:
        plan, reason = build_plan(daily)
        if plan is None:
            assert reason, "a rejection must always explain itself"
            continue

        emitted += 1
        assert plan.reward_risk >= settings.screen_min_reward_risk
        assert plan.stop < plan.entry < plan.target
        # The reported R:R must match the levels it was derived from.
        recomputed = (plan.target - plan.entry) / (plan.entry - plan.stop)
        assert recomputed == pytest.approx(plan.reward_risk, abs=0.02)

    assert emitted > 0, "the gate rejected everything — it is no longer discriminating"


def test_target_is_not_a_fixed_multiple_of_risk():
    """R:R must vary with structure.

    If targets were pinned to a fixed multiple of the stop distance, every plan would show
    the same R and the gate would be tautological — which is exactly the bug this guards.
    """
    rng = np.random.default_rng(7)
    ratios = []
    for _ in range(200):
        plan, _ = build_plan(
            make_base_breakout(
                rng, trend_pct=rng.uniform(0.1, 0.8), base_width=rng.uniform(0.005, 0.08)
            )
        )
        if plan:
            ratios.append(plan.reward_risk)

    assert len(ratios) > 20
    assert len(set(round(r, 1) for r in ratios)) > 3, "R:R shows no structural variation"


def test_target_stays_within_reach():
    """A stock far below its 52-week high must not target that high.

    This is the concrete failure that produced R:R of 1:12: price 408 against a 52-week
    high of 705, i.e. a 48% target treated as reachable.
    """
    # A long decline, then a small base near the lows.
    closes = list(np.linspace(700, 410, 220)) + [410 + (i % 5) for i in range(40)]
    daily = make_daily(closes)

    plan, _ = build_plan(daily)
    if plan is not None:
        move_pct = (plan.target - plan.entry) / plan.entry * 100
        assert move_pct < 25, f"target {move_pct:.1f}% away is not reachable in this horizon"


def test_stop_is_never_absurdly_tight():
    closes = [100 + 0.01 * i for i in range(260)]  # very low volatility drift
    plan, reason = build_plan(make_daily(closes, spread=0.0005))
    if plan is not None:
        assert (plan.entry - plan.stop) / plan.entry >= 0.005


def test_insufficient_history_is_rejected_cleanly():
    plan, reason = build_plan(make_daily([100.0] * 20))
    assert plan is None
    assert "insufficient" in reason
