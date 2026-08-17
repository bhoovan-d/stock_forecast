"""What the three V3 surfaces actually say to a reader.

Nothing rendered a card before this file, which is exactly how PIIND shipped on 14 Aug 2026
tagged ``reclaim`` on a SHORT while its own note underneath read "rejected by 10.2%". The
engine was right and the label contradicted it, and no test could have noticed because no
test built a card.

These assert the *wording*, deliberately. `carry_lines` and `execution_lines` exist so the
console, the Markdown brief and the published page cannot describe one plan three ways, so
each claim here is checked on all three.
"""

from __future__ import annotations

import pandas as pd
import pytest
from rich.console import Console

from asymmetry.engines.setups import SetupSignal, setup_label
from asymmetry.engines.v3 import V3Plan
from asymmetry.engines.v3_scan import V3Candidate, V3Scan
from asymmetry.report.v3_report import build_v3_markdown, render_v3
from asymmetry.report.v3_website import build_v3_html
from asymmetry.spec import SetupType


def _plan(direction: str = "short") -> V3Plan:
    """PIIND's published geometry, which is a real short reclaim with a fixed stop."""
    return V3Plan(
        direction=direction,
        entry=2490.10,
        stop=2503.70,
        stop_pct=0.55,
        risk=13.60,
        target=2435.70,
        target_pct=2.2,
        quantity=367,
        invalidation="15m swing high at 2,503.70",
        setup=SetupType.RECLAIM,
        entry_min=2466.70,
        entry_max=2491.24,
        trigger_bar=pd.Timestamp("2026-08-14 15:15"),
    )


def _candidate(direction: str = "short", *, catalyst_note: str = "") -> V3Candidate:
    return V3Candidate(
        symbol="PIIND",
        company="PI Industries Ltd.",
        sector="Chemicals",
        direction=direction,
        close=2490.10,
        setup=SetupSignal(
            kind=SetupType.RECLAIM,
            direction=direction,
            found=True,
            quality=71.0,
            level=2773.30,
            note="swept 2,773.30 (7 prior touches) by 1.9%, rejected by 10.2%",
        ),
        plan=_plan(direction),
        score=72.0,
        sector_state="Leading",
        sector_percentile=58.0,
        catalyst_note=catalyst_note,
    )


def _scan(candidate: V3Candidate) -> V3Scan:
    scan = V3Scan(
        as_of="2026-08-14",
        regime="aggressive",
        regime_note="Aggressive long environment (score +2 of −5…+5; ≥+2 aggressive, "
        "≤−2 defensive, otherwise selective)",
        regime_detail="NIFTY trend +1 · India VIX 0 · Gamma +1 · Global 0 · Breadth 0",
        threshold=72.0,
        threshold_basis="aggressive regime (aggressive 72 · selective 76 · defensive 80)",
        liquid=473,
        with_setup=43,
        evaluated=43,
        cleared_floor=1,
        tier="ARCHIVE (NSE EOD)",
    )
    scan.trades = [candidate]
    return scan


def _surfaces(scan: V3Scan) -> list[str]:
    console = Console(width=200, force_terminal=False, no_color=True)
    with console.capture() as capture:
        console.print(render_v3(scan))
    return [capture.get(), build_v3_markdown(scan), build_v3_html(scan)]


# ── The setup label must match the direction being traded ─────────────────────


def test_short_reclaim_is_not_labelled_reclaim():
    """A short sweep-and-reject is a failed breakout. Calling it a "reclaim" says the
    opposite of what the trade is, on a card whose own note says "rejected"."""
    for surface in _surfaces(_scan(_candidate("short"))):
        assert "failed breakout" in surface
        # "reclaim" may not appear as this trade's name anywhere, in any surface.
        assert "reclaim" not in surface.lower()


def test_long_reclaim_is_still_a_reclaim():
    for surface in _surfaces(_scan(_candidate("long"))):
        assert "reclaim" in surface
        assert "failed breakout" not in surface


@pytest.mark.parametrize(
    ("kind", "long_label", "short_label"),
    [
        (SetupType.RECLAIM, "reclaim", "failed breakout"),
        (SetupType.BASE_BREAKOUT, "base breakout", "base breakdown"),
        # Symmetric in name as well as in mechanic — inventing a mirrored word here would
        # add a synonym, not information.
        (SetupType.CONTINUATION, "continuation", "continuation"),
    ],
)
def test_setup_labels_are_mirrored(kind, long_label, short_label):
    assert setup_label(kind, "long") == long_label
    assert setup_label(kind, "short") == short_label


def test_labels_never_leak_into_gating():
    """Display names are display-only. The carry exemption, the ``--setup`` filter and every
    backtest cohort key off the enum value, so the two must not converge."""
    from asymmetry.engines.carry import gate_applies

    assert gate_applies(SetupType.BASE_BREAKOUT) is True
    assert gate_applies(SetupType.RECLAIM) is False
    assert SetupType.RECLAIM.value == "reclaim"
    assert SetupType.BASE_BREAKOUT.value == "base-breakout"


# ── "Why now" has to admit when there is no answer ────────────────────────────


def test_absent_catalyst_is_stated_not_papered_over():
    """The §12 chain silently fell through to the setup's own note, so a name with a real
    earnings catalyst and a name with nothing but a tidy chart read identically."""
    candidate = _candidate("short")
    assert candidate.has_catalyst is False
    assert "no catalyst found" in candidate.why_now
    # The structural reason is still shown — the honesty is additive, not a deletion.
    assert "rejected by 10.2%" in candidate.why_now
    for surface in _surfaces(_scan(candidate)):
        assert "no catalyst found" in surface


def test_real_catalyst_displaces_the_fallback():
    candidate = _candidate("short", catalyst_note="earnings: Q1 miss, guidance cut")
    assert candidate.has_catalyst is True
    assert candidate.why_now == "earnings: Q1 miss, guidance cut"
    for surface in _surfaces(_scan(candidate)):
        assert "no catalyst found" not in surface


# ── Numbers the reader is asked to trust must carry their own derivation ──────


def test_regime_score_is_published_with_its_scale():
    for surface in _surfaces(_scan(_candidate())):
        assert "−5…+5" in surface
        assert "NIFTY trend +1" in surface


def test_quality_floor_says_where_it_came_from():
    for surface in _surfaces(_scan(_candidate())):
        assert "72" in surface
        assert "aggressive regime" in surface


def test_surfaces_say_how_many_hard_filters_were_armed():
    """The count is not a constant any more, so no surface may hard-code it — and "off" and
    "the feed is down" must never render as the same sentence."""
    armed = _scan(_candidate())
    armed.catalyst_status, armed.catalyst_required = "armed", True
    for surface in _surfaces(armed):
        assert "Five hard filters" in surface

    off = _scan(_candidate())
    off.catalyst_status, off.catalyst_required = "off", False
    for surface in _surfaces(off):
        assert "Four hard filters" in surface
        assert "outage" not in surface

    outage = _scan(_candidate())
    outage.catalyst_status, outage.catalyst_required = "outage", False
    for surface in _surfaces(outage):
        assert "Four hard filters" in surface
        assert "outage rather than an absence of news" in surface


def test_valid_fill_band_explains_its_own_asymmetry():
    """The band is solved backwards from the fixed stop, so the entry is not in its middle.
    Without saying so, the skew reads as an error rather than as the rule working."""
    for surface in _surfaces(_scan(_candidate())):
        assert "not a tolerance around" in surface
        assert "not centred on the entry" in surface
