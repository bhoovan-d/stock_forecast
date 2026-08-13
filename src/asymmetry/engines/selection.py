"""Engine 3 — stock selection. "Which stock is most likely to move?"

Five factors, each ranked cross-sectionally across the liquid universe so the scores are
comparable: relative strength, volume/participation, price structure, catalyst, liquidity.

Ranking is deliberately cross-sectional rather than absolute. "Up 6% in 20 days" means
nothing on its own; "in the top 5% of the market over 20 days" is the actual claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..data import MarketData
from ..data import nse_archive, universe as universe_mod, yahoo
from ..models import Candidate, FactorScores, ScanResult
from .indicators import atr, ema, normalise_rank, pct_change_over
from .sectors import build_sector_composites, composite_returns
from .trade import build_plan, compression_score, intraday_trigger_note

# Horizons for relative strength. Blending short and medium horizons favours stocks that
# are both trending and accelerating, rather than a single stale 6-month winner.
_RS_HORIZONS = {5: 0.2, 20: 0.45, 60: 0.35}


def _wide(history: pd.DataFrame, field: str) -> pd.DataFrame:
    return history.pivot_table(index="date", columns="symbol", values=field).sort_index()


def _relative_strength(
    closes: pd.DataFrame, benchmark: pd.Series, sector_returns: dict[str, float] | None = None,
    sectors: dict[str, str] | None = None,
) -> pd.Series:
    """Blended excess return vs NIFTY, then adjusted for the stock's own sector.

    Outperforming a weak sector is a different and better signal than being carried by a
    strong one, so sector-relative strength is added on top of index-relative strength.
    """
    scores = pd.Series(0.0, index=closes.columns, dtype=float)
    weight_total = 0.0

    for periods, weight in _RS_HORIZONS.items():
        if len(closes) <= periods + 1 or len(benchmark) <= periods + 1:
            continue
        stock_ret = (closes.iloc[-1] / closes.iloc[-periods - 1] - 1) * 100
        bench_ret = (benchmark.iloc[-1] / benchmark.iloc[-periods - 1] - 1) * 100
        excess = stock_ret - bench_ret

        if sector_returns and sectors:
            sector_excess = pd.Series(
                [
                    excess.get(sym, np.nan)
                    - (sector_returns.get(f"{sectors.get(sym)}:{periods}", 0.0) - bench_ret)
                    for sym in closes.columns
                ],
                index=closes.columns,
            )
            # Half weight on index-relative, half on sector-relative.
            excess = 0.5 * excess + 0.5 * sector_excess.fillna(excess)

        scores = scores.add(normalise_rank(excess) * weight, fill_value=0.0)
        weight_total += weight

    return scores / weight_total if weight_total else scores


def _sector_returns(
    sectors: set[str],
    as_of: date | None = None,
    *,
    history: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
) -> dict[str, float]:
    """Return of each sector over each RS horizon, keyed "<sector>:<periods>".

    Traded NSE sector indices are preferred where they exist; the remaining sectors — 12 of
    20, covering 212 stocks including all of Capital Goods — get an equal-weight composite
    built from the universe. Without this, those stocks had no sector benchmark and their
    "sector-relative" strength was quietly just index-relative.
    """
    out: dict[str, float] = {}

    for sector in sectors:
        symbol = yahoo.SECTOR_INDEX_SYMBOLS.get(sector)
        if symbol is None:
            continue
        frame = yahoo.fetch_chart(symbol, range_="1y", interval="1d", as_of=as_of)
        if frame is None or frame.empty:
            continue
        for periods in _RS_HORIZONS:
            value = pct_change_over(frame["close"], periods)
            if np.isfinite(value):
                out[f"{sector}:{periods}"] = value

    if history is not None and sector_map:
        composites = build_sector_composites(history, sector_map)
        if not composites.empty:
            # Traded indices win where both exist; composites fill the gaps.
            for key, value in composite_returns(composites, list(_RS_HORIZONS)).items():
                out.setdefault(key, value)

    covered = {k.split(":")[0] for k in out}
    logger.info(
        f"[selection] sector benchmarks: {len(covered)}/{len(sectors)} sectors covered"
    )
    return out


def _volume_factor(history: pd.DataFrame, symbols: list[str]) -> pd.Series:
    """Volume expansion plus delivery-percentage expansion.

    Delivery % is the institutional-participation proxy: volume without delivery is
    intraday churn, while volume *with* rising delivery is real positioning being built.
    """
    volumes = _wide(history, "volume")
    scores = {}
    delivery = (
        _wide(history, "delivery_pct") if "delivery_pct" in history.columns else pd.DataFrame()
    )

    for symbol in symbols:
        if symbol not in volumes.columns:
            continue
        series = volumes[symbol].dropna()
        if len(series) < 25:
            continue
        recent = series.tail(3).mean()
        baseline = series.tail(23).head(20).mean()
        vol_ratio = recent / baseline if baseline > 0 else np.nan

        deliv_ratio = np.nan
        if symbol in delivery.columns:
            dseries = delivery[symbol].dropna()
            if len(dseries) >= 25:
                d_recent = dseries.tail(3).mean()
                d_base = dseries.tail(23).head(20).mean()
                deliv_ratio = d_recent / d_base if d_base > 0 else np.nan

        if np.isfinite(vol_ratio) and np.isfinite(deliv_ratio):
            scores[symbol] = 0.6 * vol_ratio + 0.4 * deliv_ratio
        elif np.isfinite(vol_ratio):
            scores[symbol] = vol_ratio

    return normalise_rank(pd.Series(scores, dtype=float))


def _structure_factor(history: pd.DataFrame, symbols: list[str]) -> pd.Series:
    """Distance from 52w high, EMA alignment, and base compression."""
    closes = _wide(history, "close")
    highs = _wide(history, "high")
    lows = _wide(history, "low")

    raw = {}
    for symbol in symbols:
        if symbol not in closes.columns:
            continue
        close = closes[symbol].dropna()
        if len(close) < 60:
            continue

        price = float(close.iloc[-1])
        year_high = float(close.tail(252).max())
        # Near the highs is strength; this is a proximity score, not a "cheapness" score.
        proximity = 100 * price / year_high if year_high > 0 else np.nan

        e20 = float(ema(close, 20).iloc[-1])
        e50 = float(ema(close, 50).iloc[-1])
        alignment = sum([price > e20, e20 > e50, price > e50]) / 3 * 100

        frame = pd.DataFrame(
            {"high": highs[symbol], "low": lows[symbol], "close": closes[symbol]}
        ).dropna()
        compression = compression_score(frame)

        raw[symbol] = 0.4 * proximity + 0.35 * alignment + 0.25 * compression

    return normalise_rank(pd.Series(raw, dtype=float))


def _liquidity_factor(stats: pd.DataFrame) -> pd.Series:
    return normalise_rank(stats.set_index("symbol")["median_turnover"])


def _ensure_stored(as_of: date) -> None:
    """Ingest ``as_of`` if the archive has it but the store does not.

    The bhavcopy is already cached on disk by this point, so this is a parse-and-insert
    rather than a download, and it keeps the brief's date and its data in agreement.
    """
    from ..storage import latest_stored_date, store_day

    latest = latest_stored_date()
    if latest is not None and latest >= as_of:
        return
    if store_day(as_of):
        logger.info(f"[selection] ingested {as_of} (store was at {latest})")
    else:
        logger.warning(
            f"[selection] no bhavcopy for {as_of}; scoring from data up to {latest}"
        )


@dataclass
class ScoredUniverse:
    """Everything the ranking produced, before the trade engine narrows it down."""

    as_of: date
    rows: list[tuple[str, FactorScores, float]]
    liquid: dict
    history: pd.DataFrame
    closes: pd.DataFrame
    catalyst_notes: dict[str, str]
    universe_size: int


def build_scores(
    as_of: date | None = None,
    *,
    use_catalyst: bool = True,
    refresh_catalysts: bool = True,
    history: pd.DataFrame | None = None,
) -> ScoredUniverse:
    """Score every liquid stock. Shared by the live scan and the backtest.

    Extracted so the backtest can evaluate the *whole* universe: a 10-name shortlist is far
    too small a sample to separate signal from noise, and quintile spreads need the full
    cross-section to mean anything.

    ``history`` lets a caller supply pre-loaded bars. The backtest passes one frame for the
    whole walk and slices it per day — without that it re-read ~500k rows from SQLite for
    every sampled day, which dominated the runtime completely.
    """
    from ..storage import load_history

    as_of = as_of or nse_archive.last_trading_day() or date.today()

    stocks = universe_mod.load_universe()
    if history is None:
        # The archive can publish a session the store has not ingested yet. Dating a brief
        # to that day while scoring it from the previous day's bars produces a report whose
        # header and contents disagree — breadth and A/D silently stay a day stale.
        _ensure_stored(as_of)
        history = load_history(days=400, end=as_of)
    else:
        # Caller-supplied history must still be truncated: a backtest day may not see bars
        # after itself.
        history = history[history["date"] <= as_of]
    if history.empty:
        raise RuntimeError("No stored history — run `asymmetry backfill` first.")

    # Restrict to universe members before the liquidity gate so the ranks are computed
    # over the tradeable set, not over every listed instrument.
    history = history[history["symbol"].isin(stocks)]
    liquid, liq_stats = universe_mod.apply_liquidity_gate(stocks, history)
    symbols = sorted(liquid)
    history = history[history["symbol"].isin(symbols)]

    closes = _wide(history, "close")
    nifty = yahoo.fetch_chart("^NSEI", range_="1y", interval="1d", as_of=as_of)
    if nifty is None or nifty.empty:
        raise RuntimeError("NIFTY benchmark unavailable")
    benchmark = nifty["close"].reset_index(drop=True)

    sector_map = {s: liquid[s].sector for s in symbols}
    sector_rets = _sector_returns(
        {st.sector for st in liquid.values()},
        as_of,
        history=history,
        sector_map=sector_map,
    )

    rs = _relative_strength(closes, benchmark, sector_rets, sector_map)
    vol = _volume_factor(history, symbols)
    structure = _structure_factor(history, symbols)
    liquidity = _liquidity_factor(liq_stats[liq_stats["symbol"].isin(symbols)])

    catalyst_scores: dict[str, float] = {}
    catalyst_notes: dict[str, str] = {}
    if use_catalyst:
        from .catalyst import catalyst_scores_for

        catalyst_scores, catalyst_notes = catalyst_scores_for(
            symbols, as_of, refresh=refresh_catalysts
        )

    rows = []
    for symbol in symbols:
        factors = FactorScores(
            relative_strength=float(rs.get(symbol, np.nan)),
            volume=float(vol.get(symbol, np.nan)),
            price_structure=float(structure.get(symbol, np.nan)),
            catalyst=float(catalyst_scores.get(symbol, 50.0)),
            liquidity=float(liquidity.get(symbol, np.nan)),
        )
        if not np.isfinite(factors.relative_strength) or not np.isfinite(
            factors.price_structure
        ):
            continue

        total = (
            settings.weight_relative_strength * factors.relative_strength
            + settings.weight_volume * np.nan_to_num(factors.volume, nan=50.0)
            + settings.weight_price_structure * factors.price_structure
            + settings.weight_catalyst * factors.catalyst
            + settings.weight_liquidity * np.nan_to_num(factors.liquidity, nan=50.0)
        )
        rows.append((symbol, factors, float(total)))

    rows.sort(key=lambda r: r[2], reverse=True)
    return ScoredUniverse(
        as_of=as_of,
        rows=rows,
        liquid=liquid,
        history=history,
        closes=closes,
        catalyst_notes=catalyst_notes,
        universe_size=len(stocks),
    )


def score_universe(
    as_of: date | None = None,
    *,
    use_catalyst: bool = False,
    with_regime: bool = True,
    history: pd.DataFrame | None = None,
):
    """(symbol -> score, regime) across the whole liquid universe. Used by the backtest."""
    from .regime import assess_regime

    scored = build_scores(as_of, use_catalyst=use_catalyst, history=history)
    scores = {symbol: total for symbol, _, total in scored.rows}
    regime = assess_regime(scored.as_of) if with_regime else None
    return scores, regime


def run_selection(
    as_of: date | None = None,
    *,
    top_n: int = 10,
    use_catalyst: bool = True,
    refresh_catalysts: bool = True,
    data: MarketData | None = None,
) -> ScanResult:
    data = data or MarketData()
    scored = build_scores(
        as_of, use_catalyst=use_catalyst, refresh_catalysts=refresh_catalysts
    )

    as_of = scored.as_of
    rows, liquid = scored.rows, scored.liquid
    history, closes = scored.history, scored.closes
    catalyst_notes = scored.catalyst_notes

    logger.info(f"[selection] scored {len(rows)} stocks; building plans for top {top_n * 3}")

    candidates: list[Candidate] = []
    watchlist: list[Candidate] = []

    # Build plans for a wider slice than requested: the R:R gate rejects many, and we
    # still want a full shortlist of names that actually pass it.
    for symbol, factors, total in rows[: top_n * 3]:
        stock = liquid[symbol]
        stock_daily = pd.DataFrame(
            {
                "high": _wide(history, "high")[symbol],
                "low": _wide(history, "low")[symbol],
                "close": closes[symbol],
            }
        ).dropna()

        plan, reason = build_plan(stock_daily)
        candidate = Candidate(
            symbol=symbol,
            company=stock.company,
            sector=stock.sector,
            close=float(closes[symbol].dropna().iloc[-1]),
            factors=factors,
            total_score=round(total, 2),
            catalyst_note=catalyst_notes.get(symbol, ""),
            plan=plan,
            rejected_reason=reason,
        )

        if plan is not None:
            candidates.append(candidate)
        elif "insufficient" not in reason and "no valid price" not in reason:
            watchlist.append(candidate)

        if len(candidates) >= top_n:
            break

    from .regime import assess_regime

    return ScanResult(
        as_of=as_of,
        regime=assess_regime(as_of, data),
        candidates=candidates,
        watchlist=watchlist[:5],
        universe_size=scored.universe_size,
        liquid_size=len(rows),
        tier=data.session_tier.label,
    )


def attach_macro(result: ScanResult) -> ScanResult:
    """Run Engine 4 over the shortlist only — the regression is too heavy for 500 names."""
    from .macro import macro_gap

    for candidate in result.candidates:
        candidate.macro = macro_gap(
            candidate.symbol, candidate.sector, as_of=result.as_of
        )
    return result


def attach_earnings(result: ScanResult) -> ScanResult:
    """Flag shortlisted names that just reported or are about to.

    Deliberately a warning rather than a score adjustment: an imminent result invalidates
    the risk model behind the plan, and that is a decision for the trader, not something to
    quietly average into a ranking.
    """
    from ..intelligence.earnings import earnings_flags

    try:
        upcoming, reported = earnings_flags(result.as_of)
    except Exception as exc:  # noqa: BLE001 — never break the scan over a calendar
        logger.warning(f"[earnings] calendar unavailable: {exc}")
        return result

    for candidate in result.candidates:
        if candidate.symbol in upcoming:
            due = upcoming[candidate.symbol]
            candidate.earnings_flag = (
                f"⚠ results due {due:%d %b} — a gap can jump the stop; size down or wait"
            )
        elif candidate.symbol in reported:
            candidate.earnings_flag = "results just reported — reaction is the signal"
    return result


def attach_intraday(result: ScanResult, data: MarketData) -> ScanResult:
    """Add the 5m/3m trigger read for each shortlisted name."""
    for candidate in result.candidates:
        if candidate.plan is None:
            continue
        bars = data.intraday(candidate.symbol, interval="5m", days=2)
        if bars.ok:
            note = intraday_trigger_note(bars.value, candidate.plan.entry)
            candidate.plan.setup += f" · {note}"
    return result
