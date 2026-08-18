# The 30-question worksheet — what the code already answers

*19 Aug 2026. Companion to `AsymmetryEngineWorksheet30Q.docx`.*

**Read this first.** The worksheet asks *you* what you want. Much of it cannot be answered
from the repository — your capital, your last 20 trades, whether you keep a log. Those are
marked **YOURS** and left for you; nothing has been invented to fill them.

What can be answered is the other half: **what the engine currently does**, so you are
confirming or overriding a stated default rather than specifying from scratch.

| Tag | Meaning |
| --- | --- |
| **BUILT** | Implemented and running. The value quoted is the live default. |
| **MEASURED** | Built *and* tested against replayed trades. The number is evidence, not taste. |
| **NOT BUILT** | The worksheet assumes it; the code does not do it. |
| **YOURS** | Only you can answer. No default exists and none should be guessed. |

---

## 1. Universe & Tradability

**Q1 — Universe, exclusions.** **BUILT.** NIFTY 500 constituents (`universe_index =
"nifty500"`), cash market, both directions. There is no exclusion list for PSU, recent IPOs
or ASM/surveillance names — the only filter is liquidity. On 14 Aug, 473 of 500 survived it.
**YOURS:** whether those exclusions should exist; adding them is a small change to
`data/universe.py`.

**Q2 — Liquidity and size floor.** **BUILT.** Median turnover ≥ **₹5 crore** and median
volume ≥ **50,000 shares**, both over a **20-session** lookback. There is **no minimum share
price and no market-cap floor** — a ₹30 stock passes if it turns over enough. **YOURS:**
whether you want a price or market-cap floor too.

**Q3 — Shorts: cash, F&O, or intraday.** **NOT BUILT.** Shorts are generated across the same
universe as longs with no segment restriction, so the output can contain a short in a
non-F&O name you cannot hold overnight in cash. This is a real gap — one of the few places
the system could hand you an untradeable instruction. **YOURS**, and worth answering early.

## 2. Market Regime

**Q4 — What is checked first.** **BUILT.** Five inputs, each scored −1/0/+1 and summed:
NIFTY trend, India VIX (percentile of 1y, not level), gamma positioning, global cues, and
breadth (% above 50DMA plus advance/decline). Range −5…+5.

**Q5 — Does regime change what is scanned, or just the bar?** **BUILT — it only moves the
bar.** Aggressive ⇒ floor 72, selective ⇒ 76, defensive ⇒ 80. Regime never disables a
direction and never generates a trade. So on a strongly risk-on day the engine still scans
shorts; it just demands more of everything. **YOURS:** if you actually stop looking at the
opposite direction in a trending week, that is a different design and worth saying.

## 3. Sector Selection

**Q6 — What says "this sector is leading".** **BUILT.** 19 sector composites built from
constituent daily bars (not index feeds), scored on excess return versus an all-stock
benchmark over **20 and 60 sessions**, plus acceleration (20d − 60d). Output is a state
(Leading / Weakening / …) and a percentile. Those two horizons are fixed.

**Q7 — Strong stock in a weak sector.** **BUILT — yes, it will take it.** Sector leadership
**scores, it does not gate** (weight 0.10). This is deliberate and load-bearing: an earlier
build gated on sector and discarded exactly the setups the spec exists to find.

## 4. Stock Relative Strength

**Q8 — How outperformance is seen.** **BUILT.** Computed, not eyeballed: excess return
versus the benchmark and versus the stock's own sector composite over **5, 20 and 60
sessions**, converted to a cross-sectional percentile across the ~473 liquid names. No ratio
line is drawn — the percentile is the number.

**Q9 — All horizons, or is acceleration enough?** **BUILT.** Blended, not all-or-nothing: RS
vs NIFTY 40%, RS vs sector 35%, **acceleration 25%**. Recent acceleration can carry a weaker
long-term picture. And an exceptional pattern can override mediocre RS, because setup
quality is its own weighted module and nothing about RS is a gate.

## 5. The Technical Toolkit

**Q10 — Liquidity sweep, full definition.** **BUILT & MEASURED** — the only setup with a
positive measured edge. Level: the extreme **daily** pivot in the prior 40 bars excluding
the last 8, with a shelf score from how many bars traded within 1.5% of it. Sequence: swept
within the last 8 bars, then reclaimed by the latest **daily close**. Volume is **not**
required. Second sweeps are not treated specially. Measured **+0.12R** net over 630 admitted
trades. **YOURS:** you may want a 5- or 15-minute confirmation instead of a daily close —
the code confirms on the daily bar.

**Q11 — Flag and pole.** **BUILT & MEASURED — and it loses money.** Pole ≥ **8%** over 20
bars; flag ≤ 8 bars, retracement ≤ 50% of the pole, must hold ≥ 60% of the move; ATR
contraction scores but is not required. Entry is a stop order through the flag high.
Measured at **−0.97R** net — the worst thing in the system. As of 18 Aug it can no longer
reach the published page. **YOURS:** tell me whether my flag definition is *your* flag
definition, because the failure may be my implementation rather than your pattern.

**Q12 — Base breakout.** **BUILT.** Base = **8 bars**, depth ≤ **8%**, measured on the bars
ending one *before* the breakout bar. Confirmation = close beyond the base edge on **≥2×**
the base's average volume. Volume is the discriminator, not the price break. Fires rarely —
6 of 473 names on 14 Aug.

**Q13 — Moving averages.** **BUILT, as a scored input only.** EMA 20/50 on weekly and daily,
EMA 20 on 60m. They feed trend classification, which feeds the structure module (weight
0.12). **They are not a hard filter** — wrong side of an MA scores worse, it does not
disqualify. The one MA-adjacent hard rule is the weekly veto in Q14.

**Q14 — Chart order, and what the weekly disqualifies.** **BUILT.** Order is weekly → daily
→ 60m/120m → 15m. The veto: **a long is refused if the weekly trend is DOWN, a short if it
is UP**. Sideways passes. This runs free in stage one, and was added because a name
published on 14 Aug showed "W down" and had been admitted purely because higher-timeframe
trend contributed to no gate at all.

**Q15 — Timeframe sync.** **BUILT, partially — and your worksheet's own flag is correct.**
Weekly+daily agreement is enforced only as the directional veto above. The 60m/120m pair is
tested properly by the carry gate (120m setup present, volume contracted-then-expanded, plus
a score floor of 60). **There is no requirement that all four timeframes agree.** Note the
carry gate applies to base-breakout and continuation **only** — reclaim is exempt because
applying it there cut measured edge from +0.30R to −0.07R.

**Q16 — ADR and ADX.** **ADR: BUILT. ADX: NOT BUILT — it does not exist anywhere in the
codebase.** ADR% (mean of (high−low)/low over 20 sessions) and ATR% feed a 4R feasibility
check, which *is* a hard filter — it rejected 2 names on 14 Aug. But there is no ADR
*minimum* as such, and no ADX gate on flag/pole. **YOURS:** if ADX is part of how you judge
a flag, it needs building.

## 6. Catalyst

**Q17 — Drop entirely, or keep as a non-rejecting bonus?** **MEASURED — and the evidence
supports dropping it or keeping it non-rejecting.**

It was made a rejecting hard filter on 18 Aug at your instruction, measured twice that day,
and switched **off by default** because neither measurement supported it. Per setup it
*removed* edge from both profitable setups: base-breakout +0.01R with a catalyst against
+0.72R without; reclaim +0.06R against +0.12R.

Two things to weigh before you answer:

- Armed, it refuses ~93% of candidates. On 14 Aug it would have refused **PIIND — the only
  name published that day.**
- **352 of 617 stored catalyst records were results filings scored a flat neutral 50** —
  "this company reported", direction unknown, because nothing opened the attached PDF. So
  "has a catalyst" largely meant attendance. That is now fixed
  (`intelligence/results_pdf.py`), but the fix is unmeasured.

Your worksheet says you trade purely off charts. If that is true, the honest answer is
**drop it** — and the measurement agrees with you. Say so and I will remove the module and
redistribute its 0.12 weight rather than leave dead weight in the score.

## 7. Entry, Stop & Exit

**Q18 — Stop placement, widest acceptable.** **BUILT.** Not the confirming bar's low — the
**nearest 15-minute swing pivot at least 0.5% away**, so the stop clears the noise band and
is a genuine invalidation. Band is **0.5%–1.5%**; outside it the trade is *refused*, never
re-fitted. This rule alone rejects roughly half of everything found and is why wins can be
4× losses. **A stop is never widened to admit a candidate.**

**Q19 — Resting orders; stops moved wider.** **YOURS.** The system places no orders and
holds no credentials that can trade. How often you have widened a stop is exactly what the
code cannot know, and your worksheet is right that it is the most important thing to tell me.

**Q20 — Is 4R from fill or planned entry?** **BUILT — from the fill.** The card says so:
*"4R is measured from the fill: 2,435.70 assumes entry at 2,490.10 with 13.60 of risk."* Fill
worse and the target moves; the R multiple is preserved, not the price.

**Q21 — Early exits, and what got away.** **YOURS.** The backtest holds to stop, 4R target,
or a 5-session time stop, with no early-exit logic, so it cannot tell you what your own
early exits have cost.

**Q22 / Q23 — Chasing a fill; fills outside the zone.** **BUILT — this is the "valid fill
band" your worksheet flags as unexplained.** It is *not* a tolerance around the entry, which
is why it looks lopsided. The stop is a fixed structural level, so the band is every fill
that still leaves the stop inside 0.5–1.5%:

    entry_min = stop / (1 + 0.015)      entry_max = stop / (1 + 0.005)     [short]

On PIIND: stop 2,503.70, entry 2,490.10 — a 0.55% stop, near the *tight* end, so nearly all
the remaining room lay below, giving 2,466.70–2,491.24. The asymmetry is the rule working,
not a defect. The page now says this in words. **YOURS:** how far you actually chase.

## 8. Sizing & Capacity

**Q24 — Capital and risk per trade.** **Risk per trade is BUILT at ₹5,000** — quantity is
`5000 ÷ risk-per-share`, which is where PIIND's 367 shares came from. **Total capital is not
modelled at all**, so the system cannot tell you whether a position is too large for your
account. **YOURS**, and worth setting properly.

**Q25 — Max concurrent positions.** **BUILT as an output cap, not a portfolio limit.** Two
setups published per day; the backtest allows one open position per symbol. There is no
concept of total open positions, correlation between them, or sector concentration.
**YOURS.**

## 9. Scale & Realistic Output

**Q26 — Trade at 470+ scale before re-testing?** **YOURS — but decide knowing the problem is
worse than the worksheet says.** The 80 symbols were not a random sample: with no explicit
list the backtest ranks *today's* candidates by setup quality, takes the top 80, then
replays their history. Trade decisions stay point-in-time; **the universe selection does
not.** So **+0.11R is an upper bound**, and names that stopped qualifying are absent
entirely. The gate-on/gate-off comparison survives this (same names both sides); the
absolute expectancy does not transfer to a 473-name scan. A proper re-test means passing a
symbol list frozen *before* the window — the parameter exists, it just costs network time.

**Q27 — How many genuine patterns a day?** **MEASURED: 0–1 is what the live pipeline
produces**, which matches your ~10–15/month brief closely. On 14 Aug: 473 liquid → 43 with a
setup → 1 published. Do not be misled by the backtest's 2,545 trades — that counts every
15-minute trigger with the quality floor and daily cap switched **off**, because a gate
cannot be measured against trades it never saw. The two numbers count different things.

## 10. Reality Check

**Q28 — Of your last 20 signals, how many did you take?** **YOURS**, and the single most
valuable thing you can give me. Your worksheet is right that these become fixtures directly:
each one turns into a regression test every future change must keep passing.

**Q29 — Does "works" mean hit 4R, or offered 1:4?** **The system means hit 4R.** A trade
counts as a win only if the target was actually reached before the stop, and a bar touching
both books a **loss**, because intraday sequence is unknown and resolving it favourably is
how backtests invent edges. Measured win rate **24%** against a **20%** break-even at 4R — a
thin margin, which matters: some of what looks broken on a given day is normal variance
around a small edge, not a bug.

**Q30 — Trade log.** **YOURS.** A journal command exists (`asymmetry journal log SYMBOL
taken|skipped --price --qty --note`) which records what the system said against what you
did. It is built and unused. Your suggested columns are close to what it already stores.

---

## The five answers that would change the code fastest

1. **Q17 — catalyst in or out.** You say you trade off charts; the measurement agrees. One
   word and 0.12 of the score is redistributed to factors that discriminate.
2. **Q3 — shorts and segment.** The system can currently emit a short you cannot hold.
3. **Q28 — your 20 signals.** Turns opinion into fixtures.
4. **Q19 — have you ever widened a stop.** Decides whether the 0.5–1.5% band is realistic
   for you or theatre.
5. **Q11 — is my flag your flag.** The worst-performing setup may be my definition rather
   than your pattern.

## Two answers I owe you, not the other way round

- **The quality score has never been measured.** The hard filters and the carry gate have;
  the nine weighted modules and the 72 threshold on top of them have not. That measurement
  is running now.
- **12% of the score is currently a constant.** Catalyst returned exactly 50.0 for 683 of
  688 replayed trades, and a constant cannot rank anything. Directly relevant to Q17.
