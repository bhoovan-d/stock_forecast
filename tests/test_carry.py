"""The 60m/120m carry test, and the base breakout that needed it.

The defects these lock down are all ones the engine actually shipped: a 60m read that was
fetched but never allowed to reject, a missing fetch that passed silently, a "structure"
score that was really the setup detector's own quality, and a setup taxonomy so narrow that
a 9.6% expansion out of a tight base on 36.8x volume registered as nothing at all.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from asymmetry.engines.carry import assess_carry, resample_120m
from asymmetry.engines.setups import detect_base_breakout, detect_setup
from asymmetry.spec import SetupType, Trend


def session_60m(
    days: int = 40,
    start: float = 100.0,
    drift: float = 0.004,
    volume_sequence: bool = True,
) -> pd.DataFrame:
    """60m bars over `days` NSE sessions, seven a session at 09:15…15:15.

    ``volume_sequence`` shapes volume as contraction-then-expansion, the pattern the carry
    gate requires. Setting it False gives flat volume — the PIIND shape, where alignment is
    perfect and there is no fuel.
    """
    stamps, closes, price = [], [], start
    day = pd.Timestamp("2026-06-01 09:15", tz="Asia/Kolkata")
    for _ in range(days):
        if day.weekday() < 5:
            for bar in range(7):
                stamps.append(day + pd.Timedelta(hours=bar))
                price *= 1 + drift
                closes.append(price)
        day += pd.Timedelta(days=1)
    close = np.array(closes)

    volume = np.full(len(close), 100_000.0)
    if volume_sequence and len(close) > 20:
        volume[-18:-3] = np.linspace(90_000, 45_000, 15)   # dries up through the base
        volume[-3:] = 400_000.0                            # then expands on the move
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.002,
            "low": close * 0.998,
            "close": close,
            "volume": volume,
        },
        index=pd.DatetimeIndex(stamps, name="ts"),
    )


# ── 120m resampling ───────────────────────────────────────────────────────────


def test_120m_folds_by_position_within_the_session():
    """Seven 60m bars become four buckets, and none of them spans two sessions.

    A clock resample would cut at 10:00/12:00/14:00 and straddle the 09:15 open, mixing two
    trading days into one bar.
    """
    h60 = session_60m(days=10)
    h120 = resample_120m(h60)

    per_session = h120.groupby(h120.index.date).size()
    assert set(per_session) == {4}

    for bucket_start in h120.index:
        assert bucket_start.time() in (
            pd.Timestamp("09:15").time(), pd.Timestamp("11:15").time(),
            pd.Timestamp("13:15").time(), pd.Timestamp("15:15").time(),
        )


def test_120m_aggregates_ohlcv_correctly():
    h60 = session_60m(days=6)
    h120 = resample_120m(h60)
    first_pair = h60.iloc[:2]
    first_bucket = h120.iloc[0]

    assert first_bucket["high"] == pytest.approx(first_pair["high"].max())
    assert first_bucket["low"] == pytest.approx(first_pair["low"].min())
    assert first_bucket["close"] == pytest.approx(first_pair["close"].iloc[-1])
    assert first_bucket["volume"] == pytest.approx(first_pair["volume"].sum())


def test_resample_handles_an_empty_frame():
    assert resample_120m(pd.DataFrame()).empty


# ── the gate ──────────────────────────────────────────────────────────────────


def test_missing_60m_data_fails_closed():
    """Unproven is not the same as fine.

    JYOTICNC published on 14 Aug 2026 while its 60m fetch had failed outright, because the
    higher-timeframe read decided nothing.
    """
    for absent in (None, pd.DataFrame()):
        state = assess_carry(absent, direction="long")
        assert not state.passes
        assert "no 60m data" in state.failed


def test_a_clean_uptrend_carries():
    state = assess_carry(session_60m(days=60), direction="long", required_pct=2.0)
    assert state.passes, state.failed
    assert state.score >= 60
    assert all(state.checks.values())


def test_the_same_frame_does_not_carry_a_short():
    """Direction symmetry: an uptrend must not satisfy a short's carry conditions."""
    state = assess_carry(session_60m(days=60), direction="short", required_pct=2.0)
    assert not state.passes
    assert state.failed


def test_a_failed_condition_is_named():
    """A gate whose rejections cannot be read is indistinguishable from no gate."""
    state = assess_carry(session_60m(days=60, drift=-0.004), direction="long")
    assert not state.passes
    assert state.failed in {*state.checks, ""} or "carry score" in state.failed
    assert any(not ok for ok in state.checks.values())


def test_strong_components_cannot_carry_a_fatal_one():
    """The PIIND regression.

    A first run of this gate passed PIIND short at 66/100 with volume 26 and headroom 24 —
    no volume expansion at all, and the nearest support 0.8% away against a target needing
    2.0% — because alignment and range position were perfect. A weighted average will always
    let two strong components carry a fatal one, so fuel and room are floors as well.
    """
    flat_volume = session_60m(days=60, volume_sequence=False)  # the PIIND shape
    state = assess_carry(
        flat_volume, direction="long", required_pct=2.0,
        min_volume_score=40.0, min_headroom_score=0.0,
    )
    assert not state.passes
    assert state.failed == "volume contracted then expanded"
    # It is not a low total that rejects it — the total is comfortably above the floor.
    assert state.score >= 60


def test_score_floor_can_reject_a_full_checklist():
    passing = assess_carry(session_60m(days=60), direction="long", required_pct=2.0)
    assert passing.passes
    strict = assess_carry(session_60m(days=60), direction="long", required_pct=2.0, floor=99.9)
    assert not strict.passes
    assert "carry score" in strict.failed


# ── base breakout on volume ───────────────────────────────────────────────────


def _lgeindia() -> pd.DataFrame:
    """LGEINDIA's real daily bars into 14 Aug 2026, from the stored NSE bhavcopies."""
    rows = [
        # date,        open,   high,   low,    close,  volume
        ("2026-08-03", 1510.9, 1574.0, 1499.0, 1569.8, 520588),
        ("2026-08-04", 1569.8, 1580.0, 1539.0, 1542.5, 147961),
        ("2026-08-05", 1543.7, 1582.4, 1534.1, 1578.6, 340379),
        ("2026-08-06", 1576.1, 1593.0, 1565.0, 1578.3, 196103),
        ("2026-08-07", 1578.3, 1592.0, 1569.2, 1586.5, 138372),
        ("2026-08-10", 1580.1, 1591.7, 1552.6, 1557.8, 203716),
        ("2026-08-11", 1554.1, 1576.2, 1546.6, 1566.5, 412984),
        ("2026-08-12", 1566.5, 1584.4, 1554.0, 1578.8, 291304),
        ("2026-08-13", 1583.0, 1583.0, 1563.5, 1578.3, 404196),
        ("2026-08-14", 1620.0, 1736.1, 1611.0, 1729.7, 9832158),
    ]
    # A flat run-in so the detector has its required history without adding structure.
    pad = pd.DataFrame(
        {
            "open": 1500.0, "high": 1505.0, "low": 1495.0, "close": 1500.0,
            "volume": 250_000.0,
        },
        index=pd.bdate_range(end="2026-07-31", periods=30),
    )
    real = pd.DataFrame(
        [r[1:] for r in rows],
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([r[0] for r in rows]),
    ).astype(float)
    return pd.concat([pad, real])


def test_lgeindia_fires_on_the_breakout_day():
    signal = detect_base_breakout(_lgeindia(), direction="long")
    assert signal.found
    assert signal.kind is SetupType.BASE_BREAKOUT
    assert signal.level == pytest.approx(1593.0)
    assert "36.8x volume" in signal.note
    assert signal.quality > 70


def test_lgeindia_is_silent_the_day_before():
    """The anti-lookahead property, stated as a test.

    The base is measured strictly on the bars *preceding* the breakout bar, so a signal can
    never be a relabelling of the move it claims to have caught.
    """
    signal = detect_base_breakout(_lgeindia().iloc[:-1], direction="long")
    assert not signal.found
    assert "not yet cleared" in signal.note


def test_detect_setup_surfaces_the_base_breakout():
    assert detect_setup(_lgeindia(), direction="long").kind is SetupType.BASE_BREAKOUT


def test_a_breakout_without_volume_is_refused():
    """Volume is the discriminator. Price clearing a base is common and mostly worthless."""
    frame = _lgeindia().copy()
    frame.iloc[-1, frame.columns.get_loc("volume")] = 260_000.0
    signal = detect_base_breakout(frame, direction="long")
    assert not signal.found
    assert "change of hands" in signal.note


def test_a_loose_range_is_not_a_base():
    close = np.array([100, 112, 96, 115, 94, 118, 92, 120, 95, 130.0])
    frame = pd.DataFrame(
        {
            "open": close, "high": close * 1.02, "low": close * 0.98, "close": close,
            "volume": np.full(len(close), 100_000.0),
        },
        index=pd.bdate_range("2026-06-01", periods=len(close)),
    )
    pad = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100_000.0},
        index=pd.bdate_range(end="2026-05-29", periods=30),
    )
    signal = detect_base_breakout(pd.concat([pad, frame]), direction="long")
    assert not signal.found
    assert "not a base" in signal.note


def test_jyoticnc_shape_is_vetoed_before_any_network_work():
    """The published case: a long with a weekly downtrend.

    It scored 76.4 because higher-timeframe trend fed neither the score nor a gate.
    """
    from asymmetry.engines.v3_scan import trend_permits

    assert not trend_permits(Trend.DOWN, Trend.SIDEWAYS, "long")
    assert not trend_permits(Trend.SIDEWAYS, Trend.DOWN, "long")
    assert not trend_permits(Trend.UP, Trend.SIDEWAYS, "short")
    # And the shapes that are still allowed.
    assert trend_permits(Trend.UP, Trend.UP, "long")
    assert trend_permits(Trend.UP, Trend.SIDEWAYS, "long")
    assert trend_permits(Trend.DOWN, Trend.DOWN, "short")


def test_structure_score_reflects_trend_not_setup_quality():
    """The mis-wiring that let a weekly downtrend score 90 on "structure"."""
    from asymmetry.engines.v3_scan import V3Candidate, _structure_score

    with_trend = V3Candidate(
        symbol="A", direction="long", weekly_trend=Trend.UP, daily_trend=Trend.UP
    )
    against = V3Candidate(
        symbol="B", direction="long", weekly_trend=Trend.DOWN, daily_trend=Trend.DOWN
    )
    assert _structure_score(with_trend) == pytest.approx(100.0)
    assert _structure_score(against) == pytest.approx(0.0)
    # Weekly leads: it decides whether a multi-session hold swims with the tide.
    weekly_only = V3Candidate(
        symbol="C", direction="long", weekly_trend=Trend.UP, daily_trend=Trend.DOWN
    )
    daily_only = V3Candidate(
        symbol="D", direction="long", weekly_trend=Trend.DOWN, daily_trend=Trend.UP
    )
    assert _structure_score(weekly_only) > _structure_score(daily_only)


def test_the_gate_applies_only_to_continuation_trades():
    """Measured over 2,527 replayed triggers, the gate helped some setups and hurt one.

        base-breakout   +0.75R -> +1.59R
        continuation    -0.80R -> -0.42R
        reclaim         +0.30R -> -0.07R

    A sweep-and-reclaim is a counter-trend entry by construction, so a continuation-regime
    test selects the reclaims with the least asymmetry left.
    """
    from asymmetry.engines.carry import gate_applies

    assert gate_applies(SetupType.BASE_BREAKOUT)
    assert gate_applies(SetupType.CONTINUATION)
    assert not gate_applies(SetupType.RECLAIM)


def test_ungated_setups_are_still_measured():
    """Exempt is not unexamined.

    Carry is assessed and reported for setups it cannot reject, so the decision to exempt
    them stays checkable against later data instead of becoming invisible.
    """
    from asymmetry.v3_backtest import Trade

    reclaim = Trade(
        "X", "long", "reclaim", pd.Timestamp("2026-08-14"), 100, 99, 104, 1.0,
        carry_passed=False, carry_score=41.0, carry_applies=False,
    )
    assert reclaim.admitted           # taken despite failing carry
    assert not reclaim.carry_passed   # and the failure is still on the record
    assert reclaim.carry_score == 41.0

    flag = Trade(
        "Y", "long", "continuation", pd.Timestamp("2026-08-14"), 100, 99, 104, 1.0,
        carry_passed=False, carry_applies=True,
    )
    assert not flag.admitted


def test_featureless_data_produces_no_setup():
    flat = pd.DataFrame(
        {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0, "volume": 100_000.0},
        index=pd.bdate_range("2026-01-01", periods=60),
    )
    assert not detect_base_breakout(flat, direction="long").found
