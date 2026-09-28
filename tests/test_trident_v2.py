"""Trident v2: gate events, the liquidity floor, the 5-minute track and liquidity sweeps.

`tests/test_trident.py` defends the geometry the source states. This file defends the four
things v2 added, and each test here exists because getting it wrong would produce a number
that looks fine:

* **Gate events carry the numbers.** A failure log saying "body too large" cannot tell you
  whether 30% is the right threshold. One saying "body 47.3% of range, limit 30.0%" can, and
  with 22 measured setups that log is the more informative half of the output.
* **The liquidity floor removes data holes, not just small caps.** A 30-minute bar in which
  nothing printed has a low equal to its high, which the fair-value-gap detector reads as a
  perfect imbalance. That is the specific failure the NIFTY 500 expansion invites.
* **Alerts rank by net R after costs.** Cost in R is (cost% / stop%), so the tightest stop —
  which looks best on every other measure — is the one surrendering the most of its own R.
* **A sweep is a reclaim, not a break.** Price that trades through a low and stays below has
  broken the level; the two mean opposite things and conflating them would tag every
  breakdown as a liquidity grab.
"""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from asymmetry.engines import trident_pools
from asymmetry.engines.trident import (
    GATE_NAMES,
    TridentSettings,
    default_windows,
    detect_trident_setup,
    drop_forming_bar,
)
from asymmetry.engines.trident_live import Alert, _rank_and_cap, candle_closes
from asymmetry.engines.trident_universe import (
    FetchReport,
    LiquidityScreen,
    corwin_schultz_bps,
)

from test_trident import SESSION, daily_frame, session_bars, trident_session, warmup_sessions


# ── Gate events ───────────────────────────────────────────────────────────────


def test_a_qualifying_setup_passes_all_five_gates_in_order() -> None:
    """Every gate reports, and they report in sequence. A monitor that alerts on gate 5
    without ever having emitted gate 4 has no get-ready to give."""
    events: list = []
    signal = detect_trident_setup(
        trident_session(), daily_frame(), SESSION, TridentSettings(), "TEST", sink=events
    )
    assert signal.found

    passed = [e.gate for e in events if e.passed]
    assert passed == sorted(passed), "gate events must be emitted in gate order"
    assert set(passed) == set(GATE_NAMES), f"missing gates: {set(GATE_NAMES) - set(passed)}"


def test_every_gate_event_carries_its_numbers() -> None:
    """The whole point of the failure log. A reason with no figures in it is an opinion."""
    events: list = []
    detect_trident_setup(
        trident_session(doji_body=True), daily_frame(), SESSION,
        TridentSettings(), "TEST", sink=events,
    )
    failures = [e for e in events if not e.passed]
    assert failures, "a defective fixture must produce at least one failure event"
    for event in failures:
        assert any(ch.isdigit() for ch in event.reason), (
            f"gate {event.gate} failure has no numbers in it: {event.reason!r}"
        )


def test_a_full_body_records_the_body_and_the_limit() -> None:
    """His invalidation, logged so the 30% threshold can be argued with later."""
    signal = detect_trident_setup(
        trident_session(doji_body=True), daily_frame(), SESSION, TridentSettings(), "TEST"
    )
    assert not signal.found
    assert signal.gate_reached == 4
    assert "body" in signal.gate_reason and "limit 30.0%" in signal.gate_reason


def test_a_confirmation_above_the_high_records_both_prices() -> None:
    signal = detect_trident_setup(
        trident_session(confirm_above_high=True), daily_frame(), SESSION,
        TridentSettings(), "TEST",
    )
    assert not signal.found
    assert signal.gate_reached == 5
    assert "doji high" in signal.gate_reason


def test_no_gap_is_gate_two_not_a_late_failure() -> None:
    """A session that never formed an imbalance is a completely different animal from one
    that formed the whole pattern and missed on the EMAs. The funnel is worthless if the two
    land in the same bucket."""
    flat = pd.concat(
        [
            warmup_sessions(100.0),
            session_bars(
                pd.Timestamp(SESSION),
                [(100.0, 100.05, 99.95, 100.0)] * 13,
                260_000.0,
            ),
        ]
    )
    signal = detect_trident_setup(flat, daily_frame(), SESSION, TridentSettings(), "TEST")
    assert not signal.found
    assert signal.gate_reached == 2
    assert "09:15" in signal.gate_reason and "12:45" in signal.gate_reason


# ── Closed candles only ───────────────────────────────────────────────────────


def test_a_forming_candle_is_never_evaluated() -> None:
    """The doji test is a body-to-range ratio and the reclaim test is a close. Neither means
    anything until the bar is finished, and a forming bar's range only ever widens — so a
    body that reads as a doji mid-candle can be a full-bodied invalidation by its close."""
    bars = trident_session()
    interval = TridentSettings().interval_minutes

    last = bars.index[-1]
    still_open = last + pd.Timedelta(minutes=interval - 1)
    assert len(drop_forming_bar(bars, interval, still_open)) == len(bars) - 1

    closed = last + pd.Timedelta(minutes=interval)
    assert len(drop_forming_bar(bars, interval, closed)) == len(bars)


def test_candle_boundaries_stay_inside_the_kill_zone() -> None:
    cfg = TridentSettings(anchor_interval="5m", killzone_end=time(10, 15))
    closes = candle_closes(cfg, date(2026, 8, 20))
    assert closes[0].time() == time(9, 20)
    assert all(c.time() <= time(10, 20) for c in closes)
    # Both grids run one candle past the window's end: the last bar that *starts* inside the
    # kill zone closes after it, and a monitor that stops at the boundary never sees it.
    assert closes[-1].time() == time(10, 20)
    thirty = candle_closes(TridentSettings(killzone_end=time(10, 15)), date(2026, 8, 20))
    assert thirty[-1].time() == time(10, 45)
    assert len(closes) > len(thirty), "the 5m track must wake far more often"


# ── The 5-minute parallel track ───────────────────────────────────────────────


def test_interval_minutes_is_parsed_not_assumed() -> None:
    assert TridentSettings(anchor_interval="30m").interval_minutes == 30
    assert TridentSettings(anchor_interval="5m").interval_minutes == 5
    assert TridentSettings(anchor_interval="1h").interval_minutes == 60
    with pytest.raises(ValueError):
        TridentSettings(anchor_interval="1d").interval_minutes


def test_the_same_geometry_is_a_setup_on_the_five_minute_track() -> None:
    """The 5m track is the same five gates on a shorter series, not a different strategy.
    Running the identical bar pattern through it must produce the identical verdict."""
    cfg5 = TridentSettings(anchor_interval="5m")
    signal = detect_trident_setup(trident_session(), daily_frame(), SESSION, cfg5, "TEST")
    assert signal.found
    assert signal.rr_used == cfg5.reward_risk


def test_cost_in_r_is_the_reason_five_minute_setups_rank_lower() -> None:
    """Not a preference — arithmetic. A 0.15% stop surrenders more than a full R to costs
    before the trade starts; a 0.60% stop surrenders a quarter of one. Ranking by anything
    that ignores this puts the worst setups at the top, because tight stops look best on
    every other measure."""
    cfg = TridentSettings(max_alerts_per_session=1)
    tight = Alert(gate=5, symbol="TIGHT", risk_pct=0.15, reward_risk=20.0,
                  cost_r=(cfg.cost_roundtrip_pct + cfg.slippage_pct) / 0.15)
    tight.net_r_at_target = 20.0 - tight.cost_r
    wide = Alert(gate=5, symbol="WIDE", risk_pct=0.60, reward_risk=20.0,
                 cost_r=(cfg.cost_roundtrip_pct + cfg.slippage_pct) / 0.60)
    wide.net_r_at_target = 20.0 - wide.cost_r

    assert tight.cost_r > 1.0 and wide.cost_r < 0.4
    kept, suppressed = _rank_and_cap([tight, wide], cfg)
    assert [a.symbol for a in kept] == ["WIDE"]
    assert suppressed == 1


def test_a_trigger_outranks_a_get_ready_for_the_budget() -> None:
    """One is actionable now; the other may never become actionable at all."""
    cfg = TridentSettings(max_alerts_per_session=1)
    ready = Alert(gate=4, symbol="READY", net_r_at_target=19.9)
    trigger = Alert(gate=5, symbol="GO", net_r_at_target=1.0)
    kept, _ = _rank_and_cap([ready, trigger], cfg)
    assert [a.symbol for a in kept] == ["GO"]


# ── The liquidity floor ───────────────────────────────────────────────────────


def test_corwin_schultz_recovers_a_spread_it_was_given() -> None:
    """A sanity anchor on the estimator, not a claim about its accuracy. Bars are built from
    a random walk whose highs and lows are pushed apart by a known spread; the estimate has
    to land in the same order of magnitude or the implementation is wrong."""
    rng = np.random.default_rng(7)
    mid = 1000 * np.exp(np.cumsum(rng.normal(0, 0.004, 400)))
    half = 0.0015 / 2                       # 15bp round-trip spread
    high = mid * (1 + abs(rng.normal(0, 0.006, 400)) + half)
    low = mid * (1 - abs(rng.normal(0, 0.006, 400)) - half)
    estimate = corwin_schultz_bps(pd.Series(high), pd.Series(low))
    assert 2 < estimate < 80, estimate


def test_the_estimator_is_never_negative() -> None:
    """It legitimately returns negatives on quiet data. Discarding those selectively keeps
    only the wide days and biases every stock upward, so they are floored instead."""
    flat = pd.Series([100.0] * 50)
    assert corwin_schultz_bps(flat * 1.001, flat * 0.999) >= 0


def test_a_zero_range_bar_is_the_data_hole_the_floor_exists_to_stop() -> None:
    """This is the concrete reason the floor runs first. A bar in which nothing printed has
    low == high, so the next bar's low sits above it and the detector sees a textbook
    imbalance — a data hole wearing the clothes of a liquidity void."""
    rows = [
        (100.00, 100.00, 100.00, 100.00),   # no prints: low == high
        (100.20, 101.40, 100.20, 101.35),
        (101.35, 101.90, 101.00, 101.80),
    ]
    rows.append((101.10, 101.60, 100.50, 101.15))
    rows.append((101.15, 101.55, 100.70, 101.50))
    while len(rows) < 13:
        last = rows[-1][3]
        rows.append((last, last + 0.10, last - 0.10, last + 0.05))
    frame = pd.concat(
        [warmup_sessions(100.0), session_bars(pd.Timestamp(SESSION), rows, 260_000.0)]
    )
    signal = detect_trident_setup(frame, daily_frame(), SESSION, TridentSettings(), "HOLE")
    # It qualifies as a gap. Nothing in the geometry can tell it apart from a real one, which
    # is precisely why the universe has to be screened before the geometry ever runs.
    assert signal.gap_seen


def test_the_screen_names_what_it_refused_and_why() -> None:
    """A gate reporting only its survivors cannot be audited, and a count with no names is
    how a shrinking denominator passes for a stable measurement."""
    screen = LiquidityScreen(considered=3, days_used=20, days_requested=20)
    from asymmetry.engines.trident_universe import LiquidityRow

    screen.rows = [
        LiquidityRow("BIG", 90e7, 8.0, 20, passed=True),
        LiquidityRow("THIN", 4e7, 9.0, 20, reason="turnover: ₹0.40 cr/day median, floor ₹25 cr"),
        LiquidityRow("WIDE", 90e7, 90.0, 20, reason="spread: 90.0bp estimated, cap 25bp"),
    ]
    screen.missing = ["GHOST"]
    assert screen.passing == ["BIG"]
    counts = screen.reason_counts()
    assert counts["turnover"] == 1 and counts["spread"] == 1
    assert counts["no EOD data"] == 1


def test_fetch_failures_are_named_not_merely_counted() -> None:
    report = FetchReport(requested=3, intraday_failed=["A"], daily_failed=["B"])
    assert report.ok == 1
    assert "MISSING" in report.summary()
    assert "A" in report.summary() and "B" in report.summary()


# ── Liquidity sweeps ──────────────────────────────────────────────────────────


def _bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return session_bars(pd.Timestamp(SESSION), rows, 100_000.0)


def test_a_wick_through_and_a_close_back_above_is_a_sweep() -> None:
    cfg = TridentSettings(sweep_max_candles=3)
    pool = trident_pools.LiquidityPool("equal-lows", "sell", 100.0, "equal lows (2 touches)")
    bars = _bars([
        (101.0, 101.5, 100.6, 101.2),
        (101.2, 101.3, 99.40, 100.90),      # wicks to 99.40, closes back above 100.0
        (100.9, 101.8, 100.8, 101.7),
    ])
    sweeps = trident_pools.find_sweeps(bars, [pool], cfg)
    assert len(sweeps) == 1
    assert sweeps[0].bars_to_reclaim == 0
    assert sweeps[0].excursion_pct == pytest.approx(0.6, abs=0.05)


def test_a_break_that_stays_below_is_not_a_sweep() -> None:
    """The distinction the whole layer turns on. A level taken and held is a breakdown; the
    thesis being tagged is the opposite one — orders removed and price rejected."""
    cfg = TridentSettings(sweep_max_candles=3)
    pool = trident_pools.LiquidityPool("equal-lows", "sell", 100.0, "equal lows (2 touches)")
    bars = _bars([
        (101.0, 101.5, 100.6, 101.2),
        (101.2, 101.3, 99.40, 99.50),
        (99.50, 99.70, 99.10, 99.20),
        (99.20, 99.40, 98.80, 98.90),
        (98.90, 99.10, 98.50, 98.60),
    ])
    assert trident_pools.find_sweeps(bars, [pool], cfg) == []


def test_the_reclaim_window_is_the_parameter_n() -> None:
    """N is a knob because nobody knows the right value on NSE equities; the tests pin that
    it is actually honoured rather than being decorative."""
    pool = trident_pools.LiquidityPool("equal-lows", "sell", 100.0, "equal lows")
    bars = _bars([
        (101.0, 101.5, 100.6, 101.2),
        (101.2, 101.3, 99.40, 99.60),       # through
        (99.60, 99.80, 99.30, 99.70),       # still below
        (99.70, 100.9, 99.60, 100.60),      # back above, 2 candles later
    ])
    assert trident_pools.find_sweeps(bars, [pool], TridentSettings(sweep_max_candles=1)) == []
    got = trident_pools.find_sweeps(bars, [pool], TridentSettings(sweep_max_candles=2))
    assert len(got) == 1 and got[0].bars_to_reclaim == 2


def test_prior_day_pools_never_read_the_session_they_are_used_in() -> None:
    """Anti-lookahead. The prior-day low is yesterday's, and a frame that runs to today must
    not contribute today's low to it."""
    daily = daily_frame(days=40)
    today = pd.Timestamp(SESSION)
    daily.loc[today] = {"open": 200.0, "high": 300.0, "low": 1.0, "close": 250.0,
                        "volume": 1e6}
    pools = trident_pools._prior_session_pools(daily, SESSION)
    lows = [p.level for p in pools if p.kind == "prior-day-low"]
    assert lows and lows[0] != 1.0, "today's low leaked into the prior-day pool"


def test_equal_lows_need_the_configured_number_of_touches() -> None:
    pivots = [
        (pd.Timestamp(SESSION), 100.00),
        (pd.Timestamp(SESSION), 100.04),
        (pd.Timestamp(SESSION), 105.00),
    ]
    pools = trident_pools._equal_levels(pivots, tolerance_pct=0.08, min_touches=2, low_side=True)
    assert len(pools) == 1
    assert pools[0].touches == 2
    # The level is the furthest touch, not the mean: stops rest beyond it.
    assert pools[0].level == 100.00
    assert trident_pools._equal_levels(pivots, 0.08, 3, True) == []


def test_the_sweep_tag_never_admits_or_refuses_a_setup() -> None:
    """Metadata, not a filter. The fixture has no swept pool behind its gap, and it must
    still be a setup — turning this into a gate today would shrink an already tiny sample on
    an untested assumption."""
    tagged = detect_trident_setup(
        trident_session(), daily_frame(), SESSION, TridentSettings(tag_liquidity_sweep=True),
        "TEST",
    )
    untagged = detect_trident_setup(
        trident_session(), daily_frame(), SESSION,
        TridentSettings(tag_liquidity_sweep=False), "TEST",
    )
    assert tagged.found and untagged.found
    assert tagged.entry == untagged.entry and tagged.stop == untagged.stop
    # ...and the two absences stay distinguishable.
    assert untagged.sweep_checked is False


# ── The window sweep ──────────────────────────────────────────────────────────


def test_the_grid_always_contains_the_window_it_is_testing() -> None:
    """A sweep that omits the transplant cannot say whether the transplant is any good."""
    cfg = TridentSettings()
    windows = default_windows(cfg)
    assert (cfg.killzone_start, cfg.killzone_end) in windows
    assert (time(9, 15), time(15, 30)) in windows


def test_every_swept_window_is_wide_enough_to_hold_the_pattern() -> None:
    """Three bars for the gap, one doji, one confirmation. A narrower window measures the
    absence of a pattern that could not have formed in it."""
    for interval in ("5m", "30m"):
        cfg = TridentSettings(anchor_interval=interval)
        for start, end in default_windows(cfg):
            minutes = (
                pd.Timestamp.combine(pd.Timestamp(SESSION), end)
                - pd.Timestamp.combine(pd.Timestamp(SESSION), start)
            ).total_seconds() / 60
            assert minutes >= cfg.interval_minutes * 5, (interval, start, end)


def test_a_window_that_excludes_the_pattern_finds_nothing() -> None:
    """The sweep is only meaningful if the window actually binds."""
    bars, daily = trident_session(), daily_frame()
    inside = detect_trident_setup(bars, daily, SESSION, TridentSettings(), "TEST")
    outside = detect_trident_setup(
        bars, daily,
        SESSION,
        TridentSettings(killzone_start=time(13, 0), killzone_end=time(15, 30)),
        "TEST",
    )
    assert inside.found and not outside.found


# ── The get-ready alert ───────────────────────────────────────────────────────


def test_the_get_ready_quotes_a_provisional_entry_and_a_fixed_stop() -> None:
    """At gate 4 the confirmation bar has not printed, so the entry is not knowable. The stop
    is: it is the doji's low and it does not move. Quoting a gate-4 entry as final would be a
    fabricated price — the confirming bar closes wherever it closes."""
    from asymmetry.engines.trident import GateEvent
    from asymmetry.engines.trident_live import _alert_from_doji

    cfg = TridentSettings(reward_risk=4.0)
    event = GateEvent(
        symbol="TEST", session=SESSION, gate=4, passed=True,
        at=pd.Timestamp(SESSION) + pd.Timedelta(hours=10),
        detail={"doji_open": 101.10, "doji_high": 101.60, "doji_low": 100.50,
                "doji_close": 101.15, "body_pct": 9.0, "midpoint": 100.60,
                "gap_low": 100.20, "gap_high": 101.00},
    )
    alert = _alert_from_doji(event, cfg)
    assert alert is not None
    assert alert.gate == 4 and alert.kind == "get ready"
    assert alert.provisional is True
    assert alert.stop == 100.50, "the stop is the doji low and is already known"
    assert alert.entry == 101.15, "the provisional entry is the doji's own close"
    assert "PROVISIONAL" in alert.note
    assert "101.60" in alert.invalidated_if, "the invalidation must name the doji high"

    # The payload the owner asked for, in full.
    for field in ("gap_high", "gap_low", "gap_midpoint", "doji_open", "doji_high",
                  "doji_low", "doji_close", "body_to_range_pct", "entry", "stop",
                  "target", "risk_pct", "net_r_at_target"):
        assert getattr(alert, field) != 0, f"{field} missing from the alert payload"
    # ...and net R is the ratio less this stop's own cost, not the gross ratio.
    assert alert.net_r_at_target < cfg.reward_risk
    # Both `cost_r` and `risk_pct` are rounded for display, so the rounding compounds and the
    # tolerance has to allow two units in the last place. Still far tighter than any wrong
    # constant would be: charging V3's or the pullback's cost here would miss by ~0.1R.
    assert alert.cost_r == pytest.approx(
        (cfg.cost_roundtrip_pct + cfg.slippage_pct) / alert.risk_pct, abs=2e-3
    )


def test_a_doji_that_cannot_produce_a_long_yields_no_alert() -> None:
    """A close at or below the doji's low is not a tradeable get-ready, and emitting one
    would put a zero or negative stop distance into the alert stream."""
    from asymmetry.engines.trident import GateEvent
    from asymmetry.engines.trident_live import _alert_from_doji

    event = GateEvent(
        symbol="TEST", session=SESSION, gate=4, passed=True,
        at=pd.Timestamp(SESSION),
        detail={"doji_low": 100.50, "doji_close": 100.50, "doji_high": 101.0},
    )
    assert _alert_from_doji(event, TridentSettings()) is None


def test_a_live_pass_alerts_gate_four_only_on_the_newest_closed_bar(monkeypatch) -> None:
    """A doji from earlier in the session has already had its confirmation bar and been
    decided; re-alerting it every pass is how a monitor becomes noise nobody watches."""
    from asymmetry.engines import trident_live

    bars, daily = trident_session(confirm_above_high=True), daily_frame()
    monkeypatch.setattr(trident_live, "resolve_universe", lambda *a, **k: (["TEST"], None))
    monkeypatch.setattr(trident_live, "fetch_pair", lambda *a, **k: (bars, daily))

    cfg = TridentSettings(reward_risk=4.0)
    day = bars[bars.index.date == SESSION]
    doji_at = day.index[3]

    # Standing just after the doji closed: it is the newest closed bar, so this is pending.
    ready = trident_live.scan_once(
        cfg, symbols=["TEST"], on=SESSION,
        now=doji_at + pd.Timedelta(minutes=cfg.interval_minutes),
    )
    assert [a.gate for a in ready.alerts] == [4]

    # Standing at the end of the session: the same doji was resolved hours ago.
    late = trident_live.scan_once(
        cfg, symbols=["TEST"], on=SESSION, now=day.index[-1] + pd.Timedelta(hours=1)
    )
    assert not [a for a in late.alerts if a.gate == 4]
    assert late.failures, "and it is recorded as a failure with its numbers instead"
