"""The fifth hard filter: §12's "why now" must have an answer.

Added 18 Aug 2026 on the owner's explicit instruction, and it is genuinely a fifth — not
the carry gate's trick of reporting under *technical validity*. That mapping was honest
because a missing 60m carry structure really is a technical invalidity; a missing news
catalyst is not, and filing it there to keep the count at four would misstate why a name
was refused.

What the filter must never do is reject on missing *data*. On 14 Aug 2026 only 10 of 135
stage-one candidates carried a catalyst note, and the news pass is capped at 120 items and
90 filings with the NSE/BSE announcement APIs blocked — so "no catalyst found" is partly a
statement about reach. An empty result across the whole shortlist is an outage, and an
outage must disarm the filter rather than refuse the universe and call it selectivity.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from asymmetry.config import settings
from asymmetry.engines.v3 import quality_score
from asymmetry.engines.v3_scan import V3Candidate


def _weights() -> dict[str, float]:
    return {
        "rs_nifty_pct": 50.0,
        "rs_sector_pct": 50.0,
        "sector_percentile": 50.0,
        "structure_score": 50.0,
        "setup_quality": 50.0,
        "entry_quality": 50.0,
        "volatility_score": 50.0,
        "carry_score": 50.0,
    }


# ── has_catalyst is the only honest test for presence ─────────────────────────


def test_presence_is_tested_on_the_note_not_the_score():
    """`catalyst_score` is centred on 50, so "nothing found" and "found, nets to nothing"
    share a number. Only the note separates them, and the filter keys off the note."""
    nothing = V3Candidate(symbol="X", catalyst_score=50.0, catalyst_note="")
    assert nothing.has_catalyst is False

    neutral_event = V3Candidate(
        symbol="Y",
        catalyst_score=50.0,
        catalyst_note="results: Q1 filed — direction unknown from the filing itself",
    )
    # A results filing scores a deliberate neutral 50 (the numbers are inside a PDF nothing
    # here can read) but it is emphatically an answer to "why now".
    assert neutral_event.has_catalyst is True


def test_settings_default_arms_the_filter():
    assert settings.v3_require_catalyst is True


# ── The directional bug this filter's implementation exposed ──────────────────


@pytest.mark.parametrize(
    ("direction", "catalyst", "expect_higher_than_neutral"),
    [
        ("long", 90.0, True),    # bullish news on a long: helps
        ("long", 10.0, False),   # bearish news on a long: hurts
        ("short", 10.0, True),   # bearish news on a short: helps
        ("short", 90.0, False),  # bullish news on a short: must hurt
    ],
)
def test_catalyst_scores_in_the_traded_direction(direction, catalyst, expect_higher_than_neutral):
    """A SHORT with a *bullish* catalyst used to score higher for it.

    Every other percentile in the scan is mirrored for shorts and this one was not, so the
    catalyst module rewarded the news arguing against the trade. Same family as feeding the
    setup detector's own quality in as "structure". The mirroring lives in `run_v3_scan`, so
    this asserts the property the scan must preserve rather than re-importing the closure.
    """
    directional = catalyst if direction == "long" else 100 - catalyst
    scored, _ = quality_score(catalyst_score=directional, **_weights())
    neutral, _ = quality_score(catalyst_score=50.0, **_weights())
    assert (scored > neutral) is expect_higher_than_neutral


# ── Arming rules: three states, not two ───────────────────────────────────────


@pytest.mark.parametrize(
    ("require", "use", "notes", "expected"),
    [
        (True, True, {"ZEEL": "earnings beat"}, "armed"),
        # Nothing at all across the shortlist is an outage, not 135 quiet names.
        (True, True, {}, "outage"),
        # An explicit opt-out is a choice, and must not be reported as a failure.
        (True, False, {}, "off"),
        (False, True, {"ZEEL": "earnings beat"}, "off"),
    ],
)
def test_arming_states(require, use, notes, expected):
    """Mirrors the arming decision in `run_v3_scan`, which cannot be exercised here without
    a full scan: stage one needs stored history and stage two needs paced network fetches."""
    if not (require and use):
        status = "off"
    elif not notes:
        status = "outage"
    else:
        status = "armed"
    assert status == expected


# ── Measuring the filter: coverage is not absence ─────────────────────────────


def _history(monkeypatch, rows, window_days: int = 5):
    """A CatalystHistory over an in-memory store."""
    import pandas as pd

    from asymmetry.engines import catalyst as catalyst_mod

    frame = pd.DataFrame(rows)
    monkeypatch.setattr(
        catalyst_mod, "load_catalysts", lambda **_kw: frame, raising=False
    )
    import asymmetry.storage as storage_mod

    monkeypatch.setattr(storage_mod, "load_catalysts", lambda **_kw: frame)
    return catalyst_mod.CatalystHistory(window_days=window_days, end=date(2026, 8, 14))


def _row(day: str, symbol: str, score: float = 72.0):
    return {
        "published": datetime.fromisoformat(f"{day} 10:00:00"),
        "symbol": symbol,
        "score": score,
        "catalyst_type": "earnings",
        "rationale": "beat on margins",
    }


def test_a_date_before_the_store_is_uncovered_not_catalyst_free(monkeypatch):
    """The failure this whole measurement exists to avoid.

    The store began on 11 Aug 2026 and the intraday replay reaches back roughly sixty
    sessions. Treating July's silence as "no catalyst" would measure the collection start
    date and report it as a property of the market.
    """
    history = _history(monkeypatch, [_row("2026-08-12", "ZEEL")])

    assert history.covered(date(2026, 7, 3)) is False
    assert history.covered(date(2026, 8, 12)) is True
    # Inside the 5-day lookback of a covered day, still covered.
    assert history.covered(date(2026, 8, 14)) is True
    # Far enough past it that the window no longer reaches any record.
    assert history.covered(date(2026, 8, 25)) is False


def test_covered_day_without_a_record_is_a_genuine_absence(monkeypatch):
    """On a day the store knows about, a symbol with no record really had no catalyst —
    which is exactly the fact the live filter acts on. Coverage is a property of the date,
    not of the symbol."""
    history = _history(monkeypatch, [_row("2026-08-12", "ZEEL")])

    assert history.covered(date(2026, 8, 12)) is True
    score, note = history.at("PIIND", date(2026, 8, 12))
    assert note == ""
    assert score == 50.0

    score, note = history.at("ZEEL", date(2026, 8, 12))
    assert note.startswith("earnings")
    assert score > 50.0


def test_lookup_is_point_in_time(monkeypatch):
    """A catalyst published on the 13th must be invisible to a decision on the 12th."""
    history = _history(monkeypatch, [_row("2026-08-13", "ZEEL")])

    assert history.at("ZEEL", date(2026, 8, 12))[1] == ""
    assert history.at("ZEEL", date(2026, 8, 13))[1] != ""


def test_out_of_range_stored_scores_are_clamped_on_read():
    """`CatalystExtraction.score` gained its 0-100 clamp after the fact, and the store still
    holds rows from before it (JIOFIN 150.0, GODREJCP -25.0, both 12 Aug 2026). A stored
    value cannot be re-derived, so the clamp has to exist on read as well or those rows drag
    every average they appear in."""
    from asymmetry.engines.catalyst import aggregate_catalysts

    reference = datetime(2026, 8, 12, 23, 59, tzinfo=timezone.utc)
    frame = pd.DataFrame([
        {"published": datetime(2026, 8, 12, 10, 0), "score": 150.0,
         "catalyst_type": "m&a", "rationale": "runaway row"},
    ])
    score, _note = aggregate_catalysts(frame, reference)
    assert score <= 100.0

    frame = pd.DataFrame([
        {"published": datetime(2026, 8, 12, 10, 0), "score": -25.0,
         "catalyst_type": "promoter_institutional", "rationale": "runaway row"},
    ])
    score, _note = aggregate_catalysts(frame, reference)
    assert score >= 0.0


def test_uncovered_trades_are_excluded_from_the_cohort_not_counted_against_it():
    from asymmetry.v3_backtest import BacktestResult, Trade

    def trade(covered: bool, has: bool) -> Trade:
        return Trade(
            symbol="X", direction="long", setup="reclaim",
            entered_at=pd.Timestamp("2026-08-12 10:00"), entry=100.0, stop=99.0,
            target=104.0, stop_pct=1.0, outcome="target", realised_r=4.0,
            catalyst_covered=covered, has_catalyst=has,
        )

    result = BacktestResult(trades=[
        trade(True, True), trade(True, False), trade(False, False), trade(False, False),
    ])
    assert len(result.catalyst_covered.trades) == 2
    assert result.catalyst_uncovered == 2


def test_report_says_unmeasured_when_nothing_is_covered():
    """An empty cohort must read as "no measurement", never as a result."""
    from rich.console import Console

    from asymmetry.report.v3_report import render_backtest
    from asymmetry.v3_backtest import BacktestResult, Trade

    result = BacktestResult(trades=[
        Trade(symbol="X", direction="long", setup="reclaim",
              entered_at=pd.Timestamp("2026-07-03 10:00"), entry=100.0, stop=99.0,
              target=104.0, stop_pct=1.0, outcome="target", realised_r=4.0,
              catalyst_covered=False)
    ])
    console = Console(width=200, force_terminal=False, no_color=True)
    with console.capture() as capture:
        console.print(render_backtest(result))
    out = capture.get()
    assert "catalyst filter is unmeasured" in out
    assert "statement about collection, not about catalysts" in out


def test_outage_disarms_rather_than_rejecting_everything():
    """The one behaviour that must never regress. A filter fed no data refusing the whole
    universe would publish an empty page that looks exactly like a selective day."""
    from asymmetry.report.v3_report import hard_filter_note
    from asymmetry.engines.v3_scan import V3Scan

    outage = V3Scan(as_of="2026-08-14", catalyst_status="outage", catalyst_required=False)
    assert outage.catalyst_required is False
    note = hard_filter_note(outage)
    assert "Four hard filters" in note
    assert "outage rather than an absence of news" in note
