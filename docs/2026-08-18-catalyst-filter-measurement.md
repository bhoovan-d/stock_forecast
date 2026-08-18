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

## What counts as a catalyst here

Measured twice, on two definitions, because the weak one was all that existed at first.

* **Rule-routed only** — "a material filing occurred" (results, board meeting, dividend, a
  SAST threshold disclosure) within five sessions. Neutral by construction: a results
  filing scores 50 because the numbers sit inside a PDF nothing here can read, so it marks
  attention rather than direction.
* **LLM-scored as well** — a model judging whether the filing changes forward
  earnings/value expectations. This is what §12 actually asks for.

The second backfill added **32 records across 52 sessions of the whole NIFTY 500** (LLM
rows 55 → 87, 20 distinct days). That sparsity is the prompt working as designed — it
scores forward-numbers impact rather than tone, and most filings have none — but it means
the strong definition adds very little signal to measure with.

**Both definitions give the same answer.** The numbers below are the LLM-enriched run; the
rule-only run differed by fractions of an R and reversed nothing.

One source cannot be measured at all: news RSS serves ~48 hours and has no archive. The
live filter reads filings *and* news, so any historical measurement sees strictly less than
it would.

## Result

80 symbols, 50 sessions, 2,545 triggers, gate off. Costs of ≈0.17R subtracted. Coverage now
spans the whole replay window, so every decision is inside it and none is excluded.

| Cohort | n | Win | Net |
| --- | --: | --: | --: |
| Every trigger (all covered) | 2,545 | 9.9% | −0.64R |
| Carry-gate admitted | 723 | 24.3% | **+0.10R** |
| — had a catalyst | 155 | 12.4% | −0.54R |
| — no catalyst | 2,390 | 9.7% | −0.65R |

Blended, requiring a catalyst looks like **+0.11R per trade**. That number should not be
quoted, because it reverses per setup:

| Setup | has catalyst | | no catalyst | |
| --- | --: | --: | --: | --: |
| | n | Net | n | Net |
| base-breakout | 17 | **+0.01R** | 47 | **+0.72R** |
| continuation | 91 | −0.95R | 1,713 | −0.97R |
| reclaim | 47 | **+0.06R** | 630 | **+0.12R** |

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

| Cohort (carry-admitted) | n | Win | Net |
| --- | --: | --: | --: |
| Admitted — filter 5 off (today) | 723 | 24.3% | +0.104R |
| Admitted + catalyst — filter 5 on | 50 | 27.1% | **+0.189R** |
| Admitted, no catalyst — what filter 5 cuts | 673 | 24.1% | +0.097R |

At first reading that supports the filter: +0.19R against +0.10R. It does not survive being
opened up.

The with-catalyst cohort is **47 reclaims, 2 base-breakouts and 1 continuation**. Those
three non-reclaim trades carry **+6.49R of the cohort's +9.45R total — 69% of it, from 6% of
the trades.** Strip them and the only cohort with a usable sample says the opposite:

| Like-for-like, reclaim only | n | Net |
| --- | --: | --: |
| reclaim + catalyst | 47 | **+0.063R** |
| reclaim, no catalyst | 630 | **+0.123R** |

So the apparent benefit inside the admitted population rests on three trades, and on the one
comparison with enough data to mean anything the filter is **removing** roughly 0.06R per
trade. Both blended figures — the +0.11R gate-off and the +0.09R admitted — point the
opposite way from every adequately-sampled cohort underneath them.

Adding the LLM-scored catalysts did not move this cohort at all: it stayed at exactly 47
reclaims. The 32 new records landed almost entirely on continuation, the setup that loses
either way.

## What this does and does not establish

Against it being taken as settled:

- **The cohorts are small.** 17 base-breakout and 47 reclaim trades with a catalyst
  establish nothing on their own. Read the direction, not the magnitude.
- **News is still missing from the historical view.** Both definitions above are
  filings-only, because RSS cannot be backfilled. If real "why now" lives mostly in news
  rather than filings, this measurement cannot see it — and it is the live filter's second
  source.
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

## The strong definition was measured, and it changed nothing

The obvious objection to the first run — that it tested "a filing occurred" rather than a
judged expectation change — was closed by re-running the backfill with the LLM and
repeating the measurement. Both give the same verdict, and the strong definition is if
anything *less* informative, for a structural reason:

* **It is very sparse.** 32 records across 52 sessions of the entire NIFTY 500. Most
  filings carry no expectation change, which is the prompt doing its job.
* **It did not touch the cohort that decides the question.** The admitted reclaim cohort
  stayed at exactly 47 trades. The new records landed on continuation.
* **The richer source still cannot be backfilled.** News RSS serves ~48 hours.

So the remaining uncertainty is not "the weak definition was tested" — it is that news is
invisible to any historical run. That is an argument for revisiting the filter with
*forward*-collected data, not for assuming it would have helped.

## Postscript: the largest catalyst category was never actually read

Looking at what the store *contained* rather than what the cohorts did:

| | n |
| --- | --: |
| rule-routed records in the window | 530 |
| LLM-scored records | 87 |
| — of the rule-routed: `earnings_surprise` scored **exactly 50.0** | **352** |

**352 of 617 records — 57% of the store — were results filings scored a flat neutral**,
because the only thing available was a subject line reading "Financial Results For Quarter
ended June 2026". The numbers were inside the attached PDF and nothing read them. So "has a
catalyst" mostly meant *this company reported recently*: an attendance marker, not an
answer to "why now".

That materially weakens the measurement above as a verdict on the *idea* of a catalyst
filter, while leaving it correct as a verdict on the filter **as it was implemented**. The
decisive cohort — 47 admitted reclaims — was keyed almost entirely on those neutral markers.

`intelligence/results_pdf.py` closes it. Every BSE filing carries a direct PDF link, those
PDFs fetch fine (verified: 200, ~900KB, `application/pdf`), and the reported figures print
their comparative columns beside them. Worked example — IGL's June 2026 filing, previously
recorded at a flat 50:

    Revenue from operations   5,040.15   4,584.51   4,326.60
    Profit for the period       186.18     277.08     355.94

Revenue up ~16% year-on-year, profit down ~48%. Scored **23.33**, rationale *"Net profit
declined sharply YoY (PAT down ~48%), requiring downward revisions to forward FY
estimates."* That is a real directional catalyst that the store previously held as neutral.

**This does not revive the filter, and must not be read as doing so.** It changes the input,
not the result. The honest sequence is: collect forward with the PDF reader running,
re-measure, then decide about arming filter 5 — the default stays off until a measurement
says otherwise.
