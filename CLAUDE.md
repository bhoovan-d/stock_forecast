# Asymmetry Engine — working notes

Indian-equities (NSE) screener. Finds the right stock → at the right time → for the right
reason → in the right regime → with enough asymmetry to justify the trade.

**It is decision support and contains no order-placement code at all.** Never add any. It
holds no broker credentials that can trade, and the published pages say so on every surface.

This file holds what the code cannot tell you: why things are the way they are, what has
been measured versus assumed, and the traps that have already cost time.

---

## Architecture

Three timeframes, three jobs. A candidate must clear all three.

    Daily / Weekly   why this stock, and the regime      stage 1 — zero network calls
    60m / 120m       is there actually a carry setup     engines/carry.py
    30m / 15m        where exactly to enter              engines/v3.build_v3_plan

| Module | Job |
| --- | --- |
| `engines/v3_scan.py` | the pipeline — stages, gates, scoring, rejection accounting |
| `engines/setups.py` | the three permitted patterns |
| `engines/carry.py` | the 60m/120m continuation test |
| `engines/v3.py` | entry, stop, target, position size, quality score |
| `report/v3_report.py` | console + Markdown, and the **shared** line builders |
| `report/v3_website.py` | the published page |
| `v3_backtest.py` | replay on real 15m triggers |
| `ui/` | local control panel (`asymmetry ui`), loopback only |
| `engines/hma_pullback.py` | **separate strategy** — intraday HMA/BB pullback, NIFTY 200 |
| `engines/trident.py` | **separate strategy** — kill-zone FVG reclaim at 20R, NIFTY 500 |
| `engines/trident_live.py` | intraday monitor: five gates, closed candles only, failure log |
| `engines/trident_universe.py` | the trident's own liquidity floor and loud fetch accounting |
| `engines/trident_pools.py` | liquidity pools and sweeps — **tagged, never gated** |
| `engines/timeframe_study.py` | which bar size continuation persists in, with a random-entry control |
| `v3_events.py` | **the live V3 forward record** — hash-chained events, settlement, rollout gate |
| `v3_journal.py` | superseded 11 Sep by `v3_events.py`; kept only for its test (trap 18) |
| `v3_probability.py` | leakage-resistant 2R/3R/4R probabilities, Wilson bounds, the funded-alert gate |
| `universe_snapshot.py` | freeze a dated bhavcopy universe; seeded uniform sampling (`--symbols-file`) |
| `data/bar_contract.py` | point-in-time candle contract and the immutable 15m `IntradayStore` |
| `engines/catalyst.py` | Engine 2 — news and delivery-spike catalyst scoring, filing backfill |
| `engines/fno.py` | per-stock F&O positioning (long buildup vs short covering); confirmation only |
| `backtest.py` | walk-forward evaluation of the older ranking engine |
| `systems/fno_momentum/` | **clean-room track** — must never import `asymmetry` (see "The clean-room tracks") |
| `systems/nifty500_source_news/` | **clean-room track** — design stub, proposal not yet approved |

`engines/hma_pullback.py` and `report/pullback_report.py` are the owner's intraday setup
(30m anchor → 5m pullback, 3R, 0.7% cap). They share **nothing** with V3 and must not: the
geometry differs on every axis — **including costs**. The first measurement reported
-1.35R net and was **retracted**: it charged V3's *delivery* cost (STT 0.1% both sides) to
an *intraday* strategy that pays 0.025% sell-side only, overstating cost 3.4x. Because cost
in R is `cost% / stop%`, and this strategy's stop is ~0.2%, that error alone was most of the
headline. Corrected, the result is **inconclusive, not negative**: gross -0.072R with a 95%
CI of [-0.186, +0.043], and the widest-stop cohort around break-even. `docs/
spec-hma-pullback.md`.

Two lessons, both general. **A cost constant is calibrated for a holding period** — never
reuse V3's across strategies. And **cost in R scales inversely with stop distance**, so a
tight-stop strategy is exquisitely sensitive to it; report a slippage range rather than a
point estimate, and never headline a mean-of-ratios when stop sizes vary by 100x.

`engines/trident.py` and `report/trident_report.py` are a third strategy, transcribed
26 Aug 2026 from a YouTube interview (TG Capital on Chart Fanatics) at the owner's request.
30m fair-value-gap → doji reclaim of the 50%, inside a kill zone, long only, **20R**. It
shares nothing with the other two and must not: **three strategies now means three cost
constants, three stop rules and three holding periods**, and any constant shared between
them retunes a deployed engine by accident.

Two things about it are not negotiable in any doc that describes it. **It is the NSE
adaptation of an FX/gold strategy, not the strategy** — the kill zone had to be remapped to
the NSE session, the four-colour daily confluence is a reconstruction of an unnamed
third-party indicator, and the discretionary trailing exit was replaced by a fixed 20R.
**And the source's 90% win rate at 1:20 is neither confirmed nor refuted here, because this
data cannot settle it**: ~60 sessions of 30m history against a claimed 8–10 setups a year
per instrument. Never report a win rate for it without its interval.

Measured 26 Aug 2026, 200 names, 59 sessions: **22 setups**. At 20R, 0 of 20 resolved won,
net **-1.505R**. At 4R, 6 of 22 won (27.3% against a 20% break-even), gross **+0.358R**,
net **-0.128R** after 0.486R of costs. Net is negative in every variant tried.

**Read those as a snapshot, not a constant** - and this is the part worth carrying. Over one
day the 4R gross figure printed -0.006R, +0.184R, +0.264R and +0.358R on the *same window*,
because the sample grows daily and because three accounting defects in the resolver each
flattered it: open trades dragging expectancy toward -cost, still-forming bars resolving
trades early, and right-censored trades booked as finished (worth +0.33R of pure artefact).
A fourth issue was reproducibility - a silent fetch failure moved the sample from 22 setups
to 21 while the report showed only a symbol count. All four are fixed and tested.

**An earlier claim here is retracted.** This file and a pushed commit both said a 0.5% stop
floor "makes net worse, so the floor is excluded for a measured reason". It has since
measured both ways on an **eight-trade cohort that moves on one trade**. Calling that
measured was the error, in either direction; the floor stays off because the source has no
such rule, which was always sufficient.

What *is* stable, because it follows from geometry rather than this window: cost in R is
`cost% / stop%` (0.08% stops cost 2.02R), no exit rule beats the plain fixed target, a
break-even stop lifts the hit rate by **ejecting the position from winners** (trades reaching
target roughly halve), scaling out alone buys no hit rate, and lowering the partial trigger
*reduces* the win rate here because banking 0.125R cannot cover 0.49R of costs. **No exit
rule adds edge to an entry that has none.** `docs/spec-trident.md`.

**v2, 30 Aug 2026** — live monitoring, NIFTY 500, a 5m parallel track, liquidity-sweep
tagging, and the kill-zone window made a parameter. `docs/2026-08-30-trident-v2-measurements.md`.
Five things from it that must not be relitigated from scratch:

- **The five gates are named once** (`GATE_NAMES`) and the detector emits a `GateEvent` at
  each. The monitor, the funnel and the failure log are *observers* on one geometry
  implementation — never add a second. Gate 4 (doji closed back above the 50%) and gate 5
  (confirmation) are alerted separately because a full bar separates them; **gate 5 refuses
  ~93% of what reaches gate 4**, so a get-ready is a reason to look, never to act.
- **Every refusal carries its numbers** — `body 47.3% of range, limit 30.0%`, not "body too
  large". At 21 setups on the anchor timeframe the failure log is the more informative half
  of the output, and a reason without figures cannot tell you whether a threshold is doing
  work or starving the strategy.
- **The liquidity floor runs first, on bhavcopy, with its own constant.** ₹25 cr/day median
  turnover admits **368 of 500**. It is deliberately not `settings.min_median_turnover_inr`
  (₹5 cr, V3's): sharing it would retune the deployed daily brief. It exists because a bar
  with no prints has low == high, which the FVG detector reads as a perfect imbalance —
  **data holes, not liquidity voids**, and the geometry cannot tell them apart.
- **The estimated-spread filter is built, measured and disarmed.** Corwin-Schultz from daily
  high/low correlates **-0.15** with turnover and **+0.31** with daily range — it ranks by
  volatility, not tradeability — and at 25bp refuses **40 of the NIFTY 50**, ICICIBANK and
  BHARTIARTL included. No threshold rescues it; one loose enough to admit ICICIBANK is inert.
  Computed and printed on every row, off by default, `--require-spread` arms it. Same
  disposition as the catalyst filter.
- **Alerts rank by net R after costs**, never by stop tightness or raw quality. The tightest
  stop is the one surrendering the most of its own R, and it looks best on every other measure.

At 4R over 59 sessions and 150 names: **5m −0.986R** (213 setups, 23.9% win, cost **1.205R**),
**15m −0.692R** (76, 21.1%, cost 0.712R), **30m −0.538R** (21, 19.0%, cost 0.499R). The 5m
and 15m intervals exclude zero. Note the shape: the 5m track has the **best** raw hit rate and
the **worst** net, because gross falls with bar size while cost in R falls faster — *the
cross-timeframe ranking is the cost curve, not a statement about the pattern.* **60m returns
zero setups and structurally cannot return any**: seven 60m bars a session, four inside
09:15–12:45, and a completed pattern needs five — with four, the doji can only print on the
last bar and the confirmation has nowhere to go. The kill zone, not the setup, is what rules
out the one timeframe the persistence study finds favourable.

**The window sweep answers the transplant question, negatively and usefully.** Six of eight
cells had <10 trades and are excluded from comparison — one showed +2.658R on **two trades,
both winners**, with an interval excluding every other cell's, which is why `WindowSweep`
now enforces a ten-trade floor. Of the two comparable cells: the transplant (09:15–12:45) is
21 setups at −0.538R; the whole session is 59 setups at +0.389R. **Intervals overlap, so
neither wins** — but restricting to the transplant discards two-thirds of the setups and buys
nothing. It is now a parameter on every command, and **no window has been adopted**: a grid
searched over 59 sessions always has a best cell.

**1:3 was measured 9 Sep 2026 and does not rescue anything.** Same 150 names, same 59
sessions, same window, both multiples resolved off the same fetched frames, so only the
target differs. Break-even before costs is 25.0% at 3R against 20.0% at 4R.

| TF | n | 3R wins / win% | 4R wins / win% | 3R gross | 4R gross | 3R net | 4R net |
| --- | --: | --- | --- | --: | --: | --: | --: |
| 5m | 219 | 52 / 23.7% | 50 / 22.8% | −0.050 | +0.142 | −1.258 | −1.067 |
| 15m | 82 | 25 / 30.5% | 19 / 23.2% | +0.189 | +0.128 | −0.458 | −0.519 |
| 30m | 22 | 3 / 13.6% | 3 / 13.6% | −0.401 | −0.310 | −0.896 | −0.805 |

**The mechanism is the finding, not the sign.** A nearer target only helps if trades are
dying just short of the old one. They are not — they are being stopped out. Dropping from
4R to 3R bought **two** extra winners at 5m and **none** at 30m, against a break-even that
rose five percentage points; only 15m gained enough (six) to come out ahead. So 3R is worse
at 5m, worse at 30m, mildly better at 15m, and negative everywhere. **Cost in R is identical
across both** (1.208 / 0.647 / 0.495) because it depends on stop distance alone — the entire
3R-vs-4R difference lives in gross, and it is small next to intervals that overlap heavily.

The window sweep repeats at 3R with the same shape: only 09:15–12:45 (22 setups, −0.896R)
and the whole session (64 setups, +0.164R) clear the ten-trade floor, and their intervals
overlap. **And the sweep-tag cohort flips sign**: at 15m, swept +0.167R against untagged
+0.199R, where at 4R it read +0.304R against −0.103R. A cohort difference that reverses when
the target changes was never a signal — which is the strongest evidence yet for keeping the
tag ungated.

**Liquidity sweeps are tagged and must stay tagged.** Five pool families; a sweep is a wick
through *and a close back on the origin side* within N candles — **a break that holds is not
a sweep**, and conflating them would tag every breakdown as a grab. Three states, kept
distinct: swept, not swept, **not checked**. Cohorts so far are noise (15m: +0.304R swept vs
−0.103R untagged on 23/53; 30m reverses on 7 trades). It needs forward samples, not a re-run.

`engines/spec_engine.py`, `engines/selection.py` and `report/brief.py` are the older
Engineer-Brief and daily-screen engines. They are separate, keep their own gates
(`screen_min_reward_risk` vs `min_reward_risk`), and V3 changes must not leak into them —
sharing one constant would silently retune the deployed daily brief.

---

## Which timeframe anything continuation-shaped belongs on

Measured 30 Aug 2026, 80 liquid names, `docs/2026-08-30-timeframe-study.md`, reproducible
with `asymmetry timeframe-study`. This governs the trident, the pullback and any future
continuation engine, so it lives here rather than in one strategy's spec.

**Continuation gets less hostile as the bar gets longer, and the crossover is between 30 and
60 minutes.** The share of names with VR(2) > 1 climbs 31% → 35% → 45% → **62%** from 5m to
60m, and first-order autocorrelation crosses from negative to positive in the same place.
Below the hour, NSE single-stock returns tilt mean-reverting — bid-ask bounce and transient
order-flow reversal — which is exactly the condition under which a continuation entry buys
the top of a wiggle.

**No timeframe is statistically distinguishable from a random walk.** Every robust z is
inside ±1.96. The gradient is consistent across four columns and is worth acting on as a
prior; it is not proof, and must not be quoted as one.

**The most important result is the control, and it is easy to get wrong without one.** A
long-only rule with a wide stop replayed over a market that rose earns money for reasons
unrelated to the rule, and the longer the bar the more of that drift it collects. Matched
random entries — same stop, same target, same frames — earn **+0.306R of the daily signal's
+0.381R** and **+0.537R of the weekly's +0.581R**. So the tidy monotone net-R gradient
(5m −0.426R → weekly +0.551R) is **drift capture plus the cost curve, not a sharper signal**.
Excess over random clears significance at exactly one bar size, in the wrong direction: **5m
is significantly worse than random** (−0.060R, t = −2.34).

Two rules follow. **Never rank timeframes on net R** — that ranks stop widths. And **any new
continuation rule gets the random-entry control run against it before its expectancy is
quoted anywhere**; `timeframe_study.replay_random_entries` exists for this.

NIFTY itself is above 1 at every intraday horizon while its constituents are below 1 below
the hour — index-level continuation coexisting with single-name reversal. If a future engine
trades the index rather than stocks, the intraday picture is less hostile than the stock
table suggests.

---

## The clean-room tracks

Since 15 Sep 2026 the repo holds a **second, deliberately isolated workstream** under
`systems/`: `fno_momentum` (NSE F&O stock momentum expressed as long calls/puts, paper only)
and `nifty500_source_news` (a design stub whose proposal is not yet approved). Everything
else in this file is about the legacy `asymmetry` engines. **This section constrains both.**

Two governing documents sit at the repo root. `fno_ai_momentum_options_system BHOOVAN
DHONA_spec` describes the FnO system; `final_momentum_options_engineer_direction.BHOOVAN
DHONA` **supersedes the spec wherever they conflict** and splits the work into the two
tracks. It binds nine constants:

| Constant | Meaning |
| --- | --- |
| `CLEAN_ROOM_ONLY` | no legacy code, data, defaults, thresholds, models, reports or metrics enter either track |
| `ADITYA_APPROVAL_REQUIRED` | no source, pattern, threshold, feature, model, cost policy or live behaviour activates or changes without Aditya Lakhotia's explicit approval |
| `PAPER_ONLY` | no broker orders, no account sizing, no option writing, no funded path |
| `POINT_IN_TIME_ONLY` | an observation may affect a decision only when `available_at <= decision_at` |
| `IMMUTABLE_LEDGER` | every record appends; original payloads are never overwritten |
| `APPROVED_SOURCES_ONLY` | collect only from registered, lawful sources; AI may propose a source but not use it |
| `FAIL_CLOSED` | missing, stale, contradictory or unverifiable data blocks the affected alert |
| `TRACK_ISOLATION` | no cross-track score, feature, outcome or promotion logic |
| `VERSIONED_CHANGE_CONTROL` | every approved change carries a version, content hash, rationale, evidence and approval event |

**Neither track may import `asymmetry` or read a legacy path.** This is enforced, not
requested: `scripts/check_clean_room.py` AST-walks both packages for the `LEGACY_IMPORT`
root and for forbidden path literals (`src/asymmetry`, `data/asymmetry.db`,
`v3_watch.jsonl`, …), and `.github/workflows/clean-room.yml` runs it on every push to
`systems/**`. It passes with zero source-level hits. **Never "helpfully" reuse V3, trident or
report code inside `systems/`** — existing code, thresholds and results are explicitly *not
implementation dependencies*, and an old idea may be consulted only after approval and must
then restart as a fresh hypothesis with new evidence.

Approval lives in `systems/proposals/` — content-hashed manifests (the underlying-data
acquisition manifest is on **v9**), source manifests, one pattern proposal. *"Nothing in this
directory is active merely because it is written down."* Work in progress is tracked with
`planning-with-files` under `.planning/`; `.planning/.active_plan` names the current plan.

**State as of 29 Sep 2026: data foundation only.** 210 F&O stock underlyings, an append-only
SQLite event ledger, and a historical OHLCV partition store (`data/fno_momentum/`,
gitignored, machine-local). No pattern engine is active, no model is trained, no alert has
ever been published. The historical DB was **stopped by explicit instruction on 26 Sep**,
narrowed to 20 symbols on 28 Sep, and its 29 Sep quality clearance returned **conditional
validation — full database not cleared** (24 five-minute gaps and 473 invalid daily values
confirmed against Upstox). All collection tasks are disabled. The track's own tests (96)
run only in `clean-room.yml`, never under the root `pytest`.

**The one measured pattern** (`docs/2026-09-16-fno-first-pattern-study.md`): a bearish
prior-session-high sweep-and-reclaim on completed 15m bars, fixed 2R, 128 predeclared
threshold combinations chosen on development only.

| Window | Outcomes | Mean R |
| --- | --: | --: |
| Development, 16 Jun–31 Jul | 866 | +0.0753 |
| Validation, 4 Aug–31 Aug | 428 | +0.0972 |
| Observed evaluation, 3–16 Sep | 159 | +0.6039 |

The bullish mirror was rejected (+0.0533 → −0.0431 → −0.2786R). Read the bearish result
for what it is: **underlying geometry labels, not option profits** — no spreads, fees,
slippage or premium behaviour — from a selection-biased search over a short window that
overlaps a bearish regime. And **the evaluation window can no longer serve as untouched
evidence**, because it has now been looked at. Status: inactive research only; promotion
needs a fresh untouched cohort, option BBO replay with costs, and 20 immutable forward paper
alerts.

---

## Rules that are not negotiable

**Four hard filters are armed; a fifth exists and is switched off.** Armed: 4R feasibility,
stop distance, liquidity, basic technical validity. Nothing else may reject, and a new one
needs an explicit decision — not a judgement call mid-edit.

The fifth — **a catalyst must exist** (§12) — was added 18 Aug 2026 on the owner's explicit
instruction, measured the same day, and **defaults to off because the measurement does not
support it**. It is kept rather than deleted because what was measured is the weak
definition (see below). `--require-catalyst` arms it for a run.

When the carry gate rejects, it still reports under *technical validity* — a setup with no
higher-timeframe carry structure is not technically valid for a 1–5 session hold. The
catalyst filter is deliberately **not** folded in the same way: a missing news catalyst is
not a technical invalidity, and filing it there to keep the count at four would misstate
why a name was refused.

What the catalyst filter costs, so it is judged on the real number: on 14 Aug 2026, 10 of
135 stage-one candidates carried a catalyst note, and PIIND — the only name published that
day — was not one of them. Read that as much as a coverage statement as a market one; the
news pass is capped at 120 items and 90 filings and the announcement APIs are blocked here.
**An empty catalyst result across the whole shortlist disarms the filter** (`catalyst_status`
= `outage`) rather than refusing the universe: a broken feed must never render as a
selective day. `--no-require-catalyst` turns it off deliberately, which is reported
differently again. **It has now been measured twice, and neither measurement supports it.** Blended it looks
positive (+0.11R gate-off, +0.09R inside the admitted population); both figures are setup
mix. Per setup it removes edge from the two that have any — base-breakout +0.01R with a
catalyst against +0.72R without, reclaim +0.06R against +0.12R — and inside the admitted
population 69% of the with-catalyst cohort's total R comes from 3 of its 50 trades. The
largest like-for-like comparison available (47 reclaims vs 630) says the filter costs
~0.06R per trade.

The obvious objection — that this tested "a filing occurred" rather than a judged
expectation change — was closed by backfilling the window through the LLM and repeating it.
Same verdict, and the admitted reclaim cohort did not move by a single trade: the strong
definition yields only 32 records across 52 sessions of the whole NIFTY 500, because most
filings genuinely carry no expectation change. What remains untestable is **news**, which
serves ~48h and cannot be backfilled at all — so revisit this with forward-collected data,
not by re-running history. Full analysis:
`docs/2026-08-18-catalyst-filter-measurement.md`.

**A stop is never moved to make a trade fit.** Not widened to admit a candidate, not
tightened to manufacture the R multiple. If the valid invalidation sits outside the
0.5–1.5% band, the setup is *refused*. This single rule rejects roughly half of everything
found and is the reason wins can be 4× losses.

**Selectivity is answered with patience, never a lower threshold** (§17). Output below
target is the expected state. If a change makes the engine produce more, that is a red flag
to investigate, not a success.

This rule has now been tested by a real winner, and held. **KEI, short base breakdown, 1 Sep
2026**: entry ₹5,384.81, stop ₹5,460 (1.396%), reached 3R on 2 Sep and 4R on 4 Sep for about
**+3.88R net**, never touching its stop. V3 **refused it** — point-in-time quality 47.78
against the selective floor of 76, dragged down by sector leadership (5.3) and structure
(22.0) — so the 1 Sep brief published nothing. Lowering the floor to admit a known winner is
hindsight tuning. The question it raises is a cohort question — do low-scoring but
geometrically valid breakdowns win *as a group*, out of sample — and that is how it must be
answered. `docs/2026-09-09-last-week-positive-trade.md`.

**Sector leadership scores, it does not gate.** Gating on sector is what made an earlier
build discard the setups the spec exists to find.

**No setup may be labelled by the move it is trying to catch.** Every detector measures
strictly on the bars *preceding* the confirming bar. `fetch_chart(as_of=…)` is the single
truncation chokepoint upstream — do not fetch around it.

---

## What is measured, and what is not

Replay of 2,527 real 15m triggers, 80 symbols, 50 sessions. A bar touching both stop and
target books a **loss** (intraday sequence is unknown, and resolving it favourably is how
backtests invent edges). Costs ≈0.17R are subtracted. Break-even at 4R is a 20% win rate.

| Cohort | n | Win | Mean R | Net |
| --- | --: | --: | --: | --: |
| Every trigger, gate off | 2,527 | 10% | −0.46 | −0.63R |
| **What the engine takes** | 730 | 24% | +0.28 | **+0.11R** |

| Setup | all n | all | gated n | gated | gated? |
| --- | --: | --: | --: | --: | --- |
| reclaim (liquidity sweep) | 682 | +0.30R | 682 | +0.30R | **no — exempt** |
| base-breakout | 65 | +0.75R | 8 | +1.29R | yes |
| continuation (high-tight flag) | 1,780 | −0.80R | 40 | −0.25R | yes |

Read honestly, and keep reading it honestly in any doc you write:

- **Most of the gain is removing flag trades, not selecting better ones.** "Filters harder"
  is a much smaller claim than "picks better".
- **The base-breakout gated result rests on 8 trades.** It agrees with the mechanism and
  establishes nothing.
- **The flag loses money even after gating.** It stays in the codebase because the failure
  may be the implementation rather than the pattern; it must not reach the site. This is
  now enforced rather than merely stated: `asymmetry v3` defaults to `--setup reclaim
  --setup base-breakout`, the same pair CI publishes. Until 18 Aug 2026 only CI's flags
  enforced it, so a local `v3` run followed by `site` would have published a flag trade —
  verified by doing exactly that.
- **One market period.** Intraday history is capped at ~60–80 days upstream.
- **The 80 symbols are chosen with hindsight.** With no explicit list, `run_v3_backtest`
  ranks *today's* `stage_one` output by setup quality and takes the top N, then replays
  their triggers from up to fifty sessions earlier. Trade decisions stay point-in-time; the
  universe does not. So **+0.11R is an upper bound**, and names that stopped qualifying are
  absent entirely. The gate-on/gate-off comparison survives this — same 80 names both sides
  — but the absolute expectancy does not transfer to a 473-name scan. Settle it by passing
  `symbols` fixed from a snapshot taken before the window; the parameter already exists.
- **Nothing has traded live.**

**The hindsight-universe caveat has since been tested, 9 Sep 2026, and the aggregate survives
it — the funding case does not.** Universe frozen at 13 Jul from bhavcopy alone (no index
membership), `data/universe-2026-07-13.json`, 1,034 symbols; 150 drawn uniformly with seed 1.

| Cohort | n | Win | Net R |
| --- | --: | --: | --: |
| All triggers, gate off | 5,478 | 10.0% | −0.68 |
| **Carry-admitted, blended** | 1,449 | 26% | **+0.11** |
| reclaim (carry measured, not gated) | 1,324 | 26% | +0.13 |
| base-breakout, gated | **22** | 55% | +1.59 |
| continuation, gated | 103 | 16% | −0.42 |

So +0.11R is not produced by selecting today's names. But it is a blend of three different
strategies, one of which loses and one of which rests on 22 trades — **do not fund "V3" off
the blended headline.** The same run corrected the cost model: per-trade cost averages
**0.216R**, so the old 0.17R midpoint scalar was overstating net by ~0.046R per trigger.
`docs/2026-09-09-oos-universe-measurement.md`.

**Probabilities are calibrated and nothing is fundable** (10 Sep). On the frozen universe the
2R/3R/4R estimates held out of sample (all setups 3R: 11.8% → 11.8%; reclaim 4R: 26.6% →
24.4%). But the corrected replay's final window returns **PAPER ONLY on every setup-target
cell**: reclaim 6/24 at 3R and 4R with a conservative bound of 0.0% and −1.24R net;
base-breakout **zero** carry-approved trades, so no probability claim at all; 3 usable
sessions against a required 20, 24 trades against a required 100. A funded alert needs all
seven conditions in `docs/2026-09-10-v3-probability-calibration.md` — including a
conservative bound clearing break-even and ≥+0.15R expected net — and **the thresholds are
not to be lowered to get there**. `docs/2026-09-10-v3-corrected-probability-report.md`.

Do not read the backtest's trade count as an output forecast. It counts every trigger with
the quality floor and the daily cap switched off, because a gate cannot be measured against
trades it never saw. 43 candidates → 1 published on 14 Aug is the live pipeline.

**The quality score is measured too, as of 19 Aug 2026, and only works behind the carry
gate.** On the admitted population the top score decile beats the rest by +0.31R, and within
reclaim alone the top quartile makes +0.40R against +0.14R — genuine ranking, not a proxy for
setup type. Ungated it *inverts* (top decile -0.135R): high scores attract flags, because
strong RS and clean structure is what a pole looks like. Score and gate are one mechanism and
must be judged together. Three modules contribute nothing to the decile decision — catalyst
(a constant at 50.0 for 98% of trades), sector leadership and volatility, 28% of the weight
between them. Removing the catalyst weight is arithmetic and safe; reweighting toward the
modules that measured best would be fitting this one hindsight-selected window, so it needs
out-of-sample confirmation first. Full analysis:
`docs/2026-08-19-quality-score-measurement.md`.

Full analysis: `docs/2026-08-16-carry-gate-measurement.md`. Where the floor, the regime
scale and the fill band come from: `docs/2026-08-17-published-numbers.md`.

---

## The carry gate

`assess_carry` returns a checklist **and** a score; both must pass.

- **Gating:** `120m setup present`, `volume contracted then expanded`.
- **Scored only:** headroom, 60m/120m EMA alignment, 60m/120m trend, range location.
- **Floor:** 60 (`v3_carry_score_floor`).
- **Fail closed:** no 60m data ⇒ rejected. Unproven is not the same as fine — a name whose
  60m fetch failed used to publish anyway.

**Per-setup, not global** (`v3_carry_gated_setups`). It is a continuation-regime test, and
not every setup is a continuation trade. Applied to reclaim it cut +0.30R → −0.07R: a sweep
is a counter-trend entry by construction, so the test selects the reclaims already extended
and aligned — the ones with the least asymmetry left. In the sample, **the twenty best
trades were reclaims scoring 7–43 on carry**, every one below the floor. Carry is still
measured and reported for exempt setups (`carry_passed` vs `admitted`) so the exemption
stays checkable.

Calibration history, so it is not relitigated from scratch: gating all eight conditions
admitted **2 of 2,527** triggers — unmeasurable, not strict.

---

## Data constraints

- **`www.nseindia.com` is Akamai-blocked** here (403/404 even with full headers). Never
  build against it. `nsearchives.nseindia.com` is not blocked and is the backbone.
- **Yahoo throttles hard.** Pacing, backoff and the disk cache are mandatory, not
  optimisations. A burst of ~11 symbols failed on every one; paced, the same symbols worked.
- **Intervals:** 60m gives ~730 days, 15m ~60 days, **120m returns HTTP 400 — it does not
  exist** and must be resampled.
- **A session is 09:15–15:30 IST** (`yahoo.SESSION_OPEN` / `SESSION_CLOSE`). Bars are
  stamped with their **start**.
- **The feed returns 26 bars for a 25-interval session.** The extra one is stamped 15:30 and
  is the *closing print*, not a 15-minute window. Rendering it as "15:30–15:45" quotes a
  window in which the exchange is shut.
- **No forward NSE holiday calendar exists.** `trading_days()` resolves a session by probing
  for its published bhavcopy, which only exists for days that already happened. Forward
  dates (time stop, next session) count weekdays and say so in the output.
- India's 10Y (`^IN10YT=RR`) 404s and is omitted rather than zero-filled.
- **BSE filing PDFs are reachable** (`bseindia.com/xml-data/corpfiling/AttachLive/*.pdf`,
  ~1MB each) and the announcements API takes an explicit date range, so filings — unlike
  news — can be backfilled. `intelligence/results_pdf.py` reads the figures out of them and
  caches the extracted text, because the backfill re-walks the same days.
- **There is no consensus estimate available here at any price.** Results can be judged on
  *trajectory* against the comparative columns the filing prints, never on surprise. The
  scoring context says so outright: a model given only "profit rose 40%" reports a beat,
  and a beat against nothing is a fabricated number entering a scored system.
- **The free LLM tier is marginal for PDF-sized context.** Measured 18 Aug 2026: cerebras
  returns 402 (quota exhausted), groq returns unparseable JSON on the longer prompt, gemini
  carries it. The cascade order hides this until gemini also fails, so treat a working
  catalyst pass as luck rather than capacity.

---

## Traps that have already bitten

Each of these shipped or nearly shipped. They are cheap to reintroduce.

1. **Resampling 120m by the clock.** Seven 60m bars a session means a `120min` rule anchored
   to midnight cuts at 10:00/12:00/14:00 and straddles the 09:15 open, mixing two sessions
   into one bar. Fold **by position within the session**.
2. **Testing an enum against the wrong taxonomy.** `CARRY_STRUCTURES` holds
   `structure._base_quality` values, read via `analyse_timeframe`. Testing it against
   `detect_setup` — which only returns RECLAIM / CONTINUATION / BASE_BREAKOUT / NONE — made
   four of seven values unreachable and silently demanded a second V3 setup on the 120m.
3. **Passing the wrong thing as "structure".** The scan fed `setup.quality` into
   `structure_score`, so a name in a weekly downtrend scored 90 on structure. They are
   separate modules now and must stay separate.
4. **`settings` is a module-level singleton loaded at import.** Writing `.env` does not
   update the running process. `upstox_auth.login()` must refresh
   `settings.upstox_access_token` after the write, or verification tests the expired token
   and reports a working login as rejected.
5. **A description that decides nothing rots.** The 60m read existed for weeks as
   "a description, not a filter" — so a name whose 60m fetch failed outright still
   published. If a read is worth fetching, make it decide something or delete it.
6. **`"live"` without a timestamp.** `build_v3_plan` recorded the trigger bar only for
   *stale* triggers, so the newest bar — the only one anyone acts on — had no time attached
   and rendered as "live now" on an archive-tier scan of a session closed days earlier.
7. **A setup found after the close is for the *next* session.** The session is over once its
   final bar has *begun* (15:15), not when the closing print lands.
8. **Windows + Rich in a subprocess.** The UI runner must force
   `force_terminal=True, legacy_windows=False`, or tables degrade to `+---+` ASCII.
9. **Naming a setup by its enum value.** `SetupType` values are direction-neutral because
   they key the carry exemption, `--setup` and every backtest cohort. Rendered raw they say
   the opposite of the trade: PIIND shipped tagged `reclaim` on a SHORT with its own note
   reading "rejected by 10.2%". Render via `setup_label(kind, direction)`; never parse it
   back, never gate on it.
10. **A fallback with no marker.** `why_now` fell through to the setup's own note, so a name
    with a real earnings catalyst and a name with only a tidy chart rendered identically.
    A fallback that cannot be distinguished from the real answer is a false claim — say
    "no catalyst found" out loud. Same family as trap 5.
11. **Scoring a directional factor undirectionally.** Catalyst scores are centred on 50,
    above bullish and below bearish. Every other percentile in the scan is mirrored for a
    short and this one was not, so a SHORT with a *bullish* catalyst scored higher for it.
    Anything fed to `quality_score` must be expressed in the traded direction first.
12. **A setting nothing reads.** `base_breakout_*` sat in `config.py` for weeks while
    `detect_base_breakout` used hardcoded defaults — tuning them did nothing, silently.
    Resolve settings *inside* the function body, never as a default argument: defaults bind
    at import and `settings` is a singleton, which is trap 4 wearing a different hat.
13. **A confidence interval from a cell with two observations.** The kill-zone sweep's
    11:30-14:00 window printed +2.658R with a 95% interval of +2.34 to +2.98 that excluded
    every other window's — on **two trades, both winners**. A narrow interval means "few
    observations agreed with each other", which is what a coin flipped twice also produces.
    Any grid cell needs a minimum sample before it may take part in a comparison
    (`WindowSweep.MIN_CELL`), and the thin cells are still printed rather than hidden.
14. **A statistic that stops scaling with the sample.** The Lo-MacKinlay robust z had an
    extra factor of `n` in its variance term, which left theta O(1) instead of O(1/n). An
    AR(1) with phi = 0.3 over 6,000 returns scored z = 0.29 instead of 13.9, so *every* series
    read as "indistinguishable from a random walk" — a test that can never fail, in a study
    whose entire output is that verdict. If a test never rejects, check its scaling before
    believing it.
15. **An indicator answering a question about history it has not seen.** `ewm` returns a
    value from the very first bar, so `close > ema200` is satisfiable at bar two. The
    continuation replay confirmed "above the 200 EMA" on twenty bars of data until a warm-up
    floor was added. Any rule reading a long-period indicator needs an explicit warm-up; the
    indicator will not tell you it is guessing.
16. **A hardcoded constant sitting next to the setting that was supposed to control it.**
    `build_v3_plan` computed `entry + 4.0 * risk` while `settings.min_reward_risk` sat
    unread on the line above, so editing it in `.env` moved the *older* spec engine's target
    (`entry.py:161` reads it properly) and left V3's exactly where it was — two engines
    silently disagreeing about the R multiple. Trap 12 with a different variable, and
    invisible from the output: every number the engine printed stayed self-consistent.
    Caught 9 Sep 2026 only because replaying V3 at 3R against 4R returned an **identical
    252 wins from an identical 3,542 triggers**, which cannot happen if the target moved —
    anything reaching 4R reaches 3R on the way. When a parameter sweep returns identical
    counts, suspect the parameter before the market.
    `test_the_r_multiple_is_read_from_settings_not_hardcoded` now asserts it on plan
    geometry.
17. **Publishing a threshold without its derivation.** Every watch-list row read "below 72"
    while 72 existed only in a code comment. If a number decides what the reader sees, it
    ships with where it came from — `V3Scan.threshold_basis` / `regime_detail` carry this.
18. **A superseded module that still passes its test.** `v3_journal.py` and `v3_events.py`
    both implement a V3 forward record. Only the second is wired to the CLI and to the
    portfolio limits, but the first has a green test, a data file on disk, and a docstring
    that never mentions its successor — so nothing signalled which was live, and this file
    named the dead one as "the V3 forward record" for two weeks. The cost was concrete: the
    only V3 forward data that exists (33 records, 9 Sep) sits in the superseded format and
    the live file has never been written. When a module is superseded, **delete it, or say
    in its first docstring line what replaced it and when**. A passing test proves a module
    works, not that anything uses it. Same family as trap 5.

---

## Commands

```bash
uv run asymmetry ui                   # every command in a browser — start here
uv run asymmetry doctor               # data source health and active tier
uv run asymmetry backfill --days 400  # EOD history into SQLite
uv run asymmetry v3 --setup reclaim --setup base-breakout
uv run asymmetry v3-backtest --symbols 80
uv run asymmetry trident               # kill-zone FVG scan; empty days are the norm
uv run asymmetry trident-monitor --once   # one live pass on the latest closed candle
uv run asymmetry trident-monitor          # loop through the session, alerting at each gate
uv run asymmetry trident-backtest --symbols 0 --interval 5m
uv run asymmetry trident-sweep --rr 4     # is 09:15-12:45 contributing anything?
uv run asymmetry trident-failures         # the accumulated failure log, bucketed by gate
uv run asymmetry timeframe-study          # where continuation persists, vs a random entry
uv run asymmetry freeze-universe --date 2026-07-13   # dated bhavcopy universe snapshot
uv run asymmetry v3-calibrate --symbols-file data/universe-2026-07-13.json
uv run asymmetry v3-watch             # record scan candidates to data/v3_events.jsonl
uv run asymmetry v3-monitor --once    # funded-alert gate; needs equity + live Upstox
uv run asymmetry site                 # rebuild public/
uv run pytest -q                      # 303 passed, 1 skipped (304 collected)
uv run python scripts/check_clean_room.py   # clean-room boundary; must print "verified"
```

### CI, and the collection freeze

**Collection has been frozen since 16 Sep 2026.** `daily-brief.yml` (19:00 IST) and
`trident-watch.yml` (19:30 IST) both carry `if: ${{ false }}` on their jobs, with the
comment: *"Governance freeze approved by Aditya Lakhotia on 2026-09-16 … do not run
collection, strategy, LLM, or publication steps until a later exact approval removes this
guard."* The workflows are preserved for audit history. **Lifting that guard needs that
exact approval — tidying docs, bringing things up to speed or "doing the needful" is not it.**

What the freeze explains, so nobody debugs it as a fault: `data/briefs/` stops at 9 Sep;
`data/trident_watch.jsonl` has **never been created**, so the trident forward record —
which `docs/spec-trident.md` calls the only remedy for the 60-session history cap — holds
zero records; and the V3 forward record is empty (see "V3 evidence and capital invariants").

When unfrozen, `daily-brief.yml` publishes `--setup reclaim --setup base-breakout`, commits
`public/`, and Vercel deploys on push. It must run after 18:00 IST, when the bhavcopy lands;
earlier just 404s.

The only workflow that runs today is **`clean-room.yml`** — the boundary check plus the two
`systems/` test suites. **The 304-test root suite is run by no workflow at all.** A local
green `uv run pytest -q` is currently the only signal it has, so run it before every commit
that touches `src/asymmetry`.

The panel (`asymmetry ui`) binds loopback only and has no auth, which is why the bind
address is not a knob. The browser posts a command id and a field map, never a command line;
`ui/commands.py` builds argv. Runs are serialised — Yahoo's pacing and the SQLite writer
cannot take two scans at once.

---

## Conventions

### V3 evidence and capital invariants

- Historical V3 claims must identify their universe path. `--symbols` is the current,
  hindsight-ranked compatibility path; fundable evidence uses a dated bhavcopy snapshot via
  `--symbols-file`, with any runtime sample drawn uniformly by an explicit seed.
- V3 transaction cost is frozen per trade as `(round-trip cost % + slippage %) / stop %`.
  The midpoint-stop scalar remains reportable only as `typical_cost_r` for comparison with
  older documents.
- **The live V3 forward record is `data/v3_events.jsonl`, written by `v3_events.py`** — a
  hash-chained, append-only log. `load(verify=True)` raises `event log integrity failure at
  line N` if the chain breaks, and every event carries `model_version`, `code_revision`,
  `code_dirty`, `universe_hash` and `data_hash`. It backs `v3-watch`, `v3-monitor`, `v3-fill`
  and the portfolio limits in `v3_scan` (`open_records()`).
- `v3_journal.py` → `data/v3_watch.jsonl` is the **earlier design, superseded 11 Sep 2026**
  and imported by nothing but its own test (trap 18). Do not write new records through it.
- **`data/v3_events.jsonl` has never been written.** The CLI switched to it on 11 Sep and the
  collection freeze landed on 16 Sep, with no `v3-watch` run between. So the only V3 forward
  data on disk is `v3_watch.jsonl` — 33 records, one session (9 Sep), all `outcome: open`,
  in a format the current CLI will never settle. **The V3 forward record effectively holds
  zero resolved trades.** Every funding decision below is waiting on evidence that does not
  exist yet.
- Do not merge any V3 record with the legacy or trident journals, and do not duplicate
  settlement logic: replay, `v3_journal.settle()` and `v3_events.settle()` all call
  `v3_backtest.resolve_forward`, with `simulate_entry` shared the same way.
- With account equity configured, V3 sizes from `risk_per_trade_pct`, then applies the
  notional cap. Portfolio position, heat and sector limits demote plans to WATCH with an
  explicit reason. A zero-share plan is refused.

- **Comments explain why, not what.** Especially where a number was measured rather than
  chosen — record the measurement inline so nobody "cleans it up" later.
- **Tests defend measured claims and past bugs**, not coverage. `tests/test_carry.py`
  carries LGEINDIA's real bars as a fixture precisely so the anti-lookahead property is
  asserted rather than assumed.
- **Weights must sum to 1.0** — `test_v3_weights_sum_to_one` enforces it. Rebalance
  deliberately; never pad.
- **Report surfaces share one builder.** `carry_lines()` / `execution_lines()` feed the
  console, the Markdown brief and the HTML page so three surfaces cannot describe the same
  plan differently.
- **Published pages use `report/theme.py` tokens.** Red and green are *data* — direction and
  risk — so neither may become the brand accent. The accent is a steel cyan used only for
  structure.
- Prefer refusing a candidate over emitting a hedged one. Days with no setup are correct.
