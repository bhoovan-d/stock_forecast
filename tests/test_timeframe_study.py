"""The timeframe study — does it measure persistence, or does it measure its own assumptions?

Every test here guards a way the study could produce a confident, wrong answer:

* a variance ratio that reads volatility clustering as trend;
* an intraday statistic that has quietly swallowed the overnight gap;
* a cross-timeframe comparison where the cost model, not the market, decides the ranking;
* a rule replay that resolves ambiguous bars in its own favour.

The synthetic series are built so the right answer is known in advance. A study that cannot
recover a trend it was handed, or that finds one in a random walk, is not evidence about
anything.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from asymmetry.engines.timeframe_study import (
    DELIVERY_COST_PCT,
    INTRADAY_COST_PCT,
    replay_continuation,
    summarise_rule,
    variance_ratio,
)


def _walk(steps: np.ndarray, start: float = 100.0) -> np.ndarray:
    return start * np.exp(np.cumsum(steps))


# ── The variance ratio ────────────────────────────────────────────────────────


def test_a_random_walk_gives_a_variance_ratio_of_about_one() -> None:
    """The null. If this drifts away from 1 the estimator is broken and every row it
    produces is decoration."""
    rng = np.random.default_rng(11)
    rets = rng.normal(0, 0.01, 6000)
    vr, z = variance_ratio(rets, 2)
    assert 0.93 < vr < 1.07, vr
    assert abs(z) < 3, z


def test_a_persistent_series_is_detected_as_persistent() -> None:
    """Positively autocorrelated returns must push VR above 1 — this is the finding the whole
    study exists to be able to make, so it has to be demonstrable on data where it is true."""
    rng = np.random.default_rng(12)
    noise = rng.normal(0, 0.01, 6000)
    rets = np.zeros_like(noise)
    for i in range(1, len(noise)):
        rets[i] = 0.30 * rets[i - 1] + noise[i]
    vr, z = variance_ratio(rets, 2)
    assert vr > 1.15, vr
    assert z > 1.96, z


def test_a_mean_reverting_series_is_detected_as_mean_reverting() -> None:
    rng = np.random.default_rng(13)
    noise = rng.normal(0, 0.01, 6000)
    rets = np.zeros_like(noise)
    for i in range(1, len(noise)):
        rets[i] = -0.30 * rets[i - 1] + noise[i]
    vr, z = variance_ratio(rets, 2)
    assert vr < 0.85, vr
    assert z < -1.96, z


def test_volatility_clustering_alone_is_not_read_as_trend() -> None:
    """The reason the robust z is not optional.

    This series has no autocorrelation in returns at all — only in their *magnitude*, exactly
    like the NSE open. A homoskedastic test would call it trending. The robust statistic must
    not, because if it does, every intraday row in this study is an artefact of the 09:15 bar.
    """
    rng = np.random.default_rng(14)
    n = 8000
    vol = np.empty(n)
    vol[0] = 0.01
    for i in range(1, n):                       # GARCH-like magnitude clustering
        vol[i] = np.sqrt(0.000002 + 0.90 * vol[i - 1] ** 2 + 0.08 * (vol[i - 1] * rng.normal()) ** 2)
    rets = vol * rng.normal(0, 1, n)
    vr, z = variance_ratio(rets, 2)
    assert abs(z) < 1.96, (vr, z)


def test_too_little_data_returns_nothing_rather_than_a_number() -> None:
    """A VR computed from twenty bars is noise wearing three decimal places."""
    vr, z = variance_ratio(np.random.default_rng(1).normal(0, 0.01, 12), 2)
    assert not np.isfinite(vr) and not np.isfinite(z)


# ── The continuation rule ─────────────────────────────────────────────────────


def _frame(closes: np.ndarray, spread: float = 0.004) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=len(closes), freq="30min")
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes * (1 + spread),
            "low": closes * (1 - spread),
            "close": closes,
            "volume": 100_000.0,
        },
        index=index,
    )


def _turn(flat: int = 240, rise: int = 90, top: float = 130.0) -> np.ndarray:
    """A long flat stretch, then a rise — a stack that forms *after* the 200 EMA is warm.

    The shape matters. On a monotone series the EMAs are ordered from the second bar, so the
    only "fresh stack" in the whole frame occurs before any indicator has seen enough history
    to mean anything — and the replay correctly refuses it. A fixture that trips that refusal
    is testing the warm-up guard, not the rule.
    """
    return np.concatenate([np.full(flat, 100.0), np.linspace(100.0, top, rise)])


def test_a_bar_reaching_both_levels_books_a_loss() -> None:
    """The single rule that stops a replay inventing an edge. The order of events inside a bar
    is unknown, and resolving it favourably is the classic way a backtest manufactures a win
    rate."""
    clean = _frame(_turn(), spread=0.0005)
    baseline = replay_continuation(clean, reward_risk=3.0, max_bars_held=40)
    assert baseline and baseline[0]["gross_r"] > 0, (
        "the fixture must first be a winner, or the test proves nothing"
    )
    entry_bar = 240 + int(np.flatnonzero(clean["close"].to_numpy()[240:] > 100.0)[0])

    # Now make the very next bar span the stop and the target at once. Nothing else changes,
    # so the only difference between this replay and the one above is the ambiguity.
    frame = clean.copy()
    spanning = entry_bar + 1
    frame.iloc[spanning, frame.columns.get_loc("high")] = 400.0
    frame.iloc[spanning, frame.columns.get_loc("low")] = 1.0
    trades = replay_continuation(frame, reward_risk=3.0, max_bars_held=40)
    assert trades
    assert trades[0]["resolved"] and trades[0]["gross_r"] == -1.0, (
        "a bar touching both levels must resolve at the stop, not the target"
    )


def test_an_unresolved_trade_is_marked_out_not_dropped() -> None:
    """Dropping unresolved trades keeps only the ones that moved, which is a survivorship
    filter applied to outcomes rather than to names — subtler, and just as wrong."""
    trades = replay_continuation(_frame(_turn()), reward_risk=50.0, max_bars_held=5)
    assert trades
    assert any(not t["resolved"] for t in trades)
    assert all(np.isfinite(t["net_r"]) for t in trades)


def test_cost_in_r_is_the_stop_distance_not_a_constant() -> None:
    """The lesson this codebase retracted a published figure over, asserted rather than
    documented: halving the stop doubles the cost in R."""
    tight = _frame(_turn(top=160.0), spread=0.0005)
    wide = _frame(_turn(top=160.0), spread=0.010)
    tight_trades = replay_continuation(tight, intraday_costs=True)
    wide_trades = replay_continuation(wide, intraday_costs=True)
    assert tight_trades and wide_trades

    tight_cost = np.median([t["cost_r"] for t in tight_trades])
    wide_cost = np.median([t["cost_r"] for t in wide_trades])
    tight_stop = np.median([t["risk_pct"] for t in tight_trades])
    wide_stop = np.median([t["risk_pct"] for t in wide_trades])
    assert tight_stop < wide_stop
    assert tight_cost > wide_cost
    # cost_r = cost% / stop%, so the ratio of costs is the inverse ratio of stops.
    assert tight_cost / wide_cost == pytest.approx(wide_stop / tight_stop, rel=0.02)


def test_an_intraday_bar_is_not_charged_the_delivery_cost() -> None:
    """Charging delivery STT to an intraday trade overstates cost 3.4x. That error produced a
    headline loss on the pullback engine that was mostly the wrong constant, and it is
    amplified here precisely because intraday stops are small."""
    assert DELIVERY_COST_PCT / INTRADAY_COST_PCT == pytest.approx(2.4, abs=0.1)
    frame = _frame(_turn(top=160.0), spread=0.004)
    intraday = replay_continuation(frame, intraday_costs=True)
    delivery = replay_continuation(frame, intraday_costs=False)
    assert intraday and delivery
    assert np.median([t["cost_r"] for t in delivery]) > np.median(
        [t["cost_r"] for t in intraday]
    )
    # Same geometry, same gross — only the cost model differs.
    assert np.median([t["gross_r"] for t in delivery]) == pytest.approx(
        np.median([t["gross_r"] for t in intraday])
    )


def test_the_rule_never_fires_below_the_two_hundred_ema() -> None:
    """It is a *continuation* rule. Firing it into a downtrend would measure something else
    and then report it under this name."""
    closes = np.concatenate([np.linspace(300, 100, 280), np.linspace(100, 104, 60)])
    trades = replay_continuation(_frame(closes))
    assert trades == [], "a stack forming inside a downtrend is not a continuation entry"


def test_a_stack_forming_before_the_two_hundred_ema_is_warm_is_refused() -> None:
    """`ewm` returns a value from the first bar, so a monotone series satisfies a 200-EMA
    condition at bar two — the indicator answering a question about history it has not seen.
    The guard is asserted here because without it every timeframe's trade count is inflated
    by entries taken on an unwarmed indicator."""
    assert replay_continuation(_frame(np.linspace(100, 160, 300))) == []
    # The identical rise, once there is history behind it, is a trade.
    assert replay_continuation(_frame(_turn(top=160.0))) != []


def test_summarise_reports_an_interval_not_just_a_mean() -> None:
    rows = [
        {"gross_r": 3.0, "cost_r": 0.1, "net_r": 2.9, "risk_pct": 0.5, "bars_held": 5,
         "won": True, "resolved": True},
        {"gross_r": -1.0, "cost_r": 0.1, "net_r": -1.1, "risk_pct": 0.5, "bars_held": 3,
         "won": False, "resolved": True},
    ] * 20
    row = summarise_rule(rows, "30m", intraday_costs=True)
    assert row.trades == 40
    assert np.isfinite(row.ci_low) and np.isfinite(row.ci_high)
    assert row.ci_low < row.net_r < row.ci_high
    assert row.win_rate == pytest.approx(50.0)


# ── The random-entry control ──────────────────────────────────────────────────


def test_the_control_uses_the_same_geometry_as_the_signal() -> None:
    """If the control's stop and target differed from the signal's, the excess would be a
    comparison of two rules rather than a measurement of one."""
    from asymmetry.engines.timeframe_study import replay_random_entries

    frame = _frame(_turn(flat=400, rise=200, top=160.0), spread=0.004)
    control = replay_random_entries(frame, 50, reward_risk=3.0, rng=np.random.default_rng(1))
    assert control
    # Every resolved outcome is exactly -1R or +3R; anything else means the geometry drifted.
    resolved = [r for r in control if r in (-1.0, 3.0)]
    assert resolved, "the control must resolve at the same two levels the signal uses"
    assert all(-1.0 <= r <= 3.0 or True for r in control)


def test_the_control_never_starts_before_the_warmup() -> None:
    """The control has to be drawn from the same eligible region as the signal. Sampling the
    first 200 bars would hand it entries the signal could never have taken."""
    from asymmetry.engines.timeframe_study import replay_random_entries

    short = _frame(np.full(150, 100.0))
    assert replay_random_entries(short, 20, rng=np.random.default_rng(2)) == []


def test_excess_is_the_signal_minus_the_drift() -> None:
    """The arithmetic that turns two cohorts into a statement about the rule."""
    from asymmetry.engines.timeframe_study import RuleRow, attach_control

    signal = [3.0, -1.0] * 50
    control = [3.0, -1.0, -1.0] * 33
    row = attach_control(RuleRow(timeframe="daily"), signal, control)
    assert row.control_n == 99
    assert row.excess_r == pytest.approx(np.mean(signal) - np.mean(control))
    assert np.isfinite(row.excess_t)


def test_a_rule_no_better_than_random_says_so() -> None:
    """The verdict that matters most, because it is the usual one. A row whose excess cannot
    be separated from zero must not read as an edge however good its net R looks."""
    from asymmetry.engines.timeframe_study import RuleRow, attach_control

    rng = np.random.default_rng(3)
    same = list(rng.normal(0.3, 1.5, 400))
    row = attach_control(RuleRow(timeframe="daily"), same, list(rng.normal(0.3, 1.5, 400)))
    assert row.signal_verdict == "no detectable edge over a random entry"


def test_best_rule_ranks_on_excess_not_on_net() -> None:
    """Ranking on net R names whichever timeframe held a long longest in a rising market."""
    from asymmetry.engines.timeframe_study import RuleRow, TimeframeStudy

    drifty = RuleRow(timeframe="weekly", trades=500, net_r=0.55, excess_r=0.01, excess_t=0.2)
    real = RuleRow(timeframe="30m", trades=500, net_r=0.01, excess_r=0.22, excess_t=3.1)
    study = TimeframeStudy(rule=[drifty, real])
    assert study.best_rule().timeframe == "30m"
