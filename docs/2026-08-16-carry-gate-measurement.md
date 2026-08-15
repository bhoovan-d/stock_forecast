# The 60m/120m carry gate — measured

**16 Aug 2026 · 2,527 replayed 15-minute triggers · 80 symbols · 50 sessions**

The engine picked technically interesting stocks but never proved a stock was in a
continuation regime with enough fuel to carry 1–5 sessions. This is what closing that gap
measured.

---

## Headline

| Cohort | n | Win rate | Mean R | Net of costs |
| --- | --: | --: | --: | --: |
| Every trigger (gate off) | 2,527 | 10% | −0.46 | **−0.63R** |
| What the engine now takes | 730 | 24% | +0.28 | **+0.11R** |

Net expectancy moves **+0.74R per trade**, and 24% clears the 20% break-even a 4R target
requires. This is the first configuration of the engine that is net positive after costs.

**Where the gain comes from matters more than its size.** Almost all of it is *removing*
continuation trades — 1,780 losing triggers cut to 40 — not selecting better ones. The gate
is doing subtraction. Calling that an edge would overstate it.

---

## By setup

| Setup | all n | all mean R | gated n | gated mean R | gate applies? |
| --- | --: | --: | --: | --: | --- |
| base-breakout | 65 | +0.75 | **8** | **+1.29** | yes |
| continuation | 1,780 | −0.80 | 40 | −0.25 | yes |
| reclaim | 682 | +0.30 | 682 | +0.30 | **no — exempt** |

Two findings drive the design:

**The gate improves base-breakout.** 65 → 8 trades, +0.75R → +1.29R. Coherent rather than
coincidental: a base breakout on volume *is* a continuation-regime claim, so a carry test
should reinforce it. This is the setup added because LGEINDIA — a 9.6% expansion out of a
3.7% base on 36.8× volume — was invisible to the engine's previous two setups.

**The gate destroys the edge on reclaim, so it is exempt.** Applied globally it cut reclaim
from +0.30R to −0.07R and 25% to 18%. A sweep-and-reclaim is a counter-trend entry by
construction — price has just taken out a prior low — so a continuation test selects the
reclaims that are already extended and aligned, which are precisely the ones with the least
asymmetry left.

**Continuation still loses money after gating** (−0.42R net). The gate improves it fourfold
and it remains unprofitable. It stays off the published site.

---

## The exemption, confirmed by the trades themselves

The twenty best trades in the sample are **all reclaims, with carry scores between 7 and
43** — every one far below the gate's floor of 60. Had the gate applied to reclaim, all of
them would have been rejected.

| Symbol | Dir | Setup | Carry | R | MAE | MFE | Bars |
| --- | --- | --- | --: | --: | --: | --: | --: |
| ZYDUSLIFE | short | reclaim | 19 | +4.00 | −0.50 | +4.40 | 34 |
| SARDAEN | long | reclaim | 7 | +4.00 | −0.26 | +4.31 | 45 |
| ABFRL | long | reclaim | 17 | +4.00 | −0.16 | +4.76 | 4 |
| ABFRL | long | reclaim | 23 | +4.00 | −0.59 | +6.41 | 28 |
| AMBUJACEM | long | reclaim | 15 | +4.00 | −0.38 | +6.66 | 20 |
| ACC | long | reclaim | 11 | +4.00 | −0.31 | +5.25 | 20 |

MAE is the worst excursion against the position, MFE the best in favour. Together they say
whether a trade carried cleanly or bled first — a more direct read on quality than win rate.
These have shallow MAE and MFE beyond the 4R target, which is the shape being hunted.

---

## What the numbers do not support

**n=8 for the base-breakout result.** The 95% interval on 43% from eight trades spans
roughly 10–82%. It agrees with the mechanism; it does not establish it. Tightening the score
floor from 50 to 60 moved this cohort from 9 trades/+1.59R to 8 trades/+1.29R — a
one-trade difference, which is how much weight that comparison can bear.

**Carry score is not a per-trade quality signal.** RADICO scored 96, the highest in the
sample, and stopped out in one bar with MAE −2.24 and zero favourable excursion. The score
grades a regime, not a trade.

**Losses cluster in single names.** GLENMARK supplied 9 of the 20 worst trades and
BALKRISIND 7 — sequential re-entries into the same chopping stock. The backtest already
prevents *overlapping* positions per symbol, but not repeated re-entry after a stop. A
cooling-off rule per symbol is untested and looks worth measuring.

**Sample limits.** Intraday history is capped at roughly 60–80 days upstream, so this is one
market regime. A bar touching both stop and target books a loss, since intraday sequence is
unknown.

---

## Engine changes behind these numbers

- **`engines/carry.py`** — the 60m/120m rung. 120m is folded from 60m *by position within
  each session*: NSE returns seven 60m bars a session (09:15…15:15), so a clock-based
  `120min` resample would straddle the 09:15 open and mix two sessions into one bar.
- **Two gating conditions** — a 120m structure to continue from, and a volume
  contraction→expansion sequence. Everything else scores. Making all eight conditions gating
  admitted **2 of 2,527** triggers, which is unmeasurable rather than strict.
- **Fail closed** — no 60m data means the regime is unproven, which rejects. JYOTICNC
  published on 14 Aug while its 60m fetch had failed outright, because the read decided
  nothing.
- **Counter-trend veto** in stage 1 — a long needs weekly and daily trend not down. Free,
  since both were already computed and discarded.
- **`structure` score fixed** — it was being fed the setup detector's own quality, which is
  how a name in a weekly downtrend scored 90 on "structure".
- **`detect_base_breakout`** — a tight base, then expansion on a volume surge. The base is
  measured strictly on bars *preceding* the breakout bar, so a signal can never be a
  relabelling of the move it claims to catch. It fires on LGEINDIA on 14 Aug and is silent
  on the 13th. Universe-wide it hit 6 of 473 liquid names.

---

## Reproduce

```bash
uv run asymmetry v3-backtest --symbols 80 --horizon 5
```

Roughly 16 minutes: 60m history is fetched per symbol and the carry test runs at every
decision point, sliced by timestamp so a decision at 11:15 cannot see the 14:15 bar.
