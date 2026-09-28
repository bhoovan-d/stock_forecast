"""Liquidity pools and sweeps — context for the trident, tagged and never gated.

The thesis being tested is that a setup forming *after* price has taken out a pool of
resting orders is worth more than one forming in isolation: the sweep is what supplies the
fuel, and a gap that prints with nothing behind it is a gap into thin air.

**Nothing here rejects anything.** That is a decision, not an oversight. The trident's
measured sample is 22 setups over 59 sessions; a filter applied to it today would shrink an
already tiny sample on an assumption that has never been tested on Indian equities. So each
signal is *tagged* — which pool was taken, how many bars before the gap, how far the level
sat from the gap's midpoint — and the comparison between tagged and untagged outcomes is
made later, once the forward record has enough of both. Tag now, filter never until the
numbers say so.

Five pool families, which between them cover what actually holds resting orders on an NSE
equity:

    equal highs / equal lows     two or more touches inside a tolerance band
    prior day high / low         completed prior session only
    prior week high / low        completed prior week only
    opening range high / low     the session's first `opening_range_minutes`
    swing highs / lows           daily pivots from the higher timeframe

**Anti-lookahead is the whole game here and it is easy to lose.** A pool is only a pool once
the bars that formed it have printed, and a sweep is only a sweep once the reclaiming candle
has closed. Every constructor below takes an explicit cutoff and every one of them uses
*completed* bars strictly before it. Building the prior-day pool from `daily.iloc[-1]` when
the frame ends today, for instance, would hand the detector the session it is standing in —
which is the same class of error as naming a setup after the move it is trying to catch.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import numpy as np
import pandas as pd

from .trident import SESSION_OPEN, TridentSettings


@dataclass(frozen=True)
class LiquidityPool:
    """A price level where resting orders are likely to sit.

    `side` is which side of the book the pool represents, not the direction of the trade
    that follows it: a *low* holds sell-side liquidity (protective stops under it), and the
    long setup wants that taken before the gap forms.
    """

    kind: str                       # equal-lows | prior-day-low | swing-high | ...
    side: str                       # "sell" for lows, "buy" for highs
    level: float
    label: str                      # human form, e.g. "equal lows (3 touches)"
    formed_at: pd.Timestamp | None = None
    touches: int = 1


@dataclass(frozen=True)
class Sweep:
    """A pool taken and given back: price wicked through, then closed back on the origin side.

    `bars_to_reclaim` is the N in the rule — 0 means the sweeping candle itself closed back
    across, which is the cleanest form.
    """

    pool: LiquidityPool
    at: pd.Timestamp                # the bar that wicked through
    reclaimed_at: pd.Timestamp      # the bar that closed back on the origin side
    bars_to_reclaim: int
    excursion_pct: float            # how far beyond the level price actually traded

    @property
    def label(self) -> str:
        return self.pool.label


# ── Pool construction ─────────────────────────────────────────────────────────


def _pivots(frame: pd.DataFrame, strength: int, low_side: bool) -> list[tuple[pd.Timestamp, float]]:
    """Fractal pivots: a bar whose extreme is the most extreme of the `strength` bars either
    side. Both neighbourhoods are required, so a pivot is only known `strength` bars after it
    printed — which is correct, and is why the caller must not treat the last `strength` bars
    as pivot-bearing."""
    if frame is None or len(frame) < 2 * strength + 1:
        return []
    series = frame["low"] if low_side else frame["high"]
    values = series.to_numpy(dtype=float)
    out: list[tuple[pd.Timestamp, float]] = []
    for i in range(strength, len(values) - strength):
        window = values[i - strength : i + strength + 1]
        centre = values[i]
        if low_side and centre == window.min() and (window > centre).sum() >= strength:
            out.append((frame.index[i], float(centre)))
        elif not low_side and centre == window.max() and (window < centre).sum() >= strength:
            out.append((frame.index[i], float(centre)))
    return out


def _equal_levels(
    pivots: list[tuple[pd.Timestamp, float]],
    tolerance_pct: float,
    min_touches: int,
    low_side: bool,
) -> list[LiquidityPool]:
    """Cluster pivots that sit within a tolerance band of each other.

    The tolerance is a percentage of price rather than an absolute, so "equal" means the same
    thing on an ₹80 stock and an ₹8,000 one. A cluster's level is taken at its *extreme*
    member, not its mean: the stops rest beyond the furthest touch, so that is the price a
    sweep has to trade through to have taken anything.
    """
    if len(pivots) < min_touches:
        return []
    ordered = sorted(pivots, key=lambda p: p[1])
    pools: list[LiquidityPool] = []
    cluster: list[tuple[pd.Timestamp, float]] = [ordered[0]]

    def close_cluster(members: list[tuple[pd.Timestamp, float]]) -> None:
        if len(members) < min_touches:
            return
        level = min(v for _, v in members) if low_side else max(v for _, v in members)
        # The pool exists only once its last touch has printed.
        formed = max(ts for ts, _ in members)
        pools.append(
            LiquidityPool(
                kind="equal-lows" if low_side else "equal-highs",
                side="sell" if low_side else "buy",
                level=level,
                label=f"equal {'lows' if low_side else 'highs'} ({len(members)} touches)",
                formed_at=formed,
                touches=len(members),
            )
        )

    for ts, value in ordered[1:]:
        anchor = cluster[0][1]
        if anchor > 0 and abs(value - anchor) / anchor * 100 <= tolerance_pct:
            cluster.append((ts, value))
        else:
            close_cluster(cluster)
            cluster = [(ts, value)]
    close_cluster(cluster)
    return pools


def _prior_session_pools(daily: pd.DataFrame, session: date) -> list[LiquidityPool]:
    """Prior day and prior week extremes, from completed days only.

    The week is the *previous* ISO week, not the last five sessions: a trader watching
    "prior week high" means last week's, and a rolling five-day window would drift into the
    current week and quietly include today.
    """
    if daily is None or daily.empty:
        return []
    prior = daily[daily.index.date < session]
    if prior.empty:
        return []

    pools: list[LiquidityPool] = []
    last = prior.iloc[-1]
    last_ts = prior.index[-1]
    pools.append(LiquidityPool("prior-day-low", "sell", float(last["low"]),
                               "prior day low", last_ts))
    pools.append(LiquidityPool("prior-day-high", "buy", float(last["high"]),
                               "prior day high", last_ts))

    iso = pd.Timestamp(session).isocalendar()
    weeks = pd.DataFrame(
        {"year": [ts.isocalendar()[0] for ts in prior.index],
         "week": [ts.isocalendar()[1] for ts in prior.index]},
        index=prior.index,
    )
    current = (iso[0], iso[1])
    earlier = [(y, w) for y, w in zip(weeks["year"], weeks["week"]) if (y, w) < current]
    if earlier:
        target = max(earlier)
        mask = (weeks["year"] == target[0]) & (weeks["week"] == target[1])
        block = prior[mask.to_numpy()]
        if not block.empty:
            pools.append(LiquidityPool("prior-week-low", "sell", float(block["low"].min()),
                                       "prior week low", block.index[-1]))
            pools.append(LiquidityPool("prior-week-high", "buy", float(block["high"].max()),
                                       "prior week high", block.index[-1]))
    return pools


def _opening_range_pools(
    day: pd.DataFrame, minutes: int, session_open: time = SESSION_OPEN
) -> list[LiquidityPool]:
    """The session's opening range. Only bars that have *closed* inside the window count."""
    if day is None or day.empty:
        return []
    cutoff = (
        pd.Timestamp.combine(pd.Timestamp(day.index[0].date()), session_open)
        + pd.Timedelta(minutes=minutes)
    )
    if day.index.tz is not None:
        cutoff = cutoff.tz_localize(day.index.tz)
    block = day[day.index < cutoff]
    if block.empty:
        return []
    return [
        LiquidityPool("opening-range-low", "sell", float(block["low"].min()),
                      f"opening-range low (first {minutes}m)", block.index[-1]),
        LiquidityPool("opening-range-high", "buy", float(block["high"].max()),
                      f"opening-range high (first {minutes}m)", block.index[-1]),
    ]


def build_pools(
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    session: date,
    cfg: TridentSettings,
) -> list[LiquidityPool]:
    """Every pool visible to a trader standing inside `session`, on both sides.

    `intraday` is the full entry-timeframe frame; only bars up to the end of `session` are
    used, and the daily-derived pools use completed prior sessions only.
    """
    pools: list[LiquidityPool] = []
    pools += _prior_session_pools(daily, session)

    # Daily swings from the higher timeframe, completed prior sessions only.
    prior_daily = daily[daily.index.date < session].tail(cfg.swing_lookback_sessions) if (
        daily is not None and not daily.empty
    ) else pd.DataFrame()
    for low_side in (True, False):
        for ts, level in _pivots(prior_daily, cfg.swing_pivot_strength, low_side):
            pools.append(
                LiquidityPool(
                    kind="swing-low" if low_side else "swing-high",
                    side="sell" if low_side else "buy",
                    level=level,
                    label=f"daily swing {'low' if low_side else 'high'} {ts:%d %b}",
                    formed_at=ts,
                )
            )

    if intraday is None or intraday.empty:
        return pools

    day = intraday[intraday.index.date == session]
    pools += _opening_range_pools(day, cfg.opening_range_minutes, cfg.killzone_start)

    # Equal highs/lows on the entry timeframe. The lookback spans prior sessions as well as
    # this one: a pool formed yesterday afternoon is exactly the kind that gets taken at
    # today's open, and restricting this to the current session would miss most of them.
    upto = intraday[intraday.index.date <= session].tail(cfg.pool_lookback_bars)
    for low_side in (True, False):
        pivots = _pivots(upto, cfg.swing_pivot_strength, low_side)
        pools += _equal_levels(
            pivots, cfg.equal_level_tolerance_pct, cfg.equal_level_min_touches, low_side
        )
    return pools


# ── Sweep detection ───────────────────────────────────────────────────────────


def find_sweeps(
    bars: pd.DataFrame, pools: list[LiquidityPool], cfg: TridentSettings
) -> list[Sweep]:
    """Every pool taken and given back inside `bars`.

    The rule, with N = `cfg.sweep_max_candles`: price wicks through the level and closes back
    on the origin side within N candles. For a low that means trading below it and then
    closing back above; a bar that trades below and stays below has *broken* the level, not
    swept it, and the two mean opposite things.

    A pool cannot be swept before it exists, so intraday pools carrying a `formed_at` are
    only tested against later bars.
    """
    if bars is None or bars.empty or not pools:
        return []

    index = list(bars.index)
    lows = bars["low"].to_numpy(dtype=float)
    highs = bars["high"].to_numpy(dtype=float)
    closes = bars["close"].to_numpy(dtype=float)
    sweeps: list[Sweep] = []

    for pool in pools:
        level = pool.level
        if not np.isfinite(level) or level <= 0:
            continue
        start = 0
        if pool.formed_at is not None:
            later = [k for k, ts in enumerate(index) if ts > pool.formed_at]
            if not later:
                continue
            start = later[0]

        for i in range(start, len(index)):
            through = lows[i] < level if pool.side == "sell" else highs[i] > level
            if not through:
                continue
            # ...and back across within N candles, the sweeping candle itself counting as 0.
            for k in range(0, cfg.sweep_max_candles + 1):
                j = i + k
                if j >= len(index):
                    break
                back = closes[j] > level if pool.side == "sell" else closes[j] < level
                if back:
                    excursion = (
                        (level - lows[i]) if pool.side == "sell" else (highs[i] - level)
                    )
                    sweeps.append(
                        Sweep(
                            pool=pool,
                            at=index[i],
                            reclaimed_at=index[j],
                            bars_to_reclaim=k,
                            excursion_pct=round(excursion / level * 100, 3),
                        )
                    )
                    break
            break  # one sweep per pool: the first time it was taken is the one that matters
    return sweeps


def sweep_before(
    sweeps: list[Sweep], cutoff: pd.Timestamp, side: str = "sell"
) -> Sweep | None:
    """The most recent qualifying sweep fully completed before `cutoff`.

    "Fully completed" means the *reclaim* closed before the cutoff, not merely the wick. A
    sweep whose reclaim has not printed yet is not yet a sweep, and counting it would be the
    same lookahead the gap detector is careful to avoid.

    For a long setup the side wanted is `sell` — a low taken — because that is the pool whose
    removal supplies the buying that drives the imbalance.
    """
    candidates = [
        s for s in sweeps if s.pool.side == side and s.reclaimed_at <= cutoff
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda s: s.reclaimed_at)


def bars_between(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> int:
    """How many bars of the entry timeframe separate two timestamps.

    Counted in bars rather than minutes because the answer has to mean the same thing on the
    5-minute track as on the 30-minute one — "three candles back" is the trader's unit here.
    """
    if bars is None or bars.empty:
        return 0
    inside = bars[(bars.index > start) & (bars.index <= end)]
    return len(inside)
