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

## Results — RETRACTED AND CORRECTED (20 Aug 2026)

**The first version of this section said the strategy loses −1.35R per trade. That was
wrong and is withdrawn.** The error was mine: the backtest charged **V3's delivery cost**
to an intraday strategy.

`settings.cost_roundtrip_pct` (0.12% + 0.05% slippage) is calibrated for V3, which holds
1–5 sessions and therefore pays delivery STT — **0.1% on both sides**. An intraday trade
pays **0.025%, sell side only**, and brokerage caps at ₹20 an order instead of scaling with
turnover:

    brokerage  0.013    (Rs 20/order, both sides, multi-lakh position)
    STT        0.025    (sell side only, intraday)
    exchange   0.006
    stamp      0.003    (buy side)
    GST        0.003
    ---------------
    total      0.050 %  round trip

**Cost was overstated 3.4×.** That error is amplified by this strategy's geometry: cost in R
is `cost% / stop%`, so at a 0.2% stop every 0.01% of cost error is 0.05R. The headline loss
was mostly a wrong constant.

### What the numbers actually are

100 NIFTY 200 names, 58 sessions, 874 anchors, **752 trades**. Costs at intraday rates,
shown across a slippage range because slippage is the one input still assumed rather than
measured:

| stop distance | n | gross | net @0.05% | net @0.07% | net @0.10% |
| --- | --: | --: | --: | --: | --: |
| 0.0–0.2% | 455 | −0.126R | −0.64R | −0.84R | −1.14R |
| 0.2–0.3% | 170 | −0.035R | −0.25R | −0.33R | −0.45R |
| 0.3–0.5% | 102 | +0.046R | −0.09R | −0.15R | −0.23R |
| 0.5–0.7% | 25 | +0.197R | **+0.11R** | **+0.07R** | +0.02R |

The widest-stop bucket is **around break-even to slightly positive**, not the catastrophe
first reported — though its 95% CI is ±0.57R on 25 trades, so it establishes nothing.

### The gross result was never significant either

| | |
| --- | --: |
| Gross expectancy | −0.072R |
| Standard error | 0.059 |
| **95% CI** | **[−0.186, +0.043]** |
| t | −1.22 |

**The interval contains zero.** Quoting −0.072R as "below break-even before costs" was
presenting noise as a finding. Win rate is 21.0% (145 of 689 resolved) against 25% needed,
and that difference is not established at this sample size.

### A second methodological fault

The single aggregate figure was a **mean of ratios**, which over-weights degenerate trades.
The smallest 10% of stops contributed **28%** of all cost-R, and the smallest half
contributed **74%**. At the median stop of 0.169%, risking ₹5,000 implies a **₹29.6 lakh
position** — not a trade this account can take at all. Those rows should never have been in
a headline average. Per-bucket figures are the honest summary and are what the table above
reports.

### What actually survives

- **Nothing is established about this strategy's edge, in either direction.** The correct
  verdict is *inconclusive*, not *it loses*.
- **The structural point stands**: cost in R is inversely proportional to stop distance, so
  tight stops are disproportionately expensive. That is arithmetic, and it is why the
  0.0–0.2% bucket is bad at any plausible cost.
- **A stop floor is still worth having**, for the reason V3 has one: below it the stop sits
  inside noise. But the case now rests on the geometry, not on the retracted loss figure.

### What would settle it

1. **Measure your real slippage.** It is the only input still assumed. Across the plausible
   range the widest-stop bucket moves from +0.11R to +0.02R — that is the whole answer.
2. **More sample at usable stop distances.** 25 trades in the 0.5–0.7% bucket is the
   cohort that matters and it is far too small.
3. **A structural stop** (below the swing, or an ATR multiple) rather than the entry
   candle's low, which would put most trades in the range where costs are tolerable.

### Caveats that were correct the first time

- 58 sessions, one market period, 100 of the 200 names.
- 5-minute entries; 3-minute could not be tested (no data).
- Long only, as specified.
- Stop fills assumed exact, which flatters the result.
