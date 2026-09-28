from __future__ import annotations

import pandas as pd
import pytest

from asymmetry.data.bar_contract import (
    IntradayStore,
    completed_bars,
    validate_15m,
    with_availability,
)


def _session(day="2026-09-01", price=100.0):
    index = pd.date_range(f"{day} 09:15", periods=25, freq="15min", tz="Asia/Kolkata")
    return pd.DataFrame({
        "open": price, "high": price + 1, "low": price - 1,
        "close": price + 0.25, "volume": 1000,
    }, index=index)


def test_kei_cutoff_excludes_developing_15m_hourly_and_daily_candles():
    intraday = _session()
    at_1245 = completed_bars(intraday, "15m", "2026-09-01 12:45+05:30")
    assert pd.Timestamp("2026-09-01 12:30+05:30") in at_1245.index
    assert pd.Timestamp("2026-09-01 12:45+05:30") not in at_1245.index

    hourly = intraday.resample(
        "60min", origin="start_day", offset="15min", label="left", closed="left"
    ).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    hourly = hourly.dropna()
    known_hourly = completed_bars(hourly, "60m", "2026-09-01 12:45+05:30")
    assert pd.Timestamp("2026-09-01 11:15+05:30") in known_hourly.index
    assert pd.Timestamp("2026-09-01 12:15+05:30") not in known_hourly.index

    daily = pd.DataFrame({
        "open": [100, 101], "high": [102, 999], "low": [99, 1],
        "close": [101, 700], "volume": [10, 1_000_000],
    }, index=pd.to_datetime(["2026-08-31", "2026-09-01"]))
    known_daily = completed_bars(daily, "1d", "2026-09-01 12:45+05:30")
    assert list(known_daily.index.date) == [pd.Timestamp("2026-08-31").date()]


def test_integrity_requires_exact_complete_cash_session():
    assert validate_15m(_session()).valid
    report = validate_15m(_session().drop(_session().index[4]))
    assert not report.valid
    assert "incomplete" in " ".join(report.errors)


def test_store_is_idempotent_but_refuses_candle_revisions(tmp_path):
    store = IntradayStore(tmp_path / "bars.sqlite")
    frame = _session()
    first = store.append("TEST", frame, source="fixture")
    second = store.append("TEST", frame, source="fixture")
    assert first != second
    pd.testing.assert_frame_equal(store.load("TEST"), frame.astype(float), check_freq=False)

    revised = frame.copy()
    revised.iloc[0, revised.columns.get_loc("close")] += 0.5
    with pytest.raises(ValueError, match="immutable candle conflict"):
        store.append("TEST", revised, source="fixture")


def test_contract_exposes_all_three_times():
    contracted = with_availability(_session(), "15m")
    assert {"interval_start", "interval_end", "available_at"} <= set(contracted.columns)
    assert contracted.iloc[0].available_at - contracted.iloc[0].interval_start == pd.Timedelta(minutes=15)
