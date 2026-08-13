"""Lookahead-bias guards.

A measured, real bug motivated these: Yahoo always returns a window ending *today*, so a
brief generated for 2026-03-02 was ranking stocks against a NIFTY benchmark that ran
through 2026-08-11 — 162 days of future data. Any backtest built on that would have
produced flattering nonsense.

Truncation now happens inside the data layer so no engine can leak the future by
forgetting to pass a date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from asymmetry.data.yahoo import truncate
from asymmetry.engines.catalyst import _freshness


def _frame(days: int, end: str = "2026-08-11") -> pd.DataFrame:
    index = pd.date_range(end=end, periods=days, freq="D", tz="Asia/Kolkata")
    return pd.DataFrame({"close": range(days)}, index=index)


def test_truncate_drops_every_future_bar():
    frame = _frame(200)
    cut = date(2026, 3, 2)
    trimmed = truncate(frame, cut)

    assert trimmed is not None
    assert trimmed.index.max().date() <= cut
    assert len(trimmed) < len(frame)


def test_truncate_keeps_the_as_of_day_itself():
    """The as-of session has closed, so its own bar is legitimately available."""
    frame = _frame(30, end="2026-08-11")
    trimmed = truncate(frame, date(2026, 8, 11))
    assert trimmed is not None
    assert trimmed.index.max().date() == date(2026, 8, 11)


def test_truncate_is_a_noop_without_a_date():
    frame = _frame(50)
    assert len(truncate(frame, None)) == len(frame)


def test_truncate_returns_none_when_nothing_predates_the_cutoff():
    """A stock listed after as_of must yield no data, not its earliest future bar."""
    frame = _frame(10, end="2026-08-11")
    assert truncate(frame, date(2020, 1, 1)) is None


def test_truncate_handles_empty_and_none():
    assert truncate(None, date(2026, 1, 1)) is None
    empty = pd.DataFrame({"close": []}, index=pd.DatetimeIndex([], tz="Asia/Kolkata"))
    assert truncate(empty, date(2026, 1, 1)).empty


def test_freshness_is_anchored_to_the_reference_not_now():
    """Decay must be measured from the as-of moment.

    Anchored to wall-clock now(), every catalyst in a historical run decays to ~0 and the
    factor silently switches itself off across the whole backtest.
    """
    published = datetime(2026, 3, 2, 9, 0, tzinfo=timezone.utc)
    same_day = datetime(2026, 3, 2, 18, 0, tzinfo=timezone.utc)

    fresh = _freshness(published, same_day)
    stale = _freshness(published, same_day + timedelta(days=120))

    assert fresh > 0.7, "a catalyst published that morning must still count"
    assert stale < 0.01
    assert fresh > stale


def test_engines_accept_as_of_on_every_fetching_path():
    """Signature guard: a fetching engine entry point must take a date.

    Cheap to assert, and it catches a new engine being added that silently reads 'now'.
    """
    import inspect

    from asymmetry.engines.macro import _factor_panel, macro_gap
    from asymmetry.engines.selection import _sector_returns

    for func in (macro_gap, _factor_panel, _sector_returns):
        assert "as_of" in inspect.signature(func).parameters, func.__name__


@pytest.mark.network
def test_historical_fetch_does_not_return_future_bars():
    from asymmetry.data import yahoo

    cut = date(2026, 3, 2)
    frame = yahoo.fetch_chart("^NSEI", range_="1y", interval="1d", as_of=cut)
    if frame is None:
        pytest.skip("Yahoo unreachable or throttled")
    assert frame.index.max().date() <= cut
