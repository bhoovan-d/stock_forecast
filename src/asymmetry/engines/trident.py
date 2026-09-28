"""Kill-zone fair-value-gap reclaim — the "trident" — as a separate strategy.

Source: Tyler / TG Capital on Chart Fanatics, transcribed 26 Aug 2026. In his words, the
parts that are rules rather than commentary:

    1. Only the London kill zone, 03:00-06:30 New York time. "This setup means nothing
       without the time."
    2. One entry timeframe only, the 30 minute, "to keep it simple". The daily supplies the
       bias and the target.
    3. The 5, 9, 13 and 21 EMAs must be stacking. "If the EMAs were crossing I wouldn't be
       interested in any price action that has to do with that."
    4. Above the daily 200 EMA is a long bias; below it he looks for shorts.
    5. A fair value gap prints inside the kill zone.
    6. Price returns to the consequent encroachment - the 50% of that gap - as a **doji**,
       wicking through it and closing back above. "If the body of this candle was in here
       this would be an invalidation. I want to see a doji."
    7. The next candle must close **below the doji's high**. "If it closes above the high
       I'll invalidate the trade."
    8. Entry at that close, stop below the doji's low, minimum 1:20 reward-to-risk.

**This is deliberately not part of V3, and not part of the HMA pullback either.** Nothing is
shared with them beyond generic indicator maths. V3 is a 1-5 session swing engine at 4R with
a 0.5-1.5% stop; the pullback is an intraday trade at 3R with a 0.7% cap; this is a 20R
target off a 30-minute structural stop, which is a different holding period again and
therefore a different cost constant. Sharing one would silently retune the others.

Three things about this build that the reader needs before the numbers:

* **The strategy is FX and gold; this is the NSE equities adaptation.** The owner chose that
  over a faithful FX build. The kill zone therefore had to be remapped, and that choice is a
  knob (`killzone_start` / `killzone_end`), not a discovery. Everything downstream of the
  window - the gap, the doji, the confirmation, the geometry - is transcribed unchanged.
* **20R off a 30-minute stop is not an intraday trade on an equity.** A 0.4% stop implies an
  8% move. On the measured stop distances this is a multi-week hold, so the trade is
  resolved on daily bars after the entry session and pays *delivery* costs, not intraday
  ones. The source describes holding "until the EMAs cross over"; this build takes a fixed
  20R instead, because a mechanical exit is the only kind that can be measured.
* **The source's claimed 90% win rate at 1:20 cannot be reproduced or refuted here**, and
  the reason is arithmetic rather than scepticism: at 8-10 setups a year per instrument, the
  ~60 sessions of 30-minute history the feed serves is not a large enough sample to separate
  90% from 20%. See `docs/spec-trident.md`.

Where his wording had to be made precise, the choice is named in `TridentSettings` and
listed in the spec. Nothing here places an order, and it never will.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from typing import NamedTuple

import numpy as np
import pandas as pd
from loguru import logger

from .indicators import ema

# NSE trades 09:15-15:30 IST. Bars are stamped with their START, and the feed returns one
# extra bar stamped at the close which is the closing print rather than a window.
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
SESSION_MINUTES = (
    datetime.combine(date.min, SESSION_CLOSE) - datetime.combine(date.min, SESSION_OPEN)
).total_seconds() / 60


@dataclass(frozen=True)
class TridentSettings:
    """Every threshold this strategy needs, resolved explicitly.

    Passed around rather than read from the global `settings` singleton, so tuning this
    cannot reach V3 or the pullback engine. `settings` is loaded once at import and is a
    singleton; a default argument reading it would bind at import time, which is a trap this
    codebase has already paid for twice.
    """

    # ── The kill zone ─────────────────────────────────────────────────────────
    # London 03:00-06:30 NY is the first 3h30m of the London session. Mapped here to the
    # first 3h30m of the NSE session. This is a structural analogy, not a measured claim:
    # an equity open is far more front-loaded than an FX session, and the honest position is
    # that the right NSE window is unknown. `--killzone-end` exists so it can be argued with.
    killzone_start: time = time(9, 15)
    killzone_end: time = time(12, 45)
    # The entry timeframe. The source specifies 30m and nothing else; "5m" runs the identical
    # five gates on a 5-minute series as a *parallel track*, never a replacement. Everything
    # downstream that depends on bar length reads `interval_minutes` rather than assuming 30 —
    # the elapsed-session fraction, the forming-bar guard and the sweep lookback all did, and
    # a hardcoded 30 in any of them silently mis-scores the 5m track.
    anchor_interval: str = "30m"

    # ── Trend context on the entry timeframe ──────────────────────────────────
    # "The five, the nine, the 13 and the 21 - they're all stacking."
    ema_stack: tuple[int, ...] = (5, 9, 13, 21)
    require_ema_stack: bool = True

    # ── Daily context ─────────────────────────────────────────────────────────
    # "If my daily chart is below the 200 EMA I'm not looking for longs."
    daily_bias_ema: int = 200
    require_daily_bias: bool = True
    # His fourth confluence is a third-party TradingView indicator colouring daily candles
    # green / blue / red / black. It is reconstructed here from its description - Bollinger
    # position, the 200 EMA and volume - and is therefore *my* indicator, not his. Gating on
    # it by default is faithful to what he says; the rejection count is reported so the cost
    # of my reconstruction stays visible.
    require_strong_daily: bool = True
    daily_bb_period: int = 20
    daily_bb_std: float = 2.0
    # Where in the Bollinger range a bullish daily close has to sit to count as "strong".
    strong_bb_position: float = 0.60

    # ── The fair value gap ────────────────────────────────────────────────────
    # A bullish FVG is the three-bar imbalance: bar 3's low prints above bar 1's high, so
    # the range between them never traded. Bar 2 is the displacement.
    min_gap_pct: float = 0.05          # ignore gaps thinner than this; they are noise
    # He notes 02:30-03:30 NY gaps are the most probable. Recorded, never gated - he takes
    # 04:00 gaps too, so gating on it would be stricter than the source.
    prime_window_minutes: int = 60

    # ── The trident: doji into the consequent encroachment ────────────────────
    # "I want to see a doji." Body as a share of the bar's full range.
    max_doji_body_pct: float = 30.0
    # How many bars after the gap the retrace may arrive before the setup is stale. Bounded
    # by the kill zone in practice; this is a second bound for a widened window.
    max_bars_to_retrace: int = 8

    # ── The trade ─────────────────────────────────────────────────────────────
    reward_risk: float = 20.0          # "the minimum that I'm taking is a 1 to 20"
    # No stop band, deliberately. V3's 0.5-1.5% rule is V3's; this source specifies a
    # structural stop at the doji low and nothing else. A floor and a cap are exposed so the
    # effect can be measured, and both default to inert.
    min_risk_pct: float = 0.0
    max_risk_pct: float = 100.0
    # The source has no time stop - he rides the trend until the EMAs cross. A mechanical
    # backtest needs a bound, so the position is marked out after this many sessions and
    # reported as its own outcome rather than folded into wins or losses.
    max_hold_sessions: int = 60
    # 20R at a 0.4% stop is an 8% move. Whether that is reachable is a real question, and
    # the source does not ask it - so this is computed and displayed on every signal but
    # does NOT reject by default. Turning it on measures a filter he does not have.
    require_feasible_target: bool = False
    feasibility_atr_period: int = 14

    # ── Costs: delivery, because the holding period is delivery ───────────────
    # A cost constant is calibrated for a holding period and must never be inherited. This
    # one coincides with V3's 0.12% - not because it was copied, but because a 20R target
    # off a sub-1% stop takes weeks to resolve and therefore pays the same delivery STT:
    #
    #     brokerage   0.020   (delivery, both sides)
    #     STT         0.100   (delivery, both sides)      <- the dominant term
    #     exchange    0.006
    #     stamp       0.003   (buy side)
    #     GST         0.003
    #     ---------------
    #     total       0.132 % round trip, rounded to 0.12 in line with the V3 derivation
    #
    # The reverse mistake is what had to be retracted on the pullback engine: charging this
    # delivery figure to an intraday strategy overstated its cost 3.4x. Cost in R is
    # (cost% / stop%), so at a 0.3% stop this is 0.57R - more than half of every unit
    # risked, against a 20R target where it barely registers. Both facts are true at once
    # and the report states both.
    cost_roundtrip_pct: float = 0.12
    slippage_pct: float = 0.05

    # ── Universe liquidity floor (runs before anything else) ──────────────────
    # Added for the NIFTY 500 expansion. The tail of the 500 does not trade continuously
    # inside a 30-minute window, and a bar with no prints has a low equal to its high — which
    # the FVG detector reads as a perfect imbalance. Those are *data holes*, not liquidity
    # voids, and admitting them poisons the forward record with setups that could never have
    # been filled. So this gate runs first, on EOD bhavcopy rather than on intraday bars,
    # because bhavcopy is one file per day for the whole market rather than 500 paced fetches.
    #
    # Deliberately NOT `settings.min_median_turnover_inr` (₹5 crore, V3's). That constant is
    # calibrated for a 1-5 session swing on a daily bar; this one has to survive a 5-minute
    # bar printing at all. Sharing it would retune V3 the next time this is tuned.
    min_median_turnover_inr: float = 25.0e7    # ₹25 crore/day, the owner's starting point
    liquidity_lookback_days: int = 20
    # Corwin-Schultz estimated effective spread, in basis points. There is no bid/ask feed
    # reachable from here at any price — NSE is Akamai-blocked and Yahoo serves OHLCV only —
    # so this is an *estimator from daily high/low*, not a quoted spread, and every surface
    # that prints it says so.
    #
    # **It is computed and reported; it does not gate, and that is measured rather than
    # assumed.** Over the whole NIFTY 500 on 20 sessions to 28 Aug 2026 the estimate
    # correlates -0.15 with median turnover and +0.31 with median daily range: it tracks
    # volatility about twice as strongly as it tracks illiquidity. At a 25bp cap it refuses
    # 296 of 500 names, including ICICIBANK (₹1,209 cr/day, 25.1bp) and BHARTIARTL (₹1,077
    # cr/day, 26.0bp), and 40 of the NIFTY 50 — whose real quoted spreads are low single-digit
    # basis points. The names it flags hardest (NETWEB 156bp, HINDCOPPER 91bp) are the most
    # volatile, not the least tradeable.
    #
    # So the estimator is not measuring what a spread filter needs to measure on this data,
    # and no threshold rescues it: one loose enough to keep ICICIBANK is inert. Turnover does
    # the work. `--require-spread` arms it anyway for anyone who wants to argue with that,
    # and the number stays on every row so the finding can be re-checked rather than trusted.
    max_spread_bps: float = 25.0
    require_spread_estimate: bool = False

    # ── Alert budget (the 5m track produces far more candidates) ──────────────
    # Ranked by net R after this trade's own costs, never by raw setup quality: at a 0.15%
    # stop the cost drag is over 1R, so two setups with identical geometry are not equally
    # worth taking. On the 30m track the cap is rarely binding; on 5m it always is.
    max_alerts_per_session: int = 5

    # ── Liquidity-sweep context (metadata only, never a filter) ───────────────
    # Tagged, not gated. Making it a filter today would shrink an already tiny sample on an
    # untested assumption; the point is to accumulate tagged-vs-untagged outcomes forward and
    # compare them once there are enough. `sweep_max_candles` is the N in "wicks through the
    # level and closes back on the origin side within N candles".
    tag_liquidity_sweep: bool = True
    sweep_max_candles: int = 3
    # Two touches inside this band count as equal highs/lows. Percentage of price rather than
    # absolute, so it means the same thing on a ₹80 stock and a ₹8,000 one.
    equal_level_tolerance_pct: float = 0.08
    equal_level_min_touches: int = 2
    # How far back the pool map looks on the entry timeframe, and how many prior sessions of
    # daily bars supply the swing pools.
    pool_lookback_bars: int = 60
    swing_lookback_sessions: int = 20
    swing_pivot_strength: int = 2
    # Opening range, in minutes from the open.
    opening_range_minutes: int = 30

    @property
    def interval_minutes(self) -> int:
        """Bar length in minutes, parsed from `anchor_interval`.

        Read by everything that has to know how long a bar is. It existed as a literal 30 in
        three places before the 5m track, which is exactly the kind of constant that survives
        a parameterisation and quietly mis-scores the new path.
        """
        text = self.anchor_interval.strip().lower()
        if text.endswith("m"):
            return int(text[:-1])
        if text.endswith("h"):
            return int(text[:-1]) * 60
        raise ValueError(f"unsupported entry interval {self.anchor_interval!r}")


@dataclass
class TridentSignal:
    """One detected setup, or an explicit absence carrying the reason it was refused."""

    symbol: str = ""
    found: bool = False
    note: str = ""
    rejected_by: str = ""              # which condition refused it, for the accounting
    # The same refusal expressed as a gate number, plus the numbers that decided it. Carried
    # on the signal rather than recomputed by each caller so the console funnel, the JSONL
    # failure log and the live monitor cannot disagree about where a candidate died.
    gate_reached: int = 0
    gate_reason: str = ""

    # Whether *any* qualifying imbalance printed in the window, regardless of what happened
    # afterwards. Tracked separately from `gap_at` so the rejection accounting can say how
    # many sessions got as far as a gap — a count that equals the setup count is a count
    # that tells you nothing.
    gap_seen: bool = False
    gap_at: pd.Timestamp | None = None     # the bar that completed the imbalance
    gap_low: float = 0.0
    gap_high: float = 0.0
    gap_pct: float = 0.0
    prime_time: bool = False

    doji_at: pd.Timestamp | None = None
    doji_body_pct: float = 0.0
    doji_high: float = 0.0
    doji_low: float = 0.0

    entry_at: pd.Timestamp | None = None
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_pct: float = 0.0

    daily_state: str = ""              # green | blue | red | black | unknown
    required_move_pct: float = 0.0     # what 20R actually asks for
    capacity_pct: float = 0.0          # what the stock's ATR says is available
    feasible: bool = False

    # ── Liquidity-sweep context: metadata, never a gate ───────────────────────
    # See `trident_pools.py`. `swept_pool` is empty when nothing was taken, which is the
    # *majority* case and must stay visually distinct from "not checked" — a blank that could
    # mean either is the fallback-with-no-marker trap this codebase has already paid for.
    swept: bool = False
    swept_pool: str = ""               # e.g. "prior day low" | "equal lows (3 touches)"
    swept_level: float = 0.0
    swept_at: pd.Timestamp | None = None
    sweep_bars_before_gap: int = 0
    sweep_to_midpoint_pct: float = 0.0
    sweep_checked: bool = False        # False means the layer did not run, not "none found"

    # ── Costs, resolved on the signal so every surface quotes one number ──────
    cost_r: float = 0.0
    rr_used: float = 0.0               # the ratio this signal was built with

    @property
    def net_r_at_target(self) -> float:
        """R booked if this trade reaches its target, after its own round-trip costs.

        The ranking key for the alert budget. Not `reward_risk` — that is identical for every
        setup — and not raw setup quality: cost in R is (cost% / stop%), so a 0.15% stop on
        the 5m track surrenders over a full R before the trade starts while a 0.6% stop
        surrenders a quarter of one. Two setups with the same geometry are not equally worth
        taking, and sorting by anything that ignores this puts the worst ones at the top.
        """
        return self.rr_used - self.cost_r

    @property
    def consequent_encroachment(self) -> float:
        """The 50% of the gap. ICT's name for it; the level the doji has to wick through."""
        return (self.gap_low + self.gap_high) / 2

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


# ── Daily context ─────────────────────────────────────────────────────────────


def classify_daily_candle(
    daily: pd.DataFrame,
    session: date,
    partial: dict | None = None,
    cfg: TridentSettings | None = None,
) -> str:
    """Reconstruct the four-colour daily state: green / blue / red / black.

    His description, verbatim: strong bullish closes bright green, a bullish candle that is
    not extremely strong closes blue, a strong bearish candle closes red, and a bearish
    candle without much volume closes black. The underlying indicator is a third-party
    TradingView script he names but does not define, so this is a reconstruction from that
    description plus the one structural hint he gives - "we're in the top of the Bollinger
    band above the 200 EMA".

        strong  = the close sits in the upper `strong_bb_position` of the Bollinger range
                  AND the day is trading at or above its 20-day average pace by volume

        green = bullish and strong      blue  = bullish and not strong
        red   = bearish and strong      black = bearish and not strong

    **Anti-lookahead.** The setup fires *inside* the daily candle - he is explicit that this
    is the point - so the candle is evaluated from a `partial` built out of the intraday
    bars up to and including the confirming bar, never from the finished daily bar. The
    bands and the EMA come from *completed prior* days only. Reading the finished daily
    close here would be reading the future, which is precisely the failure this codebase
    names a setup after.
    """
    cfg = cfg or TridentSettings()
    if daily is None or len(daily) < cfg.daily_bb_period + 2:
        return "unknown"

    prior = daily[daily.index.date < session]
    if len(prior) < cfg.daily_bb_period:
        return "unknown"

    closes = prior["close"]
    mid = float(closes.rolling(cfg.daily_bb_period).mean().iloc[-1])
    sd = float(closes.rolling(cfg.daily_bb_period).std(ddof=0).iloc[-1])
    if not np.isfinite(mid) or not np.isfinite(sd) or sd <= 0:
        return "unknown"
    upper, lower = mid + cfg.daily_bb_std * sd, mid - cfg.daily_bb_std * sd

    if partial is None:
        return "unknown"
    close = float(partial["close"])
    open_ = float(partial["open"])
    bullish = close > open_

    position = (close - lower) / (upper - lower) if upper > lower else 0.5
    # Volume is compared against the 20-day average scaled by how much of the session has
    # actually elapsed - otherwise every morning setup reads as low volume by construction.
    avg_volume = float(prior["volume"].tail(cfg.daily_bb_period).mean())
    elapsed = min(max(float(partial.get("elapsed_fraction", 1.0)), 1e-6), 1.0)
    pace_ok = True
    if np.isfinite(avg_volume) and avg_volume > 0:
        pace_ok = float(partial.get("volume", 0.0)) >= avg_volume * elapsed

    strong = position >= cfg.strong_bb_position and pace_ok
    if bullish:
        return "green" if strong else "blue"
    return "red" if (position <= 1 - cfg.strong_bb_position and pace_ok) else "black"


def daily_bias_ok(
    daily: pd.DataFrame, session: date, cfg: TridentSettings
) -> tuple[bool, float]:
    """Is the stock above its daily 200 EMA, using completed prior days only."""
    if daily is None or daily.empty:
        return False, float("nan")
    prior = daily[daily.index.date < session]
    if len(prior) < cfg.daily_bias_ema // 2:
        return False, float("nan")
    value = float(ema(prior["close"], cfg.daily_bias_ema).iloc[-1])
    last_close = float(prior["close"].iloc[-1])
    return (last_close > value), value


def _atr_pct(daily: pd.DataFrame, session: date, cfg: TridentSettings) -> float:
    """Daily ATR as a percentage of price, from completed prior days only."""
    if daily is None or daily.empty:
        return float("nan")
    prior = daily[daily.index.date < session]
    if len(prior) < cfg.feasibility_atr_period + 1:
        return float("nan")
    high, low, close = prior["high"], prior["low"], prior["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    value = float(tr.rolling(cfg.feasibility_atr_period).mean().iloc[-1])
    price = float(close.iloc[-1])
    return value / price * 100 if price > 0 else float("nan")


def _elapsed_fraction(bar_start: pd.Timestamp, interval_minutes: int = 30) -> float:
    """How much of the session has completed once this bar closes.

    Computed from the clock rather than from a count of the day's bars: the day's bar count
    is only known after the session has finished, and using it would leak the length of a
    session into a decision taken inside it.
    """
    minutes = (
        datetime.combine(date.min, bar_start.time())
        - datetime.combine(date.min, SESSION_OPEN)
    ).total_seconds() / 60 + interval_minutes
    return min(max(minutes / SESSION_MINUTES, 0.0), 1.0)


# ── The setup ─────────────────────────────────────────────────────────────────

# The five gates, named once. Every surface — the live monitor, the failure log, the funnel
# counts — numbers them the same way, because a scanner whose "gate 4" means something
# different in two places cannot be audited.
#
# Gate 1 is context (liquidity, daily bias, enough history); it is everything decided before
# any intraday price action is examined. Gates 2-5 are the pattern itself. The split that
# matters operationally is between 4 and 5: a doji closing back above the 50% is a *get
# ready*, and the confirmation is a full bar later — 30 minutes on the anchor track — so they
# are alerted separately rather than collapsed into one "trigger".
GATE_NAMES: dict[int, str] = {
    1: "context",
    2: "gap formed",
    3: "midpoint tagged",
    4: "doji confirmed",
    5: "confirmation closed",
}


@dataclass
class GateEvent:
    """One gate decision for one symbol on one session.

    `reason` always carries the *numbers* that decided it, not just the verdict. A failure
    log reading "body too large" is an opinion; one reading "body 47.3% of range, limit
    30.0%" is a measurement, and with a sample this small the failure log is worth more than
    the winners — it is the only thing that can say whether a gate is doing work or merely
    starving the strategy of trades.
    """

    symbol: str = ""
    session: date | None = None
    gate: int = 0
    passed: bool = False
    at: pd.Timestamp | None = None
    reason: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return GATE_NAMES.get(self.gate, "unknown")


def _emit(
    sink: list[GateEvent] | None,
    symbol: str,
    session: date,
    gate: int,
    passed: bool,
    at: pd.Timestamp | None = None,
    reason: str = "",
    **detail: float | str | bool,
) -> None:
    if sink is None:
        return
    sink.append(
        GateEvent(
            symbol=symbol, session=session, gate=gate, passed=passed, at=at,
            reason=reason, detail=dict(detail),
        )
    )


# Which gate each refusal belongs to, so the failure log can be grouped by gate rather than
# by the free text of the reason. Kept next to `_REJECTION_STAGE` deliberately: the two are
# read together and drifting apart would make the funnel and the failure log disagree.
_REJECTION_GATE: dict[str, int] = {
    "data": 1,
    "daily bias": 1,
    "illiquid": 1,
    "no gap": 2,
    "no doji retrace": 3,
    "body too large for a doji": 4,
    "no confirmation bar left in the kill zone": 5,
    "confirmation closed above the doji high": 5,
    "confirmation traded through the stop": 5,
    "EMAs not stacking": 5,
    "stop outside the configured band": 5,
    "20R unreachable inside the hold window": 5,
}


def rejection_gate(reason: str) -> int:
    """Gate number for a refusal. Unknown reasons — the daily-state check names the colour it
    saw, so its text varies — belong to gate 5, the last one able to refuse."""
    return _REJECTION_GATE.get(reason, 5)


# How far a session got before it was refused. The rejection table is only useful if it
# reports the *furthest* stage reached rather than whichever condition happened to be
# evaluated last — a session that formed a gap, a doji and a confirmation and then failed on
# the EMAs is a different animal from one where no gap ever printed, and collapsing the two
# would hide which condition is actually doing the work.
_REJECTION_STAGE = {
    "data": 0,
    "daily bias": 1,
    "no gap": 2,
    "no doji retrace": 3,
    "body too large for a doji": 4,
    "no confirmation bar left in the kill zone": 5,
    "confirmation closed above the doji high": 6,
    "confirmation traded through the stop": 6,
    "EMAs not stacking": 7,
    "stop outside the configured band": 8,
    "20R unreachable inside the hold window": 10,
}


def _stage(reason: str) -> int:
    """Unknown reasons sort just below the daily-state check, which is the only one whose
    text varies (it names the colour it saw)."""
    return _REJECTION_STAGE.get(reason, 9)


def _refuse(signal: TridentSignal, reason: str, detail: str = "") -> None:
    """Record a refusal, keeping the furthest-progressed one.

    `detail` is the numeric form — "body 47.3% of range, limit 30.0%" rather than "body too
    large". The reason string is the bucket the funnel counts; the detail is what makes the
    failure log usable, and it moves with the refusal so the two cannot describe different
    bars.
    """
    if not signal.rejected_by or _stage(reason) >= _stage(signal.rejected_by):
        # An empty detail must not erase one already recorded for the *same* reason — the
        # tail of the detector re-refuses with the bucket name and no numbers, and losing the
        # numbers there would empty out most of the failure log.
        changed = signal.rejected_by != reason
        signal.rejected_by = reason
        if detail:
            signal.gate_reason = detail
        elif changed:
            signal.gate_reason = ""




def _ema_stack_detail(bar: pd.Series, cfg: TridentSettings) -> str:
    """Which pair of EMAs crossed, with their values. "EMAs not stacking" alone says nothing
    about whether the stack missed by a rupee or by ten."""
    values = [(p, float(bar.get(f"ema{p}", np.nan))) for p in cfg.ema_stack]
    if any(not np.isfinite(v) for _, v in values):
        missing = [str(p) for p, v in values if not np.isfinite(v)]
        return f"EMA{'/'.join(missing)} not yet warm on this bar"
    for (fast, fv), (slow, sv) in zip(values, values[1:]):
        if fv <= sv:
            return f"EMA{fast} {fv:,.2f} ≤ EMA{slow} {sv:,.2f} — the stack is crossed"
    return "EMAs stacked"


def _ema_stacked(bar: pd.Series, cfg: TridentSettings) -> bool:
    """5 > 9 > 13 > 21, strictly. "If the EMAs were crossing I wouldn't be interested."""
    values = [bar.get(f"ema{period}", np.nan) for period in cfg.ema_stack]
    if any(not np.isfinite(v) for v in values):
        return False
    return all(values[i] > values[i + 1] for i in range(len(values) - 1))


def detect_trident_setup(
    m30: pd.DataFrame,
    daily: pd.DataFrame,
    session: date,
    cfg: TridentSettings | None = None,
    symbol: str = "",
    sink: list[GateEvent] | None = None,
) -> TridentSignal:
    """The whole pattern for one symbol on one session, long side only.

    `sink`, when supplied, collects a `GateEvent` for every gate decision taken along the
    way. It is an *observer* on the single geometry implementation rather than a second one:
    the live monitor, the backtest, the forward record and the window sweep all call this
    function, so none of them can develop its own idea of what a doji is.

    The gate number, the cost in R and the ratio the signal was built with are resolved here
    rather than by each caller, so the console, the Markdown brief, the JSONL failure log and
    the alert ranking all read the same fields.
    """
    cfg = cfg or TridentSettings()
    signal = _detect_trident(m30, daily, session, cfg, symbol, sink)
    signal.rr_used = cfg.reward_risk
    signal.cost_r = round(_cost_r(signal.risk_pct, cfg), 4)
    signal.gate_reached = 5 if signal.found else rejection_gate(signal.rejected_by)
    if not signal.found and not signal.gate_reason:
        signal.gate_reason = signal.note
    if signal.found and cfg.tag_liquidity_sweep:
        _tag_liquidity_sweep(signal, m30, daily, session, cfg)
    return signal


def _tag_liquidity_sweep(
    signal: TridentSignal,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    session: date,
    cfg: TridentSettings,
) -> None:
    """Attach which liquidity pool, if any, was taken before the gap formed.

    Metadata, never a gate — see the module docstring of `trident_pools`. Computed only for
    setups that actually qualified, because the pool map is the most expensive thing in the
    scan and a candidate refused at gate 2 has no gap to measure a distance to.

    `sweep_checked` records that the layer actually ran. If it could not — no pools built, no
    bars for the session — the flag stays False, so an untagged signal is never read as "no
    liquidity was taken". A fallback indistinguishable from the real answer is trap 10 in
    CLAUDE.md, and it is the whole reason there are two fields here rather than one.
    """
    from . import trident_pools

    if signal.gap_at is None:
        return
    day = intraday[intraday.index.date == session]
    if day.empty:
        return

    pools = trident_pools.build_pools(intraday, daily, session, cfg)
    if not pools:
        return
    # Sweeps are looked for in this session's bars up to the gap. A pool taken yesterday and
    # reclaimed yesterday is history, not the fuel for this morning's imbalance.
    window = day[day.index <= signal.gap_at]
    sweeps = trident_pools.find_sweeps(window, pools, cfg)
    signal.sweep_checked = True

    taken = trident_pools.sweep_before(sweeps, signal.gap_at, side="sell")
    if taken is None:
        return

    midpoint = signal.consequent_encroachment
    signal.swept = True
    signal.swept_pool = taken.label
    signal.swept_level = round(taken.pool.level, 2)
    signal.swept_at = taken.at
    signal.sweep_bars_before_gap = trident_pools.bars_between(day, taken.at, signal.gap_at)
    signal.sweep_to_midpoint_pct = round(
        (midpoint - taken.pool.level) / taken.pool.level * 100, 3
    ) if taken.pool.level > 0 else 0.0


def _detect_trident(
    m30: pd.DataFrame,
    daily: pd.DataFrame,
    session: date,
    cfg: TridentSettings,
    symbol: str,
    sink: list[GateEvent] | None,
) -> TridentSignal:
    """The geometry. Wrapped by `detect_trident_setup`, which is what callers use.

    Long only is faithful rather than lazy: the source is explicit that he is long-biased,
    that his data comes from longs, and that he is "not good at shorting". A short mirror
    would be an invention, and inventing the other half of somebody's edge and then
    measuring it teaches nothing about the edge.

    Every detector here measures on bars strictly *preceding* the confirming bar. That is
    the rule the whole codebase turns on, and it is the difference between a pattern and a
    label for a move that already happened.
    """
    signal = TridentSignal(symbol=symbol)
    tf = cfg.anchor_interval

    if m30 is None or len(m30) < max(cfg.ema_stack) + 5:
        signal.note, signal.rejected_by = f"insufficient {tf} history", "data"
        _emit(sink, symbol, session, 1, False, reason=signal.note,
              bars=0 if m30 is None else len(m30), needed=max(cfg.ema_stack) + 5)
        return signal

    frame = m30.copy()
    for period in cfg.ema_stack:
        frame[f"ema{period}"] = ema(frame["close"], period)

    day = frame[frame.index.date == session]
    if day.empty:
        signal.note, signal.rejected_by = f"no {tf} bars for this session", "data"
        _emit(sink, symbol, session, 1, False, reason=signal.note, bars=0)
        return signal

    # ── Daily bias, before any price action is examined ───────────────────────
    if cfg.require_daily_bias:
        above, ema_value = daily_bias_ok(daily, session, cfg)
        if not above:
            signal.note = (
                f"below the daily {cfg.daily_bias_ema} EMA"
                + (f" ({ema_value:,.2f})" if np.isfinite(ema_value) else "")
                + " — long bias absent"
            )
            signal.rejected_by = "daily bias"
            _emit(sink, symbol, session, 1, False, reason=signal.note,
                  ema=float(ema_value) if np.isfinite(ema_value) else 0.0)
            return signal

    bars = day[
        (day.index.time >= cfg.killzone_start) & (day.index.time <= cfg.killzone_end)
    ]
    # Four bars is the arithmetic minimum to reach gate 4: three to complete the imbalance and
    # one to retrace into it as the doji. The fifth — the confirmation — is deliberately NOT
    # required here.
    #
    # It used to be, and that was wrong in the one place it mattered most. Live, the monitor
    # stands inside a partly-formed session: at the moment the doji closes as the window's
    # fourth bar there is no fifth bar yet, and a gate-1 refusal at that instant suppresses
    # the get-ready alert exactly when it has the most warning to give. The absence of a
    # confirmation bar is already handled downstream as its own explicit outcome, which is
    # both more accurate and the state the monitor reads as "pending".
    if len(bars) < 4:
        signal.note = (
            f"kill zone holds {len(bars)} {tf} bar(s); the imbalance plus its retrace "
            "needs 4"
        )
        signal.rejected_by = "data"
        _emit(sink, symbol, session, 1, False, reason=signal.note, bars=len(bars), needed=4)
        return signal

    _emit(sink, symbol, session, 1, True, at=bars.index[0],
          reason=f"{len(bars)} {tf} bars in {cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M}",
          bars=len(bars))

    times = list(bars.index)
    atr_pct = _atr_pct(daily, session, cfg)
    session_start = times[0]

    saw_gap = False
    saw_doji = False

    # ── The fair value gap: bar i's low above bar i-2's high ──────────────────
    for i in range(2, len(times)):
        first, third = bars.iloc[i - 2], bars.iloc[i]
        gap_low, gap_high = float(first["high"]), float(third["low"])
        if gap_high <= gap_low or gap_low <= 0:
            continue
        gap_pct = (gap_high - gap_low) / gap_low * 100
        if gap_pct < cfg.min_gap_pct:
            continue
        saw_gap = True
        signal.gap_seen = True
        encroachment = (gap_low + gap_high) / 2
        prime = (times[i] - session_start).total_seconds() / 60 <= cfg.prime_window_minutes
        _emit(
            sink, symbol, session, 2, True, at=times[i],
            reason=f"{gap_pct:.2f}% imbalance {gap_low:,.2f}–{gap_high:,.2f}, 50% at "
                   f"{encroachment:,.2f}",
            gap_low=gap_low, gap_high=gap_high, midpoint=encroachment, gap_pct=gap_pct,
            prime=prime,
        )
        tagged = False

        # ── The retrace: a doji that wicks through the 50% and closes back above ──
        for j in range(i + 1, len(times)):
            if j - i > cfg.max_bars_to_retrace:
                break
            candidate = bars.iloc[j]

            # An FVG that has been closed through is inverted, not respected. Once a candle
            # has closed below the gap entirely, this is no longer the setup he describes.
            if float(candidate["close"]) < gap_low:
                break

            span = float(candidate["high"] - candidate["low"])
            if span <= 0:
                continue
            body_pct = abs(float(candidate["close"] - candidate["open"])) / span * 100

            wicked = float(candidate["low"]) <= encroachment
            reclaimed = float(candidate["close"]) > encroachment
            if not wicked:
                continue
            # Gate 3 is the tag itself — price traded down into the 50%. It is separated from
            # gate 4 because the two fail for completely different reasons: a bar that never
            # reached the level says the imbalance was not respected, while one that reached
            # it and closed below says the gap was consumed. Collapsing them would hide which.
            if not tagged:
                tagged = True
                _emit(
                    sink, symbol, session, 3, True, at=times[j],
                    reason=f"low {float(candidate['low']):,.2f} reached the 50% at "
                           f"{encroachment:,.2f}",
                    low=float(candidate["low"]), midpoint=encroachment,
                )
            if not reclaimed:
                _refuse(
                    signal, "no doji retrace",
                    f"tagged the 50% at {encroachment:,.2f} but closed "
                    f"{float(candidate['close']):,.2f}, below it",
                )
                _emit(
                    sink, symbol, session, 4, False, at=times[j],
                    reason=f"close {float(candidate['close']):,.2f} did not reclaim the 50% "
                           f"at {encroachment:,.2f}",
                    close=float(candidate["close"]), midpoint=encroachment,
                )
                continue
            if body_pct > cfg.max_doji_body_pct:
                # "Say this wasn't a doji, the body of this candle was in here — this would
                # be an invalidation." A big body means one side won the bar outright; the
                # doji is the whole point, because it says sellers pushed into the level and
                # were rejected inside a single bar.
                _refuse(
                    signal, "body too large for a doji",
                    f"body {body_pct:.1f}% of range, limit {cfg.max_doji_body_pct:.1f}%",
                )
                _emit(
                    sink, symbol, session, 4, False, at=times[j],
                    reason=f"body {body_pct:.1f}% of range, limit "
                           f"{cfg.max_doji_body_pct:.1f}%",
                    body_pct=body_pct, limit=cfg.max_doji_body_pct,
                )
                continue
            saw_doji = True
            # Gate 4 — the "get ready". A full bar separates this from the trigger, which is
            # why it is alerted on its own: `interval_minutes` of warning is the whole
            # operational value of running this live rather than after the close.
            _emit(
                sink, symbol, session, 4, True, at=times[j],
                reason=f"{body_pct:.1f}%-body doji wicked {float(candidate['low']):,.2f} "
                       f"through the 50% at {encroachment:,.2f} and closed "
                       f"{float(candidate['close']):,.2f} back above",
                body_pct=body_pct, doji_high=float(candidate["high"]),
                doji_low=float(candidate["low"]), doji_open=float(candidate["open"]),
                doji_close=float(candidate["close"]), midpoint=encroachment,
                gap_low=gap_low, gap_high=gap_high,
            )

            # ── The confirmation bar ──────────────────────────────────────────
            doji_high, doji_low = float(candidate["high"]), float(candidate["low"])
            if j + 1 >= len(times):
                # Live, this is not a failure — it is the gap between the get-ready and the
                # trigger, and the monitor reads it as `pending`. Historically it is a real
                # refusal: the window closed with the doji as its last bar.
                _refuse(
                    signal, "no confirmation bar left in the kill zone",
                    f"doji closed at {times[j]:%H:%M}, the last bar of "
                    f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M}",
                )
                _emit(
                    sink, symbol, session, 5, False, at=times[j],
                    reason="awaiting the confirmation bar — none left inside the kill zone",
                    pending=True, doji_high=doji_high, doji_low=doji_low,
                )
                continue
            confirm = bars.iloc[j + 1]

            if float(confirm["close"]) >= doji_high:
                # His rule, and it is a rule about geometry rather than direction: closing
                # above the doji's high means the entry has run away from its own stop, and
                # 1:20 only exists while entry sits on top of invalidation.
                _refuse(
                    signal, "confirmation closed above the doji high",
                    f"close {float(confirm['close']):,.2f} ≥ doji high {doji_high:,.2f}",
                )
                _emit(
                    sink, symbol, session, 5, False, at=times[j + 1],
                    reason=f"close {float(confirm['close']):,.2f} ≥ doji high "
                           f"{doji_high:,.2f}",
                    close=float(confirm["close"]), doji_high=doji_high,
                )
                continue
            if float(confirm["low"]) <= doji_low:
                # The stop is the doji low; if the confirming bar already traded through it,
                # the trade was stopped out before it could be taken.
                _refuse(
                    signal, "confirmation traded through the stop",
                    f"low {float(confirm['low']):,.2f} ≤ stop {doji_low:,.2f}",
                )
                _emit(
                    sink, symbol, session, 5, False, at=times[j + 1],
                    reason=f"low {float(confirm['low']):,.2f} ≤ stop {doji_low:,.2f}",
                    low=float(confirm["low"]), stop=doji_low,
                )
                continue
            if cfg.require_ema_stack and not _ema_stacked(confirm, cfg):
                detail = _ema_stack_detail(confirm, cfg)
                _refuse(signal, "EMAs not stacking", detail)
                _emit(sink, symbol, session, 5, False, at=times[j + 1], reason=detail)
                continue

            entry = float(confirm["close"])
            stop = doji_low
            risk = entry - stop
            if risk <= 0:
                continue
            risk_pct = risk / entry * 100
            if not (cfg.min_risk_pct <= risk_pct <= cfg.max_risk_pct):
                detail = (
                    f"stop {risk_pct:.3f}% outside the "
                    f"{cfg.min_risk_pct:.2f}–{cfg.max_risk_pct:.2f}% band"
                )
                _refuse(signal, "stop outside the configured band", detail)
                _emit(sink, symbol, session, 5, False, at=times[j + 1], reason=detail,
                      risk_pct=risk_pct)
                continue

            # ── Daily state, evaluated from the partial candle only ───────────
            elapsed_bars = day[day.index <= times[j + 1]]
            partial = {
                "open": float(elapsed_bars["open"].iloc[0]),
                "close": entry,
                "volume": float(elapsed_bars["volume"].sum()),
                "elapsed_fraction": _elapsed_fraction(times[j + 1], cfg.interval_minutes),
            }
            state = classify_daily_candle(daily, session, partial, cfg)
            if cfg.require_strong_daily and state != "green":
                detail = (
                    f"daily candle printing {state}; the reconstruction needs 'green' "
                    f"(close in the top {100 * (1 - cfg.strong_bb_position):.0f}% of the "
                    f"{cfg.daily_bb_period}-day Bollinger range, on pace by volume)"
                )
                _refuse(signal, f"daily candle printing {state}, not green", detail)
                _emit(sink, symbol, session, 5, False, at=times[j + 1], reason=detail,
                      daily_state=state)
                continue

            required = cfg.reward_risk * risk_pct
            capacity = (
                atr_pct * np.sqrt(cfg.max_hold_sessions)
                if np.isfinite(atr_pct)
                else float("nan")
            )
            feasible = bool(np.isfinite(capacity) and required <= capacity)
            if cfg.require_feasible_target and not feasible:
                detail = (
                    f"{cfg.reward_risk:.0f}R needs {required:.1f}%; ATR implies "
                    f"{capacity:.1f}% over {cfg.max_hold_sessions} sessions"
                )
                _refuse(signal, "20R unreachable inside the hold window", detail)
                _emit(sink, symbol, session, 5, False, at=times[j + 1], reason=detail,
                      required_pct=required, capacity_pct=float(capacity))
                continue

            signal.found = True
            signal.gap_at, signal.gap_low, signal.gap_high = times[i], gap_low, gap_high
            signal.gap_pct, signal.prime_time = round(gap_pct, 3), prime
            signal.doji_at, signal.doji_body_pct = times[j], round(body_pct, 1)
            signal.doji_high, signal.doji_low = doji_high, doji_low
            signal.entry_at, signal.entry = times[j + 1], round(entry, 2)
            signal.stop = round(stop, 2)
            signal.target = round(entry + cfg.reward_risk * risk, 2)
            signal.risk_pct = round(risk_pct, 3)
            signal.daily_state = state
            signal.required_move_pct = round(required, 2)
            signal.capacity_pct = round(capacity, 2) if np.isfinite(capacity) else 0.0
            signal.feasible = feasible
            signal.rejected_by = ""
            signal.gate_reason = ""
            signal.gate_reached = 5
            _emit(
                sink, symbol, session, 5, True, at=times[j + 1],
                reason=f"entry {entry:,.2f}, stop {stop:,.2f} ({risk_pct:.2f}%), target "
                       f"{signal.target:,.2f} ({required:.1f}%)",
                entry=entry, stop=stop, target=signal.target, risk_pct=risk_pct,
            )
            signal.note = (
                f"{gap_pct:.2f}% fair value gap completed at {times[i]:%H:%M}"
                + (" (prime window)" if prime else "")
                + f"; {body_pct:.0f}%-body doji at {times[j]:%H:%M} wicked the 50% at "
                f"{encroachment:,.2f} and closed back above; {times[j + 1]:%H:%M} confirmed "
                f"below the doji high with EMAs stacked and the daily printing {state}"
            )
            return signal

    if not saw_gap:
        signal.note = (
            f"no fair value gap of at least {cfg.min_gap_pct:.2f}% printed inside "
            f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M}"
        )
        _refuse(signal, "no gap", signal.note)
        _emit(sink, symbol, session, 2, False, at=times[-1], reason=signal.note,
              bars=len(times))
    elif not saw_doji:
        signal.note = (
            f"{len(times)} bars in the window formed a gap, but nothing retraced to its "
            "50% as a doji"
        )
        _refuse(signal, "no doji retrace")
    else:
        signal.note = f"pattern formed but refused: {signal.rejected_by}"
        if signal.gate_reason:
            signal.note += f" — {signal.gate_reason}"
    return signal


# ── Backtest ──────────────────────────────────────────────────────────────────


@dataclass
class TridentTrade:
    symbol: str
    entered_at: pd.Timestamp
    entry: float
    stop: float
    target: float
    risk_pct: float
    required_move_pct: float = 0.0
    feasible: bool = False
    outcome: str = "open"              # target | stop | time-stop | open
    resolved_at: pd.Timestamp | None = None
    sessions_held: int = 0
    realised_r: float = 0.0
    cost_r: float = 0.0
    gapped: bool = False               # resolved through a gap rather than at the level
    # Liquidity-sweep context, carried onto the trade so tagged and untagged outcomes can be
    # compared once there are enough of both. Never used to admit or refuse anything.
    swept: bool = False
    swept_pool: str = ""
    sweep_checked: bool = False

    @property
    def net_r(self) -> float:
        return self.realised_r - self.cost_r


@dataclass
class TridentResult:
    trades: list[TridentTrade] = field(default_factory=list)
    symbols_tested: int = 0
    sessions_spanned: int = 0
    gaps_found: int = 0
    setups_found: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    # Symbols whose data could not be fetched. Counted rather than silently skipped: a
    # transient Yahoo failure removes a name and its setups from the sample, so two runs of
    # the same command over the same window can disagree. Observed 26 Aug 2026, when one
    # failed fetch moved the trade count from 22 to 21 and the report said only "199
    # symbols" — a shrinking denominator that looked like a stable measurement.
    fetch_failures: int = 0
    # The liquidity screen that produced the universe, and the per-symbol fetch accounting.
    # Both are `Any` rather than imported types: `trident_universe` imports this module, and
    # naming its classes here would close the loop.
    liquidity: object | None = None
    fetch: object | None = None

    def sweep_split(self) -> dict[str, list[TridentTrade]]:
        """Finished trades split by whether a sell-side pool was taken before the gap.

        The comparison the sweep layer exists to make. It is reported and never acted on:
        with the sample this window can produce, a difference between the two cohorts is
        noise until it survives a forward record.
        """
        finished = self.finished
        return {
            "swept": [t for t in finished if t.sweep_checked and t.swept],
            "not swept": [t for t in finished if t.sweep_checked and not t.swept],
            "not checked": [t for t in finished if not t.sweep_checked],
        }

    @property
    def resolved(self) -> list[TridentTrade]:
        """Decided by stop or target. Time-stopped and still-open trades are excluded from
        the win rate, because counting an unfinished trade as a loss and counting it as a
        win are both wrong."""
        return [t for t in self.trades if t.outcome in ("target", "stop")]

    @property
    def censored(self) -> list[TridentTrade]:
        return [t for t in self.trades if t.outcome in ("time-stop", "open")]

    @property
    def win_rate(self) -> float:
        decided = self.resolved
        if not decided:
            return float("nan")
        return sum(t.outcome == "target" for t in decided) / len(decided) * 100

    @property
    def finished(self) -> list[TridentTrade]:
        """Trades with a P&L. A time stop has one; a still-open position does not.

        Expectancy is taken over these, never over every recorded trade. An open trade
        credits no profit but has already been charged a full round-trip cost, so counting
        it drags the mean toward -cost for no reason — which matters enormously in a forward
        record, where almost everything is open in the first weeks.
        """
        return [t for t in self.trades if t.outcome != "open"]

    @property
    def expectancy_r(self) -> float:
        finished = self.finished
        return float(np.mean([t.realised_r for t in finished])) if finished else float("nan")

    @property
    def net_expectancy_r(self) -> float:
        finished = self.finished
        return float(np.mean([t.net_r for t in finished])) if finished else float("nan")

    @property
    def total_r(self) -> float:
        return float(sum(t.net_r for t in self.finished))

    def break_even_win_rate(self, reward_risk: float) -> float:
        """Before costs, break-even at R:1 is 1/(R+1) — 4.8% at 20R."""
        return 100.0 / (reward_risk + 1.0)

    def win_rate_interval(self) -> tuple[float, float]:
        """95% Wilson interval on the win rate.

        Reported instead of a bare percentage because the entire question about this
        strategy is whether a high hit rate at 20R is real, and at the sample sizes this
        data can produce the interval is wide enough to contain both the claim and its
        opposite. A point estimate would hide exactly what the reader needs to see.
        """
        decided = self.resolved
        n = len(decided)
        if n == 0:
            return (float("nan"), float("nan"))
        wins = sum(t.outcome == "target" for t in decided)
        p, z = wins / n, 1.96
        denom = 1 + z**2 / n
        centre = (p + z**2 / (2 * n)) / denom
        margin = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
        return (max(0.0, (centre - margin) * 100), min(100.0, (centre + margin) * 100))

    def confidence_interval(self) -> tuple[float, float]:
        if len(self.finished) < 2:
            return (float("nan"), float("nan"))
        nets = np.array([t.net_r for t in self.finished])
        margin = 1.96 * nets.std(ddof=1) / np.sqrt(len(nets))
        return (float(nets.mean() - margin), float(nets.mean() + margin))


def _cost_r(risk_pct: float, cfg: TridentSettings) -> float:
    """Round-trip cost in R for *this* trade's stop distance, at delivery rates.

    Charged per trade rather than as a fixed R constant, because cost in R is
    (cost% / stop%) and stop distances here vary by more than an order of magnitude. A
    mean-of-ratios headline across such a spread would be dominated by the tightest stops.
    """
    if risk_pct <= 0:
        return 0.0
    return (cfg.cost_roundtrip_pct + cfg.slippage_pct) / risk_pct


def resolve_trade(
    m30: pd.DataFrame, daily: pd.DataFrame, signal: TridentSignal, cfg: TridentSettings
) -> TridentTrade | None:
    """Walk forward to the stop, the target, or the time stop.

    Two rules borrowed from the V3 backtest because they are what stop a backtest inventing
    an edge, not because the engines are shared:

    * **A bar touching both stop and target books a loss.** The order of events inside a bar
      is unknown, and resolving it favourably is the classic way a backtest manufactures a
      win rate. At 20R this bites less often than usual — a bar spanning both moved twenty
      stop widths — but the rule is applied anyway.
    * **Gaps are honoured at the open, not at the level.** If a session opens below the stop
      the trade books the *actual* loss, which is worse than -1R. This matters enormously
      here: without it every loss would be capped at exactly 1R while the 20R upside stayed
      intact, which is a free insurance policy no real position has.
    """
    if not signal.found or signal.entry_at is None:
        return None
    risk = signal.risk
    if risk <= 0:
        return None

    trade = TridentTrade(
        symbol=signal.symbol, entered_at=signal.entry_at, entry=signal.entry,
        stop=signal.stop, target=signal.target, risk_pct=signal.risk_pct,
        required_move_pct=signal.required_move_pct, feasible=signal.feasible,
        cost_r=_cost_r(signal.risk_pct, cfg),
    )

    bars, markout = _forward_walk(
        m30, daily, signal.entry_at, cfg.max_hold_sessions, cfg.interval_minutes
    )
    for bar in bars:
        if bar.session:
            trade.sessions_held = bar.session

        # The gap checks run first and only on daily bars, where `open_` exists.
        if bar.open_ is not None and bar.open_ <= signal.stop:
            trade.outcome = "stop"
            trade.realised_r = (bar.open_ - signal.entry) / risk
            trade.resolved_at, trade.gapped = bar.ts, True
            return trade
        if bar.open_ is not None and bar.open_ >= signal.target:
            trade.outcome = "target"
            trade.realised_r = (bar.open_ - signal.entry) / risk
            trade.resolved_at, trade.gapped = bar.ts, True
            return trade

        if bar.low <= signal.stop:
            trade.outcome, trade.realised_r, trade.resolved_at = "stop", -1.0, bar.ts
            return trade
        if bar.high >= signal.target:
            trade.outcome, trade.realised_r, trade.resolved_at = (
                "target", cfg.reward_risk, bar.ts,
            )
            return trade

    if markout is None:
        # Entered too recently for any forward data to exist. Right-censored, and reported
        # as such rather than silently dropped or marked out at the entry price.
        trade.outcome = "open"
        return trade

    trade.outcome = "time-stop"
    trade.realised_r = (markout - signal.entry) / risk
    trade.resolved_at = bars[-1].ts if bars else None
    return trade


def backtest_trident(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 40,
    universe: str = "nifty200",
    cfg: TridentSettings | None = None,
    apply_liquidity: bool = True,
    sink: list[GateEvent] | None = None,
) -> TridentResult:
    """Replay the strategy over whatever 30-minute history the feed serves.

    That is the binding constraint and it is not a small one: Yahoo returns roughly 60
    sessions of 30m data and no more, while the source's own claim is 8-10 setups a year per
    instrument. Any result here is therefore a description of one market period at a sample
    size that cannot separate a 90% hit rate from a 20% one — which the report states rather
    than leaving for the reader to work out.
    """
    from .trident_universe import FetchReport, fetch_pair, resolve_universe

    cfg = cfg or TridentSettings()
    result = TridentResult()
    names, screen = resolve_universe(
        cfg, universe=universe, symbols=symbols, max_symbols=max_symbols,
        apply_liquidity=apply_liquidity,
    )
    result.liquidity = screen
    if not names:
        logger.error("[trident] empty universe — nothing to replay")
        return result

    report = FetchReport()
    result.fetch = report
    logger.info(f"[trident] replaying {len(names)} {universe} names on {cfg.anchor_interval}")
    sessions_seen: set[date] = set()

    for position, symbol in enumerate(names, 1):
        m30, daily = fetch_pair(symbol, cfg, report)
        if m30 is None or daily is None:
            # Counted *and* named. A silent drop shrinks the denominator between two runs of
            # the same command, which is how a moving sample masquerades as a stable one.
            result.fetch_failures += 1
            continue

        result.symbols_tested += 1
        for session in sorted({ts.date() for ts in m30.index}):
            sessions_seen.add(session)
            signal = detect_trident_setup(m30, daily, session, cfg, symbol=symbol, sink=sink)
            if signal.gap_seen:
                result.gaps_found += 1
            if not signal.found:
                if signal.rejected_by:
                    result.rejections[signal.rejected_by] = (
                        result.rejections.get(signal.rejected_by, 0) + 1
                    )
                continue
            result.setups_found += 1
            trade = resolve_trade(m30, daily, signal, cfg)
            if trade is not None:
                trade.swept = signal.swept
                trade.swept_pool = signal.swept_pool
                trade.sweep_checked = signal.sweep_checked
                result.trades.append(trade)

        if position % 10 == 0:
            logger.info(
                f"[trident] {position}/{len(names)} symbols, "
                f"{len(result.trades)} trades so far"
            )

    result.sessions_spanned = len(sessions_seen)
    logger.info(f"[trident] {report.summary()}")
    logger.info(
        f"[trident] {len(result.trades)} trades from {result.symbols_tested} symbols "
        f"across {result.sessions_spanned} sessions"
    )
    return result


# ── Live scan ─────────────────────────────────────────────────────────────────


@dataclass
class TridentScan:
    """A scan and the accounting that lets it be argued with.

    The bare list of signals was enough while the universe was 200 hand-picked names and a
    dropped symbol was visible. On the 500 it is not: the funnel, the liquidity screen and
    the named fetch failures are what separate "a quiet day" from "a third of the universe
    never loaded".
    """

    as_of: date | None = None
    signals: list[TridentSignal] = field(default_factory=list)
    failures: list[TridentSignal] = field(default_factory=list)
    events: list[GateEvent] = field(default_factory=list)
    liquidity: object | None = None
    fetch: object | None = None
    universe_size: int = 0
    suppressed: int = 0

    def funnel(self) -> list[tuple[int, str, int]]:
        """Distinct symbols reaching each gate. Reported at every stage, as before."""
        reached: dict[int, set[str]] = {g: set() for g in GATE_NAMES}
        for event in self.events:
            if event.passed:
                reached[event.gate].add(event.symbol)
        return [(g, GATE_NAMES[g], len(reached[g])) for g in sorted(GATE_NAMES)]

    def failures_by_gate(self) -> dict[int, list[TridentSignal]]:
        out: dict[int, list[TridentSignal]] = {}
        for signal in self.failures:
            out.setdefault(signal.gate_reached, []).append(signal)
        return out


def scan_trident(
    as_of: date | None = None,
    *,
    symbols: list[str] | None = None,
    max_symbols: int = 0,
    universe: str = "nifty200",
    cfg: TridentSettings | None = None,
) -> list[TridentSignal]:
    """Today's qualifying setups, best net-R-after-costs first.

    Kept returning a bare list because the CLI, the brief writer and the forward record all
    consume it. `scan_trident_full` is the same pass with the funnel and the failure log
    attached; this delegates to it so there is one scan implementation, not two.
    """
    return scan_trident_full(
        as_of, symbols=symbols, max_symbols=max_symbols, universe=universe, cfg=cfg
    ).signals


def scan_trident_full(
    as_of: date | None = None,
    *,
    symbols: list[str] | None = None,
    max_symbols: int = 0,
    universe: str = "nifty200",
    cfg: TridentSettings | None = None,
    apply_liquidity: bool = True,
) -> TridentScan:
    """The scan, with the funnel counts, the liquidity screen and every failure recorded.

    `fetch_chart(as_of=...)` is the single truncation chokepoint upstream, so an archive-tier
    scan of an older session cannot see past it. Do not fetch around it.

    Ordering is by **net R after this trade's own costs**, not by stop tightness as before.
    The old sort put the tightest stop at the top, which is precisely the setup surrendering
    the most of its own R to costs — cost in R is (cost% / stop%), so a 0.10% stop hands back
    1.7R before it starts.
    """
    from ..data import nse_archive
    from .trident_universe import FetchReport, fetch_pair, resolve_universe

    cfg = cfg or TridentSettings()
    as_of = as_of or nse_archive.last_trading_day() or date.today()
    scan = TridentScan(as_of=as_of)

    names, screen = resolve_universe(
        cfg, universe=universe, symbols=symbols, max_symbols=max_symbols,
        as_of=as_of, apply_liquidity=apply_liquidity,
    )
    scan.liquidity = screen
    scan.universe_size = len(names)
    if not names:
        logger.error("[trident] empty universe — nothing scanned")
        return scan

    report = FetchReport()
    scan.fetch = report
    for position, symbol in enumerate(names, 1):
        m30, daily = fetch_pair(symbol, cfg, report, as_of=as_of)
        if m30 is None or daily is None:
            continue
        signal = detect_trident_setup(
            m30, daily, as_of, cfg, symbol=symbol, sink=scan.events
        )
        if signal.found:
            scan.signals.append(signal)
        else:
            scan.failures.append(signal)
        if position % 25 == 0:
            logger.info(
                f"[trident] scanned {position}/{len(names)}, {len(scan.signals)} found"
            )

    scan.signals.sort(key=lambda s: -s.net_r_at_target)
    if cfg.max_alerts_per_session and len(scan.signals) > cfg.max_alerts_per_session:
        scan.suppressed = len(scan.signals) - cfg.max_alerts_per_session
        scan.signals = scan.signals[: cfg.max_alerts_per_session]
    if report.failed:
        logger.warning(f"[trident] {report.summary()}")
    return scan


# ── Scaled exit: the accounting that manufactures a high win rate ──────────────


@dataclass(frozen=True)
class ScaledExit:
    """Take part of the position off early, then protect the rest at break-even.

    This is not in the source and is not an improvement to the strategy. It exists to make
    one specific comparison possible: traders quoting "80% win rate" are very often quoting
    a scoreboard like this one, where a trade that touches +1R and then reverses books as a
    *win* rather than a scratch. Replaying the same setups both ways shows how much hit rate
    the accounting produces on its own, and what the expectancy underneath it actually is.

    The two numbers move in opposite directions by construction. Nothing here is dishonest;
    it is simply a different metric, and comparing it against a fixed-target win rate is the
    error.
    """

    fraction: float = 0.5       # how much comes off at the first target
    at_r: float = 1.0           # ...and where
    breakeven_after: bool = True


def drop_forming_bar(
    frame: pd.DataFrame, interval_minutes: float, now: pd.Timestamp | None = None
) -> pd.DataFrame:
    """Drop the final bar when its window has not closed yet.

    Yahoo returns the bar currently being built. Its high and low only ever widen as the
    window fills, so anything decided from it is decided on information that is not final.

    In *resolution* that is worse than merely early, and it is the reason this exists: a
    partial bar that has printed the target but not yet the stop books a **win**, while the
    same bar completed may touch both — which this codebase books as a **loss**. A forward
    record is written once, so an early settle converts a loser into a winner permanently.
    Discovered 26 Aug 2026, when a daily frame ending on an open session resolved two trades
    off an unfinished bar.

    `now` is injectable so the behaviour can be tested without depending on the clock.
    """
    if frame is None or frame.empty:
        return frame
    if now is None:
        now = pd.Timestamp.now(tz=frame.index.tz) if frame.index.tz is not None else pd.Timestamp.now()
    closes_at = frame.index[-1] + pd.Timedelta(minutes=interval_minutes)
    return frame.iloc[:-1] if now < closes_at else frame


class ForwardBar(NamedTuple):
    """One bar after entry. `open_` is None intraday, where there is no gap to honour."""

    ts: pd.Timestamp
    open_: float | None
    high: float
    low: float
    session: int          # 0 while still inside the entry session, then 1, 2, 3...


def _forward_walk(
    m30: pd.DataFrame,
    daily: pd.DataFrame,
    entry_at: pd.Timestamp,
    max_hold_sessions: int,
    interval_minutes: int = 30,
) -> tuple[list[ForwardBar], float | None]:
    """Every bar after entry, plus the price to mark an unresolved trade out at.

    Returns `(bars, markout)`. A `markout` of None means no forward daily bar existed at
    all — the trade was entered too near the end of the data and is right-censored, which
    is a different thing from surviving the hold and being timed out.

    Both resolvers walk this. Two hand-written loops would eventually disagree about which
    bars a trade saw, and the comparison between the fixed and scaled scoreboards is only
    meaningful while they see exactly the same ones.
    """
    # Neither frame may contribute a bar that is still being built — see
    # `drop_forming_bar`. Applied here rather than at the fetch so that every caller of
    # either resolver inherits it, including the forward record.
    m30 = drop_forming_bar(m30, interval_minutes)
    daily = drop_forming_bar(daily, SESSION_MINUTES)

    entry_day = entry_at.date()
    bars: list[ForwardBar] = []

    same_day = m30[(m30.index.date == entry_day) & (m30.index > entry_at)]
    for ts, bar in same_day.iterrows():
        bars.append(ForwardBar(ts, None, float(bar["high"]), float(bar["low"]), 0))

    forward = daily[daily.index.date > entry_day].head(max_hold_sessions)
    for offset, (ts, bar) in enumerate(forward.iterrows(), start=1):
        bars.append(
            ForwardBar(ts, float(bar["open"]), float(bar["high"]), float(bar["low"]), offset)
        )

    # A mark-out price is returned ONLY when the hold genuinely expired. If the frame simply
    # ran out of bars first, the trade is still running and must stay `open` — right-censored,
    # not finished.
    #
    # Getting this wrong is expensive and was: trades entered two days before the end of the
    # window were marked out at the last available close, labelled `time-stop`, and counted
    # toward expectancy as completed outcomes. Two such trades averaging +2.61R lifted the
    # 20R gross figure by 0.237R and, at 4R, reversed the verdict on the stop floor. A
    # censored trade marked out at a favourable close is an unresolved trade wearing a
    # result. Found 26 Aug 2026.
    expired = len(forward) >= max_hold_sessions
    markout = float(forward["close"].iloc[-1]) if expired else None
    return bars, markout


def resolve_trade_scaled(
    m30: pd.DataFrame, daily: pd.DataFrame, signal: TridentSignal,
    cfg: TridentSettings, exit_rule: ScaledExit | None = None,
) -> TridentTrade | None:
    """The same trade, scored on a scaled-exit scoreboard.

    Sequence, with the same two honesty rules as the fixed-target resolver — a bar that
    reaches two levels resolves at the *worse* one, and a gap resolves at the open:

        before the partial   stop is the doji low; a bar taking it books -1R on full size
        at +`at_r`           `fraction` comes off, and the stop moves to entry
        after the partial    the runner exits at break-even or at the full target

    A break-even stop is **not** free. If a session gaps below entry the runner exits at that
    open, which is a loss on the runner — so the scoreboard that calls this a "win" can still
    hand back less than the partial booked.
    """
    exit_rule = exit_rule or ScaledExit()
    if not signal.found or signal.entry_at is None:
        return None
    risk = signal.risk
    if risk <= 0:
        return None

    partial_price = signal.entry + exit_rule.at_r * risk
    trade = TridentTrade(
        symbol=signal.symbol, entered_at=signal.entry_at, entry=signal.entry,
        stop=signal.stop, target=signal.target, risk_pct=signal.risk_pct,
        required_move_pct=signal.required_move_pct, feasible=signal.feasible,
        cost_r=_cost_r(signal.risk_pct, cfg),
    )
    booked = 0.0                 # R already realised on the part that came off
    taken = False                # has the partial filled
    stop_price = signal.stop

    bars, markout = _forward_walk(
        m30, daily, signal.entry_at, cfg.max_hold_sessions, cfg.interval_minutes
    )
    for bar in bars:
        ts, open_, high, low = bar.ts, bar.open_, bar.high, bar.low
        if bar.session:
            trade.sessions_held = bar.session

        # Gaps first: a session opening beyond a level resolves there, not at the level.
        if open_ is not None and open_ <= stop_price:
            share = (1 - exit_rule.fraction) if taken else 1.0
            trade.outcome = "stop" if not taken else "breakeven-stop"
            trade.realised_r = booked + share * ((open_ - signal.entry) / risk)
            trade.resolved_at, trade.gapped = ts, True
            return trade
        if open_ is not None and open_ >= signal.target:
            share = (1 - exit_rule.fraction) if taken else 1.0
            trade.outcome = "target"
            trade.realised_r = booked + share * ((open_ - signal.entry) / risk)
            trade.resolved_at, trade.gapped = ts, True
            return trade

        # A bar reaching both the stop and something better resolves at the stop: the order
        # of events inside a bar is unknown and resolving it favourably invents edge.
        if low <= stop_price:
            share = (1 - exit_rule.fraction) if taken else 1.0
            if taken:
                trade.outcome = "breakeven-stop"
                trade.realised_r = booked + share * ((stop_price - signal.entry) / risk)
            else:
                trade.outcome, trade.realised_r = "stop", -1.0
            trade.resolved_at = ts
            return trade

        if not taken and high >= partial_price:
            booked = exit_rule.fraction * exit_rule.at_r
            taken = True
            if exit_rule.breakeven_after:
                stop_price = signal.entry
            # The same bar may also carry the full target; that is checked below.

        if high >= signal.target:
            share = (1 - exit_rule.fraction) if taken else 1.0
            trade.outcome = "target"
            trade.realised_r = booked + share * cfg.reward_risk
            trade.resolved_at = ts
            return trade

    if markout is None:
        trade.outcome = "open"
        return trade

    # Hold expired: mark whatever is still on out at the last close.
    trade.outcome = "time-stop"
    trade.realised_r = booked + (1 - exit_rule.fraction if taken else 1.0) * (
        (markout - signal.entry) / risk
    )
    trade.resolved_at = bars[-1].ts if bars else None
    return trade


# ── Kill-zone window sweep ────────────────────────────────────────────────────
#
# The 09:15–12:45 window is a straight transplant. London 03:00–06:30 New York is the first
# three and a half hours of the London session, and it was mapped onto the first three and a
# half hours of the NSE session on the strength of that analogy alone. Nothing has ever
# tested whether that block contributes anything on Indian equities, and there is a specific
# reason to doubt it transfers: an FX session has no opening auction and no overnight gap to
# absorb, while an NSE equity session is violently front-loaded — the first thirty minutes
# routinely carry a fifth of the day's volume, and the middle of the day is a different
# market entirely.
#
# So the window becomes a parameter and gets swept. What comes back is not a window to adopt:
# the sample cannot support choosing one, and picking the best cell of a grid searched on
# ~60 sessions is the definition of fitting. What it *can* support is the negative finding —
# whether the inherited block is distinguishable from the alternatives at all.


@dataclass
class WindowResult:
    """One kill-zone window's replay, kept alongside every other window's."""

    start: time
    end: time
    setups: int = 0
    resolved: int = 0
    wins: int = 0
    gross_r: float = float("nan")
    net_r: float = float("nan")
    total_r: float = 0.0
    mean_risk_pct: float = float("nan")
    ci_low: float = float("nan")
    ci_high: float = float("nan")

    @property
    def label(self) -> str:
        return f"{self.start:%H:%M}–{self.end:%H:%M}"


@dataclass
class WindowSweep:
    results: list[WindowResult] = field(default_factory=list)
    symbols_tested: int = 0
    sessions_spanned: int = 0
    interval: str = "30m"
    reward_risk: float = 4.0
    fetch: object | None = None
    baseline: str = ""                 # the transplanted window, for comparison

    # A cell has to carry at least this many finished trades before it is allowed to take
    # part in the separability test. Measured need, not taste: on the 30m run of 30 Aug 2026
    # the window 11:30-14:00 produced **two** trades, both winners, and a 95% interval of
    # +2.34 to +2.98 — an interval that excludes every other window's and means nothing at
    # all. Without this floor the report would announce that a window separates, on a sample
    # of two, which is worse than reporting nothing.
    MIN_CELL = 10

    def best(self) -> WindowResult | None:
        scored = [
            r for r in self.results if np.isfinite(r.net_r) and r.setups >= self.MIN_CELL
        ]
        return max(scored, key=lambda r: r.net_r) if scored else None

    def comparable(self) -> list[WindowResult]:
        """Cells with enough trades to be compared with each other at all."""
        return [
            r for r in self.results
            if r.setups >= self.MIN_CELL and np.isfinite(r.ci_low) and np.isfinite(r.ci_high)
        ]

    def thin_cells(self) -> list[WindowResult]:
        return [r for r in self.results if r.setups < self.MIN_CELL]

    def overlapping_intervals(self) -> bool:
        """Do every *comparable* window's confidence intervals overlap?

        The only question this sweep is powered to answer. If they all overlap, no window is
        distinguishable from any other and the transplant is neither vindicated nor convicted
        — which is itself the useful answer, because it says the 09:15–12:45 choice is not
        carrying the strategy either way.

        Thin cells are excluded rather than allowed to decide it. A window with two trades has
        a narrow interval for the same reason a coin flipped twice looks decisive.
        """
        scored = self.comparable()
        if len(scored) < 2:
            return True
        lo = max(r.ci_low for r in scored)
        hi = min(r.ci_high for r in scored)
        return lo <= hi


def sweep_killzone(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 40,
    universe: str = "nifty500",
    cfg: TridentSettings | None = None,
    windows: list[tuple[time, time]] | None = None,
    apply_liquidity: bool = True,
) -> WindowSweep:
    """Replay the strategy once per candidate window, on identical data.

    Each symbol is fetched **once** and every window is evaluated against those same frames,
    for two reasons. It is 1/N of the network cost, and — the one that matters — it makes the
    windows a like-for-like comparison: a symbol that fails to load for one window and loads
    for another would silently change the universe between cells of the grid, which is
    exactly the defect that moved the trident's own sample from 22 setups to 21.
    """
    from .trident_universe import FetchReport, fetch_pair, resolve_universe

    cfg = cfg or TridentSettings()
    windows = windows or default_windows(cfg)

    names, _ = resolve_universe(
        cfg, universe=universe, symbols=symbols, max_symbols=max_symbols,
        apply_liquidity=apply_liquidity,
    )
    sweep = WindowSweep(
        interval=cfg.anchor_interval,
        reward_risk=cfg.reward_risk,
        baseline=f"{cfg.killzone_start:%H:%M}–{cfg.killzone_end:%H:%M}",
    )
    if not names:
        logger.error("[trident-sweep] empty universe")
        return sweep

    report = FetchReport()
    sweep.fetch = report
    buckets: dict[tuple[time, time], list[TridentTrade]] = {w: [] for w in windows}
    sessions_seen: set[date] = set()

    for position, symbol in enumerate(names, 1):
        m30, daily = fetch_pair(symbol, cfg, report)
        if m30 is None or daily is None:
            continue
        sweep.symbols_tested += 1
        sessions = sorted({ts.date() for ts in m30.index})
        sessions_seen.update(sessions)

        for start, end in windows:
            # A new frozen settings object per window. Mutating one would retune every window
            # already measured, and `TridentSettings` is frozen precisely so that cannot
            # happen by accident.
            window_cfg = replace(cfg, killzone_start=start, killzone_end=end)
            for session in sessions:
                signal = detect_trident_setup(
                    m30, daily, session, window_cfg, symbol=symbol
                )
                if not signal.found:
                    continue
                trade = resolve_trade(m30, daily, signal, window_cfg)
                if trade is not None:
                    buckets[(start, end)].append(trade)

        if position % 10 == 0:
            logger.info(f"[trident-sweep] {position}/{len(names)} symbols")

    sweep.sessions_spanned = len(sessions_seen)
    for (start, end), trades in buckets.items():
        sweep.results.append(_summarise_window(start, end, trades))
    sweep.results.sort(key=lambda r: (r.start, r.end))
    logger.info(f"[trident-sweep] {report.summary()}")
    return sweep


def _summarise_window(start: time, end: time, trades: list[TridentTrade]) -> WindowResult:
    result = WindowResult(start=start, end=end, setups=len(trades))
    finished = [t for t in trades if t.outcome != "open"]
    decided = [t for t in trades if t.outcome in ("target", "stop")]
    result.resolved = len(decided)
    result.wins = sum(t.outcome == "target" for t in decided)
    if finished:
        nets = np.array([t.net_r for t in finished])
        result.gross_r = float(np.mean([t.realised_r for t in finished]))
        result.net_r = float(nets.mean())
        result.total_r = float(nets.sum())
        result.mean_risk_pct = float(np.mean([t.risk_pct for t in finished]))
        if len(nets) >= 2:
            margin = 1.96 * nets.std(ddof=1) / np.sqrt(len(nets))
            result.ci_low = float(nets.mean() - margin)
            result.ci_high = float(nets.mean() + margin)
    return result


def default_windows(cfg: TridentSettings, block_minutes: int = 90) -> list[tuple[time, time]]:
    """A grid over the session: rolling blocks, plus the whole session and the transplant.

    Rolling rather than disjoint, because a disjoint grid can slice a real effect in half at
    a boundary and report nothing at either side of it. The step is half a block, and every
    window is at least five bars wide or the pattern cannot form inside it.
    """
    step = timedelta(minutes=max(block_minutes // 2, cfg.interval_minutes))
    span = max(timedelta(minutes=block_minutes),
               timedelta(minutes=cfg.interval_minutes * 5))

    out: list[tuple[time, time]] = []
    cursor = datetime.combine(date.min, SESSION_OPEN)
    close = datetime.combine(date.min, SESSION_CLOSE)
    while cursor + span <= close:
        out.append((cursor.time(), (cursor + span).time()))
        cursor += step
    # The transplant itself and the whole session, so the grid always contains the thing it
    # is being compared against.
    out.append((cfg.killzone_start, cfg.killzone_end))
    out.append((SESSION_OPEN, SESSION_CLOSE))
    seen, unique = set(), []
    for window in out:
        if window not in seen:
            seen.add(window)
            unique.append(window)
    return unique
