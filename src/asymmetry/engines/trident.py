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

from dataclasses import dataclass, field
from datetime import date, datetime, time

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


@dataclass
class TridentSignal:
    """One detected setup, or an explicit absence carrying the reason it was refused."""

    symbol: str = ""
    found: bool = False
    note: str = ""
    rejected_by: str = ""              # which condition refused it, for the accounting

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


def _refuse(signal: TridentSignal, reason: str) -> None:
    """Record a refusal, keeping the furthest-progressed one."""
    if not signal.rejected_by or _stage(reason) >= _stage(signal.rejected_by):
        signal.rejected_by = reason




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
) -> TridentSignal:
    """The whole pattern for one symbol on one session, long side only.

    Long only is faithful rather than lazy: the source is explicit that he is long-biased,
    that his data comes from longs, and that he is "not good at shorting". A short mirror
    would be an invention, and inventing the other half of somebody's edge and then
    measuring it teaches nothing about the edge.

    Every detector here measures on bars strictly *preceding* the confirming bar. That is
    the rule the whole codebase turns on, and it is the difference between a pattern and a
    label for a move that already happened.
    """
    cfg = cfg or TridentSettings()
    signal = TridentSignal(symbol=symbol)

    if m30 is None or len(m30) < max(cfg.ema_stack) + 5:
        signal.note, signal.rejected_by = "insufficient 30m history", "data"
        return signal

    frame = m30.copy()
    for period in cfg.ema_stack:
        frame[f"ema{period}"] = ema(frame["close"], period)

    day = frame[frame.index.date == session]
    if day.empty:
        signal.note, signal.rejected_by = "no 30m bars for this session", "data"
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
            return signal

    bars = day[
        (day.index.time >= cfg.killzone_start) & (day.index.time <= cfg.killzone_end)
    ]
    if len(bars) < 5:
        signal.note, signal.rejected_by = "kill zone too short to form the pattern", "data"
        return signal

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
            if not (wicked and reclaimed):
                continue
            if body_pct > cfg.max_doji_body_pct:
                # "Say this wasn't a doji, the body of this candle was in here — this would
                # be an invalidation." A big body means one side won the bar outright; the
                # doji is the whole point, because it says sellers pushed into the level and
                # were rejected inside a single bar.
                _refuse(signal, "body too large for a doji")
                continue
            saw_doji = True

            # ── The confirmation bar ──────────────────────────────────────────
            if j + 1 >= len(times):
                _refuse(signal, "no confirmation bar left in the kill zone")
                continue
            confirm = bars.iloc[j + 1]
            doji_high, doji_low = float(candidate["high"]), float(candidate["low"])

            if float(confirm["close"]) >= doji_high:
                # His rule, and it is a rule about geometry rather than direction: closing
                # above the doji's high means the entry has run away from its own stop, and
                # 1:20 only exists while entry sits on top of invalidation.
                _refuse(signal, "confirmation closed above the doji high")
                continue
            if float(confirm["low"]) <= doji_low:
                # The stop is the doji low; if the confirming bar already traded through it,
                # the trade was stopped out before it could be taken.
                _refuse(signal, "confirmation traded through the stop")
                continue
            if cfg.require_ema_stack and not _ema_stacked(confirm, cfg):
                _refuse(signal, "EMAs not stacking")
                continue

            entry = float(confirm["close"])
            stop = doji_low
            risk = entry - stop
            if risk <= 0:
                continue
            risk_pct = risk / entry * 100
            if not (cfg.min_risk_pct <= risk_pct <= cfg.max_risk_pct):
                _refuse(signal, "stop outside the configured band")
                continue

            # ── Daily state, evaluated from the partial candle only ───────────
            elapsed_bars = day[day.index <= times[j + 1]]
            partial = {
                "open": float(elapsed_bars["open"].iloc[0]),
                "close": entry,
                "volume": float(elapsed_bars["volume"].sum()),
                "elapsed_fraction": _elapsed_fraction(times[j + 1]),
            }
            state = classify_daily_candle(daily, session, partial, cfg)
            if cfg.require_strong_daily and state != "green":
                _refuse(signal, f"daily candle printing {state}, not green")
                continue

            required = cfg.reward_risk * risk_pct
            capacity = (
                atr_pct * np.sqrt(cfg.max_hold_sessions)
                if np.isfinite(atr_pct)
                else float("nan")
            )
            feasible = bool(np.isfinite(capacity) and required <= capacity)
            if cfg.require_feasible_target and not feasible:
                _refuse(signal, "20R unreachable inside the hold window")
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
            signal.note = (
                f"{gap_pct:.2f}% fair value gap completed at {times[i]:%H:%M}"
                + (" (prime window)" if prime else "")
                + f"; {body_pct:.0f}%-body doji at {times[j]:%H:%M} wicked the 50% at "
                f"{encroachment:,.2f} and closed back above; {times[j + 1]:%H:%M} confirmed "
                f"below the doji high with EMAs stacked and the daily printing {state}"
            )
            return signal

    if not saw_gap:
        signal.note = "no fair value gap printed inside the kill zone"
        _refuse(signal, "no gap")
    elif not saw_doji:
        signal.note = "gap printed but nothing retraced to the 50% as a doji"
        _refuse(signal, "no doji retrace")
    else:
        signal.note = f"pattern formed but refused: {signal.rejected_by}"
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

    * **A bar touching both stop and target books a loss.** The sequence inside a bar is
      unknown and resolving it favourably is the classic way to manufacture a win rate. At
      20R this bites less often than usual — a bar spanning both moved twenty stop widths —
      but the rule is applied anyway.
    * **Gaps are honoured at the open, not at the level.** If a session opens below the stop
      the trade books the *actual* loss, which is worse than -1R. This is the single most
      important correction for a multi-week hold on one stock: leaving it out would quietly
      cap every loss at exactly 1R while leaving the 20R upside intact.
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

    # The rest of the entry session, on the bars the entry was found on.
    entry_day = signal.entry_at.date()
    same_day = m30[(m30.index.date == entry_day) & (m30.index > signal.entry_at)]
    for ts, bar in same_day.iterrows():
        if float(bar["low"]) <= signal.stop:
            trade.outcome, trade.realised_r, trade.resolved_at = "stop", -1.0, ts
            return trade
        if float(bar["high"]) >= signal.target:
            trade.outcome, trade.realised_r, trade.resolved_at = (
                "target", cfg.reward_risk, ts,
            )
            return trade

    # Then daily bars. 20R off a sub-1% stop is a multi-week move; resolving it only on the
    # ~60 sessions of 30m history the feed serves would truncate most trades artificially.
    forward = daily[daily.index.date > entry_day].head(cfg.max_hold_sessions)
    for offset, (ts, bar) in enumerate(forward.iterrows(), start=1):
        trade.sessions_held = offset
        open_, high, low = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if open_ <= signal.stop:
            trade.outcome = "stop"
            trade.realised_r = (open_ - signal.entry) / risk
            trade.resolved_at, trade.gapped = ts, True
            return trade
        if open_ >= signal.target:
            trade.outcome = "target"
            trade.realised_r = (open_ - signal.entry) / risk
            trade.resolved_at, trade.gapped = ts, True
            return trade
        if low <= signal.stop:
            trade.outcome, trade.realised_r, trade.resolved_at = "stop", -1.0, ts
            return trade
        if high >= signal.target:
            trade.outcome, trade.realised_r, trade.resolved_at = (
                "target", cfg.reward_risk, ts,
            )
            return trade

    if len(forward) == 0:
        # Entered too recently for any forward data to exist. Right-censored, and reported
        # as such rather than silently dropped or marked out at the entry price.
        trade.outcome = "open"
        return trade

    trade.outcome = "time-stop"
    trade.realised_r = (float(forward["close"].iloc[-1]) - signal.entry) / risk
    trade.resolved_at = forward.index[-1]
    return trade


def backtest_trident(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 40,
    universe: str = "nifty200",
    cfg: TridentSettings | None = None,
) -> TridentResult:
    """Replay the strategy over whatever 30-minute history the feed serves.

    That is the binding constraint and it is not a small one: Yahoo returns roughly 60
    sessions of 30m data and no more, while the source's own claim is 8-10 setups a year per
    instrument. Any result here is therefore a description of one market period at a sample
    size that cannot separate a 90% hit rate from a 20% one — which the report states rather
    than leaving for the reader to work out.
    """
    from ..data import universe as universe_mod, yahoo

    cfg = cfg or TridentSettings()
    if symbols is None:
        constituents = universe_mod.load_universe(universe)
        symbols = sorted(constituents)
        if max_symbols:
            symbols = symbols[:max_symbols]

    result = TridentResult()
    logger.info(f"[trident] replaying {len(symbols)} {universe} names")
    sessions_seen: set[date] = set()

    for position, symbol in enumerate(symbols, 1):
        ysym = yahoo.to_yahoo_symbol(symbol)
        m30 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.anchor_interval)
        if m30 is None or m30.empty:
            continue
        daily = yahoo.fetch_chart(ysym, range_="2y", interval="1d")
        if daily is None or daily.empty:
            continue

        result.symbols_tested += 1
        for session in sorted({ts.date() for ts in m30.index}):
            sessions_seen.add(session)
            signal = detect_trident_setup(m30, daily, session, cfg, symbol=symbol)
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
                result.trades.append(trade)

        if position % 10 == 0:
            logger.info(
                f"[trident] {position}/{len(symbols)} symbols, "
                f"{len(result.trades)} trades so far"
            )

    result.sessions_spanned = len(sessions_seen)
    logger.info(
        f"[trident] {len(result.trades)} trades from {result.symbols_tested} symbols "
        f"across {result.sessions_spanned} sessions"
    )
    return result


# ── Live scan ─────────────────────────────────────────────────────────────────


def scan_trident(
    as_of: date | None = None,
    *,
    symbols: list[str] | None = None,
    max_symbols: int = 0,
    universe: str = "nifty200",
    cfg: TridentSettings | None = None,
) -> list[TridentSignal]:
    """Today's qualifying setups, tightest stop first.

    `fetch_chart(as_of=...)` is the single truncation chokepoint upstream, so an archive-tier
    scan of an older session cannot see past it. Do not fetch around it.
    """
    from ..data import nse_archive, universe as universe_mod, yahoo

    cfg = cfg or TridentSettings()
    as_of = as_of or nse_archive.last_trading_day() or date.today()
    if symbols is None:
        constituents = universe_mod.load_universe(universe)
        symbols = sorted(constituents)
        if max_symbols:
            symbols = symbols[:max_symbols]

    found: list[TridentSignal] = []
    for position, symbol in enumerate(symbols, 1):
        ysym = yahoo.to_yahoo_symbol(symbol)
        m30 = yahoo.fetch_chart(
            ysym, range_="60d", interval=cfg.anchor_interval, as_of=as_of
        )
        if m30 is None or m30.empty:
            continue
        daily = yahoo.fetch_chart(ysym, range_="2y", interval="1d", as_of=as_of)
        if daily is None or daily.empty:
            continue
        signal = detect_trident_setup(m30, daily, as_of, cfg, symbol=symbol)
        if signal.found:
            found.append(signal)
        if position % 25 == 0:
            logger.info(f"[trident] scanned {position}/{len(symbols)}, {len(found)} found")

    found.sort(key=lambda s: s.risk_pct)
    return found


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


def _forward_bars(m30: pd.DataFrame, daily: pd.DataFrame, entry_at: pd.Timestamp,
                  max_hold_sessions: int):
    """Every bar after entry, in order: the rest of the entry session, then daily bars.

    Factored out so the fixed-target and scaled-exit resolvers walk *identically*. Two
    hand-written loops would eventually disagree about which bars a trade saw, and the
    comparison between them is the entire point of the scaled resolver.
    """
    entry_day = entry_at.date()
    same_day = m30[(m30.index.date == entry_day) & (m30.index > entry_at)]
    for ts, bar in same_day.iterrows():
        # Intraday: no overnight gap to honour, so the open carries no special meaning.
        yield ts, None, float(bar["high"]), float(bar["low"]), 0

    forward = daily[daily.index.date > entry_day].head(max_hold_sessions)
    for offset, (ts, bar) in enumerate(forward.iterrows(), start=1):
        yield ts, float(bar["open"]), float(bar["high"]), float(bar["low"]), offset
    if len(forward):
        # Signals the caller that the hold expired rather than the data running out.
        yield None, None, None, float(forward["close"].iloc[-1]), -1


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

    for ts, open_, high, low, offset in _forward_bars(
        m30, daily, signal.entry_at, cfg.max_hold_sessions
    ):
        if offset == -1:         # hold expired; mark the remainder out at the last close
            trade.outcome = "time-stop"
            trade.realised_r = booked + (1 - exit_rule.fraction if taken else 1.0) * (
                (low - signal.entry) / risk
            )
            return trade
        if offset:
            trade.sessions_held = offset

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

    trade.outcome = "open"
    return trade
