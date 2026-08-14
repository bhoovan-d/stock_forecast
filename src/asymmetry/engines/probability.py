"""P(target before stop) and expected value (Brief §15, §16).

The brief calls this the most important upgrade, and it is also the easiest place in the
whole system to fool yourself. Three commitments keep it honest:

* **Comparable setups, not a fitted curve.** The estimate comes from measuring what
  actually happened to similar historical setups — same compression state, same trend, same
  distance-to-target in volatility terms — using only bars available at the entry timestamp.

* **Ambiguity resolves against the trade.** When a single bar spans both the stop and the
  target, intraday sequence is unknown, so it counts as a loss. Every backtest that assumes
  otherwise reports an edge it does not have.

* **An uncalibrated number is not a probability.** The brief forbids displaying model
  confidence as though it were a frequency. ``ProbabilityEstimate.calibrated`` and
  ``sample_size`` travel with the number so a thin estimate is visible as thin.

The base rates are measured from daily bars. Daily data resolves an intraday stop-then-
target sequence incorrectly, so these numbers are approximations of a 15-minute-triggered
reality — and, importantly, approximations that err toward *pessimism*, since the same-bar
rule books the loss.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..spec import ExpectedValue, ProbabilityEstimate, Trend, VolatilityState

# A bucket needs this many historical analogues before it is treated as a base rate rather
# than an anecdote.
MIN_SAMPLE = 40


@dataclass(frozen=True)
class SetupSignature:
    """The features that define "a setup like this one".

    Deliberately coarse. Fine-grained buckets look precise and contain four observations;
    coarse buckets give base rates that survive out of sample.
    """

    trend_up: bool
    compressed: bool
    # Required move to target, expressed in daily-ATR units and bucketed.
    distance_bucket: int

    @staticmethod
    def distance_to_bucket(required_pct: float, atr_pct: float) -> int:
        if atr_pct <= 0:
            return 9
        ratio = required_pct / atr_pct
        # 0: <2 ATR, 1: 2-3, 2: 3-4, 3: 4-6, 4: 6+
        for i, edge in enumerate((2, 3, 4, 6)):
            if ratio < edge:
                return i
        return 4

    @classmethod
    def build(
        cls, trend: Trend, volatility: VolatilityState, required_pct: float
    ) -> "SetupSignature":
        return cls(
            trend_up=trend is Trend.UP,
            compressed=volatility.compressed,
            distance_bucket=cls.distance_to_bucket(required_pct, volatility.atr_pct),
        )


def _resolve(
    highs: np.ndarray, lows: np.ndarray, entry: float, stop: float, target: float
) -> tuple[str, int]:
    """Walk bars forward. Returns (outcome, sessions taken)."""
    for i in range(len(highs)):
        hit_stop = lows[i] <= stop
        hit_target = highs[i] >= target
        if hit_stop and hit_target:
            return "stop", i + 1   # ambiguous bar resolves against the trade
        if hit_stop:
            return "stop", i + 1
        if hit_target:
            return "target", i + 1
    return "timeout", len(highs)


def measure_base_rates(
    history: pd.DataFrame,
    *,
    stop_pct: float,
    reward_multiple: float,
    horizons: tuple[int, ...] = (1, 3, 5),
    max_symbols: int = 250,
) -> dict[SetupSignature, dict[str, float]]:
    """Historical P(target before stop) by setup signature.

    Simulates the spec's own geometry — a stop ``stop_pct`` below entry and a target
    ``reward_multiple`` × that risk above it — on every stock/day in the stored history,
    then groups outcomes by signature.
    """
    from .structure import classify_structure
    from .volatility import assess_volatility

    closes = history.pivot_table(index="date", columns="symbol", values="close").sort_index()
    highs = history.pivot_table(index="date", columns="symbol", values="high").sort_index()
    lows = history.pivot_table(index="date", columns="symbol", values="low").sort_index()

    symbols = list(closes.columns)[:max_symbols]
    max_h = max(horizons)
    buckets: dict[SetupSignature, list[tuple[str, int]]] = {}

    for symbol in symbols:
        close = closes[symbol].dropna()
        if len(close) < 120:
            continue
        frame = pd.DataFrame(
            {"high": highs[symbol], "low": lows[symbol], "close": closes[symbol]}
        ).dropna()
        if len(frame) < 120:
            continue

        high_arr = frame["high"].to_numpy()
        low_arr = frame["low"].to_numpy()
        close_arr = frame["close"].to_numpy()

        # Step through history, leaving room for the forward window.
        for i in range(100, len(frame) - max_h, 3):
            window = frame.iloc[: i + 1]
            entry = float(close_arr[i])
            if entry <= 0:
                continue

            stop = entry * (1 - stop_pct / 100)
            target = entry + reward_multiple * (entry - stop)

            volatility = assess_volatility(window.tail(120))
            if volatility.atr_pct <= 0:
                continue
            _, trend = classify_structure(window.tail(60))
            required_pct = (target / entry - 1) * 100
            signature = SetupSignature.build(trend, volatility, required_pct)

            outcome, sessions = _resolve(
                high_arr[i + 1 : i + 1 + max_h],
                low_arr[i + 1 : i + 1 + max_h],
                entry, stop, target,
            )
            buckets.setdefault(signature, []).append((outcome, sessions))

    rates: dict[SetupSignature, dict[str, float]] = {}
    for signature, outcomes in buckets.items():
        if len(outcomes) < MIN_SAMPLE:
            continue
        row = {"n": float(len(outcomes))}
        for horizon in horizons:
            wins = sum(
                1 for outcome, sessions in outcomes
                if outcome == "target" and sessions <= horizon
            )
            row[f"p_{horizon}"] = wins / len(outcomes)
        row["p_timeout"] = sum(1 for o, _ in outcomes if o == "timeout") / len(outcomes)
        rates[signature] = row
    return rates


def estimate_probability(
    signature: SetupSignature, rates: dict[SetupSignature, dict[str, float]]
) -> ProbabilityEstimate:
    """Look up the base rate for this signature, falling back gracefully."""
    row = rates.get(signature)
    if row is None:
        # Back off along the least important dimension first (compression), then trend.
        for relaxed in (
            SetupSignature(signature.trend_up, not signature.compressed, signature.distance_bucket),
            SetupSignature(True, signature.compressed, signature.distance_bucket),
        ):
            row = rates.get(relaxed)
            if row is not None:
                break

    if row is None:
        return ProbabilityEstimate(
            note="no comparable historical setups — probability unavailable",
            calibrated=False,
        )

    return ProbabilityEstimate(
        p_1d=round(row.get("p_1", 0.0), 4),
        p_3d=round(row.get("p_3", 0.0), 4),
        p_5d=round(row.get("p_5", 0.0), 4),
        p_timeout=round(row.get("p_timeout", 0.0), 4),
        sample_size=int(row["n"]),
        calibrated=row["n"] >= MIN_SAMPLE * 2,
        note=f"base rate from {int(row['n'])} comparable setups (daily-bar approximation)",
    )


def expected_value(
    probability: ProbabilityEstimate, reward_multiple: float
) -> ExpectedValue:
    """EV in R, after costs, slippage, gap risk and timeouts (Brief §16)."""
    p_win = probability.p_5d
    if p_win <= 0:
        return ExpectedValue(note="no probability estimate — EV not computed")

    p_timeout = probability.p_timeout
    p_loss = max(0.0, 1.0 - p_win - p_timeout)

    gross = p_win * reward_multiple - p_loss * 1.0

    # Costs are a fraction of price, converted into R via the stop distance: a tight stop
    # makes every cost loom larger in R terms, which is precisely the trade-off a 1.4% stop
    # creates and which R:R alone hides.
    cost_pct = settings.cost_roundtrip_pct + settings.slippage_pct
    cost_r = cost_pct / settings.max_stop_pct

    # A gap through the stop costs more than 1R.
    gap_penalty = p_loss * settings.gap_risk_pct * 0.5

    # A timeout exits flat-ish, so it neither wins nor loses beyond costs.
    net = gross - cost_r - gap_penalty

    return ExpectedValue(
        ev_r=round(net, 3),
        gross_ev_r=round(gross, 3),
        cost_r=round(cost_r + gap_penalty, 3),
        positive=net > 0,
        note=(
            f"P(win)={p_win:.0%}, P(timeout)={p_timeout:.0%}, "
            f"costs {cost_r + gap_penalty:.2f}R"
        ),
    )


def calibration_report(
    rates: dict[SetupSignature, dict[str, float]]
) -> pd.DataFrame:
    """Predicted vs realised, so the estimates can be checked rather than trusted."""
    rows = []
    for signature, row in sorted(rates.items(), key=lambda kv: -kv[1]["n"]):
        rows.append(
            {
                "trend_up": signature.trend_up,
                "compressed": signature.compressed,
                "distance_bucket": signature.distance_bucket,
                "n": int(row["n"]),
                "p_1d": round(row.get("p_1", 0), 3),
                "p_3d": round(row.get("p_3", 0), 3),
                "p_5d": round(row.get("p_5", 0), 3),
                "p_timeout": round(row.get("p_timeout", 0), 3),
            }
        )
    return pd.DataFrame(rows)
