# Where the published numbers come from

*17 Aug 2026.* Written in response to a review of the 14 Aug page, which asked — fairly —
where several numbers on that page came from. Each was defensible and none was written
down anywhere the reader could reach. A threshold whose derivation lives only in a code
comment is, from the site, indistinguishable from a number somebody liked.

This file is the reference. The numbers now also print themselves on all three surfaces, so
this document explains rather than being the only copy.

---

## The quality floor (72 on 14 Aug)

Not a constant. It is set by the regime, in `run_v3_scan`:

| Regime | Floor |
| --- | --: |
| aggressive | 72 |
| selective | 76 |
| defensive | 80 |

**Where the levels came from.** A live scan of 269 candidates produced scores spanning
55–81 with a median of 65. A floor in the 50s admitted 180 names against a specification
asking for 10–15 a *month*, so the floors were placed in the upper tail of the observed
distribution. §17 is explicit that the answer to too many qualifiers is a higher threshold,
never a larger output.

**What this means honestly.** The floors are calibrated to produce the *rate* the
specification asks for. They are not calibrated to outcomes — nothing has established that
a 72 wins more often than a 68. That is a distribution-shaping parameter and should be read
as one. `--min-score` overrides it, and the page then says so.

PIIND scoring exactly 72.0 against a floor of 72 is a marginal admission, and the card said
so by publishing both numbers. It is worth watching whether a disproportionate share of
published names sit within a point of the floor; if they do, the floor is doing the
selecting rather than the scoring.

## The regime score (+2 on 14 Aug)

Five inputs — NIFTY trend, India VIX, gamma, global, breadth — each scored −1, 0 or +1 and
summed. Range −5…+5.

| Total | Verdict |
| --- | --- |
| ≥ +2 | aggressive |
| −1 … +1 | selective |
| ≤ −2 | defensive |

The three-way verdict is what the brief describes as risk-on / neutral / risk-off; the
number is the sum underneath it, not a second scale. The page now prints the component
breakdown, so a +2 can be traced to which two inputs supplied it.

Its *only* effect is the quality floor above. Regime never generates a trade and never
rejects one (§3).

## The valid-fill band (2,466.70 – 2,491.24 against an entry of 2,490.10)

This is not a tolerance drawn around the entry, which is why it is not centred on it.

The stop is a fixed structural level. V3 requires the stop to sit 0.5–1.5% from the fill.
Solving that constraint for the fill gives the band directly — for a short with the stop
above:

    entry_min = stop / (1 + 0.015)     entry_max = stop / (1 + 0.005)

The entry sits wherever it sits inside that window. On 14 Aug the fill was 2,490.10 against
a stop of 2,503.70 — 0.55%, near the *tight* end — so almost all of the remaining room lay
below. The asymmetry is the rule working, not a defect. **A stop is never moved to recentre
a band** (§16); the band moves.

---

## The 80-symbol backtest against a 473-name scan

The measured expectancy in `README`/`CLAUDE.md` comes from 2,527 triggers across 80
symbols. The live scan screens 473. The review asked whether the cutoffs have been shown to
hold at the larger scale. **They have not**, and there is a sharper problem underneath.

**The 80 are not a sample of the 473.** `run_v3_backtest` with no explicit symbol list runs
`stage_one` for the *run date* and takes the top N by setup quality. So membership in the
sample is decided by how good a name looks *today*, and its triggers from up to fifty
sessions earlier are then replayed. Individual trade decisions remain strictly
point-in-time — the anti-lookahead property `tests/test_carry.py` defends is intact — but
the universe selection is not.

Two effects follow, and neither can be fixed inside the replay loop:

1. **It is the good tail.** Ranking by setup quality and taking the top 80 measures the
   best-looking end of the distribution. The full 473 would include the mediocre setups
   this selection removes, so **+0.11R net should be read as an upper bound.**
2. **Names that stopped qualifying are absent.** Anything that has since fallen out of the
   setup list — including anything that broke badly during the window — is not in the
   sample at all.

This does not invalidate the comparison the backtest was built for. The gate-on / gate-off
cohorts come from the *same* 80 names, so the *relative* claim ("removing flag trades helps")
survives the bias. The *absolute* claim ("+0.11R per trade") does not transfer to the live
scan.

**What would settle it:** pass `symbols` explicitly, fixed from a universe snapshot taken
before the replay window, and re-measure. Cost is network time, not new code — the
parameter already exists. Until that is run, the honest statement is: *the direction of the
carry-gate effect is measured; the level of expectancy at 473 names is not.*

## "14–15 setups a day" — a category error worth retiring

The review inferred, from 730 gated trades over 50 sessions, a live rate of ~14–15 a day,
against a brief asking for 10–15 a month. The two numbers count different things.

The backtest counts **every 15-minute trigger** that cleared the hard filters, on 80
symbols, with the quality floor and the daily cap both switched off. That is deliberate: a
gate cannot be measured against trades it was never shown.

The live scan then applies two things the backtest does not:

- the **quality floor** (72–80 by regime), which on 14 Aug cut 43 candidates to 1;
- the **daily cap** (`max_per_day`, default 2), which demotes the rest to the watch list.

14 Aug's single published setup out of 473 names is the pipeline behaving as specified. The
backtest's 730 is not a forecast of output and was never meant to be read as one.

---

## The fifth hard filter (added 18 Aug 2026)

The version of this document written on the 17th said a missing catalyst could not reject,
because §16 fixed the hard filters at four. The owner then decided it should. It is now a
genuine fifth filter rather than the carry gate's trick of reporting under *technical
validity* — that mapping was honest, because a missing 60m carry structure really is a
technical invalidity, and a missing news catalyst is not.

**What it costs.** On 14 Aug 2026, of 135 stage-one candidates, **10 carried a catalyst
note**. PIIND — the only name published that day — was not among them and would have been
refused. Expect materially fewer published setups, which §17 says is the correct direction
for a change to move things.

**The honest risk.** "No catalyst found" is partly a statement about reach, not about the
market. The news pass is capped at 120 items and 90 filings, the NSE/BSE announcement APIs
are blocked from here, and the LLM cascade is the only thing reading the feeds. A filter
this strict sitting on that much coverage uncertainty is the thing to watch.

Three guards follow from that:

- an **empty catalyst result across the whole shortlist disarms the filter** and says so
  (`catalyst_status = "outage"`). A broken feed must never publish an empty page that looks
  identical to a selective day;
- **off and outage render differently** on all three surfaces, because they are opposite
  facts;
- `--no-require-catalyst` turns it off for a run, and the page states how many filters were
  armed rather than hard-coding a count.

**It is unmeasured.** `v3_backtest` calls `detect_setup` / `build_v3_plan` directly, so it
does not run this filter. Every expectancy figure in `CLAUDE.md` predates it and none of
them says anything about whether requiring a catalyst helps or hurts. That is the next
thing worth measuring, and it is measurable: the catalyst store is bounded by `as_of`, so a
replay can ask the same question point-in-time.

## One thing this document still does not fix

**Nothing has traded live.** Every number here is measurement or calibration.
