"""The kill-zone trident strategy.

These tests defend the four claims that would be most expensive to get wrong, and each of
them is a rule the source states explicitly rather than a preference of mine:

* the doji is the setup — a full-bodied candle at the same level is an *invalidation*;
* the confirming candle must close below the doji's high;
* the pattern outside the kill zone is not the pattern ("without time, price action means
  absolutely nothing");
* the daily state is read from the bars that had already printed, never from the finished
  daily candle the setup fires inside.

Plus the two accounting properties that decide whether any measurement of this is honest at
all: a gap through the stop must cost more than 1R, and cost in R must scale inversely with
stop distance. That second one is the lesson this codebase has already had to retract a
published figure over.
"""

from __future__ import annotations

from datetime import date, time

import pandas as pd
import pytest

from asymmetry.engines.trident import (
    TridentSettings,
    TridentSignal,
    _cost_r,
    classify_daily_candle,
    daily_bias_ok,
    detect_trident_setup,
    resolve_trade,
)

SESSION = date(2026, 8, 20)
BARS_PER_SESSION = 13  # 12 thirty-minute windows plus the closing print


def daily_frame(days: int = 140, start: float = 60.0, step: float = 0.3,
                volume: float = 1_000_000.0) -> pd.DataFrame:
    """A steadily rising daily series ending the day before SESSION.

    Rising rather than flat so the 200 EMA sits below price and the Bollinger position is
    high — otherwise every fixture would be rejected by the bias gate before reaching the
    geometry under test.
    """
    index = pd.bdate_range(end=pd.Timestamp(SESSION) - pd.Timedelta(days=1), periods=days)
    closes = [start + step * i for i in range(days)]
    return pd.DataFrame(
        [
            {"open": c - step / 2, "high": c + step, "low": c - step, "close": c,
             "volume": volume}
            for c in closes
        ],
        index=index,
    )


def session_bars(day: pd.Timestamp, rows: list[tuple[float, float, float, float]],
                 volume: float) -> pd.DataFrame:
    """OHLC bars stamped with their start, from 09:15 at 30-minute steps.

    A flat `date_range` at 30min would run past 15:30 into the next morning, which would put
    a "kill zone" bar at 04:45 and make the window test meaningless.
    """
    index = [day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=30 * i)
             for i in range(len(rows))]
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": volume}
         for o, h, lo, c in rows],
        index=pd.DatetimeIndex(index),
    )


def warmup_sessions(last_close: float, sessions: int = 4) -> pd.DataFrame:
    """Prior 30m sessions that leave the 5/9/13/21 EMAs stacked and rising.

    The EMA stack is checked on the confirming bar, so without a warm-up the stack is NaN
    and every fixture fails for the wrong reason.
    """
    frames, price = [], last_close - 0.30 * sessions * BARS_PER_SESSION
    day = pd.Timestamp(SESSION) - pd.Timedelta(days=sessions + 2)
    made = 0
    while made < sessions:
        if day.weekday() < 5:
            rows = []
            for _ in range(BARS_PER_SESSION):
                nxt = price + 0.30
                rows.append((price, nxt + 0.05, price - 0.05, nxt))
                price = nxt
            frames.append(session_bars(day, rows, volume=100_000.0))
            made += 1
        day += pd.Timedelta(days=1)
    return pd.concat(frames)


def trident_session(
    *,
    doji_body: bool = False,
    confirm_above_high: bool = False,
    shift_out_of_killzone: bool = False,
    volume: float = 260_000.0,
) -> pd.DataFrame:
    """One session containing the whole pattern, with three switchable defects.

    Bars 0-2 leave a fair value gap (bar 2's low prints above bar 0's high), bar 3 retraces
    into its 50% as a doji, bar 4 confirms below the doji high.
    """
    warm = warmup_sessions(100.0)
    # 0,1,2: the imbalance. bar0 high 100.2, bar2 low 101.0 -> gap 100.2-101.0, 50% = 100.6
    rows = [
        (100.0, 100.2, 99.9, 100.15),
        (100.2, 101.4, 100.2, 101.35),   # displacement
        (101.35, 101.9, 101.00, 101.8),
    ]
    # bar 3: back into the 50% at 100.6 and closing above it.
    if doji_body:
        # Same wick, same close-above — but the body fills the bar. His invalidation.
        rows.append((101.75, 101.8, 100.50, 100.75))
    else:
        rows.append((101.10, 101.60, 100.50, 101.15))
    doji_high, doji_low = rows[3][1], rows[3][2]
    # bar 4: the confirmation.
    if confirm_above_high:
        rows.append((101.20, doji_high + 0.60, doji_low + 0.30, doji_high + 0.50))
    else:
        rows.append((101.15, doji_high - 0.05, doji_low + 0.20, doji_high - 0.10))
    # Fill the rest of the session so the day has its full complement of bars.
    while len(rows) < BARS_PER_SESSION:
        last = rows[-1][3]
        rows.append((last, last + 0.10, last - 0.10, last + 0.05))

    if shift_out_of_killzone:
        # Same bars, moved wholesale past the window. "This setup means nothing without
        # the time" — so the identical geometry must stop being a setup.
        head = [(100.0, 100.05, 99.95, 100.0)] * 8
        rows = head + rows[:BARS_PER_SESSION - 8]

    return pd.concat([warm, session_bars(pd.Timestamp(SESSION), rows, volume)])


def test_detects_the_full_pattern() -> None:
    """The fixture is a setup, so the geometry the other tests break must first work."""
    signal = detect_trident_setup(
        trident_session(), daily_frame(), SESSION, TridentSettings(), symbol="TEST"
    )
    assert signal.found, signal.note
    assert signal.doji_at.time() == time(10, 45)
    assert signal.entry_at.time() == time(11, 15)
    # Stop is the doji's low, never anything more convenient.
    assert signal.stop == pytest.approx(100.50, abs=0.01)
    assert signal.target == pytest.approx(
        signal.entry + 20 * (signal.entry - signal.stop), abs=0.01
    )
    assert signal.daily_state == "green"


def test_full_body_at_the_level_is_an_invalidation() -> None:
    """"Say this wasn't a doji, the body of this candle was in here — this would be an
    invalidation."

    The wick and the reclaim are identical; only the body changes. If this ever starts
    returning a setup, the strategy has quietly become "any retrace to the 50%", which is a
    different and much more common pattern.
    """
    signal = detect_trident_setup(
        trident_session(doji_body=True), daily_frame(), SESSION, TridentSettings()
    )
    assert not signal.found
    assert "doji" in signal.rejected_by


def test_confirmation_above_the_doji_high_is_refused() -> None:
    """"If it closes above the high I'll invalidate the trade."

    It is a geometry rule, not a momentum one: closing above the doji high puts entry far
    from its own stop, and 1:20 only exists while the two are adjacent.
    """
    signal = detect_trident_setup(
        trident_session(confirm_above_high=True), daily_frame(), SESSION, TridentSettings()
    )
    assert not signal.found
    assert signal.rejected_by == "confirmation closed above the doji high"


def test_the_same_pattern_outside_the_kill_zone_is_not_a_setup() -> None:
    """Time is the first filter in the source and the easiest one to quietly drop."""
    cfg = TridentSettings()
    assert detect_trident_setup(trident_session(), daily_frame(), SESSION, cfg).found
    moved = detect_trident_setup(
        trident_session(shift_out_of_killzone=True), daily_frame(), SESSION, cfg
    )
    assert not moved.found


def test_daily_state_cannot_see_the_finished_daily_candle() -> None:
    """The setup fires *inside* the daily candle, so reading that candle is reading ahead.

    Appending a huge green bar stamped on the session date must change nothing: the bands
    come from completed prior days and the state comes from the partial passed in.
    """
    cfg = TridentSettings()
    prior = daily_frame()
    partial = {"open": 101.0, "close": 100.2, "volume": 5e5, "elapsed_fraction": 0.4}

    without = classify_daily_candle(prior, SESSION, partial, cfg)
    future = pd.DataFrame(
        [{"open": 100.0, "high": 190.0, "low": 99.0, "close": 185.0, "volume": 9e9}],
        index=pd.DatetimeIndex([pd.Timestamp(SESSION)]),
    )
    with_future = classify_daily_candle(pd.concat([prior, future]), SESSION, partial, cfg)

    assert without == with_future
    assert without in ("red", "black")  # the partial is down on the day, whatever follows


def test_daily_bias_uses_only_completed_days() -> None:
    """Same property on the 200 EMA gate — a monstrous same-day bar must not flip it."""
    cfg = TridentSettings()
    falling = daily_frame(days=140, start=100.0, step=-0.3)
    assert not daily_bias_ok(falling, SESSION, cfg)[0]

    spike = pd.DataFrame(
        [{"open": 60.0, "high": 400.0, "low": 59.0, "close": 395.0, "volume": 9e9}],
        index=pd.DatetimeIndex([pd.Timestamp(SESSION)]),
    )
    assert not daily_bias_ok(pd.concat([falling, spike]), SESSION, cfg)[0]


def test_a_gap_through_the_stop_costs_more_than_one_r() -> None:
    """Losses are not capped at 1R on a multi-week hold, and pretending they are is how a
    backtest keeps the 20R upside while quietly insuring the downside.
    """
    cfg = TridentSettings()
    signal = TridentSignal(
        symbol="TEST", found=True, entry_at=pd.Timestamp(f"{SESSION} 11:15"),
        entry=100.0, stop=99.0, target=120.0, risk_pct=1.0,
    )
    empty_intraday = session_bars(pd.Timestamp(SESSION), [(100.0, 100.1, 99.5, 100.0)], 1e5)
    gapped = pd.DataFrame(
        [{"open": 94.0, "high": 95.0, "low": 93.0, "close": 94.5, "volume": 1e6}],
        index=pd.DatetimeIndex([pd.Timestamp(SESSION) + pd.Timedelta(days=1)]),
    )

    trade = resolve_trade(empty_intraday, gapped, signal, cfg)
    assert trade.outcome == "stop"
    assert trade.gapped is True
    assert trade.realised_r == pytest.approx(-6.0)   # (94 - 100) / 1


def test_a_bar_spanning_both_levels_books_a_loss() -> None:
    """The order of events inside a bar is unknown; resolving it favourably is the classic
    way a backtest invents an edge. Same rule V3 uses."""
    cfg = TridentSettings()
    signal = TridentSignal(
        symbol="TEST", found=True, entry_at=pd.Timestamp(f"{SESSION} 11:15"),
        entry=100.0, stop=99.0, target=120.0, risk_pct=1.0,
    )
    empty_intraday = session_bars(pd.Timestamp(SESSION), [(100.0, 100.1, 99.5, 100.0)], 1e5)
    both = pd.DataFrame(
        [{"open": 100.0, "high": 125.0, "low": 98.0, "close": 124.0, "volume": 1e6}],
        index=pd.DatetimeIndex([pd.Timestamp(SESSION) + pd.Timedelta(days=1)]),
    )

    trade = resolve_trade(empty_intraday, both, signal, cfg)
    assert trade.outcome == "stop"
    assert trade.realised_r == pytest.approx(-1.0)


def test_cost_in_r_scales_inversely_with_stop_distance() -> None:
    """The lesson a published figure had to be retracted over: cost in R is cost% / stop%.

    Halving the stop doubles the cost in R. At 20R it is a rounding error; at the 0.2% stops
    the pullback engine produces it is the entire result. Both facts follow from this one
    line, which is why it is asserted rather than assumed.
    """
    cfg = TridentSettings()
    assert _cost_r(1.0, cfg) == pytest.approx(cfg.cost_roundtrip_pct + cfg.slippage_pct)
    assert _cost_r(0.5, cfg) == pytest.approx(2 * _cost_r(1.0, cfg))
    assert _cost_r(0.0, cfg) == 0.0


def test_costs_are_not_inherited_from_v3() -> None:
    """A cost constant is calibrated for a holding period. This one is derived in the module
    from delivery rates and must stay independent of `settings`, so that retuning V3 cannot
    silently retune this — the failure mode `spec_engine` and `hma_pullback` both exist to
    avoid."""
    from asymmetry.config import settings

    # Same *value* as V3 today, by derivation rather than by reference: a 20R target off a
    # sub-1% stop is a multi-week hold and therefore pays delivery STT too. The point of the
    # test is that changing one must not move the other.
    assert TridentSettings().cost_roundtrip_pct == pytest.approx(settings.cost_roundtrip_pct)
    settings.cost_roundtrip_pct = 0.9
    try:
        assert TridentSettings().cost_roundtrip_pct != 0.9
    finally:
        settings.cost_roundtrip_pct = 0.12


def _signal(symbol: str, entry_at: str, entry: float, stop: float, rr: float = 4.0):
    risk = entry - stop
    return TridentSignal(
        symbol=symbol, found=True, entry_at=pd.Timestamp(entry_at), entry=entry,
        stop=stop, target=entry + rr * risk, risk_pct=risk / entry * 100,
    )


def test_rerecording_the_same_scan_does_not_duplicate(tmp_path, monkeypatch) -> None:
    """The watch command is meant to be run daily and re-run without thought. If a second
    run on the same session appended the same setups again, every statistic downstream
    would inflate silently — the worst kind of error in a forward record, because it makes
    the sample look larger exactly as you start trusting it."""
    from asymmetry import trident_journal as tj

    monkeypatch.setattr(tj, "WATCH_PATH", tmp_path / "watch.jsonl")
    cfg = TridentSettings(reward_risk=4.0)
    signals = [_signal("TESTCO", "2026-08-24 11:15", 100.0, 99.5)]

    assert tj.record(signals, cfg) == 1
    assert tj.record(signals, cfg) == 0
    assert len(tj.load()) == 1


def test_the_ratio_is_frozen_when_the_setup_is_recorded(tmp_path, monkeypatch) -> None:
    """Re-running with a different --rr must not move the target of a setup already
    flagged. A forward test whose rules drift mid-flight measures nothing."""
    from asymmetry import trident_journal as tj

    monkeypatch.setattr(tj, "WATCH_PATH", tmp_path / "watch.jsonl")
    tj.record([_signal("TESTCO", "2026-08-24 11:15", 100.0, 99.5, rr=4.0)],
              TridentSettings(reward_risk=4.0))
    first = tj.load()[0]

    # Same symbol, same bar, but a scan configured at 20R.
    tj.record([_signal("TESTCO", "2026-08-24 11:15", 100.0, 99.5, rr=20.0)],
              TridentSettings(reward_risk=20.0))
    records = tj.load()
    assert len(records) == 1
    assert records[0].target == first.target
    assert records[0].reward_risk == 4.0


def test_open_records_are_excluded_from_the_win_rate(tmp_path, monkeypatch) -> None:
    """An unresolved trade is neither a win nor a loss. Counting it either way is how a
    forward record reports a verdict before it has one."""
    from asymmetry import trident_journal as tj

    monkeypatch.setattr(tj, "WATCH_PATH", tmp_path / "watch.jsonl")
    cfg = TridentSettings(reward_risk=4.0)
    tj.record(
        [
            _signal("WINNER", "2026-08-20 11:15", 100.0, 99.0),
            _signal("LOSER", "2026-08-20 11:45", 100.0, 99.0),
            _signal("PENDING", "2026-08-21 11:15", 100.0, 99.0),
        ],
        cfg,
    )
    records = tj.load()
    records[0].outcome, records[0].realised_r = "target", 4.0
    records[1].outcome, records[1].realised_r = "stop", -1.0
    tj.save(records)

    result = tj.as_result()
    assert len(result.trades) == 3
    assert len(result.resolved) == 2          # PENDING is not counted
    assert result.win_rate == pytest.approx(50.0)


def test_open_trades_do_not_drag_the_expectancy(tmp_path, monkeypatch) -> None:
    """An unresolved position credits no profit but has already been charged a full
    round-trip cost. Counting it pulls the mean toward -cost for no reason — which would
    dominate a forward record, where nearly everything is open in the first weeks."""
    from asymmetry.engines.trident import TridentResult, TridentTrade

    def trade(outcome: str, r: float) -> TridentTrade:
        return TridentTrade(
            symbol="TESTCO", entered_at=pd.Timestamp(f"{SESSION} 11:15"), entry=100.0,
            stop=99.0, target=104.0, risk_pct=1.0, outcome=outcome, realised_r=r,
            cost_r=0.17,
        )

    settled = TridentResult(trades=[trade("target", 4.0), trade("stop", -1.0)])
    with_open = TridentResult(
        trades=[trade("target", 4.0), trade("stop", -1.0)] + [trade("open", 0.0)] * 8
    )
    assert with_open.net_expectancy_r == pytest.approx(settled.net_expectancy_r)
    assert with_open.total_r == pytest.approx(settled.total_r)


# ── Scaled exits ──────────────────────────────────────────────────────────────


def _open_signal() -> TridentSignal:
    """Entry 100, stop 99, target 104 — so 1R is 101 and the risk is exactly 1.00."""
    return TridentSignal(
        symbol="TESTCO", found=True, entry_at=pd.Timestamp(f"{SESSION} 11:15"),
        entry=100.0, stop=99.0, target=104.0, risk_pct=1.0,
    )


def _daily(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.bdate_range(start=pd.Timestamp(SESSION) + pd.Timedelta(days=1), periods=len(rows))
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": 1e6}
         for o, h, lo, c in rows],
        index=index,
    )


_QUIET_ENTRY_DAY = None


def _entry_day() -> pd.DataFrame:
    """One flat 30m bar after entry, so the walk starts on the daily bars."""
    return session_bars(pd.Timestamp(SESSION), [(100.0, 100.2, 99.8, 100.0)], 1e5)


def test_scaled_exit_turns_a_scratch_into_a_win() -> None:
    """The whole reason this resolver exists.

    A trade that reaches +1R and then comes all the way back books -1R on a fixed target
    and **+0.5R** on a scaled one. Same trade, same bars, opposite entry in the win column.
    This is how an 80% hit rate is manufactured, and it is not fraud — it is a different
    metric being compared against a fixed-target one.
    """
    from asymmetry.engines.trident import ScaledExit, resolve_trade_scaled

    cfg = TridentSettings(reward_risk=4.0)
    signal = _open_signal()
    # Day 1 tags 101 (the partial) but not 104; day 2 slides back through entry.
    bars = _daily([(100.0, 101.5, 99.5, 101.0), (100.5, 100.8, 98.0, 98.5)])

    fixed = resolve_trade(_entry_day(), bars, signal, cfg)
    scaled = resolve_trade_scaled(_entry_day(), bars, signal, cfg, ScaledExit())

    assert fixed.outcome == "stop"
    assert fixed.realised_r == pytest.approx(-1.0)
    assert scaled.outcome == "breakeven-stop"
    assert scaled.realised_r == pytest.approx(0.5)   # 0.5 x 1R booked, runner out at entry


def test_scaled_exit_does_not_rescue_a_trade_that_never_reached_the_partial() -> None:
    """No partial means no protection: the loss is the full 1R, exactly as before."""
    from asymmetry.engines.trident import ScaledExit, resolve_trade_scaled

    cfg = TridentSettings(reward_risk=4.0)
    bars = _daily([(100.0, 100.5, 98.0, 98.2)])
    scaled = resolve_trade_scaled(_entry_day(), bars, _open_signal(), cfg, ScaledExit())
    assert scaled.outcome == "stop"
    assert scaled.realised_r == pytest.approx(-1.0)


def test_scaled_exit_caps_the_upside_it_bought() -> None:
    """The cost of the higher hit rate: a full 4R winner pays 2.5R, not 4R."""
    from asymmetry.engines.trident import ScaledExit, resolve_trade_scaled

    cfg = TridentSettings(reward_risk=4.0)
    bars = _daily([(100.0, 101.5, 99.5, 101.2), (101.2, 104.5, 101.0, 104.2)])
    scaled = resolve_trade_scaled(_entry_day(), bars, _open_signal(), cfg, ScaledExit())
    assert scaled.outcome == "target"
    assert scaled.realised_r == pytest.approx(0.5 * 1.0 + 0.5 * 4.0)


def test_a_breakeven_stop_is_not_free_through_a_gap() -> None:
    """A stop at entry does not hold if the session opens below it. The scoreboard can
    still call this a win while it hands back part of the partial — worth asserting,
    because 'risk-free after the partial' is the single most common overstatement about
    this exit style."""
    from asymmetry.engines.trident import ScaledExit, resolve_trade_scaled

    cfg = TridentSettings(reward_risk=4.0)
    bars = _daily([(100.0, 101.5, 99.5, 101.0), (96.0, 96.5, 95.0, 95.5)])
    scaled = resolve_trade_scaled(_entry_day(), bars, _open_signal(), cfg, ScaledExit())
    assert scaled.outcome == "breakeven-stop"
    assert scaled.gapped is True
    assert scaled.realised_r == pytest.approx(0.5 * 1.0 + 0.5 * -4.0)   # -1.5R, not +0.5R
