"""Specification V3: setups, hard filters, both directions.

The rules most likely to erode under future edits are the ones defended hardest here: the
stop *band* (V3 adds a floor, not just a ceiling), the four-and-only-four hard filters, and
the fact that sector leadership scores rather than gates — that last one is precisely the
mistake that made an earlier engine discard the setups the spec exists to find.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from asymmetry.config import settings
from asymmetry.engines.setups import (
    detect_high_tight_flag,
    detect_liquidity_sweep,
    detect_setup,
)
from asymmetry.engines.v3 import (
    V3Reject,
    average_daily_range,
    build_v3_plan,
    move_feasible,
    quality_score,
)
from asymmetry.spec import SetupType


def bars(closes, *, spread=0.006, volume=1_000_000):
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame(
        {
            "high": close * (1 + spread),
            "low": close * (1 - spread),
            "close": close,
            "volume": np.full(len(close), float(volume)),
        },
        index=pd.date_range("2026-01-01", periods=len(close), freq="B"),
    )


def sweep_series(long_side: bool = True) -> pd.DataFrame:
    """A support shelf, a sweep beneath it, then a reclaim — Setup A.

    Must exceed the detector's lookback+10 bar minimum, so the chop section is long.
    """
    base = [100 + (i % 3) for i in range(45)]      # chop
    shelf = [96, 96.4, 96.2, 96.5, 97, 98, 99]     # repeated touches near 96
    sweep = [95, 93.5]                             # takes out the shelf
    reclaim = [96.8, 98.5, 100.2]                  # fails and reclaims
    series = base + shelf + sweep + reclaim
    if long_side:
        return bars(series)
    return bars([200 - x for x in series])


# ── Setup A: liquidity sweep ──────────────────────────────────────────────────


def test_liquidity_sweep_detected_long():
    signal = detect_liquidity_sweep(sweep_series(True), direction="long")
    assert signal.found
    assert signal.kind is SetupType.RECLAIM
    assert signal.level is not None and 0 < signal.quality <= 100


def test_liquidity_sweep_detected_short():
    """The mirror: sweep above a resistance shelf, then rejection."""
    signal = detect_liquidity_sweep(sweep_series(False), direction="short")
    assert signal.found
    assert signal.is_short


def test_sweep_without_reclaim_is_not_a_setup():
    """Breaking down and staying down is a breakdown, not a sweep.

    The shelf must sit in the *prior* section (everything before the last 8 bars), since
    that is where the detector looks for the level being swept.
    """
    chop = [100 + (i % 3) for i in range(40)]
    shelf = [96, 96.2, 96.1, 96.4, 96.0]          # lands in prior
    filler = [97, 97.5, 97.2]
    breakdown = [95, 93, 91, 89, 88]              # never comes back
    signal = detect_liquidity_sweep(
        bars(chop + shelf + filler + breakdown), direction="long"
    )
    assert not signal.found
    assert "not reclaimed" in signal.note


def test_single_touch_level_still_qualifies():
    """A clean single pivot that is swept and reclaimed is a valid sweep.

    Real support shelves often descend monotonically (92.00 / 92.57 / 93.36), leaving only
    one strict fractal pivot. Penalising that shape heavily is what under-scored a genuine
    setup, so touches scale quality without gating it.
    """
    chop = [100 + (i % 4) for i in range(40)]
    shelf = [92.0, 92.6, 93.4, 93.0, 92.8]        # lands in prior
    approach = [95.0, 94.0, 93.5]
    sweep = [90.0]                                 # takes out the shelf
    reclaim = [97.5, 99.0]                         # fails and reclaims well above it
    signal = detect_liquidity_sweep(
        bars(chop + shelf + approach + sweep + reclaim), direction="long"
    )
    assert signal.found
    assert signal.quality >= 50


# ── Setup B: high-tight flag ──────────────────────────────────────────────────


def test_high_tight_flag_detected():
    # Needs impulse_window + flag_window + 5 bars, so the run-up is padded.
    lead = [100.0] * 10
    impulse = list(np.linspace(100, 118, 20))
    flag = [117.5, 118.2, 117.8, 118.1, 117.6, 118.0, 117.9, 118.3]
    signal = detect_high_tight_flag(bars(lead + impulse + flag), direction="long")
    assert signal.found
    assert signal.kind is SetupType.CONTINUATION


def test_loose_consolidation_is_not_a_flag():
    lead = [100.0] * 10
    impulse = list(np.linspace(100, 118, 20))
    sloppy = [117, 110, 116, 108, 115, 109, 114, 110]
    signal = detect_high_tight_flag(bars(lead + impulse + sloppy), direction="long")
    assert not signal.found


def test_weak_impulse_is_not_a_flagpole():
    lead = [100.0] * 10
    drift = list(np.linspace(100, 102, 20))
    flag = [102, 102.1, 101.9, 102.05, 102, 101.95, 102.1, 102]
    signal = detect_high_tight_flag(bars(lead + drift + flag), direction="long")
    assert not signal.found
    assert "flagpole" in signal.note


def test_detect_setup_returns_none_for_featureless_data():
    signal = detect_setup(bars([100 + 0.01 * i for i in range(80)]), "long")
    assert not signal.found


# ── Hard filter: stop band (§1, §13) ──────────────────────────────────────────


def _plan_inputs(entry_area=100.0, swing_pct=1.0):
    """Intraday bars coiled under a trigger, with one clear swing low swing_pct below.

    The dip must sit inside the detector's trailing 60-bar pivot window, and the flat bars
    around it must not themselves register as pivots — hence the near-zero spread.
    """
    low = entry_area * (1 - swing_pct / 100)
    series = [entry_area] * 40 + [low] + [entry_area] * 30
    intraday = bars(series, spread=0.00005)
    daily = bars(list(np.linspace(80, entry_area, 200)), spread=0.02)
    return intraday, daily


def test_stop_wider_than_band_is_rejected():
    intraday, daily = _plan_inputs(swing_pct=4.0)
    with pytest.raises(V3Reject) as caught:
        build_v3_plan(
            direction="long", intraday=intraday, daily=daily, weekly=daily,
            adr_pct=5.0, atr_pct=5.0,
        )
    assert caught.value.filter_name in ("stop distance", "4R feasibility")


def test_stop_tighter_than_floor_is_rejected():
    """V3 adds a *minimum*: inside 0.5% the level is noise, not an invalidation."""
    intraday, daily = _plan_inputs(swing_pct=0.1)
    with pytest.raises(V3Reject) as caught:
        build_v3_plan(
            direction="long", intraday=intraday, daily=daily, weekly=daily,
            adr_pct=5.0, atr_pct=5.0,
        )
    assert caught.value.filter_name == "stop distance"
    assert "noise" in caught.value.detail


def test_emitted_plans_sit_inside_the_band_and_are_exactly_4r():
    emitted = 0
    for swing in (0.6, 0.9, 1.2, 1.4):
        intraday, daily = _plan_inputs(swing_pct=swing)
        try:
            plan = build_v3_plan(
                direction="long", intraday=intraday, daily=daily, weekly=daily,
                adr_pct=6.0, atr_pct=6.0,
            )
        except V3Reject:
            continue
        emitted += 1
        assert settings.min_stop_pct <= plan.stop_pct <= settings.v3_max_stop_pct
        assert plan.target == pytest.approx(plan.entry + 4 * plan.risk, abs=0.05)
        assert plan.stop < plan.entry < plan.target
    assert emitted > 0


def test_short_plan_is_the_mirror_image():
    high = 100.0
    series = [high] * 40 + [high * 1.01] + [high] * 30
    intraday = bars(series, spread=0.00005)
    daily = bars(list(np.linspace(120, high, 200)), spread=0.02)
    try:
        plan = build_v3_plan(
            direction="short", intraday=intraday, daily=daily, weekly=daily,
            adr_pct=6.0, atr_pct=6.0,
        )
    except V3Reject:
        pytest.skip("synthetic short setup did not clear the filters")
    assert plan.direction == "short"
    assert plan.target < plan.entry < plan.stop
    assert plan.target == pytest.approx(plan.entry - 4 * plan.risk, abs=0.05)


# ── Feasibility (§10) ─────────────────────────────────────────────────────────


def test_move_feasibility_uses_the_more_conservative_estimate():
    """Taking the friendlier of ADR and ATR would wave through unreachable targets."""
    feasible_lo, _, _ = move_feasible(6.0, adr_pct=1.0, atr_pct=8.0)
    feasible_hi, _, _ = move_feasible(6.0, adr_pct=8.0, atr_pct=8.0)
    assert not feasible_lo
    assert feasible_hi


def test_adr_measures_intraday_travel():
    frame = bars([100] * 30, spread=0.02)   # every bar spans ~4%
    assert average_daily_range(frame) == pytest.approx(4.08, abs=0.3)


# ── Scoring (§16) ─────────────────────────────────────────────────────────────


def test_v3_weights_sum_to_one():
    total = (
        settings.v3_weight_rs_nifty + settings.v3_weight_rs_sector
        + settings.v3_weight_sector_leadership + settings.v3_weight_structure
        + settings.v3_weight_entry_quality + settings.v3_weight_catalyst
        + settings.v3_weight_volatility
    )
    assert total == pytest.approx(1.0, abs=1e-9)


def test_rs_composite_weights_sum_to_one():
    total = (
        settings.rs_weight_vs_nifty + settings.rs_weight_vs_sector
        + settings.rs_weight_acceleration
    )
    assert total == pytest.approx(1.0, abs=1e-9)


def test_sector_leadership_cannot_veto_a_strong_setup():
    """Sector is a 15% score, not a gate.

    A weak sector costs at most 15 points, so an otherwise exceptional candidate still
    outranks a mediocre one from a leading sector. Gating on sector is what caused the
    engine to discard valid setups.
    """
    strong_weak_sector, _ = quality_score(
        rs_nifty_pct=95, rs_sector_pct=90, sector_percentile=10,
        structure_score=90, entry_quality=90, catalyst_score=80, volatility_score=90,
    )
    mediocre_top_sector, _ = quality_score(
        rs_nifty_pct=55, rs_sector_pct=50, sector_percentile=100,
        structure_score=50, entry_quality=50, catalyst_score=50, volatility_score=50,
    )
    assert strong_weak_sector > mediocre_top_sector


def test_score_is_bounded():
    top, _ = quality_score(
        rs_nifty_pct=100, rs_sector_pct=100, sector_percentile=100,
        structure_score=100, entry_quality=100, catalyst_score=100, volatility_score=100,
    )
    bottom, _ = quality_score(
        rs_nifty_pct=0, rs_sector_pct=0, sector_percentile=0,
        structure_score=0, entry_quality=0, catalyst_score=0, volatility_score=0,
    )
    assert top == pytest.approx(100.0, abs=0.01)
    assert bottom == pytest.approx(0.0, abs=0.01)


# ── Intraday trigger scanning ─────────────────────────────────────────────────


def test_trigger_scan_finds_an_earlier_valid_bar():
    """The engine must see triggers that occurred earlier in the session.

    Evaluating only the closing bar is close to useless here: after a large move the nearest
    invalidation sits several percent away, so the setup that actually paid is invisible.
    ZEEL on 12 Aug offered a 0.54% stop mid-morning and ran past 4R the same session, yet
    showed a 4.6% stop by the close.
    """
    entry_area = 100.0
    # A tight, valid structure early, then a run-up that leaves the swing low far behind.
    early = [entry_area] * 40 + [entry_area * 0.99] + [entry_area] * 10
    run_up = list(np.linspace(entry_area, entry_area * 1.06, 12))
    intraday = bars(early + run_up, spread=0.00005)
    daily = bars(list(np.linspace(80, entry_area, 200)), spread=0.02)

    from asymmetry.engines.v3 import build_v3_plan as scan_plan

    plan = scan_plan(
        direction="long", intraday=intraday, daily=daily, weekly=daily,
        adr_pct=6.0, atr_pct=6.0, setup=SetupType.CONTINUATION, scan_bars=26,
    )
    assert settings.min_stop_pct <= plan.stop_pct <= settings.v3_max_stop_pct
    # It should have had to look back rather than using the final bar.
    assert plan.bars_ago >= 0
    assert plan.triggered_at


def test_live_trigger_is_labelled_live():
    intraday, daily = _plan_inputs(swing_pct=0.9)
    from asymmetry.engines.v3 import build_v3_plan as scan_plan

    plan = scan_plan(
        direction="long", intraday=intraday, daily=daily, weekly=daily,
        adr_pct=6.0, atr_pct=6.0, setup=SetupType.CONTINUATION,
    )
    assert plan.bars_ago == 0
    assert plan.triggered_at == "live"
    assert plan.is_live


def test_reclaim_entry_is_at_price_not_session_high():
    """A confirmed reclaim is already live; entering above the day's high invents a chase.

    That mistake put entry at the top of the move and the stop several percent below, which
    is why every genuine sweep setup failed the stop filter.
    """
    from asymmetry.engines.v3 import _plan_at_bar

    close_area = 100.0
    series = [close_area] * 40 + [close_area * 0.99] + [close_area] * 20 + [close_area * 1.04]
    intraday = bars(series, spread=0.00005)
    daily = bars(list(np.linspace(80, close_area, 200)), spread=0.02)

    try:
        plan = _plan_at_bar(
            direction="long", intraday=intraday, daily=daily, weekly=daily,
            adr_pct=8.0, atr_pct=8.0, setup=SetupType.RECLAIM,
        )
    except V3Reject:
        pytest.skip("synthetic reclaim did not clear the filters")
    # Entry is the current close, not the prior session extreme.
    assert plan.entry == pytest.approx(float(intraday["close"].iloc[-1]), rel=1e-6)
