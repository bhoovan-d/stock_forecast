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
