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

*200 NIFTY 200 names, 59 sessions, 11,800 symbol-sessions. 26 Aug 2026.*
Reproduce with `uv run asymmetry trident-backtest --symbols 0`.

### Frequency

| | |
| --- | --: |
| Sessions where a qualifying gap printed in the kill zone | 2,203 |
| Setups that cleared every condition | **22** |
| Rate | 0.19% of symbol-sessions, ~0.37 per session across the NIFTY 200 |

Rare, as advertised — and roughly in the region the source describes, though he arrives
there with six instruments rather than two hundred names.

### Outcome

| | |
| --- | --: |
| Resolved (stop or target) | 20 |
| Won | **0** |
| Still open (entered 24 Aug, no forward data) | 2 |
| Win rate | **0.0%**, 95% Wilson interval **0.0%–16.1%** |
| Break-even at 20R | 4.8% |
| Gross expectancy | −0.915R |
| Mean cost | −0.486R |
| **Net expectancy** | **−1.401R** |
| Total | −30.8R |

**Read that carefully, because it says less than it appears to.** Twenty losses in a row
feels decisive and is not:

| True win rate | P(0 wins in 20) |
| --: | --: |
| 4.8% — break-even | **0.374** |
| 10% | 0.122 |
| 13.9% | 0.05 |
| 20% | 0.012 |
| 90% — the source's claim | 1 × 10⁻²⁰ |

So **break-even is not excluded**; a system with exactly no edge produces this run better
than a third of the time. What *is* excluded at 5% is any true win rate above **13.9%**.
The 90% claim at a fixed 20R target is dead several times over — but §0 already explained
that a fixed 20R target is not what he does, so this refutes the mechanical version rather
than the man.

The −1.401R figure's interval of [−1.616, −1.185] looks precise and is an **artefact**:
with no observed wins there is almost no variance in the sample to widen it. The report
says so on the surface rather than printing a confident-looking number.

### Was it the entry or the exit?

All twenty deaths were stops against a target needing a median **9.4% move** while the stop
sat a median **0.47%** away — twenty times closer. Nine of the twenty died in the entry
session. That is a geometry problem, so the entry was measured separately by maximum
favourable excursion: how far each trade travelled in its favour on bars that **did not**
touch the stop. (Counting the killing bar's high would be the same favourable
within-bar resolution the backtest refuses; it moves MOTHERSON from 18.31R to 31.15R and
four trades from 0.00R to positive, so the distinction is not cosmetic.)

| Reached before dying | n | share |
| --- | --: | --: |
| 0.5R | 13 | 59% |
| 1R | 10 | 45% |
| 2R | 8 | 36% |
| 3R | 5 | 23% |
| 5R | 3 | 14% |
| 10R | 2 | 9% |
| **20R** | **0** | **0%** |

Median 0.89R, mean 2.66R, max 18.31R. Four trades never traded above entry at all.

Applying those excursions to alternative fixed targets — **a descriptive exercise on 22
trades, not a recommendation**, since choosing the best row here would be curve-fitting:

| Target | Wins | Win% | Gross R/trade | Net R/trade | Break-even |
| --: | --: | --: | --: | --: | --: |
| 1R | 10 | 45.5% | −0.091 | −0.577 | 50.0% |
| 2R | 8 | 36.4% | +0.091 | −0.395 | 33.3% |
| 3R | 5 | 22.7% | −0.091 | −0.577 | 25.0% |
| 5R | 3 | 13.6% | −0.182 | −0.667 | 16.7% |
| 10R | 2 | 9.1% | +0.000 | −0.486 | 9.1% |
| 20R | 0 | 0.0% | −1.000 | −1.486 | 4.8% |

(At 3R and above the two open trades are counted as losses, which they are not.)

**The entry sits within ±0.1R of gross break-even at every horizon tested.** It does not
have an edge and it does not have a negative edge; against its own geometry it is a coin
flip. No target rescues it, so "the 20R was too ambitious" is not the explanation.

### Costs are the whole of the loss

Mean cost 0.486R against a gross expectancy of roughly zero. That is not incidental — it
follows directly from a stop placed at a doji low:

| | stop | cost in R |
| --- | --: | --: |
| MOTHERSON, 5 Aug | 0.08% | **2.02R** |
| APOLLOHOSP, 8 Jun | 0.18% | 0.96R |
| median trade | 0.47% | 0.36R |
| SUZLON, 16 Jun | 1.53% | 0.11R |

**MOTHERSON was down two units of risk before the position moved.** `cost% / stop%` — the
same relationship that forced the pullback retraction, arriving here from the opposite
direction. A stop floor would remove those trades, and it is deliberately **not** added: the
source has no such rule, and inventing one to improve a measurement of somebody else's
strategy measures the invention. `--min-risk` exists for anyone who wants to test it.

One trade gapped through its stop (INDIGO, 25 Jun) and booked −1.13R rather than −1.00R.

### Why the other 11,778 sessions were refused

| Condition | Sessions |
| --- | --: |
| below the daily 200 EMA | 5,245 |
| no fair value gap in the kill zone | 4,352 |
| gap printed, nothing retraced to the 50% as a doji | 1,633 |
| body too large for a doji | 297 |
| no confirmation bar left in the window | 79 |
| confirmation traded through the stop | 56 |
| confirmation closed above the doji high | 40 |
| EMAs not stacking | 35 |
| daily printing blue, not green | 31 |
| daily printing black, not green | 10 |

Recorded by the furthest stage each session reached. Two things worth noting: the daily
200 EMA bias is doing most of the rejecting, which is a market-condition statement about
this particular window as much as a filter; and **my reconstructed daily-colour indicator
refused only 41 sessions**, so the risk that it distorted the result is small — a relief,
since it is the component least faithful to the source.

### Verdict

**Inconclusive on the source's claim, and negative on the mechanical version of it.** The
entry finds a rare, well-defined pattern roughly a third of a time per session, and that
pattern's forward distribution in this window is indistinguishable from noise before costs
and clearly negative after them. Nothing here justifies trading it, and nothing here refutes
what the source says he does — those are different questions, and only the second one has
been asked.

### 5b. At 4R, and why no exit rule rescues it

*Added 26 Aug 2026, on the owner's request to test a 1:4 target instead of 1:20.*

The same 22 setups, resolved against four rule sets:

| Variant | n | resolved | wins | win% | 95% CI | break-even | gross | cost | **net** |
| --- | --: | --: | --: | --: | --- | --: | --: | --: | --: |
| **4R** | 22 | 20 | 4 | **20.0%** | 8.1–41.6% | 20.0% | **−0.006R** | 0.499R | **−0.505R** |
| 4R, 0.5% stop floor | 8 | 7 | 1 | 14.3% | 2.6–51.3% | 20.0% | −0.304R | 0.248R | −0.552R |
| 20R | 22 | 20 | 0 | 0.0% | 0.0–16.1% | 4.8% | −1.006R | 0.499R | −1.505R |
| 20R, 0.5% stop floor | 8 | 7 | 0 | 0.0% | 0.0–35.4% | 4.8% | −1.018R | 0.248R | −1.267R |

**4R is far better than 20R and still loses, and it loses entirely to costs.** Gross
expectancy is −0.006R against a 20.0% win rate and a 20.0% break-even — the entry lands on
gross break-even almost exactly, which is the excursion finding (§5) arriving from a second
direction. Then 0.499R of friction per trade takes it to −0.505R.

**The stop floor does not fix it.** It was tested precisely because costs are the problem,
and it halves them (0.499R → 0.248R) — yet net gets *worse*, −0.505R → −0.552R. It discards
14 of 22 setups and the survivors underperform the ones it removed. The floor stays out of
the engine by default, now for a measured reason rather than only a faithfulness one.

### 5c. Buying a win rate, and what it costs

The question this answers is not the source's: it is whether the 4R-at-80% figures in
circulation are reachable. They are a **different metric**, not a better system, and
`resolve_trade_scaled` exists to put both on one scoreboard. Same entries, same stops, same
bars; only the booking differs.

| exit rule | win rate | hit 4R | gross | net |
| --- | --: | --: | --: | --: |
| **fixed 4R, no partial** | 20.0% | 20.0% | −0.006R | **−0.505R** |
| 50% off at +0.25R, then break-even | 9.5% | 4.8% | −0.048R | −0.544R |
| 75% off at +0.25R, then break-even | 4.8% | 4.8% | −0.048R | −0.544R |
| 50% off at +0.50R, then break-even | 9.5% | 4.8% | −0.197R | −0.693R |
| 75% off at +0.50R, then break-even | 33.3% | 4.8% | −0.173R | −0.669R |
| 50% off at +1.00R, then break-even | 35.0% | 10.0% | −0.206R | −0.705R |
| 75% off at +1.00R, then break-even | 35.0% | 10.0% | −0.206R | −0.705R |
| 50% off at +1.50R, then break-even | 35.0% | 10.0% | −0.194R | −0.693R |
| 75% off at +1.50R, then break-even | 35.0% | 10.0% | −0.163R | −0.662R |
| 50% off at +2.00R, then break-even | 30.0% | 20.0% | −0.006R | −0.505R |
| 75% off at +2.00R, then break-even | 30.0% | 20.0% | −0.056R | −0.555R |
| 50% off at +0.50R, **stop stays put** | 20.0% | 20.0% | −0.094R | −0.593R |
| 50% off at +1.00R, **stop stays put** | 20.0% | 20.0% | −0.106R | −0.605R |

"Win rate" here is the share of trades ending **net positive**, which is the metric the
scaled style is usually quoted on.

Four readings, and the third is the one that was not expected:

- **No variant beats the plain fixed target.** −0.505R is the baseline and also the best
  result on the board.
- **The break-even stop lifts the hit rate by ejecting you from winners.** It takes 20% →
  35%, and halves the trades reaching the full target, 20% → 10%. Trade by trade: 360ONE and
  TITAN both booked **+4.00R** on the fixed rule and **+0.50R** on the scaled one — price
  reached +1R, pulled back through entry on an ordinary retrace, stopped them at break-even,
  and *then* ran to target. Four losses became +0.50R wins, worth +6R; four winners were cut,
  worth −10R.
- **Lowering the partial trigger *reduces* the win rate here, and that was predicted
  backwards.** At +0.25R a half position banks 0.125R, which does not cover 0.499R of costs,
  so the trade still books a net loss. The manufacturing only works where cost drag is small
  relative to the partial. **At this strategy's cost level it does not even buy the cosmetic
  benefit.**
- **The partial alone buys nothing.** With the stop left where it was, the win rate and the
  target-hit rate are both unchanged at 20% and net simply falls. Scaling out is a cost, and
  the break-even stop is what moves the hit rate.

**None of this is a criticism of the traders quoting 80%.** Win rate and R multiple trade
against each other by construction; 80% at 0.8R and 25% at 4R are both coherent, and only
comparing one system's win rate against another's R multiple is the error. What no exit rule
can do is add edge to an entry that has none — and on this window, at gross −0.006R, this
entry has none.

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
