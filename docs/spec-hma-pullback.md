# Intraday HMA pullback — specification

*20 Aug 2026. Owner's strategy, built as a separate section of the product.*

Engine: `engines/hma_pullback.py`. Surfaces: `report/pullback_report.py`.
Commands: `asymmetry pullback`, `asymmetry pullback-backtest`.

**This is deliberately not part of V3 and nothing is shared between them.** V3 is a 1–5
session swing engine on the NIFTY 500 at 4R with a 0.5–1.5% stop; this is an intraday trade
on the NIFTY 200 at 3R with a 0.7% cap. A shared constant would silently retune one when the
other is edited.

---

## As specified

> 1. A bullish green candle with 75 percent body at 30 mins chart of Nifty 200 stocks
>    forming before 1 o'clock.
> 2. The Hull Moving average 9 is sloping slightly upwards and almost cutting BB middle band
>    upwards or about to.
> 3. Then at 3 minutes timeframe or 5 mins timeframe, there is a pullback on BB middle band.
> 4. We take an entry the 3/5 min candle. Stoploss on low of candle. Target is 3 is to 1.
>    Max risk is .7 percent of the stock.

## As implemented

**Stage 1 — the 30-minute anchor.** Scanning 30m bars whose start time is before 13:00:

- green: `close > open`
- body: `(close − open) / (high − low) ≥ 75%`
- `HMA(9)` rising: `hma[i] > hma[i−1]`
- HMA relative to the Bollinger middle band (20-period SMA), one of:
  - **approaching**: `−0.50% ≤ (hma − mid)/mid < 0`, or
  - **just crossed**: `hma ≥ mid` *and* it was below within the last 3 bars

The first bar of the session that satisfies all of these is the anchor.

**Stage 2 — the 5-minute pullback**, strictly after the anchor candle has closed, same
session, within 40 bars:

- the candle's low reaches the 5m middle band (`low ≤ mid`)
- and it closes back above it (`close > mid`)
- and it is green (`close > open`)

**Stage 3 — the trade.**

    entry  = close of that candle
    stop   = low of that candle
    risk%  = (entry − stop) / entry × 100        must be ≤ 0.7%, else refused
    target = entry + 3 × (entry − stop)

## Every judgement call I had to make

The spec is precise about geometry and loose in four places. Each is a knob in
`PullbackSettings`, and each is a place my reading may not match yours.

| Phrase | My reading | Knob |
| --- | --- | --- |
| "almost cutting BB middle upwards **or about to**" | rising, and either within 0.50% below the band or crossed above it within 3 bars | `hma_near_pct`, `hma_cross_lookback` |
| "**before 1 o'clock**" | the candle must *start* before 13:00, so the 12:45 bar is the last eligible one | `latest_anchor` |
| "a **pullback on** BB middle band" | low touches the band and the candle still closes above it — a touch that held, not a break | — |
| "**we take an entry** the 3/5 min candle" | at that candle's close, not on a stop order above its high | — |

Two further gaps the spec did not cover, where I chose the conservative option:

- **Exit if neither stop nor target is hit.** The spec gives no time exit. This is an
  intraday setup anchored before 13:00, so the position is **squared off at the close**
  rather than carried overnight. Carrying it would import gap risk the spec never mentions.
- **How long the anchor stays valid.** Capped at 40 entry-timeframe bars (~3.3 hours), else
  the signal is stale.

## 3-minute is not available

The spec allows "3 minutes timeframe or 5 mins". Yahoo does not serve a 3m interval at all
— the request returns no data — and the only source it could be resampled from is 1m, which
reaches back **7 sessions**. That is not enough to measure anything.

**The backtest is therefore 5-minute.** `--entry-tf` exists, but 3m needs a different data
provider before it means anything.

## What could not be tested honestly

- **~58 sessions** of intraday history is the hard upstream cap. One market period.
- **Long only.** The spec describes a bullish setup; no short mirror was invented.
- **Costs are charged per trade from its own stop distance**, not a fixed R. This matters
  much more here than in V3: at a 0.7% stop, 0.17% of round-trip friction is **0.24R**, and
  the break-even hit rate moves from **25% to 31%**. A quarter of every unit risked is spent
  getting in and out — the tighter the stop, the worse this gets.
- **A bar touching both stop and target books a loss**, since the order of events inside a
  bar is unknown. Same rule V3 uses.
- **Slippage on the stop is not modelled** — a stop is assumed to fill exactly at its price.
  On a 0.7% stop that assumption is optimistic, and it is the same flaw already flagged in
  the V3 backtest.

## Results — it loses, and the reason is geometry rather than the pattern

100 NIFTY 200 names, 58 sessions, 874 anchors, **752 trades**.

| | |
| --- | --: |
| Win rate (resolved) | **21.0%** |
| Break-even at 3R, before costs | 25.0% |
| Break-even after costs | **56.9%** |
| Gross expectancy | **−0.072R** |
| Mean cost | **−1.276R** |
| **Net expectancy** | **−1.347R** |
| Total | −1,013R over 752 trades |

Two separate problems, and the second is much larger than the first.

**1. It is below break-even even before costs.** 21% of trades reach 3R; 25% is needed.
Gross expectancy is −0.07R.

**2. The stop is far tighter than the specified cap, so costs are ~1R per trade.** This is
the finding that matters:

| | |
| --- | --: |
| specified cap | 0.70% |
| **median actual stop** | **0.169%** |
| 10th percentile | 0.075% |
| smallest | 0.007% |

Only **25 of 752** trades had a stop even in the 0.5–0.7% band. The low of a 5-minute
candle sits very close to its close, so the R unit is tiny — and 0.17% of round-trip
friction against a 0.169% stop is **a full 1R**. The transaction cost equals the entire
amount risked. Break-even rises from 25% to 57%, which no version of this pattern reaches.

Sliced by actual stop distance, the pattern is the same everywhere and only the cost changes:

| stop distance | n | win | gross | cost | net |
| --- | --: | --: | --: | --: | --: |
| 0.0–0.1% | 149 | 21.5% | −0.14R | 2.77R | **−2.91R** |
| 0.1–0.2% | 306 | 20.4% | −0.12R | 1.21R | −1.33R |
| 0.2–0.3% | 170 | 21.2% | −0.04R | 0.71R | −0.75R |
| 0.3–0.5% | 102 | 22.6% | +0.05R | 0.46R | −0.42R |
| 0.5–0.7% | 25 | 18.8% | +0.20R | 0.30R | −0.11R |

Gross expectancy improves monotonically as the stop widens, and net is negative in every
bucket.

### The missing rule: a stop *floor*

The spec caps risk and sets no minimum. V3 has both, and its reasoning applies here
verbatim — below a floor "the stop sits inside normal noise and is not an invalidation at
all". A 0.007% stop is not a thesis about the trade; it is a coin flip that also pays a
toll.

Post-hoc, applying a floor to the same data (so this is fitted, not evidence):

| floor | n | win | gross | net |
| --- | --: | --: | --: | --: |
| none | 752 | 21.0% | −0.072R | −1.347R |
| 0.2% | 297 | 21.5% | +0.012R | −0.578R |
| 0.3% | 127 | 22.0% | +0.076R | −0.355R |
| 0.4% | 58 | 26.8% | +0.279R | −0.075R |

A floor turns gross expectancy positive and still does not reach profitability net of
costs, on a sample that shrinks to 58 trades. `min_risk_pct` exists for experimenting; it
defaults to 0.0 so the scanner stays faithful to the specification as written.

### What would have to change for this to work

The three are related, and none is a tweak:

1. **A much wider stop** — structural (below the swing, or an ATR multiple) rather than the
   entry candle's own low, so 1R is large enough that costs are a small fraction of it.
2. **Materially lower costs.** At these stop distances, cost is the strategy.
3. **A higher hit rate**, which the 3R target makes hard: 21% observed against 25% needed
   before costs.

### Caveats

- 58 sessions, one market period, 100 of the 200 names.
- 5-minute entries; 3-minute could not be tested (no data).
- Long only, as specified.
- Stop fills are assumed exact, which flatters the result rather than harming it.
