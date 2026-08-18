# Does the quality score earn its weights?

*19 Aug 2026.* The first measurement of the scoring layer. Until now the replay exercised
`detect_setup` and `build_v3_plan` — the hard filters — and stopped, so the nine weighted
modules and the regime threshold sitting on top of them ran on engineering judgement alone.

Method: 2,579 replayed triggers, 80 symbols, 50 sessions, with `quality_score` reconstructed
at each decision through the live functions. RS and sector percentiles are computed
point-in-time against the **full 473-name liquid universe**, not the 80 replayed names — a
percentile against a hand-picked 80 is a different number and would measure a different
engine. Modules are scored in the traded direction, mirrored for shorts exactly as
`run_v3_scan` does it.

Ablation drops one module and **renormalises the survivors**. Without that, a shorter score
is being compared against a longer one on a different scale and every ablation looks
significant. The metric is the decision the floor actually makes: keep the top decile by
score, and compare it against the rest.

---

## 1. The score works — but only after the carry gate

| Carry-admitted (n=723) | net |
| --- | --: |
| score quintile 1 (lowest) | +0.064R |
| quintile 2 | +0.005R |
| quintile 3 | +0.145R |
| quintile 4 | +0.268R |
| **quintile 5 (highest)** | **+0.367R** |
| top decile vs the rest | **+0.306R lift** |

On the population the engine actually trades, the score ranks. That is a real result and the
first evidence the scoring layer does anything at all.

**Ungated, it inverts:**

| All 2,579 triggers | net |
| --- | --: |
| score quintile 1 (lowest) | −0.189R |
| quintile 5 (highest) | −0.845R |
| top decile vs the rest | **−0.135R** |

The highest-scoring bucket is the *worst*. The mix table says why: quintiles 1–4 of the
ungated population are ~100% reclaim, while quintile 5 is 30% continuation. **High scores
attract flags** — strong RS, clean structure, tidy pole — and the flag loses money. So the
score is not independently valid. It is valid *conditional on the carry gate having removed
the continuation trades first.* Those two components have to be judged as a pair, not
separately.

## 2. It is genuine ranking, not a proxy for setup type

The obvious objection to §1 is that the score might just be detecting "this is a reclaim".
Within reclaim alone — 669 of the 723 admitted trades:

| Reclaim only, by score quartile | net |
| --- | --: |
| Q1 | +0.139R |
| Q2 | +0.017R |
| Q3 | +0.161R |
| **Q4** | **+0.397R** |

A ~0.26R spread between top and bottom quartile on ~167 trades each. Not monotonic — Q2 sags
— but the top quartile stands clear. The score has real within-setup discrimination.

## 3. Three modules contribute nothing

Change in top-decile lift when each module is dropped and the rest renormalised, on the
admitted population:

| Module | weight | change when removed | module sd |
| --- | --: | --: | --: |
| rs_vs_sector | 0.12 | **−0.408** | 25.0 |
| rs_vs_nifty | 0.12 | **−0.386** | 24.5 |
| structure | 0.12 | **−0.309** | 25.6 |
| setup_quality | 0.10 | −0.154 | 16.0 |
| entry_quality | 0.08 | −0.092 | 14.5 |
| carry | 0.18 | −0.015 | 18.7 |
| **catalyst** | **0.12** | **+0.000** | **0.9** |
| **sector_leadership** | **0.10** | **+0.000** | 23.4 |
| **volatility** | **0.06** | **+0.000** | 10.9 |

**28% of the score does no work in this decision.** The five that do the work are RS
(both flavours), structure, setup quality and entry quality.

Two of those zeros are not the same kind of zero, and the difference decides what to do
about them:

- **Catalyst is a constant.** It sits at exactly 50.0 for **98% of trades** (sd 0.9). A
  constant cannot reorder anything at any weight — this is arithmetic, not an artefact of
  this sample, and it will hold in every window until the catalyst engine produces
  directional output.
- **`sector_leadership` and `volatility` genuinely vary** (sd 23.4 and 10.9) and still
  change nothing. That is a weaker finding: it may be collinearity with `rs_vs_sector`, or
  it may be that the decile cut is too coarse to register their effect. It should **not** be
  acted on from this alone.

Note also `carry` scoring only −0.015 as a *ranking* input. That is not an argument against
the carry gate — the gate is a pass/fail test and §1 shows it is what makes the whole score
work. It says the carry *score*, once past the floor, adds little to the ordering, despite
carrying the largest single weight at 0.18.

---

## What follows, and what does not

**Safe to act on now: remove the catalyst weight.** Justified by arithmetic rather than by
fitting — a module at one value for 98% of trades cannot rank. Redistribute its 0.12
**proportionally across the remaining eight**, preserving their relative balance. That is
neutral. Doing anything else with it is not.

**Not safe to act on: reweighting toward the modules that measured best.** Boosting
`rs_vs_sector` because it scored −0.408 here is fitting to one hindsight-selected 80-symbol
window, which is the exact failure this codebase keeps guarding against. It needs
out-of-sample confirmation — a symbol list frozen before the window — before any weight
moves on its evidence.

**Worth knowing about the published page.** "Cleared the quality floor" is doing real work
after the gate, and the 72 threshold is selecting a genuinely better cohort. That claim is
now measured rather than assumed. But it is measured on the same hindsight-selected
universe as everything else here, so treat the *magnitude* as an upper bound and the
*direction* as established.

## Caveats

- Same 80 hindsight-selected symbols as every other measurement here. Relative claims
  survive it; absolute expectancies do not.
- The top decile of the admitted population is 72 trades. Thin.
- The ablation metric is coarse: a module can matter without changing which trades land in
  the top decile, which is exactly how a "+0.000" can hide a real effect.
- One market period, ~50 sessions of 15-minute history.
