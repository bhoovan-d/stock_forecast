# V3 point-in-time universe measurement — 9 September 2026

## Decision

The carry-gated aggregate survives removal of today's-universe selection bias, but this is
**not yet sufficient evidence to fund V3 as one blended strategy**. On the frozen-universe
sample it returns **+0.11R net per trigger** (1,449 gated trades), while the ungated engine
returns **−0.68R**. That positive aggregate contains three materially different strategies:

- reclaim: +0.13R net, n=1,324;
- base-breakout: +1.59R net, but only n=22 after gating;
- continuation: −0.42R net, n=103 after gating.

Continuation does not survive. Base-breakout is promising but far too thin to fund on this
measurement alone. Reclaim is the only sizeable positive setup cohort. The next evidence is
the independent forward record, not threshold tuning or another universe selection.

## Protocol

- Local bhavcopy history was refreshed through 2026-09-09 (253 stored sessions before the
  update; 47,457 new daily rows added).
- Universe frozen at 2026-07-13 using the existing 20-session liquidity gate: median turnover
  at least ₹5 crore, median volume at least 50,000, and trading on at least 80% of sessions.
- Snapshot: `data/universe-2026-07-13.json`, 1,034 symbols. It uses every qualifying EQ-series
  bhavcopy symbol and no index-membership list.
- Headline sample: 150 symbols selected uniformly with seed 1; 149 had enough stored daily
  history to enter the replay.
- Replay: 5-session horizon, real 15-minute triggers, ambiguous stop-and-target bars booked as
  losses, score modules reconstructed against the full 463-name live liquidity panel.
- Costs: 0.12% round trip plus 0.05% slippage, divided by each trade's own stop percentage.
  The historical 1.0%-stop scalar is shown separately and is not used for net expectancy.

The snapshot date is near the beginning of Yahoo's current 60-calendar-day intraday window.
Calling it “60 trading sessions” would overstate the available history.

## Frozen-universe result (sample 150, seed 1)

| Cohort | n | Win rate | 95% Wilson | Gross R/trade | Cost R/trade | Net R/trade |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All triggers, gate off | 5,478 | 10.0% | 9.2–10.8% | −0.47 | 0.22 | **−0.68** |
| Carry-admitted, blended | 1,449 | 26% | 23.8–28.3% | +0.34 | 0.23 | **+0.11** |
| Base-breakout, gate off | 107 | 46% | 36.9–55.4% | +1.33 | 0.20 | +1.13 |
| Base-breakout, gated | 22 | 55% | 35.1–73.5% | +1.77 | 0.18 | **+1.59** |
| Continuation, gate off | 4,047 | 4% | 3.4–4.6% | −0.79 | 0.21 | −1.00 |
| Continuation, gated | 103 | 16% | 10.2–24.3% | −0.22 | 0.20 | **−0.42** |
| Reclaim (carry measured, not gated) | 1,324 | 26% | 23.7–28.4% | +0.36 | 0.23 | **+0.13** |

Overall, 5,387 of 5,478 triggers resolved at a stop or target. The exact overall win-rate
denominator is therefore 5,387; the per-setup Wilson endpoints use the rendered cohort rate
and cohort n because the original cohort renderer rounded the rate and did not print its
resolved denominator. Endpoints are consequently approximate to about 0.2 percentage points.
The report now prints an exact overall Wilson interval for future runs.

### Stop-distance cost cohorts

| Stop bucket | n | Gross R | Cost R | Net R |
| --- | ---: | ---: | ---: | ---: |
| 0.5% | 2,315 | −0.44 | 0.28 | −0.72 |
| 1.0% | 2,527 | −0.49 | 0.18 | −0.67 |
| 1.5% | 636 | −0.50 | 0.12 | −0.63 |

The old midpoint calculation reports 0.17R for every trade. Actual mean cost is 0.216R;
the old scalar therefore overstates net expectancy by roughly 0.046R per trigger here.

## Hindsight-path comparison

The compatibility command (`--symbols 150`) found only 95 symbols in today's quality-ranked
Stage 1 candidate set, so this is not an equal-size sample. It is retained to show the
direction and scale of the selection effect, not as a controlled A/B estimate.

| Cohort | n | Win rate | 95% Wilson | Gross R/trade | Net R/trade |
| --- | ---: | ---: | ---: | ---: | ---: |
| All triggers, gate off | 3,473 | 9.8% | 8.8–10.8% | −0.47 | −0.70 |
| Carry-admitted, blended | 918 | 25% | 22.3–27.9% | +0.28 | +0.05 |
| Base-breakout, gated | 20 | 56% | 35.1–75.0% | +1.76 | +1.55 |
| Continuation, gated | 39 | 13% | 5.7–26.9% | −0.36 | −0.57 |
| Reclaim | 859 | 25% | 22.2–28.0% | +0.27 | +0.04 |

The frozen sample is not worse than the hindsight path; its gated blend is 0.06R higher.
That does not validate today's quality ranking—the comparison populations differ, and the
quality threshold was reconstructed but not used as a historical entry gate. It does show
that the earlier +0.11R aggregate is not explained solely by selecting today's symbols.

## Coverage limitations

At least twelve bhavcopy-valid sampled symbols returned no Yahoo 60-minute history during the
run. The replay fails the carry test closed when hourly history is absent; reclaim remains
measurable because carry is descriptive rather than a gate for that setup. One sampled symbol
had fewer than 100 stored daily bars and was not tested. These are data-coverage exclusions,
not strategy rejections, and should be counted explicitly in the next measurement format.

The catalyst comparison is also not an OOS catalyst test: 1,235 decisions predate the local
catalyst store, and historical RSS news cannot be reconstructed. No catalyst threshold was
tuned or armed from these figures.

## Funding implication

Do not fund “V3” from the +0.11R blended headline. Keep continuation out of any funded path;
its interval and expectancy remain adverse after the carry gate. Treat base-breakout as a
forward-test candidate because its gated n is only 22. Reclaim has the only combination of a
positive net estimate and a substantial sample, but capital still waits for the new V3 JSONL
record to demonstrate that live scans, publication cuts, data availability and settlement
produce the same distribution prospectively.
