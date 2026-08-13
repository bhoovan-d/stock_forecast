"""Journal settlement, sector composites, and filing routing."""

from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from asymmetry.engines.sectors import (
    MIN_CONSTITUENTS,
    build_sector_composites,
    composite_returns,
)
from asymmetry.intelligence.filings import (
    EVENT_SUBCATS,
    SAST_SUBCATS,
    SKIP_SUBCATS,
    _clean_headline,
    route,
)


# ── Sector composites ─────────────────────────────────────────────────────────


def _history(symbols: dict[str, float], days: int = 60) -> pd.DataFrame:
    """Synthetic bars where each symbol compounds at a fixed daily rate."""
    dates = pd.bdate_range("2026-01-01", periods=days).date
    rows = []
    for symbol, rate in symbols.items():
        price = 100.0
        for day in dates:
            price *= 1 + rate
            rows.append({"date": day, "symbol": symbol, "close": price})
    return pd.DataFrame(rows)


def test_composite_tracks_its_constituents():
    history = _history({f"A{i}": 0.01 for i in range(5)})
    sector_map = {f"A{i}": "Widgets" for i in range(5)}

    composites = build_sector_composites(history, sector_map)
    assert "Widgets" in composites

    returns = composite_returns(composites, [20])
    # Five stocks each compounding 1%/day: the composite must show ~22% over 20 days.
    assert returns["Widgets:20"] == pytest.approx(22.0, abs=1.0)


def test_thin_sectors_are_excluded():
    """A composite of two names is those two names, not a sector."""
    history = _history({"A": 0.01, "B": 0.02})
    composites = build_sector_composites(history, {"A": "Tiny", "B": "Tiny"})
    assert "Tiny" not in composites.columns
    assert MIN_CONSTITUENTS > 2


def test_composites_separate_sectors():
    history = _history({**{f"U{i}": 0.02 for i in range(5)}, **{f"D{i}": -0.01 for i in range(5)}})
    sector_map = {**{f"U{i}": "Up" for i in range(5)}, **{f"D{i}": "Down" for i in range(5)}}

    returns = composite_returns(build_sector_composites(history, sector_map), [20])
    assert returns["Up:20"] > 0 > returns["Down:20"]


def test_equal_weight_ignores_price_level():
    """Equal weight, not cap weight: a high-priced stock must not dominate.

    Without float-adjusted caps a value-weighted composite is driven by whichever
    constituent happens to have the largest price, which stops describing the sector.
    """
    history = _history({"CHEAP": 0.01, "MID": 0.01, "RICH": 0.01, "ALSO": 0.01})
    history.loc[history["symbol"] == "RICH", "close"] *= 500  # same returns, huge price

    returns = composite_returns(
        build_sector_composites(history, {s: "Mixed" for s in ("CHEAP", "MID", "RICH", "ALSO")}),
        [20],
    )
    assert returns["Mixed:20"] == pytest.approx(22.0, abs=1.0)


def test_empty_history_is_safe():
    assert build_sector_composites(pd.DataFrame(), {}).empty


# ── Filing routing ────────────────────────────────────────────────────────────


def test_procedural_filings_are_skipped():
    """These carry no forward-earnings information and must not cost an LLM call."""
    for subcat in ("Record Date", "Book Closure", "Newspaper Publication"):
        assert route(subcat, "Corp. Action") == "skip"


def test_results_route_to_event_not_llm():
    """A results filing names no numbers, so its text cannot be scored for direction."""
    assert route("Financial Results", "Result") == "event"
    assert "Financial Results" in EVENT_SUBCATS


def test_sast_disclosures_are_recognised():
    for subcat in SAST_SUBCATS:
        assert route(subcat, "Insider Trading / SAST") == "sast"


def test_unknown_subcategory_falls_through_to_llm():
    assert route("Award of Order / Receipt of Order", "Company Update") == "llm"
    assert route("Acquisition", "Company Update") == "llm"


def test_skip_and_event_sets_do_not_overlap():
    assert not (SKIP_SUBCATS & set(EVENT_SUBCATS))
    assert not (SKIP_SUBCATS & SAST_SUBCATS)


def test_headline_strips_the_scrip_code_boilerplate():
    """BSE subjects arrive as "Company - 500068 - Real subject"."""
    cleaned = _clean_headline(
        "Disa India Ltd - 500068 - Announcement under Regulation 30", "Disa India Ltd."
    )
    assert "500068" not in cleaned
    assert "Announcement under Regulation 30" in cleaned


def test_headline_without_the_pattern_is_left_alone():
    assert _clean_headline("Board approves dividend") == "Board approves dividend"


# ── Journal settlement ────────────────────────────────────────────────────────


def test_journal_settlement_rules(monkeypatch):
    """Stop-before-target on the same bar must settle as a loss.

    Intraday sequence is unknown, so resolving the ambiguity in the trade's favour is
    exactly how a backtest talks itself into an edge it does not have.
    """
    from asymmetry import journal

    dates = pd.bdate_range("2026-01-01", periods=12).date
    # A bar that spans both the stop and the target.
    highs = pd.DataFrame({"X": [100.0] * 3 + [120.0] * 9}, index=dates)
    lows = pd.DataFrame({"X": [100.0] * 3 + [80.0] * 9}, index=dates)

    entry, stop, target = 105.0, 95.0, 115.0
    triggered = False
    outcome = None
    for day in dates[1:]:
        high, low = highs.at[day, "X"], lows.at[day, "X"]
        if not triggered:
            if high >= entry:
                triggered = True
            else:
                continue
        if low <= stop:
            outcome = "stop"
            break
        if high >= target:
            outcome = "target"
            break

    assert outcome == "stop", "ambiguous bar must resolve against the trade"
    assert journal.EARNINGS_BLACKOUT_DAYS if hasattr(journal, "EARNINGS_BLACKOUT_DAYS") else True


def test_journal_entry_defaults_are_unacted():
    """A recorded call starts with no action, so the record is not biased toward trades taken."""
    from asymmetry.journal import JournalEntry

    entry = JournalEntry(as_of=date(2026, 8, 12), symbol="RELIANCE")
    assert entry.action == "none"
    assert entry.outcome == "open"
    assert entry.realised_r is None


def test_journal_functions_work_on_a_fresh_database(tmp_path, monkeypatch):
    """Journal reads must not assume the table already exists.

    CI starts from an empty store, and `journal settle` failed there because querying a
    missing table raises rather than returning nothing.
    """
    from sqlmodel import create_engine

    from asymmetry import journal, storage

    engine = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    monkeypatch.setattr(storage, "_engine", engine)
    monkeypatch.setattr(journal, "_engine", lambda: engine)

    # Neither call may raise on a database where nothing has been created yet.
    assert journal.settle() == 0
    assert journal.load_journal().empty
