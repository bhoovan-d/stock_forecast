"""Walk-forward evaluation.

This module exists to answer the only question that matters: does the ranking predict
anything? Everything here is deliberately conservative — it is far easier to build a
backtest that flatters a system than one that tells the truth.

Design choices that keep it honest:

* **Point-in-time inputs.** Every engine is run with ``as_of`` set to the simulated day, so
  no future data reaches the ranking (see tests/test_point_in_time.py for why that needed
  fixing before any of this was meaningful).
* **Forward returns come from stored bhavcopy**, i.e. actual settled prices, never from a
  re-fetch that might be adjusted differently.
* **Entry is next-day**, not same-day close. A signal computed from today's close cannot be
  acted on at today's close.
* **The benchmark is the same-period universe mean**, so a rising market does not read as
  skill. What is reported is *excess* return.
* **Plans are evaluated bar by bar** — stop checked against the low, target against the
  high — and a bar touching both is scored as a loss, because intraday sequence is unknown
  and assuming the win is the classic way backtests lie.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from .models import RegimeVerdict


@dataclass
class DayResult:
    as_of: date
    regime: str
    regime_score: int
    ranked: list[tuple[str, float, float]] = field(default_factory=list)  # symbol, score, fwd
    plans: list[dict] = field(default_factory=list)
    universe_mean_return: float = 0.0


@dataclass
class BacktestReport:
    days: list[DayResult] = field(default_factory=list)
    horizon: int = 10

    # ── rank quality ──────────────────────────────────────────────────────────

    def rank_table(self, buckets: int = 5) -> pd.DataFrame:
        """Mean excess forward return by score bucket.

        If the ranking carries information, the top bucket beats the bottom one. If the
        buckets are flat, the score is decoration.
        """
        rows = []
        for day in self.days:
            if len(day.ranked) < buckets:
                continue
            frame = pd.DataFrame(day.ranked, columns=["symbol", "score", "fwd"]).dropna()
            if frame.empty:
                continue
            frame["bucket"] = pd.qcut(
                frame["score"].rank(method="first"), buckets, labels=False, duplicates="drop"
            )
            frame["excess"] = frame["fwd"] - day.universe_mean_return
            rows.append(frame)

        if not rows:
            return pd.DataFrame()
        allrows = pd.concat(rows)
        table = (
            allrows.groupby("bucket")
            .agg(
                mean_excess=("excess", "mean"),
                median_excess=("excess", "median"),
                hit_rate=("excess", lambda s: float((s > 0).mean() * 100)),
                n=("excess", "size"),
            )
            .reset_index()
        )
        table["bucket"] = table["bucket"].map(
            lambda b: f"Q{int(b) + 1}" + (" (worst)" if b == 0 else " (best)" if b == buckets - 1 else "")
        )
        return table

    def rank_correlation(self) -> float:
        """Mean daily Spearman correlation between score and forward excess return."""
        correlations = []
        for day in self.days:
            frame = pd.DataFrame(day.ranked, columns=["symbol", "score", "fwd"]).dropna()
            if len(frame) < 20:
                continue
            correlations.append(frame["score"].corr(frame["fwd"], method="spearman"))
        return float(np.nanmean(correlations)) if correlations else float("nan")

    # ── plan quality ──────────────────────────────────────────────────────────

    def plan_stats(self) -> dict:
        """Realised outcome of every emitted plan."""
        plans = [p for day in self.days for p in day.plans]
        if not plans:
            return {}
        outcomes = pd.DataFrame(plans)
        resolved = outcomes[outcomes["outcome"] != "open"]
        wins = int((resolved["outcome"] == "target").sum())
        losses = int((resolved["outcome"] == "stop").sum())
        decided = wins + losses
        return {
            "emitted": len(outcomes),
            "triggered": int((outcomes["outcome"] != "no_trigger").sum()),
            "resolved": len(resolved),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / decided * 100) if decided else float("nan"),
            "mean_r": float(resolved["realised_r"].mean()) if len(resolved) else float("nan"),
            "total_r": float(resolved["realised_r"].sum()) if len(resolved) else 0.0,
            "expectancy_r": float(resolved["realised_r"].mean()) if len(resolved) else float("nan"),
        }

    def by_regime(self) -> pd.DataFrame:
        """Does the regime verdict actually condition outcomes?

        This is the load-bearing claim of Engine 1. If green and red days produce the same
        result, the regime gate is costing selectivity for nothing.
        """
        rows = []
        for day in self.days:
            excess = [
                fwd - day.universe_mean_return for _, _, fwd in day.ranked if fwd == fwd
            ]
            if not excess:
                continue
            rows.append(
                {
                    "regime": day.regime,
                    "top_excess": float(np.mean(excess[:10])) if excess else np.nan,
                    "universe_return": day.universe_mean_return,
                }
            )
        if not rows:
            return pd.DataFrame()
        return (
            pd.DataFrame(rows)
            .groupby("regime")
            .agg(
                days=("regime", "size"),
                mean_top10_excess=("top_excess", "mean"),
                mean_universe_return=("universe_return", "mean"),
            )
            .reset_index()
        )


def _forward_prices(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    closes = history.pivot_table(index="date", columns="symbol", values="close").sort_index()
    highs = history.pivot_table(index="date", columns="symbol", values="high").sort_index()
    lows = history.pivot_table(index="date", columns="symbol", values="low").sort_index()
    return closes, highs, lows


def _evaluate_plan(
    plan: dict,
    symbol: str,
    highs: pd.DataFrame,
    lows: pd.DataFrame,
    start: date,
    horizon: int,
) -> dict:
    """Walk forward bar by bar: did entry trigger, then did stop or target come first?"""
    if symbol not in highs.columns:
        return {**plan, "outcome": "no_data", "realised_r": 0.0}

    future = highs.index[(highs.index > start)][:horizon]
    if len(future) == 0:
        return {**plan, "outcome": "open", "realised_r": 0.0}

    entry, stop, target = plan["entry"], plan["stop"], plan["target"]
    risk = entry - stop
    triggered = False

    for day in future:
        high = highs.at[day, symbol]
        low = lows.at[day, symbol]
        if not np.isfinite(high) or not np.isfinite(low):
            continue

        if not triggered:
            # A stop-buy entry fills only if price trades up through it.
            if high >= entry:
                triggered = True
            else:
                continue

        # Same-bar ambiguity resolves as a loss: without intraday sequence we cannot know
        # which came first, and assuming the win is how backtests flatter themselves.
        if low <= stop:
            return {**plan, "outcome": "stop", "realised_r": -1.0}
        if high >= target:
            return {**plan, "outcome": "target", "realised_r": (target - entry) / risk}

    if not triggered:
        return {**plan, "outcome": "no_trigger", "realised_r": 0.0}

    # Still open at the horizon: mark to the last close.
    last = future[-1]
    close = highs.at[last, symbol]
    return {**plan, "outcome": "open", "realised_r": float((close - entry) / risk)}


def run_backtest(
    start: date,
    end: date,
    *,
    horizon: int = 10,
    top_n: int = 10,
    step: int = 5,
    use_catalyst: bool = False,
) -> BacktestReport:
    """Walk from ``start`` to ``end``, ranking point-in-time and scoring forward.

    ``step`` samples every Nth trading day; a full daily walk re-runs the whole engine
    stack per day and is slow without adding much for a first read.

    ``use_catalyst`` defaults False: catalysts have only been recorded since the system
    started running, so a historical window has none and enabling it would just add a
    constant.
    """
    from .engines.selection import run_selection
    from .storage import load_history

    history = load_history(days=1200, end=end)
    if history.empty:
        raise RuntimeError("No stored history — run `asymmetry backfill` first.")

    closes, highs, lows = _forward_prices(history)
    trading_days = [d for d in closes.index if start <= d <= end]
    sampled = trading_days[::step]

    report = BacktestReport(horizon=horizon)
    logger.info(
        f"[backtest] {len(sampled)} sample days ({start} -> {end}), "
        f"horizon {horizon}d, step {step}"
    )

    for i, as_of in enumerate(sampled, 1):
        future = closes.index[closes.index > as_of][:horizon]
        if len(future) < horizon:
            continue  # not enough forward data to judge this day

        try:
            result = run_selection(
                as_of, top_n=top_n, use_catalyst=use_catalyst
            )
        except Exception as exc:  # noqa: BLE001 — one bad day must not kill the walk
            logger.warning(f"[backtest] {as_of} failed: {exc}")
            continue

        horizon_end = future[-1]
        forward = (closes.loc[horizon_end] / closes.loc[as_of] - 1) * 100
        universe_mean = float(forward.mean(skipna=True))

        ranked = [
            (c.symbol, c.total_score, float(forward.get(c.symbol, np.nan)))
            for c in result.candidates
        ]
        plans = [
            _evaluate_plan(
                {
                    "as_of": as_of,
                    "symbol": c.symbol,
                    "entry": c.plan.entry,
                    "stop": c.plan.stop,
                    "target": c.plan.target,
                    "planned_r": c.plan.reward_risk,
                },
                c.symbol, highs, lows, as_of, horizon,
            )
            for c in result.candidates
            if c.plan
        ]

        report.days.append(
            DayResult(
                as_of=as_of,
                regime=result.regime.verdict.value,
                regime_score=result.regime.total,
                ranked=ranked,
                plans=plans,
                universe_mean_return=universe_mean,
            )
        )
        if i % 5 == 0 or i == len(sampled):
            logger.info(f"[backtest] {i}/{len(sampled)} days processed")

    return report


def run_rank_backtest(
    start: date,
    end: date,
    *,
    horizon: int = 10,
    step: int = 5,
    with_regime: bool = True,
) -> BacktestReport:
    """Rank the *entire* liquid universe, not just the shortlist.

    The shortlist is only ~10 names, which is far too few to tell signal from noise.
    Scoring every stock gives the quintile spread real statistical weight, and it is the
    honest way to ask whether the factor blend works at all.
    """
    from .engines.selection import score_universe
    from .storage import load_history

    history = load_history(days=1200, end=end)
    if history.empty:
        raise RuntimeError("No stored history — run `asymmetry backfill` first.")

    closes, _, _ = _forward_prices(history)
    trading_days = [d for d in closes.index if start <= d <= end]
    sampled = trading_days[::step]

    report = BacktestReport(horizon=horizon)
    logger.info(f"[backtest] scoring full universe over {len(sampled)} days")

    for i, as_of in enumerate(sampled, 1):
        future = closes.index[closes.index > as_of][:horizon]
        if len(future) < horizon:
            continue
        try:
            # Pass the pre-loaded frame: re-reading it per day dominated the runtime.
            scores, regime = score_universe(as_of, history=history, with_regime=with_regime)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[backtest] {as_of} failed: {exc}")
            continue
        if not scores:
            continue

        forward = (closes.loc[future[-1]] / closes.loc[as_of] - 1) * 100
        universe_mean = float(forward.mean(skipna=True))
        ranked = [
            (sym, score, float(forward.get(sym, np.nan))) for sym, score in scores.items()
        ]

        report.days.append(
            DayResult(
                as_of=as_of,
                regime=regime.verdict.value if regime else RegimeVerdict.SELECTIVE.value,
                regime_score=regime.total if regime else 0,
                ranked=ranked,
                universe_mean_return=universe_mean,
            )
        )
        if i % 5 == 0 or i == len(sampled):
            logger.info(f"[backtest] {i}/{len(sampled)} days processed")

    return report
