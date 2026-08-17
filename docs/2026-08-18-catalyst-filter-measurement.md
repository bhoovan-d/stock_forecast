# Measuring hard filter 5 (catalyst)

*18 Aug 2026.* The catalyst filter was added earlier the same day on instruction, and
`CLAUDE.md` recorded it as unmeasured. This is the measurement. **It does not support the
filter**, and the one number that appears to support it is a composition artefact.

---

## What had to be built first

The filter could not be measured at all as things stood. The catalyst store held **three
days** (11–13 Aug, 81 records) while the intraday replay reaches back roughly fifty
sessions. Tagging trades against that store would have marked every decision before 11 Aug
as "no catalyst" and measured *the collection start date* as though it were a fact about
the market.

Two pieces closed that gap:

* **`CatalystHistory`** separates **coverage** from **absence**. `covered(as_of)` asks
  whether the store holds any record inside that decision's lookback window at all. Trades
  outside coverage are **excluded from the comparison**, never counted as catalyst-free.
  Coverage is a property of the *date*, not the symbol: on a day the store knows about, a
  symbol with no record genuinely had no catalyst, which is the fact the live filter acts
  on.
* **`asymmetry catalyst-backfill`** collects historical BSE filings. Filings are the only
  catalyst source with an archive — the announcements API takes an explicit date range,
  while the news RSS feeds serve about 48 hours and cannot be backfilled at all.

Backfilled coverage: **48 days, 2 Jun → 14 Aug, 387 symbols.** After that, 2,438 of 2,545
replayed decisions fell inside coverage and 107 were excluded.

## The catalyst being measured is weaker than the one the spec means

The backfill ran **rule-routed only** (`--no-llm`), so "has a catalyst" here means **a
material filing occurred** — results, board meeting, dividend, a SAST threshold disclosure
— within five sessions. It does *not* mean a model judged an expectation change.

That matters in both directions. A results filing is scored a deliberate neutral 50 because
the numbers sit inside a PDF nothing here can read, so it marks attention rather than
direction. The LLM-scored records exist only for 11–13 Aug (55 of them). **The version of
this filter that the specification actually asks for remains unmeasured.**

## Result

80 symbols, 50 sessions, 2,545 triggers, gate off. Costs of ≈0.17R subtracted.

| Cohort | n | Win | Mean R | Net |
| --- | --: | --: | --: | --: |
| Every trigger | 2,545 | 9.9% | −0.47 | −0.64R |
| Carry-gate admitted | 723 | 24.3% | +0.27 | **+0.10R** |
| Covered by the catalyst store | 2,438 | 10.1% | −0.46 | −0.63R |
| — had a catalyst | 138 | 14.0% | −0.29 | −0.46R |
| — no catalyst | 2,300 | 9.9% | −0.47 | −0.64R |

Blended, requiring a catalyst looks like **+0.17R per trade**. That number should not be
quoted, because it reverses per setup:

| Setup | has catalyst | | no catalyst | |
| --- | --: | --: | --: | --: |
| | n | Net | n | Net |
| base-breakout | 17 | **+0.01R** | 37 | **+0.96R** |
| continuation | 74 | −0.90R | 1,666 | −0.97R |
| reclaim | 47 | **+0.06R** | 597 | **+0.18R** |

**On both setups that make money, requiring a catalyst makes them worse.** It is mildly
positive only on continuation, which loses heavily either way.

## Why the blended figure disagrees with every row beneath it

Simpson's paradox, driven by setup mix. Base-breakout is **12.3%** of the with-catalyst
cohort and **1.6%** of the without — a 7.6× over-representation of the highest-expectancy
setup — while continuation falls from 72% to 54%. The blended improvement is that
reweighting, not better selection inside any setup.

This is the same shape as the carry gate on reclaim, and the same lesson: a filter can look
useful in aggregate while removing edge from every cohort it touches. The backtest report
now prints the per-setup rows directly under the blended row so the two cannot be read apart.

## The decision-relevant cut: inside what the engine actually takes

The table above is the gate-off population, which the engine never trades. The live engine
applies the carry gate first, so the question that decides whether filter 5 should ship is
narrower: **among carry-admitted trades, does requiring a catalyst help?**

| Cohort (carry-admitted, covered) | n | Win | Net |
| --- | --: | --: | --: |
| Admitted — filter 5 off (today) | 688 | 25.3% | +0.147R |
| Admitted + catalyst — filter 5 on | 50 | 27.1% | **+0.189R** |
| Admitted, no catalyst — what filter 5 cuts | 638 | 25.1% | +0.144R |

At first reading that supports the filter: +0.19R against +0.14R. It does not survive being
opened up.

The with-catalyst cohort is **47 reclaims, 2 base-breakouts and 1 continuation**. Those
three non-reclaim trades carry **+6.49R of the cohort's +9.45R total — 69% of it, from 6% of
the trades.** Strip them and the only cohort with a usable sample says the opposite:

| Like-for-like, reclaim only | n | Net |
| --- | --: | --: |
| reclaim + catalyst | 47 | **+0.063R** |
| reclaim, no catalyst | 597 | **+0.178R** |

So the apparent benefit inside the admitted population rests on three trades, and on the one
comparison with enough data to mean anything the filter is **removing** roughly 0.12R per
trade. Both blended figures — the +0.17R gate-off and the +0.04R admitted — point the
opposite way from every adequately-sampled cohort underneath them.

## What this does and does not establish

Against it being taken as settled:

- **The cohorts are small.** 17 base-breakout and 47 reclaim trades with a catalyst
  establish nothing on their own. Read the direction, not the magnitude.
- **The catalyst definition is the weak one** described above. A filing occurring is a poor
  proxy for "why now", and it is plausible that the LLM-judged version behaves differently
  — that is an argument for measuring it, not for assuming it.
- **The 80 symbols are hindsight-selected** (see `2026-08-17-published-numbers.md`), so
  absolute expectancies are upper bounds. The catalyst comparison is same-universe on both
  sides, so the *relative* claim survives that, exactly as the carry comparison does.

In its favour:

- the direction is consistent across the two profitable setups rather than resting on one;
- it holds on the single largest like-for-like comparison available — 47 reclaims against
  597, inside the population the engine actually trades;
- it is the same failure mode already measured once on this codebase, so the mechanism is
  not speculative.

## Decision taken

**`v3_require_catalyst` now defaults to `False`.** Nothing here shows the filter selecting
better trades, on the setups with edge it looks actively harmful, and it refuses ~93% of
candidates (10 of 135 on 14 Aug, PIIND among the refused).

The filter is **kept, not deleted**, and `--require-catalyst` arms it for a run. Two
reasons it stays: the code is correct and tested, and what has been measured is the *weak*
definition, so the case is unproven rather than closed.

`test_filter_is_disarmed_by_default_on_the_measurement` pins the default, so restoring it
is a decision someone has to make on purpose rather than a tidy-up.

## Why the strong definition is still unmeasured — and may stay that way

The obvious next step is to re-run the backfill **with** the LLM and repeat. That was
started on 18 Aug (and is now affordable: groq's configured model had been decommissioned
and every call burned a 60s timeout before the cascade moved on, which is fixed). Early
results say it will not settle much:

* **LLM-scored catalysts from filings are very sparse.** Three days of backfill produced
  **two** records. Most filings genuinely carry no expectation change — which is the prompt
  working as designed, since it is built to score forward-numbers impact rather than tone,
  and "Board Meeting Intimation" has none.
* **The richer source cannot be backfilled at all.** News RSS serves roughly 48 hours. The
  live filter sees filings *and* news; any historical measurement sees filings only.

So the strong-definition cohort is likely to be too small to measure on the available
window, and a historical measurement can never fully represent the live filter. That is an
argument for leaving the filter off by default and revisiting it with *forward*-collected
data, not for assuming it would have helped.
