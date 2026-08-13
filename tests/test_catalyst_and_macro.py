"""Catalyst scoring semantics, ticker resolution, and the macro R² guard."""

from __future__ import annotations

import numpy as np
import pytest

from asymmetry.config import settings
from asymmetry.engines.macro import ridge_fit
from asymmetry.intelligence.news import NewsItem, build_alias_map, resolve_tickers
from asymmetry.models import CatalystExtraction, CatalystType


class _Stock:
    def __init__(self, company: str, sector: str = "X"):
        self.company = company
        self.sector = sector


UNIVERSE = {
    "ACE": _Stock("Action Construction Equipment Ltd."),
    "PERSISTENT": _Stock("Persistent Systems Ltd."),
    "FEDERALBNK": _Stock("Federal Bank Ltd."),
    "IEX": _Stock("Indian Energy Exchange Ltd."),
    "AXISBANK": _Stock("Axis Bank Ltd."),
    "TCS": _Stock("Tata Consultancy Services Ltd."),
    "CIPLA": _Stock("Cipla Ltd."),
}


def _item(headline: str, summary: str = "") -> NewsItem:
    from datetime import datetime, timezone

    return NewsItem(headline, summary, "", "test", datetime.now(timezone.utc))


# ── Catalyst scoring semantics ────────────────────────────────────────────────


def test_positive_news_with_no_expectation_change_scores_neutral():
    """The distinction the whole engine exists for: tone is not a catalyst."""
    award = CatalystExtraction(
        catalyst_type=CatalystType.NONE,
        expectation_delta=0,
        materiality=3,
        durability=3,
        confidence=3,
    )
    assert award.score() == 50.0


def test_expectation_change_moves_the_score_off_neutral():
    upgrade = CatalystExtraction(
        catalyst_type=CatalystType.ORDER_WIN,
        expectation_delta=3,
        materiality=3,
        durability=3,
        confidence=3,
    )
    downgrade = upgrade.model_copy(update={"expectation_delta": -3})
    assert upgrade.score() > 50
    assert downgrade.score() < 50


def test_score_always_stays_within_0_100():
    """Every factor in the selection engine is a 0-100 percentile.

    An earlier formula could return 150 for a strong catalyst, which silently inflated the
    weighted total score and let one factor dominate the ranking.
    """
    for delta in range(-3, 4):
        for materiality in range(4):
            for durability in range(4):
                for confidence in range(4):
                    for priced in (True, False):
                        score = CatalystExtraction(
                            catalyst_type=CatalystType.ORDER_WIN,
                            expectation_delta=delta,
                            materiality=materiality,
                            durability=durability,
                            confidence=confidence,
                            already_priced=priced,
                        ).score()
                        assert 0.0 <= score <= 100.0, (
                            f"score {score} out of range for delta={delta}"
                        )


def test_strongest_catalyst_outranks_a_weak_one():
    strong = CatalystExtraction(
        catalyst_type=CatalystType.ORDER_WIN,
        expectation_delta=3, materiality=3, durability=3, confidence=3,
    )
    weak = CatalystExtraction(
        catalyst_type=CatalystType.ORDER_WIN,
        expectation_delta=1, materiality=1, durability=0, confidence=1,
    )
    assert strong.score() > weak.score() > 50.0


def test_already_priced_catalyst_is_discounted():
    fresh = CatalystExtraction(
        catalyst_type=CatalystType.EARNINGS_SURPRISE,
        expectation_delta=2,
        materiality=2,
        durability=2,
        confidence=2,
    )
    stale = fresh.model_copy(update={"already_priced": True})
    assert stale.score() < fresh.score()


def test_catalyst_type_none_cannot_score():
    """A non-zero delta with no identified catalyst type is not actionable."""
    assert CatalystExtraction(
        catalyst_type=CatalystType.NONE, expectation_delta=3, materiality=3
    ).score() == 50.0


# ── Ticker resolution ─────────────────────────────────────────────────────────


def test_english_word_tickers_do_not_match_prose():
    """ACE, and single-word company names, must not match ordinary text.

    These are the exact false positives seen against live feeds: "ace" in prose tagging
    ACE, and "persistent inflation" tagging PERSISTENT.
    """
    aliases = build_alias_map(UNIVERSE)
    hits = resolve_tickers(
        _item("Markets ace the inflation test", "persistent inflation remains a concern"),
        aliases,
    )
    assert "ACE" not in hits
    assert "PERSISTENT" not in hits


def test_institutional_names_do_not_match_similar_companies():
    """"Federal Reserve" is not Federal Bank; "Exchange Board" is not Indian Energy Exchange."""
    aliases = build_alias_map(UNIVERSE)
    hits = resolve_tickers(
        _item(
            "US Fed holds rates",
            "The Federal Reserve left rates unchanged. Separately the Securities and "
            "Exchange Board of India issued a circular.",
        ),
        aliases,
    )
    assert "FEDERALBNK" not in hits
    assert "IEX" not in hits


def test_real_company_mentions_still_resolve():
    aliases = build_alias_map(UNIVERSE)
    assert "AXISBANK" in resolve_tickers(_item("Axis Bank raises $300 million via bonds"), aliases)
    assert "TCS" in resolve_tickers(_item("TCS shares tumble 4% after resignation"), aliases)
    assert "CIPLA" in resolve_tickers(_item("Cipla gets USFDA approval for plant"), aliases)


# ── Macro ─────────────────────────────────────────────────────────────────────


def test_ridge_recovers_known_betas():
    rng = np.random.default_rng(0)
    X = rng.normal(size=(500, 3))
    true_betas = np.array([1.5, -0.8, 0.3])
    y = X @ true_betas + rng.normal(scale=0.05, size=500)

    betas, r2 = ridge_fit(X, y, alpha=1e-6)
    assert betas == pytest.approx(true_betas, abs=0.05)
    assert r2 > 0.95


def test_ridge_reports_low_r2_on_noise():
    """The R² guard depends on R² being honest about an unexplained series."""
    rng = np.random.default_rng(1)
    X = rng.normal(size=(400, 4))
    y = rng.normal(size=400)  # unrelated to X

    _, r2 = ridge_fit(X, y, alpha=settings.macro_ridge_alpha)
    assert r2 < settings.macro_min_r2, "pure noise must not be treated as a reliable model"


def test_ridge_penalty_shrinks_betas():
    rng = np.random.default_rng(2)
    X = rng.normal(size=(200, 3))
    y = X @ np.array([2.0, 2.0, 2.0]) + rng.normal(scale=0.1, size=200)

    weak, _ = ridge_fit(X, y, alpha=1e-6)
    strong, _ = ridge_fit(X, y, alpha=500.0)
    assert np.abs(strong).sum() < np.abs(weak).sum()
