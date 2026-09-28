"""The trident's own universe gate: turnover and estimated spread, run before anything else.

Expanding from the NIFTY 200 to the NIFTY 500 is not a matter of changing one string. The
tail of the 500 does not print continuously inside a 5- or 30-minute window, and a bar with
no trades has a low equal to its high — which the fair-value-gap detector reads as a perfect
imbalance. **Those are data holes, not liquidity voids.** Admitted, they would dominate the
forward log with setups that could never have been filled, and they would do it while
looking exactly like the real thing.

So liquidity is the first filter, and it runs on EOD bhavcopy rather than on intraday bars:
one file per day covers the whole market, against 500 paced Yahoo fetches at ~1.2s each.
Screening 500 names costs 20 archive reads, nearly all of them already cached.

**This does not reuse `data.universe.apply_liquidity_gate`.** That one reads
`settings.min_median_turnover_inr` — ₹5 crore, calibrated for a V3 swing on a daily bar —
and V3 is a deployed engine. Sharing the constant would mean the next time this threshold is
tuned for a 5-minute bar, the daily brief silently retunes with it. Same reasoning as the
cost constants: a threshold is calibrated for a holding period and a timeframe, and
inheriting one across strategies is how a deployed engine changes by accident.

Two things this module refuses to do quietly:

* **A failed fetch is reported, not skipped.** The 26 Aug run dropped a symbol silently and
  moved the trade count from 22 to 21 while the report said only "199 symbols" — a shrinking
  denominator wearing the clothes of a stable measurement. Every missing name is named.
* **The spread number is an estimate, says so, and does not gate.** There is no bid/ask feed
  reachable from here at any price: NSE is Akamai-blocked and Yahoo serves OHLCV only. What
  is computed is the Corwin-Schultz two-day high/low estimator — a well-established proxy,
  and still a proxy. It is never labelled "spread" without "estimated" attached.

  Measured on the whole NIFTY 500 over 20 sessions to 28 Aug 2026, it does not do the job a
  spread filter has to do here. It correlates **-0.15** with median turnover and **+0.31**
  with median daily range, so it ranks by volatility roughly twice as strongly as by
  illiquidity. A 25bp cap refuses 296 of 500 names and 40 of the NIFTY 50, ICICIBANK
  (₹1,209 cr/day) and BHARTIARTL (₹1,077 cr/day) among them, while the widest estimates
  belong to the most volatile names rather than the thinnest. No threshold fixes that: one
  loose enough to admit ICICIBANK admits everything.

  So it is computed on every row, printed on every surface, and **off by default**.
  `require_spread_estimate` arms it. Turnover, which is a direct observation rather than an
  inference, is what actually removes the tail. This is the same disposition as the catalyst
  filter in V3: kept, reported, and disarmed because the measurement does not support it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from loguru import logger

from .trident import TridentSettings


@dataclass
class LiquidityRow:
    symbol: str
    median_turnover: float = 0.0        # INR/day, median over the lookback
    spread_bps: float = float("nan")    # Corwin-Schultz estimate
    days_traded: int = 0
    passed: bool = False
    reason: str = ""                    # why it was refused, with the numbers


@dataclass
class LiquidityScreen:
    """The gate's verdict for a whole universe, plus the accounting to argue with it."""

    rows: list[LiquidityRow] = field(default_factory=list)
    considered: int = 0
    missing: list[str] = field(default_factory=list)   # in the index, absent from bhavcopy
    days_used: int = 0
    days_requested: int = 0
    days_missing: list[date] = field(default_factory=list)

    @property
    def passing(self) -> list[str]:
        return [r.symbol for r in self.rows if r.passed]

    @property
    def refused(self) -> list[LiquidityRow]:
        return [r for r in self.rows if not r.passed]

    def reason_counts(self) -> dict[str, int]:
        """Refusals bucketed by cause, for the funnel."""
        counts: dict[str, int] = {}
        for row in self.refused:
            bucket = row.reason.split(":")[0]
            counts[bucket] = counts.get(bucket, 0) + 1
        if self.missing:
            counts["no EOD data"] = len(self.missing)
        return counts


def corwin_schultz_bps(high: pd.Series, low: pd.Series) -> float:
    """Median estimated effective spread in basis points, from consecutive daily bars.

    Corwin & Schultz (2012). The estimator reads the spread out of the fact that a bar's
    high is usually a buy at the ask and its low a sell at the bid, so a single bar's range
    is inflated by the spread while a two-bar range is inflated by the same spread only once.
    Solving the two against each other separates spread from volatility.

    Negative estimates are a known and frequent output of the estimator on quiet days; they
    are floored at zero rather than discarded, because dropping them selectively keeps only
    the wide days and biases every stock's number upward.

    This is an estimate from OHLC. It is not a quoted spread, and no surface may print it as
    one — there is no quote data available to this project.
    """
    if high is None or len(high) < 2:
        return float("nan")
    h = high.to_numpy(dtype=float)
    l = low.to_numpy(dtype=float)
    ok = (h > 0) & (l > 0)
    h, l = h[ok], l[ok]
    if len(h) < 2:
        return float("nan")

    beta = np.log(h[1:] / l[1:]) ** 2 + np.log(h[:-1] / l[:-1]) ** 2
    h2 = np.maximum(h[1:], h[:-1])
    l2 = np.minimum(l[1:], l[:-1])
    gamma = np.log(h2 / l2) ** 2

    k = 3 - 2 * np.sqrt(2)
    alpha = (np.sqrt(2 * beta) - np.sqrt(beta)) / k - np.sqrt(gamma / k)
    spread = 2 * (np.exp(alpha) - 1) / (1 + np.exp(alpha))
    spread = np.where(np.isfinite(spread), np.maximum(spread, 0.0), np.nan)
    if not np.isfinite(spread).any():
        return float("nan")
    return float(np.nanmedian(spread) * 10_000)


def screen_liquidity(
    symbols: list[str],
    cfg: TridentSettings,
    as_of: date | None = None,
) -> LiquidityScreen:
    """Turnover and estimated spread for every name, from stacked bhavcopies.

    Returns the screen rather than just the survivors, so the funnel can report how many
    names each condition removed. A gate that reports only what it admitted cannot be
    audited — the interesting number is nearly always which condition did the rejecting.
    """
    from ..data import nse_archive

    screen = LiquidityScreen(considered=len(symbols))
    end = as_of or nse_archive.last_trading_day() or date.today()
    # Ask for a calendar span wide enough to contain the lookback in trading days. Weekends
    # and holidays are resolved by probing for a published bhavcopy — there is no forward NSE
    # holiday calendar, so counting weekdays is the only thing available and it over-asks.
    span_start = end - timedelta(days=int(cfg.liquidity_lookback_days * 1.8) + 7)
    days = nse_archive.trading_days(span_start, end)[-cfg.liquidity_lookback_days:]
    screen.days_requested = cfg.liquidity_lookback_days

    frames, missing_days = [], []
    for day in days:
        frame = nse_archive.fetch_cm_bhavcopy(day)
        if frame is None or frame.empty:
            missing_days.append(day)
            continue
        frames.append(frame)
    screen.days_used = len(frames)
    screen.days_missing = missing_days

    if not frames:
        # Refusing the whole universe on a broken archive would render as a selective day.
        # Same principle as the catalyst filter's `outage` state: a dead feed must never look
        # like a decision.
        logger.error(
            "[trident-universe] no bhavcopy for any of the last "
            f"{cfg.liquidity_lookback_days} sessions — liquidity gate cannot run"
        )
        return screen

    if missing_days:
        logger.warning(
            f"[trident-universe] {len(missing_days)} session(s) missing from the archive: "
            + ", ".join(str(d) for d in missing_days)
        )

    history = pd.concat(frames, ignore_index=True)
    history = history[history["symbol"].isin(set(symbols))]
    grouped = {sym: block for sym, block in history.groupby("symbol")}

    for symbol in symbols:
        block = grouped.get(symbol)
        if block is None or block.empty:
            screen.missing.append(symbol)
            continue

        row = LiquidityRow(symbol=symbol, days_traded=len(block))
        row.median_turnover = float(block["turnover"].median())
        row.spread_bps = corwin_schultz_bps(
            block.sort_values("date")["high"], block.sort_values("date")["low"]
        )

        # Three conditions, checked in the order a reader would ask them.
        min_days = max(1, int(0.8 * screen.days_used))
        if row.days_traded < min_days:
            row.reason = (
                f"intermittent: traded {row.days_traded} of {screen.days_used} sessions, "
                f"needs {min_days}"
            )
        elif row.median_turnover < cfg.min_median_turnover_inr:
            row.reason = (
                f"turnover: ₹{row.median_turnover / 1e7:.2f} cr/day median, floor "
                f"₹{cfg.min_median_turnover_inr / 1e7:.0f} cr"
            )
        elif cfg.require_spread_estimate and not np.isfinite(row.spread_bps):
            row.reason = "spread: estimator returned nothing on this history"
        elif cfg.require_spread_estimate and row.spread_bps > cfg.max_spread_bps:
            row.reason = (
                f"spread: {row.spread_bps:.1f}bp estimated, cap {cfg.max_spread_bps:.0f}bp"
            )
        else:
            row.passed = True
        screen.rows.append(row)

    if screen.missing:
        logger.warning(
            f"[trident-universe] {len(screen.missing)} index member(s) absent from the "
            f"bhavcopy: {', '.join(sorted(screen.missing)[:12])}"
            + (" …" if len(screen.missing) > 12 else "")
        )
    logger.info(
        f"[trident-universe] {len(screen.passing)}/{screen.considered} names cleared the "
        f"floor (₹{cfg.min_median_turnover_inr / 1e7:.0f} cr, ≤{cfg.max_spread_bps:.0f}bp "
        f"estimated) over {screen.days_used} sessions"
    )
    return screen


# ── Fetching, with the failures made loud ─────────────────────────────────────


@dataclass
class FetchReport:
    """What the intraday fetch actually got, and what it did not.

    A silent drop is the failure this exists to prevent: two runs of the same command over
    the same window disagreeing because a transient throttle removed a name, with nothing in
    the output to say so.
    """

    requested: int = 0
    intraday_failed: list[str] = field(default_factory=list)
    daily_failed: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)   # succeeded only on a retry

    @property
    def failed(self) -> list[str]:
        return sorted(set(self.intraday_failed) | set(self.daily_failed))

    @property
    def ok(self) -> int:
        return self.requested - len(self.failed)

    def summary(self) -> str:
        if not self.failed:
            base = f"{self.ok}/{self.requested} symbols fetched cleanly"
            return base + (f"; {len(self.recovered)} needed a retry" if self.recovered else "")
        return (
            f"{self.ok}/{self.requested} symbols fetched; "
            f"{len(self.failed)} MISSING: {', '.join(self.failed[:15])}"
            + (" …" if len(self.failed) > 15 else "")
        )


def fetch_pair(
    symbol: str,
    cfg: TridentSettings,
    report: FetchReport,
    *,
    as_of: date | None = None,
    intraday_range: str = "60d",
    daily_range: str = "2y",
    retries: int = 2,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    """Entry-timeframe and daily frames for one symbol, retried and recorded.

    `PacedClient` already retries the HTTP call, but its retries do not cover the case that
    actually bites here: a 200 response carrying an HTML throttle page, which parses to None
    rather than raising. That returns cleanly and looks like "no data for this symbol". So a
    second attempt happens at this level too, and either way the outcome is recorded by name.
    """
    from ..data import yahoo

    ysym = yahoo.to_yahoo_symbol(symbol)
    report.requested += 1

    intraday = daily = None
    for attempt in range(1, retries + 1):
        intraday = yahoo.fetch_chart(
            ysym, range_=intraday_range, interval=cfg.anchor_interval, as_of=as_of
        )
        if intraday is not None and not intraday.empty:
            if attempt > 1:
                report.recovered.append(symbol)
            break
        logger.debug(f"[trident] {symbol}: no {cfg.anchor_interval} bars, attempt {attempt}")
    else:
        report.intraday_failed.append(symbol)
        return None, None

    for attempt in range(1, retries + 1):
        daily = yahoo.fetch_chart(ysym, range_=daily_range, interval="1d", as_of=as_of)
        if daily is not None and not daily.empty:
            break
    else:
        report.daily_failed.append(symbol)
        return intraday, None

    return intraday, daily


def resolve_universe(
    cfg: TridentSettings,
    *,
    universe: str = "nifty500",
    symbols: list[str] | None = None,
    max_symbols: int = 0,
    as_of: date | None = None,
    apply_liquidity: bool = True,
) -> tuple[list[str], LiquidityScreen | None]:
    """The symbol list the scan will actually run on, plus the screen that produced it.

    `max_symbols` truncates *after* the liquidity gate, so capping the universe for a quick
    run gives the most liquid slice rather than an alphabetical one.
    """
    from ..data import universe as universe_mod

    if symbols is not None:
        return (symbols[:max_symbols] if max_symbols else symbols), None

    constituents = universe_mod.load_universe(universe)
    if not constituents:
        logger.error(f"[trident-universe] {universe} constituent list unavailable")
        return [], None
    names = sorted(constituents)

    if not apply_liquidity:
        return (names[:max_symbols] if max_symbols else names), None

    screen = screen_liquidity(names, cfg, as_of=as_of)
    if screen.days_used == 0:
        # The gate could not run. Proceeding with the whole 500 would fill the log with data
        # holes; refusing everything would render a feed outage as a quiet day. Neither is
        # acceptable silently, so the caller is handed an empty list *and* the screen showing
        # why, and every surface reports it as an outage rather than a result.
        return [], screen

    ranked = sorted(
        (r for r in screen.rows if r.passed), key=lambda r: -r.median_turnover
    )
    passing = [r.symbol for r in ranked]
    return (passing[:max_symbols] if max_symbols else passing), screen
