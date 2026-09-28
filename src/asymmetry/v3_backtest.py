"""Intraday-triggered backtest for Specification V3.

This measures the thing that actually matters and that nothing else in this codebase has
measured: **when the engine fires a 15-minute trigger, how often does 4R arrive before the
stop?**

Every earlier estimate used daily-close entries as a proxy, and that proxy is badly wrong
for this specification. At the daily level a 1.5% stop is a fraction of one bar's range —
it looks absurdly tight and the measured win rate collapses. At the 15-minute level the
same 1.5% sits below a genuine swing low and is perfectly ordinary. ZEEL on 12 Aug is the
worked example: 0.54% stop at 10:30, 4R reached the same session, yet the daily-close view
of that day showed a 4.6% stop and no trade at all.

Method, and the choices that keep it honest:

* **The live code decides.** Setups come from ``detect_setup`` and plans from
  ``build_v3_plan`` — the same functions the scanner uses. A backtest with its own
  reimplementation measures the reimplementation.
* **Point-in-time.** Only bars up to the decision moment are visible, and the daily frame is
  truncated the same way.
* **Resolution is on 15-minute bars**, not daily. That is the entire point — daily bars
  cannot tell whether the stop or the target came first inside a session.
* **A bar touching both books a loss.** Sequence within a 15-minute bar is unknown, and
  resolving that ambiguity favourably is the classic way a backtest invents an edge.
* **One position per symbol at a time**, so a single runaway name cannot supply fifty
  overlapping "wins".

The honest limitation, stated in the output: the upstream feed serves roughly 60–80 days of
15-minute history, so the sample is small and the buckets are thin. This measures the right
thing on limited data, rather than the wrong thing on plenty.

**The sample is chosen with hindsight, and the numbers must be read knowing it.** With no
explicit ``symbols``, ``run_v3_backtest`` runs ``stage_one`` for *today* and takes the top
``max_symbols`` by setup quality — so a name is in the sample because it shows a good setup
on the run date, and its triggers from fifty sessions earlier are then replayed. The trade
decisions stay strictly point-in-time; the *universe* does not. Two consequences that no
amount of care inside the loop can remove:

* it is the best-looking end of the distribution, not a random 80 of 473, so the measured
  expectancy is an upper bound on what the full universe would give;
* names that stopped showing setups — including any that were destroyed in the window — are
  absent by construction.

Pass ``symbols`` explicitly with a list fixed *before* the window to measure without this.
See ``docs/2026-08-17-published-numbers.md``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from .config import settings
from .data import yahoo
from .data.bar_contract import IntradayStore, completed_bars as available_bars, validate_15m
from .engines.carry import assess_carry, gate_applies
from .engines.catalyst import CatalystHistory
from .engines.setups import detect_setup
from .engines.v3 import (
    ENTRY_CONFIRMED,
    ENTRY_STOP_THROUGH,
    V3Plan,
    V3Reject,
    average_daily_range,
    build_v3_plan,
)
from .engines.indicators import atr


class ScorePanel:
    """Point-in-time inputs for `quality_score`, cached per decision date.

    Built because the quality score had never been measured at all. The replay exercised
    `detect_setup` and `build_v3_plan` — the hard filters — and stopped there, so the nine
    weighted modules and the regime threshold sitting on top of them were carried on
    engineering judgement alone. That is a lot of machinery to leave untested, and it is
    where the catalyst weight lives.

    `composite_rs` and `sector_states` both read `.iloc[-1]`, so truncating the history to a
    date gives their value *as of* that date with no other change. The cache matters: the
    cross-section is over ~470 names and the replay asks for it thousands of times across
    roughly fifty distinct dates.
    """

    def __init__(self, history: pd.DataFrame, sector_map: dict[str, str]):
        # Filtered to the liquid universe, exactly as `stage_one` does before computing RS.
        # Left unfiltered this ranks against every symbol in the store — 2,546 rather than
        # 473 — and a percentile against a different population is a different number.
        self._history = history[history["symbol"].isin(sector_map)]
        self._sector_map = sector_map
        self._cache: dict[date, tuple] = {}

    def at(self, as_of: date):
        """(rs frame, sector states) as they stood at the close of ``as_of``."""
        if as_of in self._cache:
            return self._cache[as_of]

        from .engines.sectors import build_sector_composites
        from .engines.v3 import composite_rs, sector_states

        frame = self._history[self._history["date"] <= as_of]
        try:
            closes = frame.pivot_table(index="date", columns="symbol", values="close").sort_index()
            composites = build_sector_composites(frame, self._sector_map)
            bench_frame = build_sector_composites(frame, {s: "ALL" for s in self._sector_map})
            benchmark = bench_frame["ALL"] if not bench_frame.empty else closes.mean(axis=1)
            rs = composite_rs(
                closes, benchmark, {c: composites[c] for c in composites.columns},
                self._sector_map,
            )
            states = sector_states(frame, self._sector_map)
        except Exception as exc:  # noqa: BLE001 — a thin early date must not kill the run
            logger.debug(f"[score-panel] {as_of}: {exc}")
            rs, states = pd.DataFrame(), {}

        self._cache[as_of] = (rs, states)
        return self._cache[as_of]


class BreadthRegimePanel:
    """A point-in-time market regime used only for replay features and diagnostics."""

    def __init__(self, history: pd.DataFrame):
        self._history = history.copy()
        self._cache: dict[date, str] = {}

    def at(self, as_of: date) -> str:
        if as_of in self._cache:
            return self._cache[as_of]
        frame = self._history[self._history["date"] < as_of]
        closes = frame.pivot_table(index="date", columns="symbol", values="close").sort_index()
        if len(closes) < 50:
            verdict = "selective"
        else:
            average = closes.rolling(50, min_periods=40).mean().iloc[-1]
            current = closes.iloc[-1]
            valid = current.notna() & average.notna()
            breadth = float((current[valid] > average[valid]).mean()) if valid.any() else 0.5
            verdict = "aggressive" if breadth >= 0.60 else "defensive" if breadth <= 0.40 else "selective"
        self._cache[as_of] = verdict
        return verdict


@dataclass
class Trade:
    symbol: str
    direction: str
    setup: str
    entered_at: pd.Timestamp
    entry: float
    stop: float
    target: float
    stop_pct: float
    signal_at: pd.Timestamp | None = None
    entry_available: bool = True
    entry_reason: str = "filled"
    outcome: str = "open"        # target | stop | timeout
    resolved_at: pd.Timestamp | None = None
    bars_held: int = 0
    realised_r: float = 0.0
    mae_r: float = 0.0           # worst excursion against the position, in R
    mfe_r: float = 0.0           # best excursion in favour, in R
    hit_2r: bool = False
    reached_2r_at: pd.Timestamp | None = None
    # Whether a 3R take-profit would have filled before the stop. Stored separately from
    # MFE because an ambiguous bar can touch both 3R and the stop; that bar is a loss under
    # the replay's conservative ordering and must not masquerade as a 3R win.
    hit_3r: bool = False
    reached_3r_at: pd.Timestamp | None = None
    hit_4r: bool = False
    reached_4r_at: pd.Timestamp | None = None
    # The carry verdict at the moment of entry. Trades are *tagged* rather than filtered, so
    # one replay yields both cohorts and the gate's contribution is measured against the
    # same trades instead of against a differently-sampled run.
    carry_passed: bool = False      # the raw carry verdict, recorded for every setup
    carry_score: float = 0.0
    carry_failed: str = ""
    carry_applies: bool = True      # whether the gate may reject this setup at all
    # The fifth hard filter, tagged the same way and for the same reason. `catalyst_covered`
    # is not the same question as `has_catalyst`: it asks whether the store knew anything at
    # all about this date. A decision from before the store existed has no catalyst because
    # none was ever collected, and counting that as "no catalyst" would measure the
    # collection start date and report it as a market fact.
    catalyst_covered: bool = False
    has_catalyst: bool = False
    catalyst_score: float = 50.0
    catalyst_note: str = ""
    # The nine `quality_score` modules as they stood at the decision, and the score they
    # produce under the live weights. Empty when no ScorePanel was supplied.
    modules: dict = field(default_factory=dict)
    score: float = 0.0
    setup_quality: float = 0.0
    adr_pct: float = 0.0
    atr_pct: float = 0.0
    regime: str = "selective"
    # Frozen when the trade is constructed. Cost in R changes inversely with this trade's
    # stop distance, so one midpoint scalar cannot price a mixed-stop cohort.
    cost_r: float | None = None
    sector: str = ""

    def __post_init__(self) -> None:
        if self.cost_r is None:
            self.cost_r = (
                (settings.cost_roundtrip_pct + settings.slippage_pct) / self.stop_pct
                if self.stop_pct > 0 else 0.0
            )

    @property
    def net_r(self) -> float:
        return self.realised_r - float(self.cost_r or 0.0)

    @property
    def admitted(self) -> bool:
        """What the engine would actually have done, given per-setup gating.

        Kept distinct from ``carry_passed`` on purpose: carry is still measured on setups it
        does not gate, so the decision to exempt them stays checkable against later data
        rather than becoming invisible.
        """
        return self.carry_passed or not self.carry_applies

    def hit_target(self, target_r: int) -> bool:
        return {2: self.hit_2r, 3: self.hit_3r, 4: self.hit_4r}[target_r]

    def payoff_r(self, target_r: int) -> float:
        return float(target_r) if self.hit_target(target_r) else self.realised_r


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    symbols_tested: int = 0
    sessions_spanned: int = 0
    # Decision points where the carry test could not be evaluated at all (no 60m history
    # that far back). Reported rather than silently folded into "failed".
    carry_unavailable: int = 0

    def gated(self) -> BacktestResult:
        """The same run, keeping only what the engine would actually have taken."""
        return self._subset(lambda t: t.admitted)

    def _subset(self, predicate) -> BacktestResult:
        return BacktestResult(
            trades=[t for t in self.trades if predicate(t)],
            symbols_tested=self.symbols_tested,
            sessions_spanned=self.sessions_spanned,
            carry_unavailable=self.carry_unavailable,
        )

    # ── The catalyst filter, measured only where it could be ──────────────────

    @property
    def catalyst_covered(self) -> BacktestResult:
        """Trades whose decision date the catalyst store actually knew about.

        Every catalyst comparison is made inside this subset. Outside it "no catalyst" means
        "nothing was collected", and a cohort built on that would be measuring when
        collection started.
        """
        return self._subset(lambda t: t.catalyst_covered)

    @property
    def catalyst_uncovered(self) -> int:
        return sum(not t.catalyst_covered for t in self.trades)

    # ── headline ──────────────────────────────────────────────────────────────

    @property
    def resolved(self) -> list[Trade]:
        return [t for t in self.trades if t.outcome in ("target", "stop")]

    @property
    def win_rate(self) -> float:
        decided = self.resolved
        if not decided:
            return float("nan")
        return sum(t.outcome == "target" for t in decided) / len(decided) * 100

    @property
    def win_rate_interval(self) -> tuple[float, float]:
        """95% Wilson score interval for the resolved-trade win rate, in percent."""
        decided = self.resolved
        if not decided:
            return float("nan"), float("nan")
        n = len(decided)
        proportion = sum(t.outcome == "target" for t in decided) / n
        z = 1.959963984540054
        denominator = 1 + z * z / n
        centre = (proportion + z * z / (2 * n)) / denominator
        margin = z * np.sqrt(
            (proportion * (1 - proportion) + z * z / (4 * n)) / n
        ) / denominator
        return (centre - margin) * 100, (centre + margin) * 100

    @property
    def expectancy_r(self) -> float:
        if not self.trades:
            return float("nan")
        return float(np.mean([t.realised_r for t in self.trades]))

    @property
    def total_r(self) -> float:
        return float(sum(t.realised_r for t in self.trades))

    @property
    def break_even_win_rate(self) -> float:
        """Break-even hit rate for the fixed reliability target before costs."""
        return 100.0 / (settings.reliability_target_r + 1.0)

    @property
    def typical_cost_r(self) -> float:
        """Round-trip costs expressed in R, using the typical stop distance."""
        typical_stop = (settings.min_stop_pct + settings.v3_max_stop_pct) / 2
        return (settings.cost_roundtrip_pct + settings.slippage_pct) / typical_stop

    @property
    def cost_r(self) -> float:
        """Compatibility name for the historical typical-stop cost scalar."""
        return self.typical_cost_r

    @property
    def net_expectancy_r(self) -> float:
        if not self.trades:
            return float("nan")
        return float(np.mean([trade.net_r for trade in self.trades]))

    def by(self, key) -> pd.DataFrame:
        """Group outcomes by any trade attribute."""
        if not self.trades:
            return pd.DataFrame()
        rows = []
        for trade in self.trades:
            rows.append({"bucket": key(trade), "r": trade.realised_r,
                         "cost_r": trade.cost_r, "net_r": trade.net_r,
                         "win": trade.outcome == "target",
                         "decided": trade.outcome in ("target", "stop")})
        frame = pd.DataFrame(rows)
        grouped = frame.groupby("bucket").agg(
            n=("r", "size"),
            wins=("win", "sum"),
            decided=("decided", "sum"),
            mean_r=("r", "mean"),
            mean_cost_r=("cost_r", "mean"),
            mean_net_r=("net_r", "mean"),
            total_r=("r", "sum"),
        ).reset_index()
        grouped["win_rate"] = np.where(
            grouped["decided"] > 0, grouped["wins"] / grouped["decided"] * 100, np.nan
        )
        return grouped.sort_values("n", ascending=False)


def resolve_forward(
    bars: pd.DataFrame, entry_index: int, trade: Trade, max_bars: int,
    *, include_entry_bar: bool = False, require_full_horizon: bool = False,
) -> Trade:
    """Walk 15m bars forward until the stop or target is hit."""
    long_side = trade.direction == "long"
    risk = abs(trade.entry - trade.stop)
    if risk <= 0:
        return trade

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    index = bars.index

    first_offset = 0 if include_entry_bar else 1
    for offset in range(first_offset, min(max_bars + 1, len(bars) - entry_index)):
        i = entry_index + offset
        high, low = float(highs[i]), float(lows[i])
        opened = float(bars["open"].iloc[i]) if "open" in bars else trade.entry

        if long_side:
            excursion_up = (high - trade.entry) / risk
            excursion_down = (low - trade.entry) / risk
            hit_stop = low <= trade.stop
            hit_target = high >= trade.target
        else:
            excursion_up = (trade.entry - low) / risk
            excursion_down = (trade.entry - high) / risk
            hit_stop = high >= trade.stop
            hit_target = low <= trade.target

        hits = {
            multiple: (
                high >= trade.entry + multiple * risk
                if long_side else low <= trade.entry - multiple * risk
            )
            for multiple in (2, 3, 4)
        }

        trade.mfe_r = max(trade.mfe_r, excursion_up)
        trade.mae_r = min(trade.mae_r, excursion_down)
        trade.bars_held = offset

        # Ambiguous bar resolves against the trade.
        if hit_stop:
            stop_fill = (
                min(opened, trade.stop) if long_side else max(opened, trade.stop)
            )
            trade.outcome = "stop"
            trade.realised_r = (
                (stop_fill - trade.entry) / risk if long_side
                else (trade.entry - stop_fill) / risk
            )
            trade.resolved_at = index[i]
            return trade
        if hits[2] and not trade.hit_2r:
            trade.hit_2r = True
            trade.reached_2r_at = index[i]
        if hits[3] and not trade.hit_3r:
            trade.hit_3r = True
            trade.reached_3r_at = index[i]
        if hits[4] and not trade.hit_4r:
            trade.hit_4r = True
            trade.reached_4r_at = index[i]
        if hit_target:
            trade.outcome = "target"
            # Derive the booked R from the frozen levels. Forward records must not change
            # when the live reward/risk setting is edited after the signal was recorded.
            trade.realised_r = abs(trade.target - trade.entry) / risk
            trade.resolved_at = index[i]
            return trade

    # The end of downloaded history is not a five-session time exit. Exclude such trades
    # from model fitting until their complete outcome window exists.
    last = min(entry_index + max_bars, len(bars) - 1)
    if require_full_horizon and last < entry_index + max_bars:
        trade.outcome = "incomplete"
        trade.resolved_at = None
        return trade
    # Still open at the genuine horizon: mark to the horizon close.
    close = float(bars["close"].iloc[last])
    trade.outcome = "timeout"
    trade.realised_r = (
        (close - trade.entry) / risk if long_side else (trade.entry - close) / risk
    )
    trade.resolved_at = index[last]
    return trade


def simulate_entry(
    bars: pd.DataFrame, signal_index: int, plan: V3Plan, *, max_wait_bars: int = 4,
) -> tuple[int | None, float | None, str]:
    """Fill a live-style order after its signal, or return an explicit cancellation."""
    if signal_index + 1 >= len(bars):
        return None, None, "no bar after the signal"
    signal_date = pd.Timestamp(bars.index[signal_index]).date()
    last = min(len(bars) - 1, signal_index + max_wait_bars)
    for i in range(signal_index + 1, last + 1):
        row = bars.iloc[i]
        stamp = pd.Timestamp(bars.index[i])
        if stamp.date() != signal_date or stamp.time() >= pd.Timestamp("15:30").time():
            return None, None, "order cancelled at the session close"
        opened, high, low = float(row["open"]), float(row["high"]), float(row["low"])
        long_side = getattr(plan, "direction", "long") == "long"
        if plan.entry_rule == ENTRY_CONFIRMED:
            fill = opened
        elif plan.entry_rule == ENTRY_STOP_THROUGH:
            if long_side:
                if opened >= plan.entry:
                    fill = opened
                elif high >= plan.entry:
                    fill = plan.entry
                else:
                    continue
            else:
                if opened <= plan.entry:
                    fill = opened
                elif low <= plan.entry:
                    fill = plan.entry
                else:
                    continue
        else:
            return None, None, f"unsupported entry rule {plan.entry_rule}"
        if not plan.entry_min <= fill <= plan.entry_max:
            return None, None, (
                f"gap/fill {fill:.2f} outside valid range "
                f"{plan.entry_min:.2f}–{plan.entry_max:.2f}"
            )
        return i, fill, "filled on the next bar" if plan.entry_rule == ENTRY_CONFIRMED else "stop order filled"
    return None, None, "order expired after four bars or at the session close"


def completed_daily(daily: pd.DataFrame, signal_at: pd.Timestamp) -> pd.DataFrame:
    """Return only daily candles that had completed before an intraday decision."""
    return daily[daily.index.date < pd.Timestamp(signal_at).date()]


def _score_decision(panel, symbol, sector, as_of, plan, setup, daily_slice, carry,
                    catalyst_score, atr_pct):
    """The nine modules for one historical decision, scored through the live functions.

    Every directional input is mirrored for a short here exactly as `run_v3_scan` does it.
    Getting that wrong would make the measurement flatter the score rather than test it.
    """
    from .config import settings
    from .engines.structure import analyse_timeframe
    from .engines.v3 import average_daily_range, move_feasible, quality_score
    from .engines.v3_scan import structure_grade

    rs, states = panel.at(as_of)
    long_side = plan.direction == "long"

    def directional(value: float) -> float:
        return value if long_side else 100 - value

    row = rs.loc[symbol] if (not rs.empty and symbol in rs.index) else None
    rs_nifty = float(row["rs_nifty_pct"]) if row is not None else 50.0
    rs_sector = float(row["rs_sector_pct"]) if row is not None else 50.0
    state = states.get(sector)
    sector_pct = state.percentile if state else 50.0

    weekly = (
        daily_slice.resample("W")
        .agg({"high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna()
    )
    w_state = analyse_timeframe(weekly, "Weekly", ema_spans=(20, 50))
    d_state = analyse_timeframe(daily_slice, "Daily", ema_spans=(20, 50))

    _f, volatility_score, _n = move_feasible(
        plan.target_pct, average_daily_range(daily_slice), atr_pct
    )
    band = settings.v3_max_stop_pct - settings.min_stop_pct
    centred = 1 - abs(plan.stop_pct - (settings.min_stop_pct + band / 2)) / (band / 2)
    entry_quality = float(np.clip(50 + 50 * centred, 0, 100))

    total, modules = quality_score(
        rs_nifty_pct=directional(rs_nifty),
        rs_sector_pct=directional(rs_sector),
        sector_percentile=directional(sector_pct),
        structure_score=structure_grade(plan.direction, w_state.trend, d_state.trend),
        setup_quality=setup.quality,
        entry_quality=entry_quality,
        catalyst_score=directional(catalyst_score),
        volatility_score=volatility_score,
        carry_score=carry.score,
    )
    return modules, total


def backtest_symbol(
    symbol: str,
    daily: pd.DataFrame,
    *,
    horizon_sessions: int = 5,
    bars_per_session: int = 25,
    step_bars: int = 5,
    catalysts=None,
    panel: ScorePanel | None = None,
    sector: str = "",
    cache_only: bool = False,
    regime_panel: BreadthRegimePanel | None = None,
    intraday_store: IntradayStore | None = None,
) -> list[Trade]:
    """Replay the engine's own triggers over the available 15m history for one symbol."""
    intraday = (
        intraday_store.load(symbol) if intraday_store is not None else
        yahoo.fetch_chart(
            yahoo.to_yahoo_symbol(symbol), range_="60d", interval="15m", cache_only=cache_only
        )
    )
    if intraday is None or len(intraday) < 120:
        return []
    if intraday_store is not None:
        integrity = validate_15m(intraday)
        if not integrity.valid:
            raise ValueError(f"{symbol}: invalid stored intraday history: {'; '.join(integrity.errors)}")

    # 60m is fetched once and sliced per decision, rather than re-fetched per bar. The slice
    # is by *timestamp*, not date: a decision taken at 11:15 must not see 14:15's bar.
    hourly = (
        intraday.resample("60min", origin="start_day", offset="15min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
        .dropna(subset=["close"])
        if intraday_store is not None else
        yahoo.fetch_chart(
            yahoo.to_yahoo_symbol(symbol), range_="730d", interval="60m", cache_only=cache_only
        )
    )

    sessions = sorted({ts.date() for ts in intraday.index})
    if len(sessions) < 15:
        return []

    max_bars = horizon_sessions * bars_per_session
    trades: list[Trade] = []
    busy_until = -1  # index before which a new entry would overlap an open position

    # Start once there is enough history for both the pivot window and a daily read.
    for i in range(100, len(intraday) - max_bars, step_bars):
        if i <= busy_until:
            continue

        signal_at = pd.Timestamp(intraday.index[i]) + pd.Timedelta(minutes=15)
        as_of = signal_at.date()
        # An intraday decision cannot know this session's final daily high, low, close or
        # volume. The previous completed session is the newest daily input it may see.
        daily_slice = completed_daily(daily, signal_at)
        if len(daily_slice) < 80:
            continue

        setup = detect_setup(daily_slice, "long")
        if not setup.found or setup.kind.value != "reclaim" or setup.direction != "long":
            continue

        price = float(daily_slice["close"].iloc[-1])
        day_atr = float(atr(daily_slice["high"], daily_slice["low"], daily_slice["close"], 14).iloc[-1])
        if not np.isfinite(day_atr) or price <= 0:
            continue

        window = intraday.iloc[: i + 1]
        weekly = (
            daily_slice.resample("W")
            .agg({"high": "max", "low": "min", "close": "last", "volume": "sum"})
            .dropna()
        )

        try:
            plan = build_v3_plan(
                direction=setup.direction, intraday=window, daily=daily_slice,
                weekly=weekly, adr_pct=average_daily_range(daily_slice),
                atr_pct=day_atr / price * 100, setup=setup.kind,
                scan_bars=1,   # decide on this bar only; the walk provides the sweep
            )
        except V3Reject:
            continue

        # The carry verdict as of this bar, on the same terms the scanner applies.
        carry = assess_carry(
            available_bars(hourly, "60m", signal_at).loc[:, ["open", "high", "low", "close", "volume"]]
            if hourly is not None else None,
            direction=plan.direction,
            required_pct=plan.target_pct,
            floor=settings.v3_carry_score_floor,
            min_volume_score=settings.v3_carry_min_volume_score,
            min_headroom_score=settings.v3_carry_min_headroom_score,
        )
        # The production population is long reclaims plus base breakouts that pass the
        # carry test. A rejected base breakout cannot lend evidence to an accepted one.
        if gate_applies(setup.kind) and not carry.passes:
            continue

        # The catalyst as known on the decision date — same lookback and same freshness
        # weighting the live scan uses, via the shared aggregator.
        catalyst_score, catalyst_note, covered = 50.0, "", False
        if catalysts is not None:
            covered = catalysts.covered(as_of)
            if covered:
                catalyst_score, catalyst_note = catalysts.at(symbol, as_of)

        modules, score = {}, 0.0
        if panel is not None:
            modules, score = _score_decision(
                panel, symbol, sector, as_of, plan, setup, daily_slice, carry,
                catalyst_score, day_atr / price * 100,
            )

        common = dict(
            modules=modules, score=score, setup_quality=setup.quality,
            adr_pct=average_daily_range(daily_slice), atr_pct=day_atr / price * 100,
            regime=regime_panel.at(as_of) if regime_panel is not None else "selective",
            symbol=symbol, direction="long", setup=setup.kind.value, sector=sector,
            carry_passed=carry.passes, carry_score=carry.score,
            carry_failed=carry.failed, carry_applies=gate_applies(setup.kind),
            catalyst_covered=covered, has_catalyst=bool(catalyst_note),
            catalyst_score=catalyst_score, catalyst_note=catalyst_note,
        )
        plan.decision_at = signal_at
        fill_index, fill, entry_reason = simulate_entry(intraday, i, plan)
        if fill_index is None or fill is None:
            trades.append(Trade(
                **common, signal_at=signal_at, entered_at=signal_at,
                entry=plan.entry, stop=plan.stop, target=plan.target,
                stop_pct=plan.stop_pct, entry_available=False,
                entry_reason=entry_reason, outcome="unfilled", cost_r=0.0,
            ))
            busy_until = i + 4
            continue

        risk = fill - plan.stop
        stop_pct = risk / fill * 100 if fill > 0 else 0.0
        if risk <= 0 or not settings.min_stop_pct <= stop_pct <= settings.v3_max_stop_pct:
            trades.append(Trade(
                **common, signal_at=signal_at, entered_at=signal_at,
                entry=fill, stop=plan.stop, target=plan.target,
                stop_pct=max(stop_pct, 0.0), entry_available=False,
                entry_reason="actual fill invalidated the stop-distance rule",
                outcome="unfilled", cost_r=0.0,
            ))
            busy_until = fill_index
            continue
        trade = Trade(
            **common, signal_at=signal_at, entered_at=pd.Timestamp(intraday.index[fill_index]),
            entry=fill, stop=plan.stop, target=fill + settings.reliability_target_r * risk,
            stop_pct=stop_pct, entry_available=True, entry_reason=entry_reason,
        )
        trades.append(resolve_forward(
            intraday, fill_index, trade, max_bars, include_entry_bar=True,
            require_full_horizon=True,
        ))
        busy_until = fill_index + trade.bars_held

    return trades


def run_v3_backtest(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 60,
    horizon_sessions: int = 5,
    score_modules: bool = False,
    cache_only: bool = False,
    intraday_store: IntradayStore | None = None,
) -> BacktestResult:
    """Backtest the V3 engine across the names that currently show setups.

    ``score_modules`` additionally reconstructs `quality_score` at each decision. Off by
    default because it costs a cross-sectional RS computation per distinct decision date.
    """
    from .data import universe as universe_mod
    from .engines.v3_scan import stage_one
    from .data import nse_archive
    from .storage import load_history

    store_first = store_last = None
    if intraday_store is not None and symbols:
        store_first, store_last, _ = intraday_store.coverage(list(symbols))
    as_of = store_last or nse_archive.last_trading_day() or date.today()
    history_days = settings.reliability_min_history_days + 200 if intraday_store else 400
    history = load_history(days=history_days, end=as_of)

    if symbols is None:
        candidates, _states, _hist, _liquid = stage_one(as_of, history=history)
        # Order by setup quality so a truncated run still tests the best examples.
        candidates.sort(key=lambda c: -c.setup.quality)
        symbols = [c.symbol for c in candidates[:max_symbols]]

    history = history.copy()
    history["dt"] = pd.to_datetime(history["date"])

    panel, sector_of = None, {}
    if score_modules:
        # The RS cross-section has to be the whole liquid universe, not the 80 replayed
        # names: a percentile against 80 hand-picked symbols is not the percentile the live
        # scan computes, and scoring against it would measure a different engine.
        full_universe = universe_mod.load_universe()
        liquid, _stats = universe_mod.apply_liquidity_gate(full_universe, history)
        sector_of = {s: liquid[s].sector for s in liquid}
        panel = ScorePanel(history, sector_of)
        logger.info(f"[v3-backtest] scoring modules against {len(sector_of)} liquid names")

    catalysts = CatalystHistory(end=as_of)
    regime_panel = BreadthRegimePanel(history)
    if catalysts.empty:
        logger.warning(
            "[v3-backtest] the catalyst store is empty — the catalyst cohort will be "
            "unmeasurable. Populate it with `asymmetry catalyst-backfill`."
        )

    result = BacktestResult()
    started = time.time()
    logger.info(f"[v3-backtest] replaying 15m triggers across {len(symbols)} symbols")

    for position, symbol in enumerate(symbols, 1):
        group = history[history["symbol"] == symbol].sort_values("dt").set_index("dt")
        daily = group[["high", "low", "close", "volume"]].astype(float)
        if len(daily) < 100:
            continue

        trades = backtest_symbol(
            symbol, daily, horizon_sessions=horizon_sessions, catalysts=catalysts,
            panel=panel, sector=sector_of.get(symbol, ""),
            cache_only=cache_only,
            regime_panel=regime_panel,
            intraday_store=intraday_store,
        )
        result.trades.extend(trades)
        result.symbols_tested += 1
        if position % 10 == 0:
            logger.info(
                f"[v3-backtest] {position}/{len(symbols)} symbols, "
                f"{len(result.trades)} trades so far"
            )

    if result.trades:
        span = {t.entered_at.date() for t in result.trades}
        result.sessions_spanned = len(span)

    logger.info(
        f"[v3-backtest] {len(result.trades)} trades from {result.symbols_tested} symbols "
        f"in {time.time() - started:.0f}s"
    )
    return result
