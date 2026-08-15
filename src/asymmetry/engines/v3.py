"""Engineering Specification V3 — the selection engine.

The hierarchy V3 insists on:

    RIGHT MARKET → RIGHT SECTOR → RIGHT STOCK → RIGHT TIME → RIGHT RISK/REWARD

Two design points carry most of the weight:

**Hard filters are exactly four** (§16): 4R, stop distance 0.5–1.5%, liquidity, basic
technical validity. Nothing else may reject a candidate. Sector leadership, relative
strength and catalyst are *scoring* factors — which is deliberate, and is why a strong
setup in a merely-average sector can still qualify. An earlier build treated leadership as a
filter, and it discarded exactly the candidates the spec exists to find.

**Selectivity comes from the filters, not from truncation** (§17): the engine screens the
whole NIFTY 500 and lets the hard filters do the cutting, targeting ~10–15 setups a month.
If too many qualify the threshold rises; the 4R and stop requirements never loosen.

Stage 1 runs entirely off stored daily bars — weekly is resampled rather than fetched — so
all 473 names are screened without a single network call. Only survivors pay for intraday
data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..spec import SetupType, Trend
from .indicators import atr, ema
from .setups import SetupSignal, detect_setup
from .structure import classify_structure, find_pivots

# Horizons for relative strength (V3 §5): one week, one month, one quarter.
RS_HORIZONS = (5, 20, 60)

# How the entry order is placed. A reclaim has already confirmed, so the trigger bar's close
# is the entry; a continuation is not a trade until price clears the flag, so the order rests
# beyond it. Renderers key their wording off these.
ENTRY_CONFIRMED = "confirmed"
ENTRY_STOP_THROUGH = "stop-through"

# Intraday bar length. Entry, stop and trigger timing are all measured on this timeframe.
TRIGGER_TIMEFRAME_MINUTES = 15


# ── Volatility: ATR and ADR (§10) ─────────────────────────────────────────────


def average_daily_range(frame: pd.DataFrame, window: int = 20) -> float:
    """ADR% — mean of (high-low)/low over the window.

    V3 asks for ADR alongside ATR because they answer different questions. ATR includes
    overnight gaps; ADR is what the stock actually travels *within* a session, which is the
    honest input to "can this reach 4R in 1-5 days?"
    """
    if frame is None or len(frame) < window:
        return 0.0
    recent = frame.tail(window)
    return float(((recent["high"] - recent["low"]) / recent["low"]).mean() * 100)


def move_feasible(required_pct: float, adr_pct: float, atr_pct: float, sessions: int = 5):
    """(feasible, score 0-100, note) for reaching the target within the horizon.

    Uses the *lower* of the ADR- and ATR-based estimates. Taking the friendlier of two
    estimates is how a target that cannot realistically be reached gets waved through.
    """
    if adr_pct <= 0 and atr_pct <= 0:
        return False, 0.0, "no volatility estimate"

    # Range accumulates sub-linearly: a stock does not travel 5 x ADR in 5 days.
    adr_capacity = adr_pct * np.sqrt(sessions) if adr_pct else 0.0
    atr_capacity = atr_pct * np.sqrt(sessions) if atr_pct else 0.0
    capacity = min(x for x in (adr_capacity, atr_capacity) if x > 0)

    ratio = required_pct / capacity if capacity else 99.0
    feasible = ratio <= 1.0
    score = float(np.clip((1.6 - ratio) / 1.6 * 100, 0, 100))
    return (
        feasible,
        score,
        f"needs {required_pct:.1f}% vs {capacity:.1f}% capacity over {sessions}d "
        f"(ADR {adr_pct:.1f}%, ATR {atr_pct:.1f}%)",
    )


# ── Sector leadership (§4) ────────────────────────────────────────────────────


@dataclass
class SectorState:
    sector: str
    rs_20: float
    rs_60: float
    acceleration: float
    state: str          # Leading | Improving | Weakening | Lagging
    percentile: float   # 0-100 among sectors

    @property
    def favours_long(self) -> bool:
        return self.state in ("Leading", "Improving")

    @property
    def favours_short(self) -> bool:
        return self.state in ("Lagging", "Weakening")


def sector_states(history: pd.DataFrame, sector_map: dict[str, str]) -> dict[str, SectorState]:
    """Classify every sector by relative strength and its direction (§4)."""
    from .sectors import build_sector_composites

    composites = build_sector_composites(history, sector_map)
    if composites.empty:
        return {}
    benchmark = build_sector_composites(history, {s: "ALL" for s in sector_map})
    if benchmark.empty:
        return {}
    market = benchmark["ALL"]

    rows = []
    for sector in composites.columns:
        series = composites[sector].dropna()
        if len(series) < 65:
            continue

        def excess(periods: int) -> float:
            stock = (series.iloc[-1] / series.iloc[-periods - 1] - 1) * 100
            bench = (market.iloc[-1] / market.iloc[-periods - 1] - 1) * 100
            return stock - bench

        rs20, rs60 = excess(20), excess(60)
        rows.append({"sector": sector, "rs20": rs20, "rs60": rs60, "accel": rs20 - rs60})

    if not rows:
        return {}
    frame = pd.DataFrame(rows)
    frame["percentile"] = frame["rs20"].rank(pct=True) * 100

    states: dict[str, SectorState] = {}
    for row in frame.itertuples():
        # Level and direction together, exactly as V3 describes the four states.
        if row.rs20 > 0 and row.accel > 0:
            label = "Leading"
        elif row.rs20 <= 0 and row.accel > 0:
            label = "Improving"
        elif row.rs20 > 0:
            label = "Weakening"
        else:
            label = "Lagging"
        states[row.sector] = SectorState(
            sector=row.sector, rs_20=round(row.rs20, 2), rs_60=round(row.rs60, 2),
            acceleration=round(row.accel, 2), state=label,
            percentile=round(row.percentile, 1),
        )
    return states


# ── Composite relative strength (§6) ──────────────────────────────────────────


def composite_rs(
    closes: pd.DataFrame, benchmark: pd.Series, sector_series: dict[str, pd.Series],
    sector_map: dict[str, str],
) -> pd.DataFrame:
    """RS vs NIFTY, RS vs sector, and acceleration — blended 40/35/25.

    Acceleration earns its 25% because level alone is a trailing measure: a stock with very
    high but deteriorating RS is a worse candidate than one that is high and still improving,
    and only the change term separates them.
    """
    def excess(series: pd.Series, bench: pd.Series, periods: int) -> pd.Series:
        if len(series) <= periods + 1 or len(bench) <= periods + 1:
            return pd.Series(dtype=float)
        stock = (series.iloc[-1] / series.iloc[-periods - 1] - 1) * 100
        base = (bench.iloc[-1] / bench.iloc[-periods - 1] - 1) * 100
        return stock - base

    rows = {}
    for symbol in closes.columns:
        series = closes[symbol].dropna()
        if len(series) < 70:
            continue
        sector = sector_map.get(symbol)
        sector_line = sector_series.get(sector)

        vs_nifty = {}
        vs_sector = {}
        for periods in RS_HORIZONS:
            vs_nifty[periods] = float(excess(series, benchmark, periods) or np.nan)
            if sector_line is not None and len(sector_line) > periods + 1:
                vs_sector[periods] = float(excess(series, sector_line, periods) or np.nan)

        nifty_level = np.nanmean(list(vs_nifty.values())) if vs_nifty else np.nan
        sector_level = np.nanmean(list(vs_sector.values())) if vs_sector else np.nan
        # Short horizon minus long horizon: is the outperformance building or fading?
        accel = vs_nifty.get(5, np.nan) - vs_nifty.get(60, np.nan)

        rows[symbol] = {
            "rs_nifty": nifty_level,
            "rs_sector": sector_level,
            "rs_accel": accel,
            "rs_nifty_5": vs_nifty.get(5, np.nan),
            "rs_nifty_20": vs_nifty.get(20, np.nan),
            "rs_nifty_60": vs_nifty.get(60, np.nan),
        }

    frame = pd.DataFrame(rows).T
    if frame.empty:
        return frame
    for column in ("rs_nifty", "rs_sector", "rs_accel"):
        frame[f"{column}_pct"] = frame[column].rank(pct=True) * 100
    frame["rs_score"] = (
        settings.rs_weight_vs_nifty * frame["rs_nifty_pct"].fillna(50)
        + settings.rs_weight_vs_sector * frame["rs_sector_pct"].fillna(50)
        + settings.rs_weight_acceleration * frame["rs_accel_pct"].fillna(50)
    )
    return frame


# ── Entry, stop and target, both directions (§13, §14) ────────────────────────


@dataclass
class V3Plan:
    direction: str
    entry: float
    stop: float
    stop_pct: float
    risk: float
    target: float
    target_pct: float
    quantity: int
    invalidation: str
    setup: SetupType
    nearest_barrier: float | None = None
    barrier_blocks: bool = False
    feasible: bool = True
    feasibility_note: str = ""
    # When the qualifying geometry existed. "live" means the latest bar; otherwise the
    # trigger already passed during the session and this is a record, not an order.
    triggered_at: str = "live"
    bars_ago: int = 0

    # ── How the entry is actually executed ────────────────────────────────────
    # The entry price alone does not describe a trade: 826.90 is a resting buy-stop for one
    # setup and "you are already in, at market" for another. These record which, and the
    # level the price was derived from, so no renderer has to re-derive it and get it wrong.
    #
    # ENTRY_CONFIRMED  reclaim — confirmation already happened on the trigger bar
    # ENTRY_STOP_THROUGH  continuation — resting order just beyond the flag boundary
    entry_rule: str = ENTRY_CONFIRMED
    entry_level: float | None = None
    # The band of fills where the *fixed* structural stop still sits inside V3's 0.5–1.5%
    # rule. Fill outside it and the trade is no longer the one that was screened.
    entry_min: float = 0.0
    entry_max: float = 0.0
    # Start of the 15m bar the geometry was measured on, IST. Carried for every plan,
    # including a live one — an untimed "live" is what made an archive-tier scan of a closed
    # session claim to be actionable right now.
    trigger_bar: pd.Timestamp | None = None

    @property
    def is_live(self) -> bool:
        return self.bars_ago == 0


class V3Reject(Exception):
    """One of the four hard filters refused the setup."""

    def __init__(self, filter_name: str, detail: str):
        self.filter_name = filter_name
        self.detail = detail
        super().__init__(f"{filter_name}: {detail}")


def _plan_at_bar(
    *,
    direction: str,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    weekly: pd.DataFrame | None,
    adr_pct: float,
    atr_pct: float,
    setup: SetupType = SetupType.NONE,
) -> V3Plan:
    """Entry from the 15m trigger, stop at technical invalidation, target at 4R.

    **Entry depends on the setup**, and getting this wrong silently destroys the strategy.
    A generic "break the session high" trigger puts entry at the top of the day, which
    leaves the nearest swing low several percent below — so every setup fails the 1.5% stop
    once the move has happened, and the engine reports nothing on exactly the days it should
    be firing.

    * **Reclaim / sweep** — the confirmation has already occurred; the trade is live at the
      current price, with the stop under the swing that produced the reclaim.
    * **Continuation / flag** — the trade is not live until price clears the flag, so entry
      sits just above (or below) that boundary.

    The stop is computed from structure and then *checked* against the 0.5–1.5% band. It is
    never adjusted to fit: V3 §13 forbids tightening a stop to qualify a trade, and a stop
    inside the noise band is not an invalidation level anyway.
    """
    long_side = direction == "long"
    if intraday is None or len(intraday) < 20:
        raise V3Reject("technical validity", "no 15m data for a trigger")

    session = intraday.tail(26)
    close = float(intraday["close"].iloc[-1])
    last_bar = intraday.iloc[-1]

    entry_level: float | None = None
    entry_rule = ENTRY_CONFIRMED
    if setup is SetupType.RECLAIM:
        # Already confirmed: enter on the current bar, not at the day's extreme.
        entry = close if long_side else close
    elif long_side:
        level = float(session["high"].iloc[:-1].max())
        # Do not chase: if price has already run past the flag, the trade is the current
        # bar's edge rather than the stale breakout level.
        entry = min(max(close, float(last_bar["high"])), level * 1.0005) if close > level else level * 1.0005
        entry_level, entry_rule = level, ENTRY_STOP_THROUGH
    else:
        level = float(session["low"].iloc[:-1].min())
        entry = max(min(close, float(last_bar["low"])), level * 0.9995) if close < level else level * 0.9995
        entry_level, entry_rule = level, ENTRY_STOP_THROUGH

    # Stop: nearest 15m swing that is a genuine invalidation.
    #
    # "Nearest" alone is wrong. Intraday pivots cluster densely, and the closest one is
    # usually a few ticks away — inside the noise band, where it is not an invalidation but
    # a coin flip. So the candidate set is first restricted to levels that clear the
    # minimum stop distance, and the nearest of *those* is taken. That keeps the stop as
    # tight as V3 wants without it becoming meaningless.
    highs, lows = find_pivots(intraday.tail(60), window=2)
    noise_floor = settings.min_stop_pct / 100
    if long_side:
        qualifying = [p for p in lows if p <= entry * (1 - noise_floor)]
        structural = max(qualifying) if qualifying else float(session["low"].min())
    else:
        qualifying = [p for p in highs if p >= entry * (1 + noise_floor)]
        structural = min(qualifying) if qualifying else float(session["high"].max())

    stop = structural
    risk = abs(entry - stop)
    if risk <= 0:
        raise V3Reject("technical validity", "stop is not on the correct side of entry")

    stop_pct = risk / entry * 100

    # ── Hard filter: stop distance band (§1, §13) ─────────────────────────────
    if stop_pct > settings.v3_max_stop_pct:
        raise V3Reject(
            "stop distance",
            f"technical invalidation is {stop_pct:.2f}% away "
            f"(max {settings.v3_max_stop_pct:.1f}%) — rejected, not tightened",
        )
    if stop_pct < settings.min_stop_pct:
        raise V3Reject(
            "stop distance",
            f"invalidation only {stop_pct:.2f}% away "
            f"(min {settings.min_stop_pct:.1f}%) — inside noise, not a real level",
        )

    # The same §13 band, solved for entry instead of for stop distance.
    #
    # The stop is a structural level and does not move, so a fill away from the quoted entry
    # changes the stop *percentage* — and far enough out, the trade no longer satisfies the
    # rule it was screened under. Reporting the band makes "is 831 still this trade?"
    # answerable instead of a judgement call.
    lo, hi = settings.min_stop_pct / 100, settings.v3_max_stop_pct / 100
    if long_side:
        entry_min, entry_max = stop / (1 - lo), stop / (1 - hi)
    else:
        entry_min, entry_max = stop / (1 + hi), stop / (1 + lo)

    target = entry + 4.0 * risk if long_side else entry - 4.0 * risk
    target_pct = abs(target / entry - 1) * 100

    # ── Hard filter: 4R feasibility (§10, §14) ────────────────────────────────
    feasible, _score, note = move_feasible(target_pct, adr_pct, atr_pct)
    if not feasible:
        raise V3Reject("4R feasibility", note)

    # Opposing structure before the target.
    #
    # Only *weekly* pivots can reject. V3 §14 says "major resistance/support", and §7 gives
    # the weekly timeframe the specific job of holding major levels. Daily swing highs are
    # far denser — rejecting on those vetoes almost every setup, including ones where the
    # barrier is well inside a single session's normal range. Daily levels are still
    # reported, so the barrier is visible without being fatal.
    def nearest_barrier(frame: pd.DataFrame | None) -> float | None:
        if frame is None or len(frame) <= 10:
            return None
        pivot_highs, pivot_lows = find_pivots(frame, window=3)
        if long_side:
            ahead = [p for p in pivot_highs if entry * 1.003 < p < target]
            return min(ahead) if ahead else None
        ahead = [p for p in pivot_lows if target < p < entry * 0.997]
        return max(ahead) if ahead else None

    major = nearest_barrier(weekly)
    minor = nearest_barrier(daily)
    barrier = major if major is not None else minor

    if major is not None:
        room = abs(major - entry) / abs(target - entry)
        if room < 0.6:
            raise V3Reject(
                "4R feasibility",
                f"major weekly structure at {major:,.2f} sits {room:.0%} of the way to target",
            )

    quantity = int(settings.risk_budget_inr // risk) if risk > 0 else 0

    return V3Plan(
        direction=direction,
        entry=round(entry, 2),
        stop=round(stop, 2),
        stop_pct=round(stop_pct, 3),
        risk=round(risk, 2),
        target=round(target, 2),
        target_pct=round(target_pct, 2),
        quantity=quantity,
        invalidation=(
            f"{TRIGGER_TIMEFRAME_MINUTES}m swing {'low' if long_side else 'high'} "
            f"at {stop:,.2f}"
        ),
        setup=setup,
        nearest_barrier=round(barrier, 2) if barrier else None,
        barrier_blocks=barrier is not None,
        feasible=True,
        feasibility_note=note,
        entry_rule=entry_rule,
        entry_level=round(entry_level, 2) if entry_level is not None else None,
        entry_min=round(entry_min, 2),
        entry_max=round(entry_max, 2),
    )


# ── Quality score (§16) ───────────────────────────────────────────────────────


def quality_score(
    *,
    rs_nifty_pct: float,
    rs_sector_pct: float,
    sector_percentile: float,
    structure_score: float,
    entry_quality: float,
    catalyst_score: float,
    volatility_score: float,
) -> tuple[float, dict[str, float]]:
    """Seven weighted factors, 0-100. Hard filters are applied elsewhere and never here."""
    modules = {
        "rs_vs_nifty": rs_nifty_pct,
        "rs_vs_sector": rs_sector_pct,
        "sector_leadership": sector_percentile,
        "structure": structure_score,
        "entry_quality": entry_quality,
        "catalyst": catalyst_score,
        "volatility": volatility_score,
    }
    weights = {
        "rs_vs_nifty": settings.v3_weight_rs_nifty,
        "rs_vs_sector": settings.v3_weight_rs_sector,
        "sector_leadership": settings.v3_weight_sector_leadership,
        "structure": settings.v3_weight_structure,
        "entry_quality": settings.v3_weight_entry_quality,
        "catalyst": settings.v3_weight_catalyst,
        "volatility": settings.v3_weight_volatility,
    }
    total = sum(modules[k] * weights[k] for k in modules)
    return round(total, 2), {k: round(v, 1) for k, v in modules.items()}


def build_v3_plan(
    *,
    direction: str,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    weekly: pd.DataFrame | None,
    adr_pct: float,
    atr_pct: float,
    setup: SetupType = SetupType.NONE,
    scan_bars: int = 26,
) -> V3Plan:
    """Find the most recent 15m bar offering qualifying geometry.

    An end-of-session evaluation alone is close to useless for this specification. By the
    close of a big move the nearest invalidation is several percent away, so the setup that
    actually paid is invisible — ZEEL on 12 Aug offered a 0.54% stop at 10:30 and ran past
    4R the same session, yet showed a 4.6% stop by the close.

    So the last ``scan_bars`` bars are checked, newest first. A hit on the latest bar is a
    live trigger; an earlier hit is reported with its timestamp as a record of what the
    session offered, which is what makes an after-hours run informative rather than blind.
    """
    if intraday is None or len(intraday) < 25:
        raise V3Reject("technical validity", "no 15m data for a trigger")

    last_error: V3Reject | None = None
    horizon = min(scan_bars, len(intraday) - 20)

    for offset in range(horizon):
        window = intraday.iloc[: len(intraday) - offset]
        try:
            plan = _plan_at_bar(
                direction=direction, intraday=window, daily=daily, weekly=weekly,
                adr_pct=adr_pct, atr_pct=atr_pct, setup=setup,
            )
        except V3Reject as rejection:
            if last_error is None:
                last_error = rejection  # keep the reason from the live bar
            continue

        plan.bars_ago = offset
        # Record the bar for every plan, not only for stale ones. Previously a hit on the
        # newest bar dropped its timestamp for the bare word "live", which reads as "right
        # now" no matter how old the data is — and an archive-tier scan runs against a
        # session that closed hours or days ago.
        plan.trigger_bar = window.index[-1]
        plan.triggered_at = (
            "live" if offset == 0 else str(window.index[-1].strftime("%d %b %H:%M"))
        )
        return plan

    raise last_error or V3Reject("technical validity", "no qualifying trigger in the window")
