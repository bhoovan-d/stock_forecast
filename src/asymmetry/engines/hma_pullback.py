"""Intraday HMA/Bollinger pullback — a separate strategy from V3.

Owner's specification, 20 Aug 2026, in his words:

    1. A bullish green candle with 75 percent body at 30 mins chart of Nifty 200 stocks
       forming before 1 o'clock.
    2. The Hull Moving average 9 is sloping slightly upwards and almost cutting BB middle
       band upwards or about to.
    3. Then at 3 minutes timeframe or 5 mins timeframe, there is a pullback on BB middle
       band.
    4. We take an entry the 3/5 min candle. Stoploss on low of candle. Target is 3 is to 1.
       Max risk is .7 percent of the stock.

**This is deliberately not part of V3 and must not be wired into it.** V3 is a 1-5 session
swing engine on the NIFTY 500 with a 4R target and a 0.5-1.5% stop; this is an intraday
trade on the NIFTY 200 at 3R with a 0.7% cap. Sharing a constant between them would
silently retune one when the other is edited — the same reason `spec_engine` keeps its own
gates.

Where the wording had to be made precise, the choice is named in `PullbackSettings` and
flagged in `docs/spec-hma-pullback.md`. Two are worth knowing here:

* **"almost cutting BB middle upwards or about to"** is the loosest phrase in the spec. It
  is read as: HMA9 rising, and either still below the middle band by no more than
  `hma_near_pct`, or crossed above it within the last `hma_cross_lookback` bars. A rising
  HMA already far above the band is *not* "about to cut" and is refused.
* **The trade is squared off at the close.** The spec gives no exit other than stop and
  target, and this is an intraday setup anchored to a pre-13:00 candle, so an unresolved
  position is marked out at the last bar of the session rather than carried overnight.

Costs are charged per trade from the *actual* stop distance rather than a fixed R figure.
That matters more here than in V3: at a 0.7% stop, 0.17% of round-trip cost is 0.24R, so
roughly a quarter of every 1R is spent on getting in and out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings

# NSE session bounds are already defined for the V3 path; reuse rather than restate.
from ..data.yahoo import SESSION_CLOSE


@dataclass(frozen=True)
class PullbackSettings:
    """Every threshold the strategy needs, and nothing else.

    Resolved and passed explicitly rather than read from the global singleton, so this
    strategy can be tuned without touching V3's behaviour.
    """

    # ── Stage 1: the 30-minute anchor candle ──────────────────────────────────
    anchor_interval: str = "30m"
    min_body_pct: float = 75.0          # body as a share of the bar's full range
    latest_anchor: time = time(13, 0)   # "forming before 1 o'clock"
    hma_period: int = 9
    bb_period: int = 20                 # Bollinger middle band = 20-period SMA
    # How near the HMA must be to the middle band to count as "about to cut".
    hma_near_pct: float = 0.50
    # …or how recently it may already have crossed above it.
    hma_cross_lookback: int = 3

    # ── Stage 2: the lower-timeframe pullback ─────────────────────────────────
    # 3m is specified as an option but Yahoo does not serve it, and 1m — the only source it
    # could be resampled from — reaches back 7 sessions. 5m is what can be measured.
    entry_interval: str = "5m"
    entry_bb_period: int = 20
    # How long after the anchor the pullback may arrive before the signal is stale.
    max_bars_to_entry: int = 40

    # ── Stage 3: the trade ────────────────────────────────────────────────────
    reward_risk: float = 3.0
    max_risk_pct: float = 0.7
    # A *floor*, which the spec does not have and the measurement says it needs. The 0.7%
    # is a cap almost nothing reaches: the median stop came out at 0.17%, because the low
    # of a 5-minute candle sits very close to its close. At that distance 0.17% of
    # round-trip friction is a full 1R, and the trade is uneconomic before it starts.
    # Default 0.0 keeps the scanner faithful to the specification as written; raise it to
    # experiment. V3 learned the same lesson and carries min_stop_pct = 0.5 for it.
    min_risk_pct: float = 0.0
    square_off_at_close: bool = True


@dataclass
class PullbackSignal:
    """One detected trade, or an explicit absence with the reason."""

    symbol: str = ""
    found: bool = False
    note: str = ""

    anchor_at: pd.Timestamp | None = None
    anchor_body_pct: float = 0.0
    anchor_hma_gap_pct: float = 0.0     # (hma - bb_mid) / bb_mid * 100 at the anchor

    entry_at: pd.Timestamp | None = None
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    risk_pct: float = 0.0

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)


# ── Indicators ────────────────────────────────────────────────────────────────


def weighted_moving_average(series: pd.Series, period: int) -> pd.Series:
    """Linear WMA — the building block of the Hull average."""
    if period < 1:
        raise ValueError("period must be >= 1")
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(
        lambda w: float(np.dot(w, weights) / weights.sum()), raw=True
    )


def hull_moving_average(series: pd.Series, period: int = 9) -> pd.Series:
    """HMA = WMA(2·WMA(n/2) − WMA(n), √n).

    Written out rather than approximated with an EMA: the whole point of the Hull average
    is that it lags far less than a same-period EMA, so substituting one would test a
    different indicator from the one specified.
    """
    half = max(int(period / 2), 1)
    root = max(int(np.sqrt(period)), 1)
    raw = 2 * weighted_moving_average(series, half) - weighted_moving_average(series, period)
    return weighted_moving_average(raw, root)


def bollinger_middle(series: pd.Series, period: int = 20) -> pd.Series:
    """The middle Bollinger band is a simple moving average; the bands themselves are not
    used by this strategy, only the centre line."""
    return series.rolling(period).mean()


# ── Stage 1: the 30-minute anchor ─────────────────────────────────────────────


def find_anchor(
    h30: pd.DataFrame, session: date, cfg: PullbackSettings | None = None
) -> PullbackSignal:
    """The first qualifying 30-minute candle of ``session``, or why there was none.

    Indicators are computed over the **whole frame** and then sliced to the session, so the
    HMA and the band at 09:45 carry the previous days' history rather than restarting each
    morning. Restarting them daily would leave the first several bars of every session
    reading from a warm-up period rather than the market.
    """
    cfg = cfg or PullbackSettings()
    if h30 is None or len(h30) < cfg.bb_period + cfg.hma_period:
        return PullbackSignal(note="insufficient 30m history")

    frame = h30.copy()
    frame["hma"] = hull_moving_average(frame["close"], cfg.hma_period)
    frame["bb_mid"] = bollinger_middle(frame["close"], cfg.bb_period)
    frame["hma_prev"] = frame["hma"].shift(1)
    # Was the HMA below the band at any point in the recent past? That is what makes a
    # present position above it a *recent cross* rather than a long-established uptrend.
    below = frame["hma"] < frame["bb_mid"]
    frame["was_below"] = below.shift(1).rolling(cfg.hma_cross_lookback).max().astype(float)

    day = frame[frame.index.date == session]
    if day.empty:
        return PullbackSignal(note="no 30m bars for this session")

    for ts, bar in day.iterrows():
        if ts.time() >= cfg.latest_anchor:
            break
        if not np.isfinite(bar.get("hma", np.nan)) or not np.isfinite(bar.get("bb_mid", np.nan)):
            continue

        span = float(bar["high"] - bar["low"])
        if span <= 0:
            continue
        body = float(bar["close"] - bar["open"])
        if body <= 0:
            continue  # must be green
        body_pct = body / span * 100
        if body_pct < cfg.min_body_pct:
            continue

        # Rising, however slightly — "sloping slightly upwards".
        if not np.isfinite(bar["hma_prev"]) or bar["hma"] <= bar["hma_prev"]:
            continue

        gap_pct = (bar["hma"] - bar["bb_mid"]) / bar["bb_mid"] * 100
        approaching = -cfg.hma_near_pct <= gap_pct < 0
        just_crossed = gap_pct >= 0 and bool(bar["was_below"])
        if not (approaching or just_crossed):
            continue

        return PullbackSignal(
            found=True, anchor_at=ts, anchor_body_pct=round(body_pct, 1),
            anchor_hma_gap_pct=round(float(gap_pct), 3),
            note=(
                f"30m green candle {body_pct:.0f}% body at {ts:%H:%M}, HMA{cfg.hma_period} "
                f"rising and {'just above' if just_crossed else 'approaching'} the band "
                f"({gap_pct:+.2f}%)"
            ),
        )

    return PullbackSignal(note="no qualifying 30m anchor before the cutoff")


# ── Stage 2: the lower-timeframe pullback and the trade ───────────────────────


def find_pullback_entry(
    m5: pd.DataFrame, anchor: PullbackSignal, cfg: PullbackSettings | None = None
) -> PullbackSignal:
    """The first pullback to the middle band after the anchor, priced as a trade.

    A "pullback on the BB middle band" is read as: the candle trades down into the band
    (its low reaches it) and still closes above it — a touch that held. A candle that closes
    *below* the band has broken it rather than pulled back to it, and is not an entry.
    """
    cfg = cfg or PullbackSettings()
    if anchor.anchor_at is None:
        return PullbackSignal(note="no anchor")
    if m5 is None or len(m5) < cfg.entry_bb_period + 5:
        return PullbackSignal(note="insufficient entry-timeframe history")

    frame = m5.copy()
    frame["bb_mid"] = bollinger_middle(frame["close"], cfg.entry_bb_period)

    # Strictly after the anchor bar has *closed*, never during it. The anchor is a 30-minute
    # candle; taking an entry from inside it would use information that did not exist when
    # the candle completed.
    anchor_close = anchor.anchor_at + pd.Timedelta(minutes=30)
    forward = frame[frame.index >= anchor_close]
    forward = forward[forward.index.date == anchor.anchor_at.date()]
    if forward.empty:
        return PullbackSignal(note="no entry-timeframe bars after the anchor")

    for offset, (ts, bar) in enumerate(forward.iterrows()):
        if offset >= cfg.max_bars_to_entry:
            break
        mid = float(bar["bb_mid"])
        if not np.isfinite(mid):
            continue

        touched = float(bar["low"]) <= mid
        held = float(bar["close"]) > mid
        green = float(bar["close"]) > float(bar["open"])
        if not (touched and held and green):
            continue

        entry = float(bar["close"])
        stop = float(bar["low"])
        risk = entry - stop
        if risk <= 0:
            continue
        risk_pct = risk / entry * 100
        if risk_pct < cfg.min_risk_pct:
            continue
        if risk_pct > cfg.max_risk_pct:
            # Refused rather than re-priced. Moving the stop to fit the cap would make the
            # 0.7% rule decorative — the same discipline V3 applies to its stop band.
            continue

        return PullbackSignal(
            found=True,
            anchor_at=anchor.anchor_at,
            anchor_body_pct=anchor.anchor_body_pct,
            anchor_hma_gap_pct=anchor.anchor_hma_gap_pct,
            entry_at=ts,
            entry=round(entry, 2),
            stop=round(stop, 2),
            target=round(entry + cfg.reward_risk * risk, 2),
            risk_pct=round(risk_pct, 3),
            note=(
                f"{anchor.note}; pulled back to the {cfg.entry_interval} band at "
                f"{ts:%H:%M} and held, risk {risk_pct:.2f}%"
            ),
        )

    return PullbackSignal(
        anchor_at=anchor.anchor_at,
        note="anchor formed but no pullback held the band inside the risk cap",
    )


def detect_pullback_setup(
    h30: pd.DataFrame, m5: pd.DataFrame, session: date,
    cfg: PullbackSettings | None = None, symbol: str = "",
) -> PullbackSignal:
    """Both stages for one symbol on one session."""
    cfg = cfg or PullbackSettings()
    anchor = find_anchor(h30, session, cfg)
    if not anchor.found:
        anchor.symbol = symbol
        return anchor
    signal = find_pullback_entry(m5, anchor, cfg)
    signal.symbol = symbol
    return signal


# ── Backtest ──────────────────────────────────────────────────────────────────


@dataclass
class PullbackTrade:
    symbol: str
    entered_at: pd.Timestamp
    entry: float
    stop: float
    target: float
    risk_pct: float
    outcome: str = "open"          # target | stop | square-off
    resolved_at: pd.Timestamp | None = None
    bars_held: int = 0
    realised_r: float = 0.0
    cost_r: float = 0.0

    @property
    def net_r(self) -> float:
        return self.realised_r - self.cost_r


@dataclass
class PullbackResult:
    trades: list[PullbackTrade] = field(default_factory=list)
    symbols_tested: int = 0
    sessions_spanned: int = 0
    anchors_found: int = 0
    entries_missed: int = 0        # anchor fired, no qualifying pullback

    @property
    def resolved(self) -> list[PullbackTrade]:
        return [t for t in self.trades if t.outcome in ("target", "stop")]

    @property
    def win_rate(self) -> float:
        decided = self.resolved
        if not decided:
            return float("nan")
        return sum(t.outcome == "target" for t in decided) / len(decided) * 100

    @property
    def expectancy_r(self) -> float:
        return float(np.mean([t.realised_r for t in self.trades])) if self.trades else float("nan")

    @property
    def net_expectancy_r(self) -> float:
        return float(np.mean([t.net_r for t in self.trades])) if self.trades else float("nan")

    @property
    def total_r(self) -> float:
        return float(sum(t.net_r for t in self.trades))

    @property
    def break_even_win_rate(self) -> float:
        """At 3R with a 1R loss, break-even before costs is 1/(3+1) = 25%."""
        return 100.0 / (settings.min_reward_risk_pullback + 1.0) if hasattr(
            settings, "min_reward_risk_pullback"
        ) else 25.0


def _cost_r(risk_pct: float) -> float:
    """Round-trip cost expressed in R for *this* trade's stop distance.

    A fixed cost-in-R constant would be wrong here. The strategy caps risk at 0.7%, so the
    same 0.17% of round-trip friction is 0.24R — nearly a quarter of every unit risked, and
    materially worse than the ~0.17R V3 carries on a 1% stop.
    """
    if risk_pct <= 0:
        return 0.0
    return (settings.cost_roundtrip_pct + settings.slippage_pct) / risk_pct


def resolve_trade(
    m5: pd.DataFrame, signal: PullbackSignal, cfg: PullbackSettings
) -> PullbackTrade | None:
    """Walk forward on entry-timeframe bars to the stop, the target, or the close.

    A bar touching both stop and target books a **loss**, the same rule V3 uses: the order
    of events inside a bar is unknown, and resolving that ambiguity favourably is the
    classic way a backtest manufactures an edge.
    """
    if not signal.found or signal.entry_at is None:
        return None

    session_bars = m5[m5.index.date == signal.entry_at.date()]
    forward = session_bars[session_bars.index > signal.entry_at]
    risk = signal.risk
    trade = PullbackTrade(
        symbol=signal.symbol, entered_at=signal.entry_at, entry=signal.entry,
        stop=signal.stop, target=signal.target, risk_pct=signal.risk_pct,
        cost_r=_cost_r(signal.risk_pct),
    )
    if risk <= 0:
        return None

    for offset, (ts, bar) in enumerate(forward.iterrows(), start=1):
        trade.bars_held = offset
        hit_stop = float(bar["low"]) <= signal.stop
        hit_target = float(bar["high"]) >= signal.target
        if hit_stop:
            trade.outcome, trade.realised_r, trade.resolved_at = "stop", -1.0, ts
            return trade
        if hit_target:
            trade.outcome, trade.realised_r, trade.resolved_at = "target", cfg.reward_risk, ts
            return trade

    if not len(forward):
        return None

    # Unresolved by the close: marked out at the last print rather than carried.
    last_ts = forward.index[-1]
    last_close = float(forward["close"].iloc[-1])
    trade.outcome = "square-off"
    trade.realised_r = (last_close - signal.entry) / risk
    trade.resolved_at = last_ts
    return trade


def backtest_pullback(
    symbols: list[str] | None = None,
    *,
    max_symbols: int = 40,
    cfg: PullbackSettings | None = None,
) -> PullbackResult:
    """Replay the strategy over the available intraday history.

    Universe is the NIFTY 200, as specified. History is whatever the feed serves — roughly
    58 sessions of 30m and 5m — so this is one market period and a small sample by
    construction.
    """
    from ..data import universe as universe_mod, yahoo

    cfg = cfg or PullbackSettings()
    if symbols is None:
        constituents = universe_mod.load_universe("nifty200")
        symbols = sorted(constituents)[:max_symbols] if max_symbols else sorted(constituents)

    result = PullbackResult()
    logger.info(f"[pullback] replaying {len(symbols)} NIFTY 200 names")

    sessions_seen: set[date] = set()
    for position, symbol in enumerate(symbols, 1):
        ysym = yahoo.to_yahoo_symbol(symbol)
        h30 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.anchor_interval)
        if h30 is None or h30.empty:
            continue
        m5 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.entry_interval)
        if m5 is None or m5.empty:
            continue

        result.symbols_tested += 1
        for session in sorted({ts.date() for ts in h30.index}):
            sessions_seen.add(session)
            signal = detect_pullback_setup(h30, m5, session, cfg, symbol=symbol)
            if signal.anchor_at is not None:
                result.anchors_found += 1
            if not signal.found:
                if signal.anchor_at is not None:
                    result.entries_missed += 1
                continue
            trade = resolve_trade(m5, signal, cfg)
            if trade is not None:
                result.trades.append(trade)

        if position % 10 == 0:
            logger.info(
                f"[pullback] {position}/{len(symbols)} symbols, "
                f"{len(result.trades)} trades so far"
            )

    result.sessions_spanned = len(sessions_seen)
    logger.info(
        f"[pullback] {len(result.trades)} trades from {result.symbols_tested} symbols, "
        f"{result.anchors_found} anchors, {result.entries_missed} without an entry"
    )
    return result


# ── Live scan ─────────────────────────────────────────────────────────────────


def scan_pullback(
    as_of: date | None = None,
    *,
    symbols: list[str] | None = None,
    max_symbols: int = 0,
    cfg: PullbackSettings | None = None,
) -> list[PullbackSignal]:
    """Today's qualifying setups across the NIFTY 200, best risk first."""
    from ..data import nse_archive, universe as universe_mod, yahoo

    cfg = cfg or PullbackSettings()
    as_of = as_of or nse_archive.last_trading_day() or date.today()
    if symbols is None:
        constituents = universe_mod.load_universe("nifty200")
        symbols = sorted(constituents)
        if max_symbols:
            symbols = symbols[:max_symbols]

    found: list[PullbackSignal] = []
    for position, symbol in enumerate(symbols, 1):
        ysym = yahoo.to_yahoo_symbol(symbol)
        h30 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.anchor_interval, as_of=as_of)
        if h30 is None or h30.empty:
            continue
        m5 = yahoo.fetch_chart(ysym, range_="60d", interval=cfg.entry_interval, as_of=as_of)
        if m5 is None or m5.empty:
            continue
        signal = detect_pullback_setup(h30, m5, as_of, cfg, symbol=symbol)
        if signal.found:
            found.append(signal)
        if position % 25 == 0:
            logger.info(f"[pullback] scanned {position}/{len(symbols)}, {len(found)} found")

    found.sort(key=lambda s: s.risk_pct)
    return found
