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

`engines/spec_engine.py`, `engines/selection.py` and `report/brief.py` are the older
Engineer-Brief and daily-screen engines. They are separate, keep their own gates
(`screen_min_reward_risk` vs `min_reward_risk`), and V3 changes must not leak into them —
sharing one constant would silently retune the deployed daily brief.

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

Do not read the backtest's trade count as an output forecast. It counts every trigger with
the quality floor and the daily cap switched off, because a gate cannot be measured against
trades it never saw. 43 candidates → 1 published on 14 Aug is the live pipeline.

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
12. **Publishing a threshold without its derivation.** Every watch-list row read "below 72"
    while 72 existed only in a code comment. If a number decides what the reader sees, it
    ships with where it came from — `V3Scan.threshold_basis` / `regime_detail` carry this.

---

## Commands

```bash
uv run asymmetry ui                   # every command in a browser — start here
uv run asymmetry doctor               # data source health and active tier
uv run asymmetry backfill --days 400  # EOD history into SQLite
uv run asymmetry v3 --setup reclaim --setup base-breakout
uv run asymmetry v3-backtest --symbols 80
uv run asymmetry site                 # rebuild public/
uv run pytest -q                      # 145 passed, 1 skipped
```

CI (`.github/workflows/daily-brief.yml`) runs weekdays 19:00 IST — NSE closes 15:30 and the
bhavcopy lands ~18:00, so earlier just 404s. It publishes `--setup reclaim --setup
base-breakout`, commits `public/`, and Vercel deploys on push.

The panel (`asymmetry ui`) binds loopback only and has no auth, which is why the bind
address is not a knob. The browser posts a command id and a field map, never a command line;
`ui/commands.py` builds argv. Runs are serialised — Yahoo's pacing and the SQLite writer
cannot take two scans at once.

---

## Conventions

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
