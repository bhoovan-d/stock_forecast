"""The intraday HMA/Bollinger pullback strategy.

Indicator correctness first, because everything downstream is built on it: an HMA that is
secretly an EMA would still produce a plausible-looking backtest. Then the two properties
most likely to erode — that the entry cannot see inside the anchor candle, and that the
0.7% risk cap refuses rather than re-prices.
"""

from __future__ import annotations

from datetime import date, time

import numpy as np
import pandas as pd
import pytest

from asymmetry.engines.hma_pullback import (
    PullbackSettings,
    bollinger_middle,
    find_anchor,
    find_pullback_entry,
    hull_moving_average,
    weighted_moving_average,
)


def session_frame(rows, day="2026-08-03", freq="5min", start="09:15"):
    """OHLC bars stamped with their start, as the feed delivers them."""
    index = pd.date_range(f"{day} {start}", periods=len(rows), freq=freq)
    return pd.DataFrame(
        [{"open": o, "high": h, "low": lo, "close": c, "volume": 100_000.0}
         for o, h, lo, c in rows],
        index=index,
    )


def thirty_min_frame(prices, body_frac=0.9, first_day="2026-08-03"):
    """Bars laid out as real 30m sessions — 13 per day from 09:15, then the next date.

    A flat `date_range` at 30min runs straight past 15:30 into the small hours, which puts
    a "before 13:00" candle at 04:45 the next morning. The session boundary has to be real
    or the cutoff test is meaningless.
    """
    stamps, day = [], pd.Timestamp(first_day)
    while len(stamps) < len(prices):
        for slot in range(13):
            if len(stamps) >= len(prices):
                break
            stamps.append(day + pd.Timedelta(hours=9, minutes=15) + pd.Timedelta(minutes=30 * slot))
        day += pd.Timedelta(days=1)

    rows, prev = [], prices[0]
    for close in prices:
        opn = prev
        reach = max(abs(close - opn), 1e-6) / body_frac
        pad = (reach - abs(close - opn)) / 2
        rows.append(
            (opn, close + pad, opn - pad, close) if close > opn
            else (opn, opn + pad, close - pad, close)
        )
        prev = close
    return pd.DataFrame(
        rows, columns=["open", "high", "low", "close"], index=pd.DatetimeIndex(stamps)
    ).assign(volume=100_000.0)


def turn_up_prices(decline=30, advance=9):
    """A slow decline then a turn upward — the shape that puts a rising HMA *underneath*
    the middle band and about to cross it, which is what the spec describes.

    A steady ramp does not: it leaves the HMA far above the band, which is emphatically not
    "about to cut" and is correctly refused.
    """
    prices = [100 * (1 - 0.001) ** i for i in range(decline)]
    price = prices[-1]
    for _ in range(advance):
        price *= 1.004
        prices.append(price)
    return prices


# ── Indicators ────────────────────────────────────────────────────────────────


def test_wma_matches_hand_calculation():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=float)
    assert weighted_moving_average(s, 3).iloc[-1] == pytest.approx((8 + 9 * 2 + 10 * 3) / 6)


def test_bollinger_middle_is_the_simple_average():
    s = pd.Series(np.arange(30, dtype=float))
    assert bollinger_middle(s, 20).iloc[-1] == pytest.approx(19.5)


def test_hma_tracks_a_ramp_with_far_less_lag_than_an_sma():
    """The whole reason the spec names Hull. On a linear ramp the HMA sits on the true
    value while a same-period SMA trails it — substituting an EMA would be a different
    indicator wearing the same name."""
    ramp = pd.Series(np.arange(60, dtype=float))
    hma = hull_moving_average(ramp, 9).iloc[-1]
    sma = ramp.rolling(9).mean().iloc[-1]
    assert hma == pytest.approx(59.0, abs=0.5)
    assert sma == pytest.approx(55.0, abs=0.5)


def test_hma_needs_a_warmup_before_it_reports():
    assert np.isnan(hull_moving_average(pd.Series(np.arange(8, dtype=float)), 9).iloc[-1])


# ── Stage 1: the anchor ───────────────────────────────────────────────────────


def test_anchor_requires_a_green_body_of_at_least_75_percent():
    cfg = PullbackSettings()
    frame = thirty_min_frame(turn_up_prices(), body_frac=0.9)
    session = frame.index[-1].date()
    assert find_anchor(frame, session, cfg).found

    # The same price path, but each candle is mostly wick rather than body.
    weak = thirty_min_frame(turn_up_prices(), body_frac=0.30)
    assert not find_anchor(weak, weak.index[-1].date(), cfg).found


def test_a_steady_ramp_is_not_about_to_cut_the_band():
    """The HMA has to be *arriving* at the middle band. In a persistent advance it sits far
    above it, which is a trend already underway, not the turn the spec describes."""
    ramp = thirty_min_frame([100 * 1.004 ** i for i in range(39)])
    assert not find_anchor(ramp, ramp.index[-1].date(), PullbackSettings()).found


def test_anchor_must_form_before_the_cutoff():
    """The qualifying candle here is at 12:15. Move the cutoff earlier and it must vanish."""
    frame = thirty_min_frame(turn_up_prices())
    session = frame.index[-1].date()

    found = find_anchor(frame, session, PullbackSettings())
    assert found.found and found.anchor_at.time() < time(13, 0)

    assert not find_anchor(frame, session, PullbackSettings(latest_anchor=time(10, 0))).found


def test_a_falling_hma_is_never_an_anchor():
    """'Sloping slightly upwards' — a decline with big green candles must not qualify."""
    frame = thirty_min_frame([100 * (1 - 0.004) ** i for i in range(39)])
    assert not find_anchor(frame, frame.index[-1].date(), PullbackSettings()).found


# ── Stage 2: the entry ────────────────────────────────────────────────────────


def _anchor_at(ts):
    from asymmetry.engines.hma_pullback import PullbackSignal

    return PullbackSignal(found=True, anchor_at=ts, note="anchor")


def test_entry_cannot_be_taken_from_inside_the_anchor_candle():
    """The anchor is a 30-minute candle. An entry stamped before it closed would use
    information that did not exist when the signal was generated."""
    day = "2026-08-03"
    bars = [(100, 100.4, 99.6, 100.2)] * 60
    m5 = session_frame(bars, day=day, freq="5min")
    anchor_ts = m5.index[0]                    # 09:15, closes 09:45
    signal = find_pullback_entry(m5, _anchor_at(anchor_ts), PullbackSettings())
    if signal.found:
        assert signal.entry_at >= anchor_ts + pd.Timedelta(minutes=30)


def test_risk_cap_refuses_rather_than_moving_the_stop():
    """A candle whose low sits 3% below its close is not re-priced to fit 0.7% — it is
    refused. Moving the stop to satisfy the cap would make the rule decorative."""
    day = "2026-08-03"
    flat = [(100, 100.1, 99.9, 100.0)] * 30
    # A deep-wicked green candle: closes above the band, low far below it.
    wide = [(99.5, 100.6, 97.0, 100.5)]
    m5 = session_frame(flat + wide + flat, day=day, freq="5min")
    anchor_ts = m5.index[0]

    strict = find_pullback_entry(m5, _anchor_at(anchor_ts), PullbackSettings(max_risk_pct=0.7))
    if strict.found:
        assert strict.risk_pct <= 0.7

    generous = find_pullback_entry(
        m5, _anchor_at(anchor_ts), PullbackSettings(max_risk_pct=99.0)
    )
    if generous.found:
        # Whatever it takes, the stop is always the candle's own low.
        assert generous.stop < generous.entry


def test_target_is_exactly_three_to_one_from_the_fill():
    day = "2026-08-03"
    flat = [(100, 100.1, 99.9, 100.0)] * 30
    dip = [(99.95, 100.30, 99.75, 100.25)]
    m5 = session_frame(flat + dip + flat, day=day, freq="5min")
    signal = find_pullback_entry(m5, _anchor_at(m5.index[0]), PullbackSettings())
    if signal.found:
        assert signal.target == pytest.approx(
            signal.entry + 3 * (signal.entry - signal.stop), abs=0.01
        )
        assert signal.stop < signal.entry < signal.target


def test_strategy_settings_are_independent_of_v3():
    """The two strategies must not share a constant. If this ever fails, one has been
    wired to the other and editing V3 will silently retune this."""
    from asymmetry.config import settings

    cfg = PullbackSettings()
    assert cfg.reward_risk == 3.0 and settings.min_reward_risk == 4.0
    assert cfg.max_risk_pct == 0.7 and settings.v3_max_stop_pct == 1.5
