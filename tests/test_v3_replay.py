from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from asymmetry.engines.v3 import ENTRY_CONFIRMED, ENTRY_STOP_THROUGH
from asymmetry.config import settings
from asymmetry.v3_backtest import (
    Trade,
    completed_daily,
    resolve_forward,
    simulate_entry,
)
from asymmetry.v3_probability import _split


def _bars(rows, start="2026-01-05 09:15"):
    return pd.DataFrame(
        rows,
        index=pd.date_range(start, periods=len(rows["open"]), freq="15min"),
    )


def _plan(rule=ENTRY_CONFIRMED, entry=100, low=99, high=101):
    return SimpleNamespace(entry=entry, entry_rule=rule, entry_min=low, entry_max=high)


def test_current_daily_candle_cannot_change_a_morning_decision_input():
    index = pd.to_datetime(["2026-01-02", "2026-01-05"])
    original = pd.DataFrame(
        {"open": [100, 101], "high": [102, 103], "low": [99, 100],
         "close": [101, 102], "volume": [10, 11]}, index=index,
    )
    changed = original.copy()
    changed.loc[index[-1], ["high", "low", "close", "volume"]] = [999, 1, 700, 1_000_000]
    signal = pd.Timestamp("2026-01-05 10:00")
    pd.testing.assert_frame_equal(completed_daily(original, signal), completed_daily(changed, signal))


def test_confirmed_entry_fills_only_on_next_available_bar():
    bars = _bars({
        "open": [100, 100.4, 100.8], "high": [100.2, 100.8, 101],
        "low": [99.8, 100.1, 100.5], "close": [100, 100.6, 100.9],
    })
    position, fill, reason = simulate_entry(bars, 0, _plan())
    assert position == 1 and fill == 100.4
    assert "next bar" in reason


def test_stop_entry_waits_for_price_and_expires_after_one_hour():
    bars = _bars({
        "open": [99] * 5, "high": [99.5] * 5,
        "low": [98.5] * 5, "close": [99] * 5,
    })
    position, fill, reason = simulate_entry(
        bars, 0, _plan(ENTRY_STOP_THROUGH, entry=100, low=99, high=101)
    )
    assert position is None and fill is None
    assert "expired" in reason


def test_stop_entry_fills_on_touch_but_cancels_an_invalid_gap():
    touched = _bars({
        "open": [99, 99.5], "high": [99.5, 100.2],
        "low": [98.5, 99.2], "close": [99, 100],
    })
    position, fill, _ = simulate_entry(
        touched, 0, _plan(ENTRY_STOP_THROUGH, entry=100, low=99, high=101)
    )
    assert position == 1 and fill == 100

    gap = touched.copy()
    gap.iloc[1, gap.columns.get_loc("open")] = 102
    position, fill, reason = simulate_entry(
        gap, 0, _plan(ENTRY_STOP_THROUGH, entry=100, low=99, high=101)
    )
    assert position is None and fill is None
    assert "outside valid range" in reason


def test_short_stop_entry_uses_the_low_and_preserves_adverse_gap_price():
    touched = _bars({
        "open": [101, 100.5], "high": [101.5, 100.8],
        "low": [100.5, 99.8], "close": [101, 100],
    })
    plan = _plan(ENTRY_STOP_THROUGH, entry=100, low=99, high=101)
    plan.direction = "short"
    position, fill, _ = simulate_entry(touched, 0, plan)
    assert position == 1 and fill == 100

    gap = touched.copy()
    gap.iloc[1, gap.columns.get_loc("open")] = 99.5
    position, fill, _ = simulate_entry(gap, 0, plan)
    assert position == 1 and fill == 99.5


def test_stop_gap_is_booked_at_the_adverse_open():
    bars = _bars({
        "open": [100, 98], "high": [100, 98.5],
        "low": [100, 97.5], "close": [100, 98],
    })
    trade = Trade("T", "long", "reclaim", bars.index[0], 100, 99, 102, 1)
    resolve_forward(bars, 0, trade, 5)
    assert trade.outcome == "stop"
    assert trade.realised_r == -2


def test_order_never_rolls_over_the_session_close():
    bars = _bars({
        "open": [99, 100], "high": [99.5, 101], "low": [98.5, 99], "close": [99, 100],
    }, start="2026-01-05 15:15")
    position, fill, reason = simulate_entry(bars, 0, _plan(ENTRY_STOP_THROUGH))
    assert position is None and fill is None
    assert "session close" in reason


def test_time_exit_requires_a_complete_horizon():
    complete = _bars({
        "open": [100, 100, 100], "high": [100.5, 100.5, 101.5],
        "low": [99.5, 99.5, 99.5], "close": [100, 100.2, 101],
    })
    trade = Trade("T", "long", "reclaim", complete.index[0], 100, 99, 104, 1)
    resolve_forward(complete, 0, trade, 2, require_full_horizon=True)
    assert trade.outcome == "timeout" and trade.resolved_at == complete.index[2]

    truncated = complete.iloc[:2]
    trade = Trade("T", "long", "reclaim", truncated.index[0], 100, 99, 104, 1)
    resolve_forward(truncated, 0, trade, 2, require_full_horizon=True)
    assert trade.outcome == "incomplete" and trade.resolved_at is None


def test_trade_charges_round_trip_costs_and_slippage_once():
    trade = Trade("T", "long", "reclaim", pd.Timestamp("2026-01-05 10:00"),
                  100, 99, 104, 1)
    expected_cost = settings.cost_roundtrip_pct + settings.slippage_pct
    assert trade.cost_r == expected_cost
    trade.realised_r = 2
    assert trade.net_r == 2 - expected_cost


def test_split_purges_outcomes_that_cross_the_next_period():
    days = pd.bdate_range("2025-01-02", periods=80)
    trades = []
    for day in days:
        entered = day + pd.Timedelta(hours=10)
        trades.append(Trade(
            symbol="T", direction="long", setup="reclaim", signal_at=entered,
            entered_at=entered, entry=100, stop=99, target=104, stop_pct=1,
            outcome="stop", resolved_at=entered + pd.Timedelta(hours=1), realised_r=-1,
        ))
    validation_start = days[int(len(days) * .60) - 1 + 1 + 5]
    crossing = trades[int(len(days) * .60) - 1]
    crossing.resolved_at = validation_start + pd.Timedelta(days=1)
    _, development, validation, final = _split(trades, 5)
    assert crossing not in development
    assert max(t.resolved_at.date() for t in development) < min(t.signal_at.date() for t in validation)
    assert max(t.resolved_at.date() for t in validation) < min(t.signal_at.date() for t in final)
