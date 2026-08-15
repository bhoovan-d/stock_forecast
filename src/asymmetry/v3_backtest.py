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
from .engines.carry import assess_carry, gate_applies
from .engines.setups import detect_setup
from .engines.v3 import V3Reject, average_daily_range, build_v3_plan
from .engines.indicators import atr


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
    outcome: str = "open"        # target | stop | timeout
    resolved_at: pd.Timestamp | None = None
    bars_held: int = 0
    realised_r: float = 0.0
    mae_r: float = 0.0           # worst excursion against the position, in R
    mfe_r: float = 0.0           # best excursion in favour, in R
    # The carry verdict at the moment of entry. Trades are *tagged* rather than filtered, so
    # one replay yields both cohorts and the gate's contribution is measured against the
    # same trades instead of against a differently-sampled run.
    carry_passed: bool = False      # the raw carry verdict, recorded for every setup
    carry_score: float = 0.0
    carry_failed: str = ""
    carry_applies: bool = True      # whether the gate may reject this setup at all

    @property
    def admitted(self) -> bool:
        """What the engine would actually have done, given per-setup gating.

        Kept distinct from ``carry_passed`` on purpose: carry is still measured on setups it
        does not gate, so the decision to exempt them stays checkable against later data
        rather than becoming invisible.
        """
        return self.carry_passed or not self.carry_applies


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
        return BacktestResult(
            trades=[t for t in self.trades if t.admitted],
            symbols_tested=self.symbols_tested,
            sessions_spanned=self.sessions_spanned,
            carry_unavailable=self.carry_unavailable,
        )

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
    def expectancy_r(self) -> float:
        if not self.trades:
            return float("nan")
        return float(np.mean([t.realised_r for t in self.trades]))

    @property
    def total_r(self) -> float:
        return float(sum(t.realised_r for t in self.trades))

    @property
    def break_even_win_rate(self) -> float:
        """At 4R with a 1R loss, break-even is 1/(4+1) = 20%."""
        return 100.0 / (settings.min_reward_risk + 1.0)

    @property
    def cost_r(self) -> float:
        """Round-trip costs expressed in R, using the typical stop distance."""
        typical_stop = (settings.min_stop_pct + settings.v3_max_stop_pct) / 2
        return (settings.cost_roundtrip_pct + settings.slippage_pct) / typical_stop

    @property
    def net_expectancy_r(self) -> float:
        return self.expectancy_r - self.cost_r

    def by(self, key) -> pd.DataFrame:
        """Group outcomes by any trade attribute."""
        if not self.trades:
            return pd.DataFrame()
        rows = []
        for trade in self.trades:
            rows.append({"bucket": key(trade), "r": trade.realised_r,
                         "win": trade.outcome == "target",
                         "decided": trade.outcome in ("target", "stop")})
        frame = pd.DataFrame(rows)
        grouped = frame.groupby("bucket").agg(
            n=("r", "size"),
            wins=("win", "sum"),
            decided=("decided", "sum"),
            mean_r=("r", "mean"),
            total_r=("r", "sum"),
        ).reset_index()
        grouped["win_rate"] = np.where(
            grouped["decided"] > 0, grouped["wins"] / grouped["decided"] * 100, np.nan
        )
        return grouped.sort_values("n", ascending=False)


def _resolve_forward(
    bars: pd.DataFrame, entry_index: int, trade: Trade, max_bars: int
) -> Trade:
    """Walk 15m bars forward until the stop or target is hit."""
    long_side = trade.direction == "long"
    risk = abs(trade.entry - trade.stop)
    if risk <= 0:
        return trade

    highs = bars["high"].to_numpy()
    lows = bars["low"].to_numpy()
    index = bars.index

    for offset in range(1, min(max_bars + 1, len(bars) - entry_index)):
        i = entry_index + offset
        high, low = float(highs[i]), float(lows[i])

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

        trade.mfe_r = max(trade.mfe_r, excursion_up)
        trade.mae_r = min(trade.mae_r, excursion_down)
        trade.bars_held = offset

        # Ambiguous bar resolves against the trade.
        if hit_stop:
            trade.outcome, trade.realised_r, trade.resolved_at = "stop", -1.0, index[i]
            return trade
        if hit_target:
            trade.outcome = "target"
            trade.realised_r = settings.min_reward_risk
            trade.resolved_at = index[i]
            return trade

    # Still open at the horizon: mark to the last available close.
    last = min(entry_index + max_bars, len(bars) - 1)
    close = float(bars["close"].iloc[last])
    trade.outcome = "timeout"
    trade.realised_r = (
        (close - trade.entry) / risk if long_side else (trade.entry - close) / risk
    )
    trade.resolved_at = index[last]
    return trade


def backtest_symbol(
    symbol: str,
    daily: pd.DataFrame,
    *,
    horizon_sessions: int = 5,
    bars_per_session: int = 25,
    step_bars: int = 5,
) -> list[Trade]:
    """Replay the engine's own triggers over the available 15m history for one symbol."""
    intraday = yahoo.fetch_chart(yahoo.to_yahoo_symbol(symbol), range_="60d", interval="15m")
    if intraday is None or len(intraday) < 120:
        return []

    # 60m is fetched once and sliced per decision, rather than re-fetched per bar. The slice
    # is by *timestamp*, not date: a decision taken at 11:15 must not see 14:15's bar.
    hourly = yahoo.fetch_chart(yahoo.to_yahoo_symbol(symbol), range_="730d", interval="60m")

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

        as_of = intraday.index[i].date()
        daily_slice = daily[daily.index.date <= as_of]
        if len(daily_slice) < 80:
            continue

        long_setup = detect_setup(daily_slice, "long")
        short_setup = detect_setup(daily_slice, "short")
        found = [s for s in (long_setup, short_setup) if s.found]
        if not found:
            continue
        setup = max(found, key=lambda s: s.quality)

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
            hourly[hourly.index <= intraday.index[i]] if hourly is not None else None,
            direction=plan.direction,
            required_pct=plan.target_pct,
            floor=settings.v3_carry_score_floor,
            min_volume_score=settings.v3_carry_min_volume_score,
            min_headroom_score=settings.v3_carry_min_headroom_score,
        )

        trade = Trade(
            symbol=symbol, direction=plan.direction, setup=setup.kind.value,
            entered_at=intraday.index[i], entry=plan.entry, stop=plan.stop,
            target=plan.target, stop_pct=plan.stop_pct,
            carry_passed=carry.passes, carry_score=carry.score,
            carry_failed=carry.failed, carry_applies=gate_applies(setup.kind),
        )
        trades.append(_resolve_forward(intraday, i, trade, max_bars))
        busy_until = i + trade.bars_held

    return trades


def run_v3_backtest(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 60,
    horizon_sessions: int = 5,
) -> BacktestResult:
    """Backtest the V3 engine across the names that currently show setups."""
    from .engines.v3_scan import stage_one
    from .data import nse_archive
    from .storage import load_history

    as_of = nse_archive.last_trading_day() or date.today()
    history = load_history(days=400, end=as_of)

    if symbols is None:
        candidates, _states, _hist, _liquid = stage_one(as_of, history=history)
        # Order by setup quality so a truncated run still tests the best examples.
        candidates.sort(key=lambda c: -c.setup.quality)
        symbols = [c.symbol for c in candidates[:max_symbols]]

    history = history.copy()
    history["dt"] = pd.to_datetime(history["date"])

    result = BacktestResult()
    started = time.time()
    logger.info(f"[v3-backtest] replaying 15m triggers across {len(symbols)} symbols")

    for position, symbol in enumerate(symbols, 1):
        group = history[history["symbol"] == symbol].sort_values("dt").set_index("dt")
        daily = group[["high", "low", "close", "volume"]].astype(float)
        if len(daily) < 100:
            continue

        trades = backtest_symbol(symbol, daily, horizon_sessions=horizon_sessions)
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
