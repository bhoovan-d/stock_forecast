# Corrected V3 probability report

Evidence: 2026-07-14 to 2026-09-03; five-session gaps separate development, validation and final test.

## Plain-language result

The corrected replay did not find a strategy that is safe to fund. The table below shows how often each target was reached in the final period. The required win rates were not reached, the conservative estimates were not reached, and the final sample was much too short. The system therefore chooses no trade and remains in paper mode.

The raw columns explain what happened. Approval still uses only predictions made by the validation-selected model in the untouched final period.

| Setup | Target | Model | Training | Raw validation | Raw final result | Conservative | Conservative net | Model-selected final | Decision |
| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| reclaim | 2R | logistic | 340 | 54 | 7/24 (29.2%) | 0.0% | -1.24R | 0 | PAPER ONLY |
| reclaim | 3R | logistic | 340 | 54 | 6/24 (25.0%) | 0.0% | -1.24R | 0 | PAPER ONLY |
| reclaim | 4R | logistic | 340 | 54 | 6/24 (25.0%) | 0.0% | -1.24R | 0 | PAPER ONLY |
| base-breakout | 2R | empirical | 0 | 0 | 0/0 | — | — | 0 | PAPER ONLY |
| base-breakout | 3R | empirical | 0 | 0 | 0/0 | — | — | 0 | PAPER ONLY |
| base-breakout | 4R | empirical | 0 | 0 | 0/0 | — | — | 0 | PAPER ONLY |

## Exact decision groups

Each group is kept separate by market regime, stop size and entry time. A strong result elsewhere cannot approve it.

| Setup | Target | Regime / stop / time | Final trades | Wins | Conservative | Decision |
| --- | ---: | --- | ---: | ---: | ---: | --- |
| reclaim | 2R | defensive / medium / open | 3 | 0/3 | 0.0% | PAPER ONLY |
| reclaim | 2R | selective / medium / late | 1 | 1/1 | 100.0% | PAPER ONLY |
| reclaim | 2R | selective / medium / mid | 2 | 1/2 | 0.0% | PAPER ONLY |
| reclaim | 2R | selective / medium / open | 3 | 1/3 | 0.0% | PAPER ONLY |
| reclaim | 2R | selective / tight / late | 4 | 2/4 | 0.0% | PAPER ONLY |
| reclaim | 2R | selective / tight / mid | 8 | 1/8 | 0.0% | PAPER ONLY |
| reclaim | 2R | selective / tight / open | 2 | 1/2 | 50.0% | PAPER ONLY |
| reclaim | 2R | selective / wide / mid | 1 | 0/1 | 0.0% | PAPER ONLY |
| reclaim | 3R | defensive / medium / open | 3 | 0/3 | 0.0% | PAPER ONLY |
| reclaim | 3R | selective / medium / late | 1 | 1/1 | 100.0% | PAPER ONLY |
| reclaim | 3R | selective / medium / mid | 2 | 1/2 | 0.0% | PAPER ONLY |
| reclaim | 3R | selective / medium / open | 3 | 1/3 | 0.0% | PAPER ONLY |
| reclaim | 3R | selective / tight / late | 4 | 1/4 | 0.0% | PAPER ONLY |
| reclaim | 3R | selective / tight / mid | 8 | 1/8 | 0.0% | PAPER ONLY |
| reclaim | 3R | selective / tight / open | 2 | 1/2 | 50.0% | PAPER ONLY |
| reclaim | 3R | selective / wide / mid | 1 | 0/1 | 0.0% | PAPER ONLY |
| reclaim | 4R | defensive / medium / open | 3 | 0/3 | 0.0% | PAPER ONLY |
| reclaim | 4R | selective / medium / late | 1 | 1/1 | 100.0% | PAPER ONLY |
| reclaim | 4R | selective / medium / mid | 2 | 1/2 | 0.0% | PAPER ONLY |
| reclaim | 4R | selective / medium / open | 3 | 1/3 | 0.0% | PAPER ONLY |
| reclaim | 4R | selective / tight / late | 4 | 1/4 | 0.0% | PAPER ONLY |
| reclaim | 4R | selective / tight / mid | 8 | 1/8 | 0.0% | PAPER ONLY |
| reclaim | 4R | selective / tight / open | 2 | 1/2 | 50.0% | PAPER ONLY |
| reclaim | 4R | selective / wide / mid | 1 | 0/1 | 0.0% | PAPER ONLY |

No strategy cleared every rule, so funded alerts remain disabled.

The final window contains only 3 trading sessions with usable signals; the rule requires 20. More chronological history is required even if a displayed win rate looks attractive.

No carry-approved base-breakout trade survived in this replay, so that setup has no probability claim at all.

Closest result by the stated probability floors: reclaim at 4R (6/24, 25.0%; conservative 0.0%). It still has only 24 trades across 3 sessions, versus the required 100 trades and 20 sessions. Continue collecting it without lowering the thresholds.
