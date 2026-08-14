"""Orchestrator for the Engineer Brief specification.

Runs the full chain per candidate: liquidity → regime → catalyst → leadership → structure
→ volatility → F&O → entry/stop/target → probability → EV → verdict.

Order matters for cost as well as logic. The cheap cross-sectional filters run over the
whole universe first; the expensive per-symbol work (five timeframes of intraday data, an
F&O parse, a probability lookup) runs only on names that already survived. Fetching 15m
bars for 470 stocks to reject 460 of them would take hours and tell us nothing.
"""

from __future__ import annotations

import json
import time
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

from ..config import CACHE_DIR, settings
from ..data import MarketData, yahoo
from ..data import nse_archive
from ..spec import (
    FnOState,
    MTFChain,
    RejectReason,
    SpecCandidate,
    SpecScan,
    Verdict,
)
from .entry import EntryRejection, build_plan
from .fno import assess_fno, fno_score
from .probability import (
    SetupSignature,
    estimate_probability,
    expected_value,
    measure_base_rates,
)
from .structure import analyse_timeframe
from .volatility import assess_volatility, compression_score, move_feasibility

_RATES_CACHE = CACHE_DIR / "base_rates.json"


# ── Base-rate cache ───────────────────────────────────────────────────────────


def _load_rates(history: pd.DataFrame, *, refresh: bool = False) -> dict:
    """Base rates are expensive (~45s) and change slowly, so they are cached."""
    if not refresh and _RATES_CACHE.exists():
        try:
            raw = json.loads(_RATES_CACHE.read_text(encoding="utf-8"))
            return {
                SetupSignature(r["trend_up"], r["compressed"], r["distance_bucket"]): r["row"]
                for r in raw
            }
        except (json.JSONDecodeError, KeyError, OSError):
            logger.warning("[spec] base-rate cache unreadable, recomputing")

    logger.info("[spec] measuring historical base rates (one-off, ~45s)")
    rates = measure_base_rates(
        history, stop_pct=settings.max_stop_pct,
        reward_multiple=settings.min_reward_risk, max_symbols=150,
    )
    _RATES_CACHE.write_text(
        json.dumps(
            [
                {
                    "trend_up": s.trend_up, "compressed": s.compressed,
                    "distance_bucket": s.distance_bucket, "row": row,
                }
                for s, row in rates.items()
            ]
        ),
        encoding="utf-8",
    )
    return rates


# ── Per-symbol evaluation ─────────────────────────────────────────────────────


def _fetch_chain(symbol: str, as_of: date) -> dict[str, pd.DataFrame | None]:
    """The five timeframes the brief requires."""
    ysym = yahoo.to_yahoo_symbol(symbol)
    return {
        "weekly": yahoo.fetch_chart(ysym, range_="5y", interval="1wk", as_of=as_of),
        "daily": yahoo.fetch_chart(ysym, range_="2y", interval="1d", as_of=as_of),
        "hourly": yahoo.fetch_chart(ysym, range_="730d", interval="60m", as_of=as_of),
        "m30": yahoo.fetch_chart(ysym, range_="60d", interval="30m", as_of=as_of),
        "m15": yahoo.fetch_chart(ysym, range_="60d", interval="15m", as_of=as_of),
    }


def evaluate_symbol(
    symbol: str,
    as_of: date,
    *,
    stock,
    rates: dict,
    catalyst_score: float,
    catalyst_note: str,
    rs_nifty: dict[str, float],
    rs_sector: dict[str, float],
    rs_acceleration: float,
    sector_quadrant: str,
    regime_score: float,
    delivery_pct: float | None,
    fo_today: pd.DataFrame | None,
    fo_prev: pd.DataFrame | None,
) -> SpecCandidate:
    """Full spec evaluation of one name."""
    candidate = SpecCandidate(
        symbol=symbol,
        company=getattr(stock, "company", ""),
        sector=getattr(stock, "sector", ""),
        catalyst_score=catalyst_score,
        catalyst_note=catalyst_note,
        rs_nifty=rs_nifty,
        rs_sector=rs_sector,
        rs_acceleration=rs_acceleration,
        sector_quadrant=sector_quadrant,
        delivery_pct=delivery_pct,
    )

    frames = _fetch_chain(symbol, as_of)
    daily, weekly = frames["daily"], frames["weekly"]
    if daily is None or len(daily) < 60:
        candidate.reject_reason = RejectReason.DATA_QUALITY
        candidate.reject_detail = "insufficient daily history"
        return candidate

    candidate.close = float(daily["close"].iloc[-1])

    # ── Multi-timeframe chain ─────────────────────────────────────────────────
    chain = MTFChain(
        weekly=analyse_timeframe(weekly, "Weekly", ema_spans=(20, 50)) if weekly is not None else None,
        daily=analyse_timeframe(daily, "Daily", ema_spans=(20, 50, 200)),
        hourly=analyse_timeframe(frames["hourly"], "60m", ema_spans=(20, 50)) if frames["hourly"] is not None else None,
        m30=analyse_timeframe(frames["m30"], "30m", ema_spans=(20, 50)) if frames["m30"] is not None else None,
        m15=analyse_timeframe(frames["m15"], "15m", ema_spans=(9, 21)) if frames["m15"] is not None else None,
    )
    candidate.chain = chain

    # ── Volatility ────────────────────────────────────────────────────────────
    volatility = assess_volatility(daily, frames["hourly"])
    candidate.volatility = volatility

    # ── F&O ───────────────────────────────────────────────────────────────────
    fno = assess_fno(symbol, as_of, candidate.close, fo_today=fo_today, fo_prev=fo_prev)
    candidate.fno = fno
    candidate.instrument = "futures" if fno.has_fno else "equity MTF"

    # ── Entry / stop / target, with the hard gates ────────────────────────────
    try:
        plan = build_plan(
            symbol=symbol, chain=chain, volatility=volatility,
            daily=daily, weekly=weekly, m15=frames["m15"], m30=frames["m30"],
            call_oi_wall=fno.call_oi_wall,
        )
    except EntryRejection as rejection:
        candidate.reject_reason = rejection.reason
        candidate.reject_detail = rejection.detail
        candidate.verdict = (
            Verdict.WATCH
            if rejection.reason is RejectReason.NO_TRIGGER
            else Verdict.REJECT
        )
        return candidate

    candidate.plan = plan

    # ── Probability and expected value ────────────────────────────────────────
    signature = SetupSignature.build(
        chain.daily.trend if chain.daily else None, volatility, plan.target_pct
    )
    candidate.probability = estimate_probability(signature, rates)
    candidate.expected_value = expected_value(
        candidate.probability, settings.min_reward_risk
    )

    # EV is the ranking layer, but a negative-EV trade is still refused (§16, §18).
    if not candidate.expected_value.positive:
        candidate.verdict = Verdict.WATCH
        candidate.reject_reason = RejectReason.NEGATIVE_EV
        candidate.reject_detail = (
            f"EV {candidate.expected_value.ev_r:+.2f}R — "
            f"{candidate.expected_value.note}"
        )
    else:
        candidate.verdict = Verdict.TRADE

    # ── Master score (§17) ────────────────────────────────────────────────────
    feasibility, feasibility_note = move_feasibility(volatility, plan.target_pct)
    modules = {
        "catalyst": catalyst_score,
        "structure": chain.alignment_score,
        "relative_strength": float(np.mean(list(rs_nifty.values())) if rs_nifty else 50.0),
        "sector": float(np.mean(list(rs_sector.values())) if rs_sector else 50.0),
        "volume": min(100.0, volatility.relative_volume * 50),
        "volatility": (compression_score(volatility) + feasibility) / 2,
        "fno": fno_score(fno),
        "entry_quality": 100.0 - min(plan.stop_pct / settings.max_stop_pct, 1.0) * 50,
        "regime": regime_score,
    }
    weights = {
        "catalyst": settings.weight_catalyst,
        "structure": settings.weight_structure,
        "relative_strength": settings.weight_relative_strength,
        "sector": settings.weight_sector,
        "volume": settings.weight_volume,
        "volatility": settings.weight_volatility,
        "fno": settings.weight_fno,
        "entry_quality": settings.weight_entry_quality,
        "regime": settings.weight_regime,
    }
    candidate.module_scores = {k: round(v, 1) for k, v in modules.items()}
    candidate.score = round(sum(modules[k] * weights[k] for k in modules), 2)
    candidate.participation_note = feasibility_note
    return candidate


# ── Scan ──────────────────────────────────────────────────────────────────────


def run_spec_scan(
    as_of: date | None = None,
    *,
    max_evaluate: int = 40,
    use_catalyst: bool = True,
    refresh_catalysts: bool = False,
    refresh_rates: bool = False,
) -> SpecScan:
    """Full specification scan.

    ``max_evaluate`` bounds the expensive per-symbol pass. Names are pre-ranked by the
    cheap cross-sectional factors, so the budget is spent on the most promising candidates.
    """
    from ..storage import load_history
    from .regime import assess_regime
    from .selection import build_scores

    data = MarketData()
    as_of = as_of or nse_archive.last_trading_day() or date.today()

    scored = build_scores(as_of, use_catalyst=use_catalyst, refresh_catalysts=refresh_catalysts)
    history = load_history(days=400, end=as_of)
    rates = _load_rates(history, refresh=refresh_rates)

    regime = assess_regime(as_of, data)
    regime_score = float(np.clip(50 + regime.total * 10, 0, 100))

    scan = SpecScan(
        as_of=str(as_of),
        tier=data.session_tier.label,
        universe_size=scored.universe_size,
        liquid_size=len(scored.rows),
        market_regime=regime.verdict.value,
        market_note=f"{regime.verdict.headline} (score {regime.total:+d})",
    )

    fo_today = nse_archive.fetch_fo_bhavcopy(as_of)
    prior = nse_archive.trading_days(as_of - pd.Timedelta(days=10).to_pytimedelta(), as_of)
    fo_prev = nse_archive.fetch_fo_bhavcopy(prior[-2]) if len(prior) > 1 else None

    delivery = history[history["date"] == history["date"].max()].set_index("symbol")
    sector_returns = {}

    logger.info(
        f"[spec] evaluating top {max_evaluate} of {len(scored.rows)} liquid names "
        f"against the {settings.min_reward_risk:.0f}R / {settings.max_stop_pct:.1f}% spec"
    )

    started = time.time()
    for rank, (symbol, factors, _total) in enumerate(scored.rows[:max_evaluate], 1):
        stock = scored.liquid.get(symbol)
        if stock is None:
            continue

        candidate = evaluate_symbol(
            symbol, as_of, stock=stock, rates=rates,
            catalyst_score=factors.catalyst,
            catalyst_note=scored.catalyst_notes.get(symbol, ""),
            rs_nifty={"blended": factors.relative_strength},
            rs_sector={"blended": factors.relative_strength},
            rs_acceleration=0.0,
            sector_quadrant=_quadrant(regime.verdict.value, sector_returns.get(stock.sector)),
            regime_score=regime_score,
            delivery_pct=(
                float(delivery.at[symbol, "delivery_pct"])
                if symbol in delivery.index and pd.notna(delivery.at[symbol, "delivery_pct"])
                else None
            ),
            fo_today=fo_today, fo_prev=fo_prev,
        )

        if candidate.verdict is Verdict.TRADE:
            scan.trades.append(candidate)
        elif candidate.verdict is Verdict.WATCH:
            scan.watch.append(candidate)
        else:
            key = candidate.reject_reason.value or "unspecified"
            scan.reject_counts[key] = scan.reject_counts.get(key, 0) + 1
        scan.evaluated += 1

    scan.trades.sort(key=lambda c: (-c.expected_value.ev_r, -c.score))
    scan.watch.sort(key=lambda c: -c.score)
    for i, candidate in enumerate(scan.trades, 1):
        candidate.rank = i

    logger.info(
        f"[spec] {scan.evaluated} evaluated in {time.time() - started:.0f}s → "
        f"{len(scan.trades)} TRADE, {len(scan.watch)} WATCH, {scan.total_rejected} REJECT"
    )
    return scan


def _quadrant(market: str, sector_return: float | None) -> str:
    """Brief §5 — the four market/sector combinations."""
    market_strong = market == "aggressive"
    if sector_return is None:
        return f"{'strong' if market_strong else 'weak'} market / sector unknown"
    sector_strong = sector_return > 0
    return (
        f"{'strong' if market_strong else 'weak'} market / "
        f"{'strong' if sector_strong else 'weak'} sector"
    )
