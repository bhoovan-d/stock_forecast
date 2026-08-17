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


def score_filings(
    as_of: date, *, lookback_days: int = 3, llm_budget: int = 90, cascade=None
) -> list[CatalystRecord]:
    """Score BSE filings for a date range. The only historically fetchable catalyst source.

    Split out from ``score_news`` so a backfill can reach into the past. The BSE
    announcements API takes an explicit ``strPrevDate``/``strToDate`` pair, so filings for
    July can still be pulled in August; the news RSS feeds serve roughly 48 hours and have
    no archive at all, which is why a historical catalyst record is filings-only and any
    measurement built on one understates live coverage.

    Routed by subcategory rather than all sent to the LLM: most filings are procedural, and
    paying for a model call to be told "Record Date scores zero" is pure waste.
    """
    records: list[CatalystRecord] = []
    try:
        filings = fetch_filings(as_of, lookback_days=lookback_days)
    except Exception as exc:  # noqa: BLE001 — filings must never break the scan
        logger.warning(f"[catalyst] filings failed: {exc}")
        return records

    routed = {"skip": 0, "event": 0, "sast": 0, "llm": 0}
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
        # The LLM routes are the only ones that cost anything, and the only ones that can
        # produce a *directional* catalyst. A backfill with no cascade still collects the
        # rule-routed events, which is worth having but is a weaker catalyst definition —
        # say so wherever the resulting measurement is reported.
        if cascade is not None and cascade.available and llm_budget > 0:
            llm_budget -= 1
            records.extend(
                _score_one(
                    cascade, {filing.symbol}, filing.headline, filing.summary,
                    filing.published, f"BSE {filing.subcategory or filing.category}",
                    filing.url,
                )
            )

    logger.info(
        f"[catalyst] {as_of} filings routed — skip {routed['skip']}, event {routed['event']}, "
        f"sast {routed['sast']}, llm {routed['llm']}"
    )
    return records


def backfill_filings(
    start: date, end: date, *, llm: bool = True, llm_budget_per_day: int = 40
) -> int:
    """Walk a date range collecting BSE filings into the catalyst store.

    Exists so the catalyst filter can be measured at all. Day by day rather than one wide
    range because the API paginates per query and a sixty-day pull silently truncates.
    Returns the number of records saved.
    """
    cascade = build_cascade() if llm else None
    if llm and (cascade is None or not cascade.available):
        logger.warning("[catalyst] no LLM provider — backfilling rule-routed filings only")
        cascade = None

    total, cursor = 0, start
    while cursor <= end:
        if cursor.weekday() < 5:  # filings land on trading days
            records = score_filings(
                cursor, lookback_days=1, llm_budget=llm_budget_per_day, cascade=cascade
            )
            if records:
                save_catalysts(records)
                total += len(records)
            logger.info(f"[catalyst] backfill {cursor}: {len(records)} records ({total} total)")
        cursor += timedelta(days=1)
    return total


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

    records: list[CatalystRecord] = list(
        score_filings(as_of, lookback_days=3, llm_budget=max_filings, cascade=cascade)
    )

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


def aggregate_catalysts(group, reference: datetime) -> tuple[float, str]:
    """Freshness-weighted score and headline note for one symbol's records.

    Extracted so the live scan and the backtest cannot drift apart on what "the catalyst as
    of this moment" means. ``reference`` is the as-of moment, never wall-clock now —
    anchoring to now decays every historical catalyst to nothing and silently disables the
    factor across a whole replay.
    """
    weighted, weight_sum, best, best_score = 0.0, 0.0, None, 50.0
    for row in group.itertuples():
        published = row.published
        if isinstance(published, str):
            published = datetime.fromisoformat(published)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        weight = _freshness(published, reference)
        # Clamped on read as well as on write. `CatalystExtraction.score` gained its clamp
        # after the fact, and the store still holds rows from before it — JIOFIN at 150 and
        # GODREJCP at -25, both on 12 Aug 2026. Clamping only on write leaves those rows
        # dragging every average they appear in, and a stored value cannot be re-derived.
        score = float(np.clip(row.score, 0.0, 100.0))
        weighted += (score - 50.0) * weight
        weight_sum += weight
        if best is None or abs(score - 50) > abs(best_score - 50):
            best, best_score = row, score
    if weight_sum <= 0:
        return 50.0, ""
    score = float(np.clip(50 + weighted / weight_sum, 0, 100))
    note = f"{best.catalyst_type}: {best.rationale}".strip(": ") if best is not None else ""
    return score, note


class CatalystHistory:
    """Point-in-time catalyst lookup over a whole replay window, loaded once.

    The live scan calls ``catalyst_scores_for`` once per run. A backtest asks the same
    question at thousands of decision moments, so it reads the store once here and slices
    in memory — a query per decision would dominate the replay.

    **Coverage is tracked separately from absence**, and that distinction is the whole
    reason this class exists. The store began on 11 Aug 2026; the intraday replay window
    reaches back roughly sixty sessions. A decision on 3 July has no catalyst records
    behind it because none were ever collected, not because the tape was quiet, and
    scoring that as "no catalyst" would measure the store's start date and call it a
    market finding. ``covered`` answers "could this date have had a catalyst at all", and
    uncovered decisions are excluded from the measurement rather than counted against it.

    Coverage is deliberately a property of the *date*, not the symbol: on a day the store
    knows about, a symbol with no record genuinely had no catalyst, which is exactly the
    fact the filter acts on.
    """

    def __init__(self, window_days: int = 5, *, end: date | None = None, span_days: int = 400):
        from ..storage import load_catalysts

        self.window_days = window_days
        self._frame = load_catalysts(since_days=span_days, as_of=end or date.today())
        self._by_symbol: dict[str, pd.DataFrame] = {}
        self._days: set[date] = set()
        if self._frame.empty:
            return
        published = pd.to_datetime(self._frame["published"])
        self._frame = self._frame.assign(_day=published.dt.date)
        self._days = set(self._frame["_day"])
        for symbol, group in self._frame.groupby("symbol"):
            self._by_symbol[str(symbol)] = group

    @property
    def empty(self) -> bool:
        return not self._days

    def covered(self, as_of: date) -> bool:
        """Does the store hold *any* record inside this decision's lookback window?"""
        start = as_of - timedelta(days=self.window_days)
        return any(start <= day <= as_of for day in self._days)

    def at(self, symbol: str, as_of: date) -> tuple[float, str]:
        """The catalyst score and note for ``symbol`` as known on ``as_of``."""
        group = self._by_symbol.get(symbol)
        if group is None:
            return 50.0, ""
        start = as_of - timedelta(days=self.window_days)
        window = group[(group["_day"] > start) & (group["_day"] <= as_of)]
        if window.empty:
            return 50.0, ""
        reference = datetime.combine(as_of, datetime.max.time()).replace(tzinfo=timezone.utc)
        return aggregate_catalysts(window, reference)


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
            score, note = aggregate_catalysts(group, reference)
            if note or score != 50.0:
                scores[symbol] = score
                if note:
                    notes[symbol] = note

    # Delivery spikes nudge the score; they never create a catalyst on their own.
    history = load_history(days=60, end=as_of)
    if not history.empty:
        for symbol, bump in delivery_spike_scores(history, symbols).items():
            scores[symbol] = float(np.clip(scores.get(symbol, 50.0) + bump, 0, 100))
            note = notes.get(symbol, "")
            notes[symbol] = (note + " · " if note else "") + "delivery+turnover spike"

    logger.info(f"[catalyst] {len(scores)} symbols carry a catalyst score")
    return scores, notes
