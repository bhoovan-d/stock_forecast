# Kill-zone trident — specification

*26 Aug 2026. Transcribed from Tyler / TG Capital on Chart Fanatics
(`youtu.be/ADnslyKOwFE`), built as a third separate strategy.*

Engine: `engines/trident.py`. Surfaces: `report/trident_report.py`.
Commands: `asymmetry trident`, `asymmetry trident-backtest`.

**This is not part of V3 and not part of the HMA pullback.** Three strategies now live in
this repository and they share nothing but generic indicator maths:

| | V3 | HMA pullback | Trident |
| --- | --- | --- | --- |
| Universe | NIFTY 500 | NIFTY 200 | NIFTY 200 |
| Entry timeframe | 15m | 5m | 30m |
| Target | 4R | 3R | **20R** |
| Stop rule | 0.5–1.5% band | 0.7% cap | structural, no band |
| Holding period | 1–5 sessions | intraday | **weeks** |
| Cost basis | delivery | intraday | delivery |

The cost row is the one that has already cost this codebase a retraction. A cost constant is
calibrated for a holding period; charging V3's delivery figure to the intraday pullback
overstated its costs 3.4× and produced a headline loss that had to be withdrawn. This
strategy's constant is derived independently in `TridentSettings` and lands on the same
0.12% as V3 — not by copying it, but because a 20R target off a sub-1% stop is a multi-week
hold that pays the same delivery STT.

---

## 0. What the source claims, and what that claim would mean

Before any of the rules, the headline: **a ~90% win rate at a minimum 1:20 reward-to-risk**,
from a trader reporting ~$400,000 in prop-firm payouts across three months and one trade
that returned 1:51.

Those two numbers are not independent. At a fixed 20R target, expectancy is
`w × 20 − (1−w) × 1`:

| Win rate at 20R | Expectancy per trade |
| --: | --: |
| 4.8% | 0.00R — break-even |
| 20% | +3.2R |
| 50% | +9.5R |
| **90%** | **+17.9R** |

**+17.9R per trade is not a strategy, it is a money pump.** Risking a fixed 1% per position,
that compounds by roughly 18% *per trade*. At his own stated frequency — 8–10 setups a year
per instrument, across six instruments — that is on the order of 50 trades a year, and no
documented strategy of any kind survives contact with that arithmetic.

The reconciliation he offers in the interview is worth stating fairly, because it is not
nothing: he is not claiming 90% of *entries* reach 20R. He is claiming 90% of the setups he
*takes* are winners, and he manages them discretionarily — "I'll ride the trend until the
EMAs cross over", "I cut it, the trade was still going to go". A trailed exit that books
+3R, +8R or +51R depending on what the market gives is a different distribution from a fixed
20R limit order, and its win rate is not comparable to a fixed-target win rate at all. He
also says plainly that this part is discretion and cannot be taught.

**So the version measured here is deliberately not his.** The owner chose a mechanical
fixed-20R exit, because a mechanical exit is the only kind that can be replayed. What
follows measures *the entry*, holding the exit constant. That is a real and useful question —
does the trident entry produce trades whose subsequent distribution is favourable — but it is
not a test of his 90%, and no number in this document should be read as one.

**The honest restatement:**

> The entry rules are precise, transcribable and testable. The claimed win rate is neither
> confirmed nor refuted here, and the available data cannot settle it — see §5. Treat the
> scanner as a way to find the pattern, not as evidence the pattern pays.

---

## 1. As specified

From the transcript, the parts that are rules rather than commentary:

> 1. Strictly the London kill zone, **03:00–06:30 New York time**. "This setup means nothing
>    without the time. Without time, price action means absolutely nothing."
> 2. One entry timeframe, the **30 minute**, "to keep it simple". The daily supplies bias and
>    target.
> 3. The **5, 9, 13 and 21 EMAs stacking**. "If the EMAs were crossing like here, I wouldn't
>    be interested in any price action that has to do with that."
> 4. **Above the daily 200 EMA** is a long bias; below it he looks for shorts.
> 5. A **fair value gap** prints inside the kill zone. Gaps at 02:30/03:00/03:30 are the
>    highest-probability slots; one at 04:00 still counts.
> 6. Price returns to the gap's **consequent encroachment** — the 50% — as a **doji**, wicking
>    through it and closing back above. "If this candle closed here, say this wasn't a doji,
>    the body of this candle was in here — this would be an invalidation."
> 7. The **next candle closes below the doji's high**. "If it closes above the high, I'll
>    invalidate the trade… it's going to diminish the win rate."
> 8. **Entry** at that close, **stop** below the doji's low, **minimum 1:20**.
> 9. Universe: USD pairs (GBPUSD, EURUSD, USDCAD, NZDUSD, USDJPY) and **gold**, which is his
>    best instrument. Explicitly not AUDUSD — "in my back testing it didn't work well".
> 10. On gold only, **no hard stop** — he waits for a candle close below, because gold's
>     liquidity wicks stop everyone out before it runs.

---

## 2. As implemented, and every judgement call I had to make

The source is precise about geometry and loose in six places. Each is a knob in
`TridentSettings`, and each is somewhere my reading may not match yours.

| Phrase | My reading | Knob |
| --- | --- | --- |
| "London kill zone, 03:00–06:30 NY" | the first 3h30m of the NSE session, **09:15–12:45 IST** | `killzone_start`, `killzone_end` |
| "a fair value gap" | the standard three-bar bullish imbalance: bar 3's low prints above bar 1's high | `min_gap_pct` |
| "a **doji** candle" | body ≤ **30%** of the bar's full range | `max_doji_body_pct` |
| "wicks through" the 50% | the bar's low reaches the consequent encroachment **and** its close is back above it | — |
| "the candle closes below this high" | close < the doji's high, and its low has not already taken the stop | — |
| the green/blue/red/black daily indicator | **reconstructed**, see below | `require_strong_daily`, `strong_bb_position` |

### The kill zone is the largest liberty taken

The owner chose the NSE-equities adaptation over a faithful FX build, and the kill zone does
not survive that translation intact. London 03:00–06:30 NY is the first three and a half
hours of the London session, so it is mapped here to the first three and a half hours of the
NSE session. **That is a structural analogy, not a measured claim.** An equity open is far
more front-loaded than an FX session — most of the day's information arrives in the first
thirty minutes — so the right NSE window may well be 09:15–10:45, or the window may not
transfer at all. `--killzone-end` exists so this can be argued with rather than assumed.

It is worth being blunt about what this costs. The source's single strongest assertion is
that the *time* is what makes the pattern work. Remapping the time to a different market's
session is exactly the kind of substitution that would break it, and nothing here can tell
you whether it did.

### The four-colour daily indicator is my reconstruction, not his

He names a third-party TradingView script ("bull trading, one minute easy scalping", first
timeframe set to daily) and describes its output but not its formula: strong bullish closes
bright green, weaker bullish closes blue, strong bearish red, low-volume bearish black. He
gates on it — "if these candles weren't green and they were red and blue, it would be an
invalidated setup".

It is rebuilt here from that description plus the one structural hint he gives, "we're in the
top of the Bollinger band above the 200 EMA":

```
strong = close in the upper 60% of the daily Bollinger range
         AND the day is trading at or above its 20-day average pace by volume

green = bullish and strong        blue  = bullish and not strong
red   = bearish and strong        black = bearish and not strong
```

**This is my indicator wearing his name.** It gates by default because that is faithful to
what he says, `--any-daily` turns it off, and the rejection table reports how many setups it
removed so the cost of my reconstruction stays visible rather than baked in.

### Long only, and no gold rule

The source describes a bidirectional model — below the 200 EMA he looks for shorts — but is
explicit that he is long-biased, that his data comes from longs, and that he is "not good at
shorting". **No short mirror was invented.** Inventing the other half of somebody's edge and
then measuring it teaches nothing about the edge.

His no-hard-stop gold rule is likewise **not implemented**: it applies to one instrument that
is not in this universe, and an unbounded overnight loss on a single equity is a materially
different risk from a wick on gold.

### The exit is mechanical, and that is a substitution

He rides the trend and cuts on judgement — an inverted fair value gap, a strong bearish
candle, the EMAs crossing. This build takes a **fixed 20R limit and a hard stop**, plus a
time stop at 60 sessions because a replay needs a bound. §0 explains why: a discretionary
exit cannot be replayed, and modelling my guess at his judgement would measure my guess.

### One thing the source does not ask, which is computed anyway

**20R off a 30-minute structural stop is not an intraday trade on an equity.** The measured
median stop distance here is around 0.43% of price, so a 20R target is a **~8.7% move**. On
an NSE large cap that is weeks, not hours — which is why trades resolve on daily bars after
the entry session and pay delivery costs.

Whether that move is reachable at all is a real question. Every signal therefore reports
`required_move_pct` against an ATR-implied capacity, and the backtest splits the population
by it. It **does not reject by default**: the source has no feasibility test, and switching
one on would measure a rule he does not have. `--feasible-only` arms it.

---

## 3. What the backtest does, and the two rules that stop it inventing an edge

Both are borrowed from the V3 backtest because they are what keep a replay honest, not
because the engines are shared:

- **A bar touching both stop and target books a loss.** The order of events inside a bar is
  unknown, and resolving it favourably is the classic way a backtest manufactures a win rate.
- **Gaps are honoured at the open, not at the level.** If a session opens below the stop, the
  trade books the *actual* loss, which is worse than −1R. This matters enormously here:
  without it, every loss would be capped at exactly 1R while the 20R upside stayed intact,
  which is a free insurance policy no real position has. `tests/test_trident.py` asserts a
  gap-through books −6R on a −6R gap.

Two further properties are asserted rather than assumed, both against the traps this
codebase has already paid for:

- **Nothing reads the finished daily candle.** The setup fires *inside* that candle — the
  source is explicit that this is the point — so the daily state is computed from a partial
  built out of the intraday bars up to and including the confirming bar, with bands and the
  200 EMA taken from completed prior days only. The test appends a monstrous same-day green
  bar and asserts the classification does not move.
- **Cost in R scales inversely with stop distance.** `cost% / stop%`, asserted directly.

---

## 4. Rejection accounting

A scanner that reports only what it admitted cannot be audited — the interesting number is
almost always which condition did the rejecting. Refusals are recorded by the **furthest
stage a session reached**, not by whichever check happened to run last, so "no gap printed"
and "everything formed but the EMAs were crossing" stay distinguishable.

---

## 5. Results

> **Read this before the numbers.** Everything below is a **snapshot taken 26 Aug 2026**, not
> a constant. The same command tomorrow returns different figures, for two reasons that have
> nothing to do with the market:
>
> 1. **The window grows.** Each new session resolves trades that were open, and on a
>    22-trade sample one extra resolution moves expectancy by roughly 0.2R.
> 2. **The sample size itself varies.** A transient Yahoo failure drops a symbol and its
>    setups. Two runs minutes apart on 26 Aug returned 199 and 200 symbols — 21 and 22
>    setups. `fetch_failures` now reports this; before it did, the denominator shrank
>    invisibly while the report still read as a clean measurement.
>
> Over a single day this document went through four generations of point estimates, three of
> them caused by accounting defects in the resolver (§5d) rather than by data. **Treat the
> geometry as findings and the statistics as observations** — §5e sorts every claim into one
> or the other.

*200 NIFTY 200 names, 59 sessions, ~11,800 symbol-sessions, 0 fetch failures.*
Reproduce with `uv run asymmetry trident-backtest --symbols 0`, and expect it to differ.

### Frequency

| | |
| --- | --: |
| Sessions where a qualifying gap printed in the kill zone | 2,166 |
| Setups clearing every condition | **22** |
| Rate | 0.19% of symbol-sessions, ~0.37 per session across the NIFTY 200 |

Rare, as advertised. The source estimates 8–10 a year per instrument, though he reaches that
with six instruments rather than two hundred names.

### Outcome at 20R

| | |
| --- | --: |
| Resolved (stop or target) | 20 |
| Won | **0** |
| Still open (entered near the end of the window) | 2 |
| Win rate | **0.0%**, 95% Wilson interval **0.0%–16.1%** |
| Break-even at 20R | 4.8% |
| Gross expectancy | −1.006R |
| Mean cost | −0.499R |
| **Net expectancy** | **−1.505R** |
| Total | −30.1R |

**Twenty losses in a row says less than it appears to:**

| True win rate | P(0 wins in 20) |
| --: | --: |
| 4.8% — break-even | **0.374** |
| 10% | 0.122 |
| 13.9% | 0.05 |
| 20% | 0.012 |
| 90% — the source's claim | 1 × 10⁻²⁰ |

**Break-even is not excluded** — a system with exactly no edge produces this run better than
a third of the time. What *is* excluded at 5% is any true win rate above **13.9%**. The 90%
claim at a fixed 20R target is dead many times over, but §0 already explained that a fixed
20R target is not what the source does.

The −1.505R interval of [−1.684, −1.326] looks precise and is an **artefact**: with no
observed wins there is almost no variance in the sample to widen it. The report says so on
its own surface rather than printing a confident-looking number.

### Was it the entry or the exit?

All twenty deaths were stops, against a target needing a median **8.9% move** while the stop
sat a median **0.45%** away — twenty times closer. Excursion analysis measures the entry
separately: how far each trade travelled in its favour on bars that **did not** touch the
stop. (Counting the killing bar's high would be the same within-bar favourable resolution the
backtest refuses; it moves MOTHERSON from 18.31R to 31.15R and four trades from 0.00R to
positive, so the distinction is not cosmetic.)

| Reached before dying | n | share |
| --- | --: | --: |
| 0.5R | 13 | 59% |
| 1R | 10 | 45% |
| 2R | 8 | 36% |
| 3R | 6 | 27% |
| 5R | 5 | 23% |
| 10R | 2 | 9% |
| **20R** | **0** | **0%** |

Median 0.89R, mean 2.97R, max 18.31R. Four trades never traded above entry at all.

Applying those excursions to alternative fixed targets — **descriptive, not a
recommendation**, since picking the best row from 22 trades is curve-fitting:

| Target | Wins | Win% | Gross | Net | Break-even |
| --: | --: | --: | --: | --: | --: |
| 1R | 10 | 45.5% | −0.091R | −0.577R | 50.0% |
| 2R | 8 | 36.4% | +0.091R | −0.395R | 33.3% |
| 3R | 6 | 27.3% | +0.091R | −0.395R | 25.0% |
| 4R | 6 | 27.3% | +0.364R | −0.122R | 20.0% |
| 5R | 5 | 22.7% | +0.364R | −0.122R | 16.7% |
| 10R | 2 | 9.1% | +0.000R | −0.486R | 9.1% |
| 20R | 0 | 0.0% | −1.000R | −1.486R | 4.8% |

Gross runs from −0.09R to +0.36R across every horizon and **net is negative at all of them**.
At 10R and 20R the two open trades are counted as losses, which they are not.

### Costs are the whole of the loss

Mean cost 0.486R against a gross expectancy that never leaves the neighbourhood of zero. That
follows directly from a stop placed at a doji low:

| | stop | cost in R |
| --- | --: | --: |
| MOTHERSON, 5 Aug | 0.08% | **2.02R** |
| APOLLOHOSP, 8 Jun | 0.18% | 0.96R |
| median trade | 0.45% | 0.36R |
| SUZLON, 16 Jun | 1.53% | 0.11R |

**MOTHERSON was down two units of risk before the position moved.** `cost% / stop%` — the
relationship that forced the pullback retraction, arriving here from the opposite direction.
One trade gapped through its stop (INDIGO, 25 Jun) and booked −1.13R rather than −1.00R.

### Why the other ~11,780 sessions were refused

| Condition | Sessions |
| --- | --: |
| below the daily 200 EMA | 5,157 |
| no fair value gap in the kill zone | 4,309 |
| gap printed, nothing retraced to the 50% as a doji | 1,599 |
| body too large for a doji | 292 |
| insufficient history | 108 |
| everything else — confirmation, EMAs, daily colour | ~200 |

Recorded by the furthest stage each session reached. Two things worth noting: the daily
200 EMA bias does most of the rejecting, which is as much a statement about this window as
about the filter; and **the reconstructed daily-colour indicator refused only ~40 sessions**,
so the risk that my reconstruction distorted the result is small — a relief, since it is the
component least faithful to the source.

### 5b. At 4R, and the stop floor

*Re-measured on the owner's request to test 1:4 instead of 1:20.*

| Variant | n | resolved | wins | win% | 95% CI | break-even | gross | cost | **net** |
| --- | --: | --: | --: | --: | --- | --: | --: | --: | --: |
| **4R** | 22 | 22 | 6 | **27.3%** | 13.2–48.2% | 20.0% | **+0.358R** | 0.486R | **−0.128R** |
| 4R, 0.5% stop floor | 8 | 8 | 2 | 25.0% | 7.1–59.1% | 20.0% | +0.234R | 0.251R | −0.017R |
| **20R** | 22 | 20 | 0 | 0.0% | 0.0–16.1% | 4.8% | −1.006R | 0.499R | −1.505R |
| 20R, 0.5% stop floor | 8 | 7 | 0 | 0.0% | 0.0–35.4% | 4.8% | −1.018R | 0.248R | −1.267R |

**4R is far better than 20R and still loses.** Gross is positive at +0.358R and the 27.3% win
rate sits above the 20.0% break-even — then 0.486R of costs takes it to −0.128R net. The
interval, 13.2–48.2%, contains break-even, so this is not evidence of an edge; it is a sample
too small to exclude one.

**Retraction — the stop floor.** Earlier revisions of this document, and the commit that
introduced it, stated that a 0.5% stop floor "halves the cost drag and makes net *worse*, so
the floor is now excluded for a measured reason and not only a faithful one." **That was
wrong, and the correction is not merely that the sign flipped.**

The floor has since measured both ways: −0.552R against −0.505R in one generation of the
data, then −0.017R against −0.128R in another. The cohort is **eight trades** and it moves on
one. Calling that "measured" was the error, in either direction. This codebase already holds
the standard that applies — `docs/spec-a-plus.md` on the gated base-breakout: *"rests on 8
trades, which agrees with the mechanism and establishes nothing."*

**The floor cannot be evaluated at n=8.** It stays off because the source has no such rule,
which was always the sufficient reason. `--min-risk` arms it for anyone who gathers enough
trades to settle it.

### 5c. Buying a win rate, and what it costs

The question this answers is not the source's: it is whether the 4R-at-80% figures in
circulation are reachable. They are a **different metric**, not a better system, and
`resolve_trade_scaled` exists to put both on one scoreboard. Same entries, same stops, same
bars; only the booking differs.

| exit rule | win rate | hit 4R | gross | net |
| --- | --: | --: | --: | --: |
| **fixed 4R, no partial** | 27.3% | 27.3% | +0.358R | **−0.128R** |
| 50% off at +0.25R, then break-even | 13.6% | 9.1% | +0.051R | −0.435R |
| 75% off at +0.25R, then break-even | 9.1% | 9.1% | +0.009R | −0.477R |
| 50% off at +0.50R, then break-even | 13.6% | 9.1% | −0.085R | −0.571R |
| 75% off at +0.50R, then break-even | 36.4% | 9.1% | −0.102R | −0.588R |
| 50% off at +1.00R, then break-even | 40.9% | 13.6% | −0.051R | −0.537R |
| 75% off at +1.00R, then break-even | 40.9% | 13.6% | −0.074R | −0.560R |
| 50% off at +1.50R, then break-even | 40.9% | 13.6% | −0.017R | −0.503R |
| 75% off at +1.50R, then break-even | 40.9% | 13.6% | −0.000R | −0.486R |
| 50% off at +2.00R, then break-even | 36.4% | 22.7% | +0.176R | −0.310R |
| 75% off at +2.00R, then break-even | 36.4% | 22.7% | +0.131R | −0.355R |
| 50% off at +0.50R, **stop stays put** | 27.3% | 27.3% | +0.119R | −0.366R |
| 50% off at +1.00R, **stop stays put** | 27.3% | 27.3% | +0.131R | −0.355R |

"Win rate" here is the share of trades ending **net positive**, which is the metric the
scaled style is usually quoted on.

Four readings, and the third was predicted backwards:

- **No variant beats the plain fixed target.** −0.128R is both the baseline and the best
  result on the board. **80% never appears at any setting**; the ceiling is 40.9%.
- **The break-even stop lifts the hit rate by ejecting you from winners.** It takes 27.3% →
  40.9% while halving the trades that reach the full target, 27.3% → 13.6%. Trade by trade:
  360ONE and TITAN both booked **+4.00R** on the fixed rule and **+0.50R** on the scaled one —
  price reached +1R, pulled back through entry on an ordinary retrace, stopped them at
  break-even, and *then* ran to target.
- **Lowering the partial trigger *reduces* the win rate here.** At +0.25R a half position
  banks 0.125R, which does not cover ~0.49R of costs, so the trade still books a net loss.
  The manufacturing only works where cost drag is small relative to the partial. **At this
  strategy's cost level it does not even buy the cosmetic benefit.**
- **The partial alone buys nothing.** With the stop left where it was, the win rate and the
  target-hit rate are both unchanged at 27.3% and net simply falls. Scaling out is a cost;
  the break-even stop is what moves the hit rate.

**None of this is a criticism of the traders quoting 80%.** Win rate and R multiple trade
against each other by construction; 80% at 0.8R and 25% at 4R are both coherent, and the
error is only in comparing one system's win rate against another's R multiple. What no exit
rule can do is add edge to an entry that has none.

### 5d. Three accounting bugs, and what each did to the answer

*26 Aug 2026. Recorded here rather than only in git history, because the numbers above moved
twice in one day for reasons that had nothing to do with the market.*

Every one of these **flattered** the result, and none was visible from the output alone —
each produced a plausible number, which is what makes this class of bug expensive.

**1. Open trades dragged expectancy toward −cost.** Expectancy averaged over *every* recorded
trade, so a still-open position credited no profit while already carrying a full round-trip
cost. Harmless in a 22-trade replay; ruinous in a forward record, where nearly everything is
open in the first weeks. Fixed by averaging over `finished` trades only.

**2. A still-forming bar could resolve a trade.** Yahoo serves the bar currently being built,
and its high and low only widen as the session fills. A partial bar showing the target but
not yet the stop books a **win**, where the completed bar might touch both — which this
codebase books as a **loss**. The forward record writes once, so an early settle would make
that permanent, and `trident-watch` settles on every run. Fixed by `drop_forming_bar`.

**3. Right-censored trades were booked as finished.** This is the one that moved the
published answer. Once fix 1 landed, a trade that ran out of forward data stopped being
labelled `open` and started being labelled `time-stop` — marked out at the last available
close and counted as a completed outcome. Two such trades, entered two days before the end of
the window, averaged **+2.61R**:

| | with the bug | corrected |
| --- | --: | --: |
| 20R gross | −0.678R | **−1.006R** |
| 20R net | −1.163R | **−1.505R** |

That is +0.33R of pure artefact on a 22-trade sample, and at 4R it was briefly enough to
reverse the verdict on the stop floor. **A censored trade marked out at a favourable close is
an unresolved trade wearing a result.** Fixed by returning a mark-out price only when the hold
genuinely expired.

**A fourth issue was reproducibility rather than accounting.** A failed fetch silently dropped
a symbol and its setups, so two runs of the same command minutes apart returned 199 and 200
symbols — 21 and 22 setups — while the report showed only a symbol count. Now counted and
surfaced as `fetch_failures`.

A fifth was checked and cleared rather than fixed: the two resolvers were put on a single
shared walk (`_forward_walk`), and the refactor was verified against the previous
implementation over the same 22 trades and the same frames — **0 mismatches** on outcome,
realised R, sessions held, gap flag and resolution timestamp.

### 5e. What is stable, and what is not

The most useful output of a day spent measuring this: some of these results are properties of
the geometry and some are properties of a 22-trade sample. They should not be quoted with the
same confidence.

**Stable — survived every generation, and follows from arithmetic:**

- **Cost in R is `cost% / stop%`.** A stop at a doji low runs from 0.08% to 1.53%, so cost
  drag runs from 0.11R to 2.02R within one strategy. Definitional, not measured.
- **Net expectancy is negative in every variant tested** — every target, every exit rule,
  every stop-floor setting, every generation of the data.
- **A break-even stop raises the hit rate by ejecting the position from winners**, roughly
  halving the count of trades that reach target.
- **Scaling out alone buys no hit rate**; net simply falls by the cost of the partial.
- **A very low partial trigger cannot manufacture a win rate here**, because banking 0.125R
  does not cover ~0.49R of costs.
- **20R off a 30-minute structural stop is a multi-week hold on an equity**, so it pays
  delivery costs. Median stop 0.45%, median required move 8.9%.

**Not stable — a snapshot, and not to be quoted as a finding:**

- **Gross expectancy at either target.** At 4R it has printed −0.006R, +0.184R, +0.264R and
  +0.358R across one day, on the same window, as data accrued and defects were fixed.
- **The win rate**, at any target. The interval has contained break-even in every generation,
  which is the only durable statement available.
- **Whether the 0.5% stop floor helps or hurts.** It has measured both ways on eight trades.
- **Anything about the source's 90% claim.** Unchanged from §0: this data cannot settle it.

**The rule this suggests.** A result that moves when you change an accounting convention is
not a result about the market. Twice in one day the sign of a conclusion here turned on how an
unresolved trade was booked. Before quoting any expectancy from this engine, re-run it and
check the trade count first.

---

## 6. What this cannot tell you

- **The sample cannot test the claim.** This is the binding constraint and it is not a
  matter of opinion: Yahoo serves ~60 sessions of 30-minute data and no more, 120m does not
  exist as an interval, and 30m cannot be resampled from anything that reaches further back.
  At the source's own rate of 8–10 setups a year per instrument, no sample this window can
  produce will separate a 90% win rate from a coin flip. Settling this needs
  **forward-collected data**, not a longer replay of history that does not exist.
- **One market period, and one direction.** Long only, ~60 sessions, one regime.
- **It is not his strategy.** Different market, remapped session window, reconstructed
  confluence indicator, mechanical exit in place of discretion. Four substitutions, each
  documented, any of which could be the one that matters.
- **The exit is doing work the entry gets credit for.** A fixed 20R limit is not what
  produced his record, and a favourable result here would not validate his numbers any more
  than an unfavourable one would refute them.
- **Right-censoring.** A 20R target on a multi-week hold, replayed over ~60 sessions, means
  trades entered near the end of the window have not had time to resolve. They are reported
  as their own outcome rather than counted as wins or losses.
- **Nothing has traded live.**

This is decision support. It contains no order-placement code and never will.
