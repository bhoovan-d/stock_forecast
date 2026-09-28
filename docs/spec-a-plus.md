# The A+ specification — four filters, and what they can honestly return

*24 Aug 2026.* A rebuild of the selection layer around one principle: **fewer, better
trades**. Written against the measured record in `docs/2026-08-16-carry-gate-measurement.md`
and `docs/2026-08-19-quality-score-measurement.md` rather than from scratch, because those
two files already contain the answer to most of the design questions — and one uncomfortable
answer to the target.

---

## 0. The target, answered before the design

**The brief asks for ~80% win rate at a minimum 1:4 reward-to-risk. That is not achievable,
and it is not close.**

The two numbers are not independent. At a fixed 4R target, expectancy is
`w × 4 − (1−w) × 1 − costs`. Solving:

| Win rate at 4R | Expectancy before costs |
| --: | --: |
| 20% | 0.00R — break-even |
| 28% | +0.40R |
| 40% | +1.00R |
| **80%** | **+3.00R per trade** |

A system returning +3.00R per trade, risking a fixed fraction per position, compounds faster
than any documented equity strategy, discretionary or systematic. If a backtest here ever
prints it, the backtest is wrong — most likely resolving stop-and-target-in-one-bar
favourably, which is exactly why this codebase books that case as a loss.

**What the record actually supports.** The best cohort ever measured in this engine is the
top score quartile of carry-exempt reclaims: **+0.397R net, implying a win rate near 28%**.
That is on a hindsight-selected 80-symbol universe, so it is an upper bound
(`docs/2026-08-17-published-numbers.md`). Nothing has traded live.

So the honest restatement of the brief:

> **Keep the 1:4 minimum. Abandon the 80% win rate.** The realistic A+ target is a
> **25–32% win rate at 4R**, an expectancy of **+0.25R to +0.60R per trade** — and the
> design goal is to raise expectancy by *cutting trade count*, not by raising the hit rate.

Losing 7 trades in 10 is the normal operating state of a 4R system. Any rule added to make
the win rate feel better will cut the tail that pays for everything.

---

## 1. Universe and timeframes

**Universe:** NSE F&O stocks (~190 names). Narrower than the current 473-name liquid scan,
and better for this purpose — every name has an options chain, borrow for shorts, and depth.
The Rs 5 crore median 20-day turnover floor (`min_median_turnover_inr`) stays, but inside
the F&O list essentially everything clears it, so it is a safety rail rather than one of the
four filters.

**Timeframes — three rungs, because each answers a different question:**

| Rung | Question | Data available |
| --- | --- | --- |
| Weekly + Daily | which stock, which direction, is it strong | EOD, zero network calls |
| 60m (120m folded) | is the higher-timeframe structure intact | 60m, ~730 days |
| 15m | where exactly, and is the stop close enough | 15m, ~60 days |

**Holding period: maximum 5 trading sessions**, hard time stop. This is what the 4R
feasibility test is calibrated against — `move_feasible` allows `min(ADR, ATR) × √5` of
travel. Change the horizon and that constant is wrong.

---

## 2. The four filters

Four, and nothing else may reject. Each is stated with what it costs, so it can be argued
with.

### Filter 1 — Direction (a veto, not a signal)

```
LONG  requires: weekly trend not down AND daily trend not down
SHORT requires: weekly trend not up   AND daily trend not up
```

Nothing more. This does not select; it refuses the one combination with no thesis at all — a
counter-trend entry against a counter-trend backdrop.

*Why it is cheap:* both trends are already computed in stage one and were being discarded.
Adding the veto cost zero network calls.

*Why it is not stronger:* the setup this system trades is itself counter-trend on the 15m — a
sweep is entered against the immediate move. Demanding alignment on every rung is precisely
how the carry gate destroyed reclaim (see Filter 3). Two rungs of agreement, one rung of
disagreement, is the shape being hunted.

### Filter 2 — Relative strength (the strongest measured module)

```
LONG  requires: RS vs NIFTY ≥ 80th pctile AND RS vs own sector ≥ 70th pctile
SHORT requires: RS vs NIFTY ≤ 20th pctile AND RS vs own sector ≤ 30th pctile
```

Percentiles computed point-in-time against the **full F&O universe**, never against the
scan's own shortlist — a percentile against a hand-picked list is a different number and
measures a different engine.

*Why it earns its place:* in the ablation, `rs_vs_sector` and `rs_vs_nifty` are the two
largest contributors to the decile decision (−0.408R and −0.386R when removed and the rest
renormalised). They are the only modules whose removal collapses the ranking. This is the
best-evidenced component in the engine.

*Percentile rather than an absolute cut* because an absolute RS threshold drifts with the
index.

*Sector leadership scores, it does not gate.* Gating on sector is what made an earlier build
discard the setups this specification exists to find.

### Filter 3 — Liquidity sweep with intact HTF structure

The only permitted setup. Exact definition, as implemented in `detect_liquidity_sweep`
(`src/asymmetry/engines/setups.py:64`):

```
lookback         40 bars
cluster band     1.5% around the anchor pivot
recent window    last 8 bars      — where the sweep must occur
prior structure  bars 1..32       — where the shelf must be found

1. Shelf     the extreme fractal pivot (window=2) in the PRIOR bars only,
             plus a count of bars that traded into a 1.5% band around it
2. Sweep     within the last 8 bars, price trades beyond that level
3. Failure   no follow-through
4. Reclaim   the latest close is back on the original side of the level

quality = 0.45·depth + 0.25·touch + 0.30·reclaim
  depth   = clip(100 − sweep_depth_pct × 25, 20, 100)   shallow sweep is better
  touch   = clip(50 + touches × 18, 50, 100)
  reclaim = clip(reclaim_pct × 40, 10, 100)
```

A *cluster* of prior pivots rather than a single one is the point: one stray low is noise,
several lows within ~1.5% of each other is where stops actually sit.

The shelf must be located from bars *preceding* the sweep. Searching the whole window lets
the sweep's own low become the anchor, after which nothing has been swept by definition and
every real setup is missed. This is asserted in `tests/test_carry.py` against real bars, not
assumed.

**HTF structure requirement — computed and reported, but it does not gate this setup.**
120m is folded from 60m **by position within the session** (NSE returns seven 60m bars a
session), never by a clock-based resample, which would straddle the 09:15 open and mix two
sessions into one bar.

*Why the HTF read does not gate — this is measured, not a preference.* Applying the carry
gate to reclaim cut it from +0.30R to −0.07R and 25% to 18%. A sweep is a counter-trend entry
by construction, so a continuation test selects the reclaims already extended and aligned —
the ones with the least asymmetry left. **The twenty best trades in the 2,527-trigger sample
were all reclaims with carry scores of 7–43, every one below the gate's floor of 60.** Gating
on HTF continuation would have rejected all twenty.

What the HTF read *does*: it is recorded (`carry_passed` vs `admitted`) so the exemption
stays checkable, and a failed 60m fetch is a data outage that rejects, never a pass.

*Why no second setup:* base-breakout measured +1.29R gated — on **8 trades**, which agrees
with the mechanism and establishes nothing (the 95% interval on 43% from eight trades spans
roughly 10–82%). The high-tight flag loses money even after gating (−0.25R). One setup that
is well understood beats three where two are unproven.

### Filter 4 — Trigger geometry (the filter that does most of the rejecting)

On the 15m, at the confirming bar:

```
Entry   the current close. The reclaim IS the confirmation. Do not wait for a
        break of the session extreme — that puts entry at the top of the day and
        pushes the nearest structural stop several percent away, so the setup
        that actually paid becomes invisible.

Stop    the nearest 15m fractal pivot (window=2, last 60 bars) that is at least
        0.5% from entry. Nearest-of-the-qualifying, not nearest outright:
        intraday pivots cluster densely and the closest is usually inside noise,
        where it is not an invalidation but a coin flip.

HARD BAND  stop distance must land in 0.5% – 1.5% of entry.
           Outside the band the setup is REFUSED. Never widened to admit it,
           never tightened to manufacture the R multiple.

Target  entry ± 4 × risk, and it must pass move_feasible:
           capacity = min(ADR%, ATR%) × √5
           the required 4R move% must be ≤ capacity
        The LOWER of the two volatility estimates, always — taking the friendlier
        of two estimates is how an unreachable target gets waved through.

Veto    a weekly pivot between entry and target. Weekly only; daily and intraday
        levels are noise at this horizon.
```

*Why this is the real filter:* the 0.5–1.5% band alone rejects roughly half of everything
found. It is also the entire reason a win can be 4× a loss — it forces the entry to sit right
on top of its own invalidation, which only happens at the moment a sweep completes.

**Publish the valid-fill band.** The stop is a fixed structural level, so a fill away from
the quoted entry changes the stop *percentage*, and far enough out the trade no longer
satisfies the rule it was screened under. Solve the same constraint for the fill:

```
long:  entry_min = stop / (1 − 0.005),  entry_max = stop / (1 − 0.015)
short: entry_min = stop / (1 + 0.015),  entry_max = stop / (1 + 0.005)
```

It is not centred on the entry, and should not be. The asymmetry is the rule working.

---

## 3. The selectivity cut — where "A+" is actually enforced

The four filters produce a *valid* trade. They do not produce a *rare* one. Selectivity is a
fifth step, and it is a **ranking cut, not a filter** — the distinction matters because a
ranking cut can be dialled without changing what the system believes.

```
Score  = the weighted composite, with the catalyst weight REMOVED and
         redistributed proportionally across the survivors.

ADMIT  only the top quartile of the score distribution, measured against a
       rolling 60-session distribution — not a fixed number.

CAP    2 published setups per session. Everything else is a watch-list row.
```

Removing the catalyst weight is arithmetic, not fitting: it sits at exactly 50.0 for **98% of
trades** (sd 0.9), and a constant cannot reorder anything at any weight.
`sector_leadership` and `volatility` also contributed nothing to the decile decision, but
they genuinely vary (sd 23.4 and 10.9) — that is a weaker finding, possibly collinearity with
`rs_vs_sector`, and must not be acted on from one window.

*Why the top quartile:* within reclaim alone, the top score quartile returned **+0.397R
against +0.139R / +0.017R / +0.161R** for Q1–Q3. A ~0.26R spread on ~167 trades per bucket,
and genuine within-setup discrimination rather than a proxy for setup type.

*Why rolling rather than fixed:* the current floors (72 / 76 / 80 by regime) were placed in
the upper tail of one observed score distribution in order to produce a *rate*. They are
calibrated to output volume, not to outcomes — nothing establishes that a 72 wins more often
than a 68. A rolling percentile makes that honest by construction.

*Regime affects only this cut.* Risk-off tightens the quartile to a decile. Regime never
generates a trade and never rejects one.

---

## 4. Conditions for standing down

Refuse the trade regardless of score:

1. **Stop outside 0.5–1.5%.** No exceptions. The whole edge rests on this one.
2. **4R needs more than min(ADR, ATR) × √5.** The target is arithmetically unreachable
   inside the holding period.
3. **A weekly pivot between entry and target.**
4. **No 60m data.** Fail closed — an unproven regime is not the same as an acceptable one.
   A name whose 60m fetch failed outright used to publish anyway.
5. **Both trends against the direction.**
6. **Same symbol stopped out within the last 5 sessions.** Untested, and recommended anyway:
   in the measured sample GLENMARK supplied 9 of the 20 worst trades and BALKRISIND 7 —
   sequential re-entries into the same chopping stock. The backtest blocks overlapping
   positions per symbol but not re-entry after a stop. **Publish this as a hypothesis, not a
   measured rule.**
7. **A results date inside the holding window.** A 5-day hold through an earnings print is a
   different trade with a different distribution, and there is no consensus estimate
   available here at any price — so the event cannot be handicapped, only avoided.
8. **Trigger found after 15:15.** The session is over once its final bar has *begun*. The
   setup is for the next session. The feed's 26th bar, stamped 15:30, is the closing print,
   not a 15-minute window.

**Not on the list, deliberately: a news catalyst.** It has been measured twice and neither
measurement supports it. Per setup it *removes* edge from the two setups that have any —
reclaim +0.06R with a catalyst against +0.12R without — and the largest like-for-like
comparison available (47 reclaims vs 630) puts the cost at ~0.06R per trade. The obvious
objection, that this tested "a filing occurred" rather than a judged expectation change, was
closed by backfilling the window through an LLM and repeating it: same verdict, and the
admitted reclaim cohort did not move by a single trade. What remains untestable is *news*,
which serves ~48h and cannot be backfilled — revisit with forward-collected data, never by
re-running history.

---

## 5. Expected frequency, and how confident that number is

This is an extrapolation, and it is the weakest number in this document.

**What is measured:** 682 reclaim triggers across 80 symbols and 50 sessions, with the score
floor and the daily cap both switched *off* — because a gate cannot be measured against
trades it never saw. That is ~13.6 triggers per session on 80 names.

**What the cuts do to it, arithmetically:**

| Step | Rate |
| --- | --- |
| Reclaim triggers, 80 names, no cuts | ~13.6 / session |
| Scaled to a ~190-name F&O universe | ~32 / session — linear scaling, optimistic |
| RS filter (top/bottom ~20% by construction) | ~6–8 / session |
| Top score quartile | ~1.5–2 / session |
| Daily cap of 2 | **~1.5–2 / session** |

That arithmetic says **30–40 a month, not a handful** — and it is wrong, in a direction that
is documented rather than guessed. Two things pull it down hard:

- The 80 backtest symbols are **the good tail**. `run_v3_backtest` with no explicit list
  ranks *today's* stage-one output by setup quality and takes the top N, then replays their
  triggers from up to fifty sessions earlier. The full universe contains the mediocre setups
  that selection removes, so the trigger rate does **not** scale linearly.
- The live pipeline behaves nothing like the replay: on 14 Aug, **43 candidates became 1
  published setup out of 473 names.**

**Honest estimate: 4–10 qualifying setups per month**, with entire weeks producing none.
Days with no setup are the correct output, not a failure. **If a change makes the engine
produce more, that is a red flag to investigate, not a success** — selectivity is answered
with patience, never a lower threshold.

Replacing this estimate with a measurement is stated and not yet run: pass the symbol list
explicitly, frozen from an F&O universe snapshot taken *before* the replay window, and
re-measure. The parameter already exists; the cost is network time, not new code.

---

## 6. Expected expectancy, with the error bars attached

| Scenario | Win rate | Mean R gross | Net of ~0.15R costs |
| --- | --: | --: | --: |
| Measured best cohort — upper bound | ~28% | +0.55 | **+0.40R** |
| Realistic, allowing for universe bias | 24–26% | +0.30 | **+0.15 to +0.25R** |
| Pessimistic — the edge is mostly selection bias | 20% | +0.15 | **0.00R** |

At 6 trades a month and +0.25R average, that is roughly **1.5R of expectancy per month**.
Real, and nothing like the ~18R/month that 80% at 4R would imply.

**Costs, and the trap underneath them.** `cost_roundtrip_pct` is 0.12% — brokerage, STT,
exchange and stamp duty, GST — for a *delivery* holding period. Cost in R is
`cost% / stop%`, so at a 0.8% stop this is ~0.15R and at a 0.5% stop ~0.24R. **A cost
constant is calibrated for a holding period and must never be reused across strategies.**
Charging this delivery constant to an intraday strategy overstated its cost 3.4× and produced
a headline loss that had to be retracted (`docs/spec-hma-pullback.md`). Report a slippage
range, never a point estimate, and never headline a mean-of-ratios when stop sizes vary.

---

## 7. What this system does not know

Everything above is conditional on this list, so it belongs next to the numbers rather than
in a footnote:

- **One market regime.** Intraday history is capped at ~60–80 days upstream. The entire
  measured record is a single window.
- **The universe is hindsight-selected.** +0.11R engine-wide and +0.40R for the top reclaim
  quartile are both upper bounds, and names that stopped qualifying are absent entirely. The
  *relative* claims survive this (same names on both sides); the *absolute* expectancy does
  not transfer to a full-universe scan.
- **Most of the measured gain is subtraction, not selection.** The carry gate's headline
  improvement came from removing 1,740 losing flag triggers. "Filters harder" is a much
  smaller claim than "picks better".
- **The score is not independently valid.** Ungated it *inverts* — the top decile is the
  worst bucket (−0.135R), because high scores attract flags: strong RS and clean structure is
  what a pole looks like. Score and gate are one mechanism and must be judged together.
- **Nothing has traded live.**

This is decision support. It contains no order-placement code and never will.
