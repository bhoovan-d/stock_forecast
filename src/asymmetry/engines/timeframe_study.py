"""Which timeframe do trend-following and kill-zone setups actually work in on NSE?

The question this answers — asked directly, and worth answering with a measurement rather
than a preference — is at what bar size a continuation trade has anything to continue into.
Three instruments, deliberately, because each one fails in a way the others do not:

**1. The variance ratio.** Model-free, and the closest thing to a direct read on the
question. If prices follow a random walk, the variance of a q-period return is exactly q
times the variance of a one-period return, so VR(q) = 1. Above 1, returns are positively
autocorrelated — moves extend, and a trend rule has something to hold. Below 1, they revert,
and every continuation entry is buying the top of a wiggle. This measures the *market*, not
a strategy, so no rule of mine can flatter it, and it needs no cost assumption. Reported
with the Lo-MacKinlay heteroskedasticity-robust z: Indian equity intraday returns are
violently heteroskedastic around the open, and the homoskedastic statistic would call that
volatility clustering "trend".

**2. First-order autocorrelation of bar returns.** The crudest possible version of the same
question, kept because it is impossible to misread. Negative ρ₁ at a given bar size means
that bar size is mean-reverting, full stop.

**3. A mechanical continuation rule at each timeframe, with that timeframe's own costs.**
The variance ratio can be favourable and the trade still lose, because a signal has to pay
to be acted on. This is where the codebase's hardest-won lesson applies: **cost in R is
(cost% / stop%)**, so shrinking the bar size shrinks the stop and inflates the cost in R
hyperbolically. A rule with identical geometry at 5m and at daily is not the same trade.

**4. The same geometry entered at random, as a control.** This is the instrument that
changes the answer, and without it the study is misleading. A long-only rule with a wide stop,
replayed over a market that rose, looks profitable for a reason that has nothing to do with
the rule — and the longer the bar, the wider the stop and the more drift it captures. So every
signal entry is matched with a randomly chosen entry bar **in the same frame**, carrying the
identical stop, target and holding cap. Whatever the random cohort earns is drift. Only the
difference between the two is attributable to the signal, and on this data almost none of the
apparent timeframe gradient survives that subtraction.

Three things this study cannot do, stated before its numbers rather than after:

* **The intraday and daily samples are not the same length.** Yahoo serves ~60 days of
  intraday bars and years of daily ones. Every intraday row here is one market period; the
  daily rows are many. A cross-timeframe ranking therefore compares a noisy estimate against
  a stable one, and the intraday rows should be read as "this window" rather than "this
  timeframe".
* **It says nothing about a specific strategy's edge.** A timeframe where continuation
  persists is a timeframe where a trend rule is *possible*, not one where any given rule
  works.
* **It is one market.** Persistence is regime-dependent, and 60 sessions contains one regime.

Nothing here places an order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from .indicators import ema

# Cost per round trip at each holding period, in percent of notional. These are *derived*
# here rather than imported from any strategy engine, and that is the point: V3, the HMA
# pullback and the trident each carry their own constant, and this module comparing
# timeframes must not inherit any one of them or it would tilt the comparison toward whichever
# strategy's constant it borrowed.
#
#     intraday (squared off same session)
#         brokerage   0.020      STT 0.025 (sell side only)      exchange 0.006
#         stamp       0.003      GST 0.003
#         total       0.050 % round trip, plus slippage
#
#     delivery (held overnight or longer)
#         brokerage   0.020      STT 0.100 (both sides)          exchange 0.006
#         stamp       0.003      GST 0.003
#         total       0.132 % round trip, rounded to 0.12, plus slippage
#
# The 3.4x gap between them is exactly the error this codebase had to retract a published
# figure over, and it is why the intraday rows below are not charged the delivery number.
INTRADAY_COST_PCT = 0.050
DELIVERY_COST_PCT = 0.120
INTRADAY_SLIPPAGE_PCT = 0.020
DELIVERY_SLIPPAGE_PCT = 0.050

# What each label means to the fetcher, and whether a position taken on it is squared off in
# the session. `120m` is absent because Yahoo returns HTTP 400 for it — it does not exist and
# has to be resampled by position within the session, which is a trap this codebase has
# already paid for.
TIMEFRAMES: dict[str, tuple[str, str, bool]] = {
    # label: (yahoo interval, yahoo range, intraday cost applies)
    "5m": ("5m", "60d", True),
    "15m": ("15m", "60d", True),
    "30m": ("30m", "60d", True),
    "60m": ("60m", "60d", True),
    "daily": ("1d", "5y", False),
    "weekly": ("1wk", "10y", False),
}


@dataclass
class PersistenceRow:
    """What the market itself says about continuation at one bar size."""

    timeframe: str
    bars: int = 0
    symbols: int = 0
    variance_ratio: float = float("nan")     # VR(2), cross-sectional median
    vr_z: float = float("nan")               # Lo-MacKinlay heteroskedasticity-robust z
    vr_4: float = float("nan")               # VR(4), for the shape of the decay
    autocorr_1: float = float("nan")         # first-order autocorrelation of bar returns
    share_trending: float = float("nan")     # share of symbols with VR(2) > 1
    median_bar_range_pct: float = float("nan")

    @property
    def verdict(self) -> str:
        """Read off the z, never the point estimate.

        |z| < 1.96 means the data cannot distinguish this bar size from a random walk, which
        is a genuine answer and the most common one. Calling a VR of 1.03 "trending" because
        it is above 1 is how a study invents a result.
        """
        if not np.isfinite(self.vr_z):
            return "insufficient data"
        if abs(self.vr_z) < 1.96:
            return "indistinguishable from a random walk"
        return "persistent (trend-friendly)" if self.variance_ratio > 1 else "mean-reverting"


@dataclass
class RuleRow:
    """A mechanical continuation rule replayed at one bar size, paying that size's costs."""

    timeframe: str
    trades: int = 0
    wins: int = 0
    gross_r: float = float("nan")
    net_r: float = float("nan")
    cost_r: float = float("nan")
    median_stop_pct: float = float("nan")
    ci_low: float = float("nan")
    ci_high: float = float("nan")
    bars_held: float = float("nan")
    intraday_costs: bool = True

    # The random-entry control: identical geometry, entry bar chosen at random from the same
    # frame. `excess_r` is the only number here that is about the *signal* rather than about
    # the market the signal was run in.
    control_gross_r: float = float("nan")
    control_n: int = 0
    excess_r: float = float("nan")
    excess_t: float = float("nan")

    @property
    def win_rate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else float("nan")

    @property
    def decisive(self) -> bool:
        """Does the interval exclude zero? Nearly always no, and saying so is the finding."""
        return np.isfinite(self.ci_low) and (self.ci_low > 0 or self.ci_high < 0)

    @property
    def signal_verdict(self) -> str:
        """What the signal adds over a coin-flip entry with the same geometry.

        Read this and not `net_r` when the question is whether a *rule* works. `net_r` answers
        a different question — whether holding a long with this stop and this target made
        money over this period — and in a market that rose, the answer to that can be yes
        while the rule itself is worthless or harmful.
        """
        if not np.isfinite(self.excess_t):
            return "no control"
        if abs(self.excess_t) < 1.96:
            return "no detectable edge over a random entry"
        return (
            "beats a random entry" if self.excess_r > 0 else "WORSE than a random entry"
        )


@dataclass
class TimeframeStudy:
    persistence: list[PersistenceRow] = field(default_factory=list)
    rule: list[RuleRow] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    reward_risk: float = 3.0
    atr_stop_mult: float = 1.0
    fetch_failed: list[str] = field(default_factory=list)
    index_persistence: list[PersistenceRow] = field(default_factory=list)

    def best_rule(self) -> RuleRow | None:
        """Ranked on excess over a random entry, not on net R.

        Net R rewards whichever timeframe held a long the longest in a rising market. That is
        a real thing to earn but it is not the rule working, and ranking on it would name a
        winner for a reason unrelated to the signal.
        """
        scored = [r for r in self.rule if np.isfinite(r.excess_r) and r.trades >= 30]
        return max(scored, key=lambda r: r.excess_r) if scored else None


# ── Instrument 1 and 2: what the market does, before any rule is applied ──────


def variance_ratio(returns: np.ndarray, q: int) -> tuple[float, float]:
    """Lo-MacKinlay VR(q) and its heteroskedasticity-robust z-statistic.

    VR(q) = Var(q-period return) / (q · Var(1-period return)), computed with the overlapping
    estimator and the unbiased corrections from the original paper.

    The robust z is not optional here. Intraday NSE returns are strongly heteroskedastic —
    the 09:15 bar is a different animal from the 13:00 one — and the homoskedastic statistic
    reads that clustering as persistence. Using it would manufacture a trend finding at
    exactly the bar sizes the question is about.
    """
    x = np.asarray(returns, dtype=float)
    x = x[np.isfinite(x)]
    n = len(x)
    # A variance ratio from a few dozen returns is noise wearing three decimal places. The
    # floor is absolute rather than a multiple of q, because the estimator's sampling error
    # is driven by n and a small-q pass would otherwise sail through on 12 observations.
    if q < 2 or n < max(60, q * 10):
        return float("nan"), float("nan")

    mu = x.mean()
    # Unbiased one-period variance.
    var1 = np.sum((x - mu) ** 2) / (n - 1)
    if var1 <= 0:
        return float("nan"), float("nan")

    cumulative = np.cumsum(x)
    q_returns = cumulative[q - 1 :] - np.concatenate(([0.0], cumulative[: -q]))
    m = q * (n - q + 1) * (1 - q / n)
    if m <= 0:
        return float("nan"), float("nan")
    varq = np.sum((q_returns - q * mu) ** 2) / m
    vr = varq / var1

    # Heteroskedasticity-robust variance of VR: a weighted sum of the delta-j asymptotic
    # variances of each autocovariance term.
    #     delta_j = SUM_t (x_t - mu)^2 (x_{t-j} - mu)^2  /  [ SUM_t (x_t - mu)^2 ]^2
    #     theta   = SUM_j [ 2(q-j)/q ]^2 . delta_j
    #     z       = (VR - 1) / sqrt(theta)
    #
    # delta_j already carries the 1/n: its numerator sums n terms while its denominator is a
    # squared sum of n. Rescaling it by n — which an earlier draft of this did — leaves theta
    # O(1) instead of O(1/n), so z stops growing with the sample and every series on earth
    # reads as indistinguishable from a random walk. An AR(1) with phi = 0.3 over 6,000
    # returns scored z = 0.29 rather than 13.9, which is a test that can never fail and
    # therefore a study that can never find anything.
    theta = 0.0
    dev2 = (x - mu) ** 2
    denom = dev2.sum() ** 2
    if denom <= 0:
        return vr, float("nan")
    for j in range(1, q):
        num = float(np.sum(dev2[j:] * dev2[: n - j]))
        delta = num / denom
        theta += (2 * (q - j) / q) ** 2 * delta
    if theta <= 0:
        return vr, float("nan")
    return vr, float((vr - 1) / np.sqrt(theta))


def persistence_of(frames: dict[str, pd.DataFrame], timeframe: str) -> PersistenceRow:
    """Cross-sectional persistence at one bar size.

    Each symbol contributes its own VR; the row reports the **median** across symbols and the
    share above 1, rather than pooling every return into one series. Pooling would let the
    two or three most active names dominate, and the question is about the timeframe, not
    about Reliance.
    """
    row = PersistenceRow(timeframe=timeframe, symbols=len(frames))
    vrs, zs, vr4s, acs, ranges, bars = [], [], [], [], [], 0

    for frame in frames.values():
        if frame is None or len(frame) < 60:
            continue
        close = frame["close"].to_numpy(dtype=float)
        # Log returns *within* each session only, so the overnight gap is not mixed into an
        # intraday autocorrelation. A gap is not a 5-minute move, and counting it as one is
        # how an intraday study picks up the daily series by accident.
        rets = np.diff(np.log(close))
        if frame.index.tz is not None or timeframe in ("5m", "15m", "30m", "60m"):
            same_day = np.array(
                [frame.index[i].date() == frame.index[i + 1].date()
                 for i in range(len(frame) - 1)]
            )
            if same_day.any():
                rets = rets[same_day]
        rets = rets[np.isfinite(rets)]
        if len(rets) < 60:
            continue
        bars += len(rets)

        vr, z = variance_ratio(rets, 2)
        vr4, _ = variance_ratio(rets, 4)
        if np.isfinite(vr):
            vrs.append(vr)
        if np.isfinite(z):
            zs.append(z)
        if np.isfinite(vr4):
            vr4s.append(vr4)
        if len(rets) > 2 and rets.std() > 0:
            acs.append(float(np.corrcoef(rets[:-1], rets[1:])[0, 1]))
        rng = ((frame["high"] - frame["low"]) / frame["close"]).replace(
            [np.inf, -np.inf], np.nan
        ).dropna()
        if len(rng):
            ranges.append(float(rng.median() * 100))

    row.bars = bars
    if vrs:
        row.variance_ratio = float(np.median(vrs))
        row.share_trending = float(np.mean(np.array(vrs) > 1) * 100)
    if zs:
        # The median z across symbols, not a pooled statistic. Names are correlated — they
        # are all NIFTY constituents — so a pooled z would overstate significance badly by
        # treating 150 correlated series as 150 independent ones.
        row.vr_z = float(np.median(zs))
    if vr4s:
        row.vr_4 = float(np.median(vr4s))
    if acs:
        row.autocorr_1 = float(np.median(acs))
    if ranges:
        row.median_bar_range_pct = float(np.median(ranges))
    return row


# ── Instrument 3: one continuation rule, replayed at every bar size ───────────


def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = frame["high"], frame["low"], frame["close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def replay_continuation(
    frame: pd.DataFrame,
    *,
    reward_risk: float = 3.0,
    atr_stop_mult: float = 1.0,
    intraday_costs: bool = True,
    max_bars_held: int = 200,
) -> list[dict]:
    """A deliberately plain trend-following rule, identical at every timeframe.

    Entry: the 5/9/13/21 EMAs *become* stacked on this bar having not been on the previous
    one, and the close is above the 200 EMA. Stop: one ATR(14) below entry. Target:
    `reward_risk` ATRs above.

    Chosen because it is the same stack the trident requires, so the two studies are asking
    about the same notion of trend — and because it has no free parameters to tune per
    timeframe, which is the only way a cross-timeframe comparison means anything. A rule
    optimised separately at each bar size would measure the optimiser.

    A bar touching both stop and target books a **loss**: the order of events inside a bar is
    unknown, and resolving it favourably is the standard way a replay invents an edge.
    """
    if frame is None or len(frame) < 220:
        return []
    work = frame.copy()
    for period in (5, 9, 13, 21, 200):
        work[f"ema{period}"] = ema(work["close"], period)
    work["atr"] = _atr(work)

    stacked = (
        (work["ema5"] > work["ema9"])
        & (work["ema9"] > work["ema13"])
        & (work["ema13"] > work["ema21"])
        & (work["close"] > work["ema200"])
    )
    fresh = stacked & ~stacked.shift(1, fill_value=False)

    cost_pct = (
        INTRADAY_COST_PCT + INTRADAY_SLIPPAGE_PCT
        if intraday_costs
        else DELIVERY_COST_PCT + DELIVERY_SLIPPAGE_PCT
    )
    highs = work["high"].to_numpy(dtype=float)
    lows = work["low"].to_numpy(dtype=float)
    closes = work["close"].to_numpy(dtype=float)
    atrs = work["atr"].to_numpy(dtype=float)
    triggers = np.flatnonzero(fresh.to_numpy())

    # The 200 EMA is not meaningful until roughly 200 bars have fed it. `ewm` returns a value
    # from the very first bar, so without this a monotone series "confirms" a 200-EMA
    # condition at bar 2 — the indicator answering a question about history it has not seen.
    warmup = 200

    out: list[dict] = []
    for i in triggers:
        if i < warmup or i + 1 >= len(work):
            continue
        if not np.isfinite(atrs[i]) or atrs[i] <= 0:
            continue
        entry = closes[i]
        risk = atr_stop_mult * atrs[i]
        stop, target = entry - risk, entry + reward_risk * risk
        if entry <= 0 or risk <= 0:
            continue
        risk_pct = risk / entry * 100
        cost_r = cost_pct / risk_pct

        realised, held, resolved = None, 0, False
        for j in range(i + 1, min(i + 1 + max_bars_held, len(work))):
            held = j - i
            if lows[j] <= stop:                      # checked first, deliberately
                realised, resolved = -1.0, True
                break
            if highs[j] >= target:
                realised, resolved = reward_risk, True
                break
        if not resolved:
            # Marked out at the last bar seen rather than dropped: dropping unresolved trades
            # keeps only the ones that moved, which is a survivorship filter on outcomes.
            last = min(i + max_bars_held, len(work) - 1)
            if last <= i:
                continue
            realised = (closes[last] - entry) / risk
        out.append(
            {
                "gross_r": float(realised),
                "cost_r": float(cost_r),
                "net_r": float(realised) - float(cost_r),
                "risk_pct": float(risk_pct),
                "bars_held": held,
                "won": bool(resolved and realised > 0),
                "resolved": resolved,
            }
        )
    return out


def replay_random_entries(
    frame: pd.DataFrame,
    count: int,
    *,
    reward_risk: float = 3.0,
    atr_stop_mult: float = 1.0,
    max_bars_held: int = 200,
    warmup: int = 200,
    rng: np.random.Generator | None = None,
) -> list[float]:
    """`count` trades with the same geometry, entered on randomly chosen bars of `frame`.

    Matched per frame rather than pooled, so the control cohort sees exactly the same
    instruments over exactly the same periods as the signal cohort. A control drawn from a
    different set of names would compare two universes and call the difference alpha.

    Returns gross R only. Costs are identical by construction — same stop distance
    distribution, same cost model — so they cancel in the difference and adding them would
    only obscure it.
    """
    if frame is None or len(frame) < warmup + 20 or count <= 0:
        return []
    rng = rng or np.random.default_rng()
    atrs = _atr(frame).to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)

    pool = np.arange(warmup, len(frame) - 2)
    if len(pool) == 0:
        return []
    out: list[float] = []
    for i in rng.choice(pool, size=min(count, len(pool)), replace=False):
        if not np.isfinite(atrs[i]) or atrs[i] <= 0 or closes[i] <= 0:
            continue
        entry = closes[i]
        risk = atr_stop_mult * atrs[i]
        stop, target = entry - risk, entry + reward_risk * risk
        realised, resolved = None, False
        for j in range(i + 1, min(i + 1 + max_bars_held, len(frame))):
            # Same honesty rule as the signal replay: the stop is checked first, so a bar
            # reaching both levels books the loss.
            if lows[j] <= stop:
                realised, resolved = -1.0, True
                break
            if highs[j] >= target:
                realised, resolved = reward_risk, True
                break
        if not resolved:
            last = min(i + max_bars_held, len(frame) - 1)
            if last <= i:
                continue
            realised = (closes[last] - entry) / risk
        out.append(float(realised))
    return out


def summarise_rule(rows: list[dict], timeframe: str, intraday_costs: bool) -> RuleRow:
    out = RuleRow(timeframe=timeframe, trades=len(rows), intraday_costs=intraday_costs)
    if not rows:
        return out
    net = np.array([r["net_r"] for r in rows])
    out.wins = sum(r["won"] for r in rows)
    out.gross_r = float(np.mean([r["gross_r"] for r in rows]))
    out.net_r = float(net.mean())
    out.cost_r = float(np.mean([r["cost_r"] for r in rows]))
    out.median_stop_pct = float(np.median([r["risk_pct"] for r in rows]))
    out.bars_held = float(np.mean([r["bars_held"] for r in rows]))
    if len(net) >= 2:
        margin = 1.96 * net.std(ddof=1) / np.sqrt(len(net))
        out.ci_low, out.ci_high = float(net.mean() - margin), float(net.mean() + margin)
    return out


def attach_control(row: RuleRow, signal_gross: list[float], control: list[float]) -> RuleRow:
    """Fold the random-entry cohort into a rule row as an excess and a t-statistic."""
    s, c = np.asarray(signal_gross, dtype=float), np.asarray(control, dtype=float)
    row.control_n = len(c)
    if len(s) < 2 or len(c) < 2:
        return row
    row.control_gross_r = float(c.mean())
    row.excess_r = float(s.mean() - c.mean())
    pooled = np.sqrt(s.var(ddof=1) / len(s) + c.var(ddof=1) / len(c))
    if pooled > 0:
        row.excess_t = float(row.excess_r / pooled)
    return row


# ── The study ─────────────────────────────────────────────────────────────────


def run_study(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 60,
    universe: str = "nifty500",
    reward_risk: float = 3.0,
    atr_stop_mult: float = 1.0,
    timeframes: list[str] | None = None,
    as_of: date | None = None,
    seed: int = 20260830,
) -> TimeframeStudy:
    """Fetch each timeframe once per symbol and run all three instruments off those frames.

    The universe comes through the trident's liquidity floor, for the reason the floor
    exists: a 5-minute bar in a thin name has low == high, and a study of intraday
    persistence run over data holes measures the holes.
    """
    from ..data import yahoo
    from .trident import TridentSettings
    from .trident_universe import resolve_universe

    labels = timeframes or list(TIMEFRAMES)
    cfg = TridentSettings()
    names, _ = resolve_universe(
        cfg, universe=universe, symbols=symbols, max_symbols=max_symbols, as_of=as_of
    )
    study = TimeframeStudy(
        symbols=names, reward_risk=reward_risk, atr_stop_mult=atr_stop_mult
    )
    if not names:
        logger.error("[timeframe] empty universe")
        return study

    # Seeded, so the control cohort is reproducible. An unseeded control would move the
    # study's headline finding between runs, which is the one number here that must not.
    rng = np.random.default_rng(seed)

    for label in labels:
        interval, range_, intraday = TIMEFRAMES[label]
        frames: dict[str, pd.DataFrame] = {}
        rows: list[dict] = []
        control: list[float] = []

        for position, symbol in enumerate(names, 1):
            frame = yahoo.fetch_chart(
                yahoo.to_yahoo_symbol(symbol), range_=range_, interval=interval, as_of=as_of
            )
            if frame is None or frame.empty:
                study.fetch_failed.append(f"{symbol}@{label}")
                continue
            frames[symbol] = frame
            trades = replay_continuation(
                frame, reward_risk=reward_risk, atr_stop_mult=atr_stop_mult,
                intraday_costs=intraday,
            )
            rows += trades
            # One control entry per signal entry, drawn from this same frame.
            control += replay_random_entries(
                frame, len(trades), reward_risk=reward_risk,
                atr_stop_mult=atr_stop_mult, rng=rng,
            )
            if position % 25 == 0:
                logger.info(f"[timeframe] {label} {position}/{len(names)}")

        study.persistence.append(persistence_of(frames, label))
        summary = summarise_rule(rows, label, intraday)
        study.rule.append(
            attach_control(summary, [r["gross_r"] for r in rows], control)
        )
        logger.info(
            f"[timeframe] {label}: {len(frames)} symbols, {len(rows)} rule trades, "
            f"VR {study.persistence[-1].variance_ratio:.3f}, "
            f"excess over random {summary.excess_r:+.3f}R"
        )

    # The index itself, separately. A cross-section of stocks and the index they compose can
    # disagree: idiosyncratic mean reversion in single names can coexist with persistence in
    # the aggregate, and someone trading NIFTY futures needs the second number.
    for label in labels:
        interval, range_, _ = TIMEFRAMES[label]
        frame = yahoo.fetch_chart("^NSEI", range_=range_, interval=interval, as_of=as_of)
        if frame is not None and not frame.empty:
            study.index_persistence.append(persistence_of({"^NSEI": frame}, label))
    return study
