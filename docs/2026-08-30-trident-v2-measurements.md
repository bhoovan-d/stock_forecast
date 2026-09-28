# Trident v2 — what the four changes measured

*30 August 2026. NIFTY 500, screened to 368 names by the turnover floor, replayed on the
150 most liquid of those. 59 sessions of intraday history — the feed's cap, and the binding
constraint on everything below.*

This records what the v2 build measured, including the two findings that contradict what was
asked for. Both are kept as built and disarmed, in the same disposition as V3's catalyst
filter: the feature exists, reports, and does not gate, because the measurement does not
support gating.

---

## 1. The universe: 368 of 500 clear the floor

| Stage | Names |
| --- | --: |
| NIFTY 500 constituents | 500 |
| refused — median turnover below ₹25 cr/day | −132 |
| **cleared** | **368** |

Turnover distribution across the 500, in ₹ crore/day: p10 12.4, p50 59.1, p90 287.6. The
₹25 cr floor sits just below the 25th percentile, which is roughly where the owner aimed it.

### The spread filter does not work here, and no threshold rescues it

This is the first of the two contrary findings. The filter was requested, built, measured,
and is **off by default**.

There is no bid/ask feed reachable from this project — NSE is Akamai-blocked, Yahoo serves
OHLCV only — so the spread had to be estimated. Corwin-Schultz (2012) from daily high/low is
the standard choice. On this universe it is not measuring tradeability:

| | value |
| --- | --: |
| Spearman(estimate, median turnover) | **−0.15** |
| Spearman(estimate, median daily range) | **+0.31** |
| Median estimate, NIFTY 500 | 53.9 bp |
| Median estimate, NIFTY 50 | 41.1 bp |
| NIFTY 50 names refused by a 25 bp cap | **40 of 50** |

It tracks volatility about twice as strongly as it tracks illiquidity. Concretely, at 25 bp
it refuses ICICIBANK (₹1,209 cr/day, 25.1 bp) and BHARTIARTL (₹1,077 cr/day, 26.0 bp), while
the widest estimates in the whole index belong to NETWEB (156 bp) and HINDCOPPER (91 bp) —
the most volatile names, not the thinnest. Real quoted spreads on NIFTY 50 constituents are
low single-digit basis points.

No threshold fixes this, which is why it is disarmed rather than retuned: a cap loose enough
to admit ICICIBANK admits essentially everything, so the filter is either wrong or inert.
The estimate is still computed and printed on every row, so the finding can be re-checked
rather than taken on trust. `--require-spread` arms it for anyone who wants to argue.

**Turnover does the work.** It is a direct observation rather than an inference from range,
which is exactly why it survives and the estimator does not.

---

## 2. Entry timeframe: the 5-minute track loses to its own costs

Same five gates, same universe, same 59 sessions, same window. Only the bar size changes.
Reported at **4R**, because at 20R almost nothing resolves inside the available history.

| Entry TF | Setups | Win % | Median stop | Cost in R | Gross R | **Net R** | 95% CI on net |
| --- | --: | --: | --: | --: | --: | --: | --- |
| 5m | 213 | 23.9 % | 0.20 % | 1.205 | +0.219 | **−0.986** | −1.33 to −0.64 |
| 15m | 76 | 21.1 % | 0.32 % | 0.712 | +0.020 | **−0.692** | −1.22 to −0.17 |
| 30m | 21 | 19.0 % | 0.47 % | 0.499 | −0.039 | **−0.538** | −1.36 to +0.28 |
| 60m | 0 | — | — | — | — | — | — |

Break-even at 4R is a 20 % hit rate before costs.

Three things in that table, in order of how much they matter.

**The 5-minute track has the best raw hit rate and the worst result.** 23.9 % clears the
20 % break-even; +0.219R gross is a real, if small, positive. It still loses a full R per
trade, because the cost drag is **1.205R** — five and a half times the gross edge. This is
not a surprise, it is arithmetic: cost in R is `cost% / stop%`, and a 0.20 % stop against
0.17 % of round-trip cost surrenders 1.2R before the position is open.

**The ranking across timeframes is the cost curve, not a skill curve.** Gross expectancy
falls monotonically as the bar grows (+0.219 → +0.020 → −0.039) while cost in R falls faster
(1.205 → 0.712 → 0.499). The net column is the difference between two monotone series and
tells you almost nothing about which timeframe the *pattern* prefers.

**Every one of these is negative, and the 5m and 15m intervals exclude zero.** 213 and 76
finished trades is enough to say those two are genuinely losing on this window, not merely
unproven. The 30m interval still contains zero on 21 trades, which is the same "cannot
settle it" verdict the v1 measurement reached.

At 20R the picture is worse and thinner: 5m books 12 wins in 212 resolved for −1.021R net,
15m 5 in 72 for −0.337R, 30m **0 in 20** for −1.509R — the same zero-of-twenty as the
26 August run.

### 60m produces nothing, and the reason is the window

The 60-minute track returned zero setups across every symbol and session, and it cannot
return any. NSE serves seven 60-minute bars a session, of which four (09:15, 10:15, 11:15,
12:15) fall inside 09:15–12:45. A completed pattern needs five: three to form the imbalance,
one to retrace as the doji, one to confirm. With four, the earliest a doji can print is the
last bar in the window — so **the confirmation bar has nowhere to go**, and the track tops out
at gate 4 by construction.

This is structural rather than a verdict on the setup. **The transplanted kill zone is what
makes the 60-minute track impossible**, which is worth holding next to the window findings
below — and next to the timeframe study, which finds 60 minutes the *first* bar size where
continuation stops being hostile.

### Where candidates die

Distinct gate passes, all sessions and symbols pooled:

| Gate | | 5m | 15m | 30m |
| --: | --- | --: | --: | --: |
| 1 | context | 5,676 | 5,676 | 5,668 |
| 2 | gap formed | 15,462 | 5,493 | 2,548 |
| 3 | midpoint tagged | 6,421 | 1,882 | 584 |
| 4 | doji confirmed | 3,191 | 984 | 263 |
| 5 | confirmation closed | 213 | 76 | 21 |

Gate 5 is where 93 % of gate-4 candidates die on every timeframe. The get-ready is common;
the trigger is not. That ratio is the operational argument for alerting on gate 4 separately
— roughly one in fifteen get-readies becomes a trade, so a gate-4 alert is a reason to look,
never a reason to act.

---

## 3. The kill-zone window: the transplant is not carrying the strategy

The 09:15–12:45 block is a straight transplant of a forex London-session window and had
never been tested. Every window below was replayed on **identical fetched frames**, so no
cell can differ from another by which symbols happened to load.

30-minute entry, 4R, 150 names, 59 sessions:

| Window | Setups | Win % | Median stop | Gross R | Net R | 95% CI on net |
| --- | --: | --: | --: | --: | --: | --- |
| 09:15–11:45 | 4 | 25.0 | 0.56 % | +0.218 | −0.100 | −2.49 to +2.29 |
| 10:00–12:30 | 2 | 0.0 | 0.50 % | −1.000 | −1.487 | −2.01 to −0.96 |
| 10:45–13:15 | 9 | 37.5 | 0.46 % | +1.228 | +0.874 | −1.30 to +3.05 |
| 11:30–14:00 | 2 | 100.0 | 0.13 % | +4.000 | +2.658 | +2.34 to +2.98 |
| 12:15–14:45 | 5 | 0.0 | 0.41 % | −1.000 | −1.389 | −1.49 to −1.29 |
| 13:00–15:30 | 2 | 50.0 | 1.57 % | +1.500 | +1.112 | −4.43 to +6.66 |
| **09:15–12:45** (the transplant) | **21** | **19.0** | 0.47 % | −0.039 | **−0.538** | −1.36 to +0.28 |
| **09:15–15:30** (whole session) | **59** | **32.8** | 0.41 % | +1.071 | **+0.389** | −0.57 to +1.35 |

**Six of the eight cells produced fewer than ten trades and are excluded from every
comparison.** They are printed because hiding them would be worse, and because one of them
is instructive: 11:30–14:00 shows +2.658R with an interval of +2.34 to +2.98 that excludes
every other window's. It rests on **two trades, both winners**. An earlier draft of the
report would have announced on that basis that a window separates; the sweep now enforces a
ten-trade floor before a cell may take part, and that floor exists because of this cell.

What the two comparable cells say:

- **Restricting to the transplant costs about two-thirds of the setups** — 21 against 59 —
  and does not buy expectancy. It buys −0.538R against +0.389R.
- **Their confidence intervals overlap** (−1.36 to +0.28 against −0.57 to +1.35). So the
  full session is not *demonstrably* better, and the honest verdict is: the inherited window
  is neither vindicated nor convicted, and it is not what is deciding this strategy's result.
- The direction of the difference is nevertheless the opposite of the transplant's
  justification. If the London analogy transferred, the morning block should have been where
  the setups were; instead it is where two-thirds of them were discarded.

**Nothing here justifies adopting a new window.** A grid searched over 59 sessions always
has a best cell, and adopting it is fitting, not measuring. What the sweep does justify is
demoting 09:15–12:45 from an assumption to a parameter — which it now is, on every command.

---

## 4. The liquidity-sweep tag: recorded, not yet informative

Tagged and never gated, as specified. Finished trades at 4R, split by whether a sell-side
pool was taken and reclaimed before the gap formed:

| Entry TF | Cohort | n | Win % | Gross R | Net R |
| --- | --- | --: | --: | --: | --: |
| 5m | swept | 30 | 26.7 | +0.333 | −1.008 |
| 5m | not swept | 183 | 23.5 | +0.200 | −0.983 |
| 15m | swept | 23 | 26.1 | +0.304 | −0.422 |
| 15m | not swept | 53 | 18.9 | −0.103 | −0.809 |
| 30m | swept | 7 | 0.0 | −1.000 | −1.498 |
| 30m | not swept | 14 | 28.6 | +0.442 | −0.058 |

The 15-minute split is the one that looks like something: +0.304R gross for the swept cohort
against −0.103R for the untagged, on 23 and 53 trades. The 30-minute split points the
opposite way on seven trades. **Both are noise**, the intervals overlap heavily, and reading
either as evidence would be exactly the error the "tag now, filter never until the numbers
say so" instruction was written to avoid.

The right reading is that the tagging works and the comparison has started. It needs forward
samples, not a re-run of this window.

---

## What would change these conclusions

- **The cost constants.** Every net figure here is dominated by them. They are derived in
  `engines/trident.py` at delivery rates, because a 20R target off a sub-1 % stop is a
  multi-week hold. If the trade were genuinely intraday the constant would be 0.05 % rather
  than 0.12 % and the 5m net would improve by roughly 0.4R — still negative.
- **A longer intraday history.** 59 sessions is the feed's cap, and it is one market period.
- **Forward collection.** The only thing that can settle either the sweep tag or the window
  question, because both were measured on the data that would justify changing them.

Nothing here has been traded. The system places no orders and holds no credential that could.
