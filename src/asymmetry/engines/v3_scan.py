"""V3 scan orchestration.

Two stages, for cost as much as for logic:

* **Stage 1** screens all ~473 liquid names entirely from stored daily bars. Weekly is
  resampled rather than fetched, so this costs zero network calls and runs in ~20s. It ends
  with the names that show one of V3's two permitted setups and can plausibly reach 4R.

* **Stage 2** fetches 15m and 60m only for those survivors, then applies the hard filters.

The ordering matters: pulling intraday data for 473 names to reject 450 of them would take
an hour and teach nothing. It is also what went wrong before — the old engine truncated to
the top-N by *leadership*, which is a different question from "can this stock travel 4R
against a 1.5% stop", and so it kept discarding the right candidates.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..data import MarketData, nse_archive, yahoo
from ..spec import SetupType, Trend
from .carry import CarryState, assess_carry, gate_applies
from .setups import SetupSignal, detect_setup
from .structure import analyse_timeframe, classify_structure
from .v3 import (
    SectorState,
    V3Plan,
    V3Reject,
    average_daily_range,
    build_v3_plan,
    composite_rs,
    move_feasible,
    quality_score,
    sector_states,
)
from .indicators import atr


@dataclass
class V3Candidate:
    symbol: str
    company: str = ""
    sector: str = ""
    direction: str = "long"
    close: float = 0.0

    setup: SetupSignal = field(default_factory=SetupSignal)
    plan: V3Plan | None = None
    score: float = 0.0
    modules: dict[str, float] = field(default_factory=dict)

    sector_state: str = ""
    sector_percentile: float = 50.0
    rs_nifty_pct: float = 50.0
    rs_sector_pct: float = 50.0
    rs_accel_pct: float = 50.0
    adr_pct: float = 0.0
    atr_pct: float = 0.0
    catalyst_note: str = ""
    catalyst_score: float = 50.0
    weekly_note: str = ""
    daily_note: str = ""
    hourly_note: str = ""
    # The trends themselves, not only their rendered form — the veto and the structure score
    # both need the enum, and keeping only the strings is why neither existed before.
    weekly_trend: Trend = Trend.SIDEWAYS
    daily_trend: Trend = Trend.SIDEWAYS
    carry: CarryState = field(default_factory=CarryState)
    rejected_by: str = ""
    reject_detail: str = ""

    @property
    def why_now(self) -> str:
        return self.catalyst_note or self.setup.note or "technical setup only"


@dataclass
class V3Scan:
    as_of: str
    regime: str = ""
    regime_note: str = ""
    universe: int = 0
    liquid: int = 0
    with_setup: int = 0
    evaluated: int = 0
    trades: list[V3Candidate] = field(default_factory=list)
    near_miss: list[V3Candidate] = field(default_factory=list)
    # How many cleared the quality floor before the daily cap was applied.
    cleared_floor: int = 0
    # Of the technical-validity rejections, how many were the carry gate specifically. The
    # funnel is only useful if it says which rung of the timeframe chain did the cutting.
    carry_rejected: int = 0
    reject_counts: dict[str, int] = field(default_factory=dict)
    tier: str = ""

    @property
    def rejected(self) -> int:
        return sum(self.reject_counts.values())


def _resample_weekly(daily: pd.DataFrame) -> pd.DataFrame:
    return (
        daily.resample("W")
        .agg({"high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )


def stage_one(
    as_of: date, *, history: pd.DataFrame | None = None
) -> tuple[list[V3Candidate], dict[str, SectorState], pd.DataFrame, dict]:
    """Screen the full liquid universe from stored bars. No network."""
    from ..data import universe as universe_mod
    from ..storage import load_history
    from .sectors import build_sector_composites

    history = history if history is not None else load_history(days=400, end=as_of)
    universe = universe_mod.load_universe()
    history = history[history["symbol"].isin(universe)]

    liquid, _stats = universe_mod.apply_liquidity_gate(universe, history)
    history = history[history["symbol"].isin(liquid)]
    sector_map = {s: liquid[s].sector for s in liquid}

    states = sector_states(history, sector_map)
    closes = history.pivot_table(index="date", columns="symbol", values="close").sort_index()
    composites = build_sector_composites(history, sector_map)
    benchmark_frame = build_sector_composites(history, {s: "ALL" for s in sector_map})
    benchmark = benchmark_frame["ALL"] if not benchmark_frame.empty else closes.mean(axis=1)
    rs = composite_rs(
        closes, benchmark, {c: composites[c] for c in composites.columns}, sector_map
    )

    history = history.copy()
    history["dt"] = pd.to_datetime(history["date"])

    candidates: list[V3Candidate] = []
    for symbol, group in history.groupby("symbol"):
        group = group.sort_values("dt").set_index("dt")
        daily = group[["high", "low", "close", "volume"]].astype(float)
        if len(daily) < 80:
            continue

        # Both directions are checked; V3 permits either.
        long_setup = detect_setup(daily, "long")
        short_setup = detect_setup(daily, "short")
        found = [s for s in (long_setup, short_setup) if s.found]
        if not found:
            continue
        setup = max(found, key=lambda s: s.quality)

        price = float(daily["close"].iloc[-1])
        day_atr = float(atr(daily["high"], daily["low"], daily["close"], 14).iloc[-1])
        atr_pct = day_atr / price * 100 if price else 0.0
        adr_pct = average_daily_range(daily)

        # Cheapest possible 4R feasibility check, using the tightest permitted stop. If even
        # that cannot reach the target, no intraday structure will rescue it.
        best_case_target = 4.0 * settings.min_stop_pct
        feasible, _score, _note = move_feasible(best_case_target, adr_pct, atr_pct)
        if not feasible:
            continue

        weekly = _resample_weekly(daily)
        weekly_state = analyse_timeframe(weekly, "Weekly", ema_spans=(20, 50))
        daily_state = analyse_timeframe(daily, "Daily", ema_spans=(20, 50))

        if not trend_permits(weekly_state.trend, daily_state.trend, setup.direction):
            continue

        stock = liquid[symbol]
        sector_state = states.get(stock.sector)
        rs_row = rs.loc[symbol] if symbol in rs.index else None

        candidates.append(
            V3Candidate(
                symbol=symbol,
                company=stock.company,
                sector=stock.sector,
                direction=setup.direction,
                close=price,
                setup=setup,
                sector_state=sector_state.state if sector_state else "",
                sector_percentile=sector_state.percentile if sector_state else 50.0,
                rs_nifty_pct=float(rs_row["rs_nifty_pct"]) if rs_row is not None else 50.0,
                rs_sector_pct=float(rs_row["rs_sector_pct"]) if rs_row is not None else 50.0,
                rs_accel_pct=float(rs_row["rs_accel_pct"]) if rs_row is not None else 50.0,
                adr_pct=round(adr_pct, 2),
                atr_pct=round(atr_pct, 2),
                weekly_note=f"{weekly_state.trend.value}, {weekly_state.structure or 'no structure'}",
                daily_note=f"{daily_state.trend.value}, {daily_state.setup.value}",
                weekly_trend=weekly_state.trend,
                daily_trend=daily_state.trend,
            )
        )

    return candidates, states, history, liquid


def trend_permits(weekly: Trend, daily: Trend, direction: str) -> bool:
    """May a 1-5 session hold be taken in this direction at all? (stage 1, free)

    A hold taken against the weekly or daily trend is not a continuation trade, whatever the
    entry looks like. JYOTICNC published at 76.4 on 14 Aug 2026 showing "W down, LH/LL → D
    sideways", because higher-timeframe trend contributed nothing to the score and nothing
    to any gate. Both states are already computed in stage 1, so this costs no network work
    and shrinks the shortlist before any is paid for.
    """
    against = Trend.DOWN if direction == "long" else Trend.UP
    return weekly is not against and daily is not against


def _structure_score(candidate: V3Candidate) -> float:
    """Weekly and daily trend, scored for the direction actually being traded.

    This is what "structure" was always supposed to mean. The scan previously passed the
    setup detector's own quality here, so a name could score 90 on structure while its
    weekly trend was down — the exact case that put JYOTICNC on the site.
    """
    with_trend = Trend.UP if candidate.direction == "long" else Trend.DOWN
    against = Trend.DOWN if candidate.direction == "long" else Trend.UP

    def grade(trend: Trend) -> float:
        if trend is with_trend:
            return 100.0
        return 0.0 if trend is against else 55.0

    # Weekly leads: it decides whether a multi-session hold is swimming with the tide.
    return round(0.6 * grade(candidate.weekly_trend) + 0.4 * grade(candidate.daily_trend), 1)


def _prefilter_rank(candidate: V3Candidate) -> float:
    """Cheap pre-score used only to order stage-2 work, never to reject.

    Ordering matters because stage 2 is paced network work; if it is cut short, the most
    promising names should already have been evaluated.
    """
    directional_rs = (
        candidate.rs_nifty_pct if candidate.direction == "long" else 100 - candidate.rs_nifty_pct
    )
    directional_sector = (
        candidate.sector_percentile
        if candidate.direction == "long"
        else 100 - candidate.sector_percentile
    )
    return 0.4 * candidate.setup.quality + 0.35 * directional_rs + 0.25 * directional_sector


def run_v3_scan(
    as_of: date | None = None,
    *,
    max_intraday: int = 60,
    use_catalyst: bool = True,
    refresh_catalysts: bool = False,
    min_score: float = 0.0,
    max_per_day: int = 2,
    setups: tuple[str, ...] | None = None,
) -> V3Scan:
    """Full V3 scan across the NIFTY 500."""
    from ..storage import load_history
    from .regime import assess_regime

    data = MarketData()
    as_of = as_of or nse_archive.last_trading_day() or date.today()

    started = time.time()
    history = load_history(days=400, end=as_of)
    candidates, states, history, liquid = stage_one(as_of, history=history)
    stage1_time = time.time() - started

    if setups:
        # Measured edge differs sharply by setup, so the engine can be restricted to the
        # ones that earn their place. See README: the liquidity sweep tested positive while
        # the flag did not.
        wanted = {s.lower() for s in setups}
        candidates = [c for c in candidates if c.setup.kind.value.lower() in wanted]

    scan = V3Scan(as_of=str(as_of), universe=500, liquid=len(liquid), with_setup=len(candidates))
    logger.info(
        f"[v3] stage 1: {len(candidates)} of {len(liquid)} liquid names show a V3 setup "
        f"({stage1_time:.0f}s, no network)"
    )

    regime = assess_regime(as_of, data)
    scan.regime = regime.verdict.value
    scan.regime_note = f"{regime.verdict.headline} (score {regime.total:+d})"
    # V3 §3: regime changes the quality threshold, it does not generate trades.
    #
    # These floors are set from the observed score distribution, not by taste. On a live
    # scan of 269 candidates the scores ran 55–81 with a median of 65, so a floor in the
    # 50s admitted 180 names — against a specification asking for 10–15 a *month*. §17 is
    # explicit that the response to too many qualifiers is a higher threshold, never a
    # larger output.
    threshold = min_score or {"aggressive": 72.0, "selective": 76.0, "defensive": 80.0}.get(
        regime.verdict.value, 76.0
    )

    catalyst_scores: dict[str, float] = {}
    catalyst_notes: dict[str, str] = {}
    if use_catalyst:
        from .catalyst import catalyst_scores_for

        try:
            catalyst_scores, catalyst_notes = catalyst_scores_for(
                [c.symbol for c in candidates], as_of, refresh=refresh_catalysts
            )
        except Exception as exc:  # noqa: BLE001 — catalysts must never break the scan
            logger.warning(f"[v3] catalyst pass failed: {exc}")

    # Ordered by promise, but *all* candidates are evaluated. Truncating the intraday pass
    # is what caused the engine to miss valid setups: a name can rank mid-pack on leadership
    # and still be the cleanest 4R geometry on the board. Only 15m is fetched here — the
    # 60m read is a description, not a filter, so it waits until a name has qualified.
    candidates.sort(key=_prefilter_rank, reverse=True)
    shortlist = candidates if max_intraday <= 0 else candidates[:max_intraday]
    logger.info(
        f"[v3] stage 2: 60m/120m carry test across {len(shortlist)} candidates, "
        f"then a 15m trigger check on whatever survives "
        f"(~{len(shortlist) * 2.4 / 60:.0f} min)"
    )

    for candidate in shortlist:
        symbol = yahoo.to_yahoo_symbol(candidate.symbol)

        group = history[history["symbol"] == candidate.symbol].sort_values("dt").set_index("dt")
        daily = group[["high", "low", "close", "volume"]].astype(float)
        weekly = _resample_weekly(daily)

        candidate.catalyst_score = catalyst_scores.get(candidate.symbol, 50.0)
        candidate.catalyst_note = catalyst_notes.get(candidate.symbol, "")

        scan.evaluated += 1

        # ── Stage 2a: the carry test, before any 15m data is paid for ─────────
        #
        # Ordering is deliberate. The gate rejects most candidates, so testing carry first
        # means the 15m fetch — the expensive half of stage 2 — is only paid for names that
        # could actually be traded. Running it afterwards would cost more and prove less.
        h60 = yahoo.fetch_chart(symbol, range_="730d", interval="60m", as_of=as_of)
        candidate.carry = assess_carry(
            h60,
            direction=candidate.direction,
            required_pct=4.0 * settings.min_stop_pct,
            floor=settings.v3_carry_score_floor,
            min_volume_score=settings.v3_carry_min_volume_score,
            min_headroom_score=settings.v3_carry_min_headroom_score,
        )
        # Carry is measured for every candidate, but only *gates* the setups that are
        # continuation trades. On reclaim it removed the edge rather than protecting it.
        if gate_applies(candidate.setup.kind) and not candidate.carry.passes:
            # Reported under technical validity rather than as a fifth hard filter: §16 is
            # explicit that there are four, and a setup with no higher-timeframe carry
            # structure is not technically valid for a 1-5 session hold.
            candidate.rejected_by = "technical validity"
            candidate.reject_detail = candidate.carry.failed
            scan.reject_counts["technical validity"] = (
                scan.reject_counts.get("technical validity", 0) + 1
            )
            scan.carry_rejected += 1
            continue

        if h60 is not None and len(h60) > 30:
            state = analyse_timeframe(h60, "60m", ema_spans=(20,))
            candidate.hourly_note = f"{state.trend.value}, {state.setup.value}"

        # ── Stage 2b: the 15m entry, only for names that proved they carry ────
        m15 = yahoo.fetch_chart(symbol, range_="60d", interval="15m", as_of=as_of)

        try:
            plan = build_v3_plan(
                direction=candidate.direction, intraday=m15, daily=daily, weekly=weekly,
                adr_pct=candidate.adr_pct, atr_pct=candidate.atr_pct,
                setup=candidate.setup.kind,
            )
        except V3Reject as rejection:
            candidate.rejected_by = rejection.filter_name
            candidate.reject_detail = rejection.detail
            scan.reject_counts[rejection.filter_name] = (
                scan.reject_counts.get(rejection.filter_name, 0) + 1
            )
            # Keep the near-misses that failed only on stop distance: they are the ones
            # worth watching for a tighter setup tomorrow.
            if rejection.filter_name == "stop distance":
                scan.near_miss.append(candidate)
            continue

        plan.setup = candidate.setup.kind
        candidate.plan = plan

        _feasible, volatility_score, _note = move_feasible(
            plan.target_pct, candidate.adr_pct, candidate.atr_pct
        )
        directional_rs = (
            candidate.rs_nifty_pct if candidate.direction == "long" else 100 - candidate.rs_nifty_pct
        )
        directional_sector_rs = (
            candidate.rs_sector_pct if candidate.direction == "long" else 100 - candidate.rs_sector_pct
        )
        directional_sector = (
            candidate.sector_percentile
            if candidate.direction == "long"
            else 100 - candidate.sector_percentile
        )
        # A stop in the middle of the permitted band scores best: at the floor it risks
        # being noise, at the ceiling it eats the R multiple.
        band = settings.v3_max_stop_pct - settings.min_stop_pct
        centred = 1 - abs(plan.stop_pct - (settings.min_stop_pct + band / 2)) / (band / 2)
        entry_quality = float(np.clip(50 + 50 * centred, 0, 100))

        candidate.score, candidate.modules = quality_score(
            rs_nifty_pct=directional_rs,
            rs_sector_pct=directional_sector_rs,
            sector_percentile=directional_sector,
            structure_score=_structure_score(candidate),
            setup_quality=candidate.setup.quality,
            entry_quality=entry_quality,
            catalyst_score=candidate.catalyst_score,
            volatility_score=volatility_score,
            carry_score=candidate.carry.score,
        )

        if candidate.score >= threshold:
            scan.trades.append(candidate)
        else:
            candidate.rejected_by = "quality threshold"
            candidate.reject_detail = f"score {candidate.score:.1f} below {threshold:.0f}"
            scan.near_miss.append(candidate)

    scan.trades.sort(key=lambda c: -c.score)
    scan.near_miss.sort(key=lambda c: -c.score)

    # §17 selectivity: even above the floor, only the day's very best are shown. V3 targets
    # ~10-15 setups a month — roughly one every other session — so anything beyond the cap
    # is demoted rather than published. Nothing is hidden: the count that cleared the floor
    # is reported, and the demoted names appear on the watch list.
    scan.cleared_floor = len(scan.trades)
    if max_per_day > 0 and len(scan.trades) > max_per_day:
        for demoted in scan.trades[max_per_day:]:
            demoted.rejected_by = "daily cap"
            demoted.reject_detail = (
                f"score {demoted.score:.1f} cleared the floor but was not in today's top "
                f"{max_per_day}"
            )
        scan.near_miss = scan.trades[max_per_day:] + scan.near_miss
        scan.trades = scan.trades[:max_per_day]

    # The 60m read is no longer a postscript: it ran as the carry gate in stage 2a, before
    # any name could qualify, and `hourly_note` was filled there.

    scan.tier = data.session_tier.label

    logger.info(
        f"[v3] {scan.evaluated} evaluated in {time.time() - started:.0f}s → "
        f"{scan.carry_rejected} failed the carry test, "
        f"{scan.cleared_floor} cleared the floor ({threshold:.0f}), "
        f"showing top {len(scan.trades)}, {scan.rejected} failed a hard filter"
    )
    return scan
