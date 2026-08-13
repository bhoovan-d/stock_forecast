"""Engine 2 — the catalyst engine. "WHY should this stock move?"

Two sources feed it:

1. **News**, scored by the LLM cascade against the question that matters — does this change
   forward earnings/value expectations, not is it positive.
2. **Data-derived positioning**, from the delivery-percentage spike. The brief rules out
   insider filings and 13F as too slow, and NSE/BSE announcement APIs are blocked from
   here, so an unusual delivery spike alongside a turnover spike is the available proxy
   for institutional accumulation: volume alone is churn, volume *taken to delivery* is
   someone building a position.

Scores are centred on 50 (no catalyst), so a stock with nothing happening is neither
rewarded nor punished, and a negative catalyst pushes it below the field.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from loguru import logger

from ..data import universe as universe_mod
from ..intelligence import filings as fl_mod
from ..intelligence import news as news_mod
from ..intelligence.filings import fetch_filings
from ..intelligence.openai_compat import build_cascade
from ..models import CatalystExtraction, CatalystRecord, CatalystType
from ..storage import save_catalysts

# An item naming more than this many companies is a market wrap or a sector round-up, not
# a company-specific catalyst.
_MAX_TICKERS_PER_ITEM = 3
# Catalysts decay: a three-day-old order win is largely in the price.
_HALF_LIFE_HOURS = 36.0


def _freshness(published: datetime, reference: datetime | None = None) -> float:
    """Decay weight. ``reference`` must be the as-of moment, not wall-clock now.

    Anchoring to now() would decay every catalyst in a historical run to nearly zero,
    silently disabling the factor across the whole backtest.
    """
    now = reference or datetime.now(timezone.utc)
    age_hours = (now - published).total_seconds() / 3600
    return float(0.5 ** (max(age_hours, 0) / _HALF_LIFE_HOURS))


def _score_one(
    cascade, symbol_set: set[str], headline: str, summary: str,
    published, source: str, url: str,
) -> list[CatalystRecord]:
    """Score one item and fan it out to every ticker it concerns."""
    result = cascade.score(headline, summary)
    if result is None:
        return []
    extraction, provider = result

    base = extraction.score()
    if base == 50.0:
        return []  # no expectation change — exactly what the prompt is built to filter

    return [
        CatalystRecord(
            published=published.replace(tzinfo=None),
            symbol=symbol,
            headline=headline[:400],
            url=url,
            source=source,
            catalyst_type=extraction.catalyst_type.value,
            expectation_delta=extraction.expectation_delta,
            materiality=extraction.materiality,
            durability=extraction.durability,
            already_priced=extraction.already_priced,
            confidence=extraction.confidence,
            score=base,
            rationale=extraction.rationale,
            provider=provider,
        )
        for symbol in symbol_set
    ]


def _event_record(filing) -> CatalystRecord:
    """A filing whose *occurrence* is the signal, not its text.

    Results are the main case. The subject line confirms results were approved, but the
    actual numbers are inside a multi-megabyte PDF, so no text model can read the surprise
    from what we have. Scoring it as a mild positive would be inventing information.

    Instead this is recorded as a neutral-but-flagged event: it marks the stock as "just
    reported", and the volume/delivery factors carry the market's own verdict on whether
    the numbers were good. The brief surfaces it so you know to look.
    """
    catalyst_type, durability = fl_mod.EVENT_SUBCATS.get(
        filing.subcategory, ("none", 0)
    )
    return CatalystRecord(
        published=filing.published.replace(tzinfo=None),
        symbol=filing.symbol,
        headline=filing.headline[:400],
        url=filing.url,
        source=f"BSE {filing.subcategory}",
        catalyst_type=catalyst_type,
        expectation_delta=0,
        materiality=1,
        durability=durability,
        confidence=1,
        # Neutral by construction: the event is known, the direction is not.
        score=50.0,
        rationale=f"{filing.subcategory} filed — direction unknown from the filing itself",
        provider="rule",
    )


def _sast_record(filing) -> CatalystRecord:
    """Substantial-acquisition disclosure — someone crossed a shareholding threshold.

    This is the promoter/institutional-activity catalyst, taken from a primary disclosure
    rather than the slow insider/13F route the brief rules out. Direction is not parseable
    from the subject line (an acquirer building a stake and one exiting file under the same
    regulation), so this is scored as a modest positive on attention rather than a strong
    directional call.
    """
    return CatalystRecord(
        published=filing.published.replace(tzinfo=None),
        symbol=filing.symbol,
        headline=filing.headline[:400],
        url=filing.url,
        source=f"BSE {filing.subcategory}",
        catalyst_type="promoter_institutional",
        expectation_delta=1,
        materiality=1,
        durability=2,
        confidence=1,
        score=CatalystExtraction(
            catalyst_type=CatalystType.PROMOTER_INSTITUTIONAL,
            expectation_delta=1, materiality=1, durability=2, confidence=1,
        ).score(),
        rationale="SAST threshold disclosure — substantial shareholding change",
        provider="rule",
    )


def score_news(
    as_of: date, max_items: int = 120, max_filings: int = 90
) -> list[CatalystRecord]:
    """Score BSE filings and news feeds. Returns persisted-shape records.

    Filings are processed first and get the larger share of the budget: they are the
    primary-source disclosures that actually move forward numbers, they resolve to tickers
    exactly via ISIN, and they cover far more of the universe than the news feeds do.
    """
    cascade = build_cascade()
    if not cascade.available:
        logger.warning("[catalyst] no LLM providers configured — skipping scoring")
        return []

    records: list[CatalystRecord] = []

    # ── BSE filings (primary source) ──────────────────────────────────────────
    # Routed by subcategory rather than all sent to the LLM: most filings are procedural,
    # and paying for a model call to be told "Record Date scores zero" is pure waste.
    try:
        filings = fetch_filings(as_of, lookback_days=3)
        routed = {"skip": 0, "event": 0, "sast": 0, "llm": 0}
        llm_budget = max_filings

        for filing in filings:
            decision = filing.route
            routed[decision] += 1

            if decision == "skip":
                continue

            if decision == "event":
                records.append(_event_record(filing))
                continue

            if decision == "sast":
                records.append(_sast_record(filing))
                continue

            if llm_budget > 0:
                llm_budget -= 1
                records.extend(
                    _score_one(
                        cascade, {filing.symbol}, filing.headline, filing.summary,
                        filing.published, f"BSE {filing.subcategory or filing.category}",
                        filing.url,
                    )
                )

        logger.info(
            f"[catalyst] filings routed — skip {routed['skip']}, event {routed['event']}, "
            f"sast {routed['sast']}, llm {routed['llm']}"
        )
    except Exception as exc:  # noqa: BLE001 — filings must never break the scan
        logger.warning(f"[catalyst] filings failed: {exc}")

    # ── News feeds (secondary, adds market interpretation) ────────────────────
    universe = universe_mod.load_universe()
    aliases = news_mod.build_alias_map(universe)
    items = news_mod.fetch_news()

    scored = 0
    for item in items:
        if scored >= max_items:
            break
        tickers = news_mod.resolve_tickers(item, aliases)
        if not tickers or len(tickers) > _MAX_TICKERS_PER_ITEM:
            continue
        scored += 1
        records.extend(
            _score_one(
                cascade, tickers, item.headline, item.summary,
                item.published, item.source, item.url,
            )
        )

    logger.info(
        f"[catalyst] {len(records)} ticker-catalysts across "
        f"{len({r.symbol for r in records})} symbols"
    )
    save_catalysts(records)
    return records


def delivery_spike_scores(history: pd.DataFrame, symbols: list[str]) -> dict[str, float]:
    """Institutional-accumulation proxy: delivery % and turnover both unusually high.

    Requiring *both* is the point. A turnover spike on falling delivery is intraday churn;
    a turnover spike where the shares are actually taken to delivery is positioning.
    """
    if "delivery_pct" not in history.columns:
        return {}

    delivery = history.pivot_table(index="date", columns="symbol", values="delivery_pct")
    turnover = history.pivot_table(index="date", columns="symbol", values="turnover")

    out: dict[str, float] = {}
    for symbol in symbols:
        if symbol not in delivery.columns or symbol not in turnover.columns:
            continue
        d_series = delivery[symbol].dropna()
        t_series = turnover[symbol].dropna()
        if len(d_series) < 25 or len(t_series) < 25:
            continue

        d_base, d_std = d_series.iloc[-21:-1].mean(), d_series.iloc[-21:-1].std()
        t_base, t_std = t_series.iloc[-21:-1].mean(), t_series.iloc[-21:-1].std()
        if not np.isfinite(d_std) or d_std == 0 or not np.isfinite(t_std) or t_std == 0:
            continue

        d_z = (d_series.iloc[-1] - d_base) / d_std
        t_z = (t_series.iloc[-1] - t_base) / t_std
        if d_z > 1.5 and t_z > 1.5:
            # Cap the contribution: this is a supporting signal, not a thesis.
            out[symbol] = float(min(15.0, 5.0 * min(d_z, t_z)))
    return out


def catalyst_scores_for(
    symbols: list[str], as_of: date, *, refresh: bool = True
) -> tuple[dict[str, float], dict[str, str]]:
    """Combined catalyst score (0-100, 50 = neutral) and a human-readable note.

    ``refresh`` fetches live news, which only makes sense for a run on the current day.
    Historical runs read stored catalysts bounded by ``as_of`` instead — today's headlines
    say nothing about what was knowable in March.
    """
    from ..storage import load_catalysts, load_history

    is_current = as_of >= date.today() - timedelta(days=1)
    if refresh and not is_current:
        logger.info(f"[catalyst] historical run ({as_of}) — using stored catalysts only")
    if refresh and is_current:
        try:
            score_news(as_of)
        except Exception as exc:  # noqa: BLE001 — news must never break the scan
            logger.warning(f"[catalyst] news scoring failed: {exc}")

    scores: dict[str, float] = {}
    notes: dict[str, str] = {}

    stored = load_catalysts(since_days=5, as_of=as_of)
    if not stored.empty:
        stored = stored[stored["symbol"].isin(symbols)]
        # End of the as-of day, so a catalyst published that morning is still fresh.
        reference = datetime.combine(as_of, datetime.max.time()).replace(tzinfo=timezone.utc)
        for symbol, group in stored.groupby("symbol"):
            weighted, weight_sum, best = 0.0, 0.0, None
            for row in group.itertuples():
                published = row.published
                if isinstance(published, str):
                    published = datetime.fromisoformat(published)
                if published.tzinfo is None:
                    published = published.replace(tzinfo=timezone.utc)
                weight = _freshness(published, reference)
                weighted += (row.score - 50.0) * weight
                weight_sum += weight
                if best is None or abs(row.score - 50) > abs(best.score - 50):
                    best = row
            if weight_sum > 0:
                scores[symbol] = float(np.clip(50 + weighted / weight_sum, 0, 100))
                if best is not None:
                    notes[symbol] = f"{best.catalyst_type}: {best.rationale}".strip(": ")

    # Delivery spikes nudge the score; they never create a catalyst on their own.
    history = load_history(days=60, end=as_of)
    if not history.empty:
        for symbol, bump in delivery_spike_scores(history, symbols).items():
            scores[symbol] = float(np.clip(scores.get(symbol, 50.0) + bump, 0, 100))
            note = notes.get(symbol, "")
            notes[symbol] = (note + " · " if note else "") + "delivery+turnover spike"

    logger.info(f"[catalyst] {len(scores)} symbols carry a catalyst score")
    return scores, notes
