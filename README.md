# Asymmetry Engine

Find the right stock → at the right time → because of the right catalyst → in the right
market regime → with enough upside/downside asymmetry to justify the trade.

An Indian-equities (NSE) scanner built as five engines that compose into one daily brief.
**Decision support only** — it never places, modifies or cancels an order, and contains no
order-placement code at all. You place every trade yourself.

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env          # optional — it runs with no keys
uv run asymmetry ui           # every command, in a browser  ← start here
```

Or drive it from the terminal:

```bash
uv run asymmetry doctor       # check every data source
uv run asymmetry backfill     # ~600k daily bars, a few minutes
uv run asymmetry brief --html # the daily output, Markdown + dashboard
uv run asymmetry backtest     # does the ranking actually predict anything?
```

## The panel

`asymmetry ui` serves a local control panel on `http://127.0.0.1:8765` — every command
with its options as a form, output streaming live as it runs, and every brief this machine
has generated in one place. Nothing has to be typed.

- **Commands** — the same fourteen commands, grouped: health, engines, evidence, journal,
  publish. Each shows the CLI line it will run and roughly how long it takes; options
  persist between visits.
- **Output** — the child process's real output, colour and tables intact, streamed line by
  line. A V3 scan takes minutes, so the state pill counts while it works, and Cancel
  actually kills the run.
- **Briefs & pages** — every generated Markdown brief rendered, plus the published pages
  from `public/` shown as they will appear on the site.
- **Run history** — what ran this session, how long it took, and its full output again.

Two decisions worth knowing. Runs are **serialised**: a second run queues rather than
starting, because Yahoo throttles hard enough that the data layer paces itself and two
scans would fight over that pacing and the SQLite writer. And each run is a **child
process**, so a scan that dies takes nothing with it and a long one can be cancelled.

It binds to loopback and has no authentication, which is also why the bind address is not
an option — a "run this command" endpoint does not belong on a network. The page posts a
command id and a field map, never a command line: `src/asymmetry/ui/commands.py` holds the
only description of how each command is spelled, and builds the argument list itself.

## Does it work?

Measured, not assumed. Walk-forward over 50–67 sample days (~31,000 stock-days),
point-in-time, 10-day forward horizon, returns taken as excess over the same-day universe
mean so a rising market is not mistaken for skill:

| Bucket | Median excess | Hit rate |
| --- | --: | --: |
| Q1 (worst) | −0.29% | 47% |
| Q2 | −0.16% | 49% |
| Q3 | −0.13% | 49% |
| Q4 | −0.01% | 50% |
| Q5 (best) | **+0.31%** | **52%** |

Median and hit rate rise monotonically across all five buckets, reproducibly across
independent samples. Mean daily rank correlation is +0.026 to +0.028.

**Read that honestly: this is a weak but consistent signal — a screen with mild edge, not
alpha.** The mean spread (+0.13%) is much noisier than the median (+0.59%) because equity
returns are fat-tailed. And note the catalyst factor was *disabled* for this test, since
historical catalysts do not exist in the store — so this measures the technical factors
alone. Paper-trade it and log decisions before risking size.

## The five engines

| # | Engine | Question | Output |
| --- | --- | --- | --- |
| 1 | **Regime** | Should I be aggressive today? | 🟢 aggressive / 🟡 selective / 🔴 defensive |
| 2 | **Catalyst** | *Why* should this stock move? | 0–100, centred on 50 |
| 3 | **Selection** | Which stock is most likely to move? | ranked shortlist |
| 4 | **Macro fair value** | Is macro helping or hurting? | gap %, gated by model R² |
| 5 | **Trade** | Can I make 2R? | entry / stop / target, or *no trade* |

### 1. Regime — context, not a trigger

Five inputs scored −1/0/+1: NIFTY trend, India VIX (percentile, not level), dealer gamma,
global risk (S&P/Nasdaq/DXY/US10Y) and market breadth.

Dealer gamma is computed from the NSE F&O bhavcopy: implied vol is solved per contract with
Brent, then Black–Scholes gamma is weighted by open interest to give net GEX and the gamma
flip level. Negative gamma means moves extend (good for breakouts); positive gamma means
dealers dampen them (expect chop). **Gamma is regime context and never an entry signal.**

### 2. Catalyst — the distinction that matters

Every news item is scored against one question:

> Does this change the company's **future earnings or value expectation**?

Not "is this news positive?" An award, a rebrand, a conference notice or a broker note
restating consensus all score neutral no matter how positive they sound. The LLM returns a
structured judgement — catalyst type, expectation delta (−3…+3), materiality relative to
company size, durability, whether it is already priced, and confidence — and the score
decays with age.

Because official corporate filings are unreachable from this environment (see below), an
unusual **delivery-percentage spike alongside a turnover spike** supplements the feed as
the institutional-accumulation proxy: volume alone is churn, volume actually taken to
delivery is positioning.

### 3. Selection

Five factors, each a cross-sectional percentile over the liquid universe: relative strength
(vs NIFTY *and* vs sector), volume/delivery expansion, price structure (distance from 52w
high, EMA alignment, base compression), catalyst, and liquidity. Liquidity is also a hard
gate, not just a score.

### 4. Macro fair value

A ridge regression of each stock's returns on NIFTY, USD/INR, US 10Y, crude, gold, India
VIX and sector-specific commodities gives a macro-implied fair value, and the gap against
the actual price. **Below the configured R² floor the gap is marked unreliable and
contributes nothing to ranking** — most single stocks are mostly idiosyncratic, and without
this guard the gap is noise. This is deliberately not a Quant Insight replication.

### 5. Trade — the gate

Entry from structure (breakout over the base, or continuation). Stop at the tightest *valid
structural* invalidation — base low, swing low, 21 EMA or session VWAP — with an ATR stop
only as a fallback when no structural level sits within range. Target is the **nearest**
genuine overhead supply, capped at a reachable distance.

**If R:R is below 2.0, no plan is emitted.** Not downgraded — rejected. Days with no
qualifying setup are a valid and expected outcome.

## Data sources

The system tiers its data and always tells you which tier fed the brief:
`LIVE (Upstox)` → `ARCHIVE (NSE EOD)` → `DELAYED (~15min)`.

Two constraints were found by probing before the build, and they shape everything:

- **`www.nseindia.com` is Akamai-blocked** from this environment (403 on the homepage, 404
  on `/api/*`) even with full browser headers and cookie warm-up. Nothing may be built
  against it. `nsearchives.nseindia.com` is *not* blocked and serves the universe,
  cash/F&O bhavcopies and delivery data — that is the backbone.
- **Yahoo throttles hard.** A rapid burst of ~11 symbols failed on every one; the same
  symbols succeeded when paced. Pacing, backoff and on-disk caching are mandatory, not
  optimisations.

Known gaps, handled explicitly rather than papered over: India's 10Y yield (`^IN10YT=RR`
404s) is omitted from the macro panel rather than zero-filled, and official NSE/BSE
corporate announcements are unavailable, so catalysts come from RSS plus delivery spikes.

## Deliberately excluded

Pre-FOMC drift · Google Trends / FEARS · insider & 13F · social media ·
unusual-options-activity following · full Quant Insight replication · vanna/charm as
per-stock signals.

## Commands

```bash
uv run asymmetry ui                  # all of the below, from a browser
uv run asymmetry doctor              # data source health and active tier
uv run asymmetry auth                # Upstox token refresh instructions
uv run asymmetry backfill --days 400 # EOD history into SQLite
uv run asymmetry regime              # engine 1 only
uv run asymmetry scan --top 10       # engines 3+5
uv run asymmetry brief --html        # all five -> Markdown + dashboard
uv run asymmetry backtest --days 380 --step 4 --no-regime
uv run asymmetry journal settle      # mark open calls against real prices
uv run asymmetry journal log RELIANCE taken --price 1330 --qty 40
uv run asymmetry journal review      # your decisions vs the system's
uv run pytest -q                     # tests
```

### Scheduling — locally

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Registers a weekday 18:45 task: backfill → settle journal → brief + dashboard. NSE closes
15:30 IST and the bhavcopy lands ~18:00 IST, so anything earlier just 404s and builds a
brief from stale data. Logs to `data/logs/`.

## Deployment

The pipeline runs on **GitHub Actions**; **Vercel serves static HTML**. Nothing is computed
at request time — a daily brief describes one closed session and never changes, so a server
would have nothing to do.

```
Actions (weekday 19:00 IST)                 Vercel
  backfill → engines → brief --html   →  public/  →  static site
  → build site → commit public/          (auto-deploys on push)
```

Serverless is the wrong shape for the pipeline itself: the store is ~100MB and must
persist, a full refresh takes 10+ minutes, and Vercel functions are stateless with a 60s
ceiling. So Actions does the work and Vercel only serves the output.

**Reachability was verified, not assumed.** `.github/workflows/probe-sources.yml` tests
every endpoint from a runner — one step per source, because Actions logs need admin rights
even on a public repo while per-step conclusions are publicly readable. All sources answer
from GitHub's IPs. BSE rate-limits under rapid sequential requests, which is why the paced
client with backoff matters in CI.

### Deploying

1. **Vercel** → New Project → import this repo. Framework preset **Other**, output
   directory **`public`**, no build command. `vercel.json` already sets this.
2. **Optional LLM keys** for catalyst scoring — repo Settings → Secrets → Actions:
   `CEREBRAS_API_KEY`, `GROQ_API_KEY`, `GEMINI_API_KEY`. With none set the pipeline still
   runs and the brief simply carries no news catalysts.
3. **First run**: Actions → *Daily brief* → *Run workflow*. The cold start backfills 400
   sessions (~4 min); later runs restore the cached store and are incremental.

Each run commits `public/` and Vercel redeploys. `/latest` always redirects to the newest
brief.

### Costs

Free tier throughout: Actions gives 2,000 min/month on private repos and is unlimited on
public ones (this run is ~5–15 min/day), Vercel's Hobby plan covers static hosting, and the
LLM cascade uses free tiers. Groq's free allowance is 100k tokens/day, which a full refresh
can exhaust — the cascade then falls through to Cerebras and Gemini automatically.

## Honest limitations

- **The catalyst factor is thin.** Filings and news cover ~30 of 473 liquid names on a given
  day, so the highest-weighted factor is neutral for most of the universe. BSE filing
  subjects also do not carry the financial *numbers* — those live in multi-megabyte PDF
  attachments — so a results filing is recorded as a neutral event flag rather than a
  fabricated direction.
- **No demonstrated alpha**, only a weak monotonic screen (see above).
- **Historical catalysts do not exist**, so backtests measure technical factors only.
- **India 10Y is unavailable** and omitted from the macro panel rather than zero-filled.
- **The live tier needs your Upstox token**; without it everything degrades to EOD/delayed
  and the brief says so in its header.

## Not investment advice

This is a screening and analysis tool. It surfaces candidates and levels for your own
judgement; it does not predict outcomes, and no output here is a recommendation to trade.

---

## Specification V3 — the current engine

`NIFTY 500 Asymmetry Engine.pdf` supersedes the earlier docx brief. Both directions, a stop
*band* rather than a ceiling, and roughly 10–15 setups a **month** rather than a daily list.

```bash
uv run asymmetry v3                      # full NIFTY 500 scan, ~6 min
uv run asymmetry v3 --setup reclaim      # only the setup that tested positive
uv run asymmetry v3-backtest             # does any of it actually pay?
```

| | Daily screen | Engineer Brief | **V3** |
| --- | --- | --- | --- |
| Direction | long | long | **long + short** |
| Minimum R:R | 2.0 | 4.0 | **4.0** |
| Stop | none | ≤1.4% | **0.5–1.5% band** |
| Output | shortlist | daily | **~10–15 / month** |
| Setups | breakout | generic | **sweep + flag only** |

Selection follows V3's hierarchy — right market → right sector → right stock → right time →
right risk/reward — with sector leadership as a **15% score, not a gate**. §16 names the
hard filters exactly (4R, stop distance, liquidity, technical validity) and nothing else may
reject. Gating on sector is what made an earlier build discard valid candidates.

Stage 1 screens all 473 liquid names from stored bars in ~20s with **zero network calls**
(weekly is resampled from daily). Only the survivors pay for intraday data.

### Does V3 work? Measured on real 15-minute triggers

`v3-backtest` replays the engine's own entries — same `detect_setup`, same `build_v3_plan` —
resolved on 15-minute bars, with a bar touching both stop and target booked as a loss:

| Setup | n | Win rate | Mean R |
| --- | --: | --: | --: |
| **Liquidity sweep (reclaim)** | 461 | **26%** | **+0.32R** |
| High-tight flag (continuation) | 1,178 | 5% | −0.76R |
| *Break-even at 4R* | | *20%* | *0* |

**The two setups are not equivalent.** The liquidity sweep clears break-even and stays
positive after ~0.17R of costs. The flag, as implemented, does not come close — and since it
generates the majority of signals, the blended result (10.5%, −0.62R net) is dominated by
it.

Use `--setup reclaim` to run only the one with measured support. The flag detector is kept
because the failure may be my implementation rather than the pattern, but it should not be
traded on this evidence.

Caveat that matters: intraday history is capped at ~60–80 days upstream, so this is one
market regime and a few thousand trades. It measures the right thing on limited data, rather
than the wrong thing on plenty.

---

## Engineer Brief specification engine

A second, much stricter engine implementing `Asymmetry_Engine_Engineer_Brief.docx`:

```bash
uv run asymmetry spec --evaluate 30
```

| | Daily screen (`brief`) | Specification (`spec`) |
| --- | --- | --- |
| Minimum R:R | 2.0 | **4.0** |
| Max initial stop | none | **1.4% of entry** |
| Horizon | ~5–15 sessions | **1–5 sessions** |
| Timeframes | daily | **Weekly → Daily → 60m → 30m → 15m** |
| Output | ranked shortlist | **TRADE / WATCH / REJECT** |

The two engines keep separate gates (`min_reward_risk` vs `screen_min_reward_risk`) so
tuning the specification never silently retunes the deployed daily brief.

### What the specification measures about itself

At 4R, break-even is a 20% win rate. Measured over ~4,800 historical setups using the
spec's own geometry — a 1.4% stop, a 4R target, resolved bar by bar with ambiguous bars
booked as losses:

| | |
| --- | --: |
| P(4R within 5 sessions) | **15.8%** |
| P(timeout) | 8.0% |
| Break-even win rate at 4R | 20.0% |
| Round-trip costs | ~0.18R |

**So the unconditional base rate is below break-even**, and the engine correctly refuses to
emit trades: a live scan returns 0 TRADE, with candidates landing at P(win) 6–21% against
the ~24% needed after costs.

That is the specification working as designed — §18 is explicit that a minimum number of
trades must never be forced. It is also an honest finding about the parameters: 4R against
a 1.4% stop is close to, but currently under, the line.

The open question is whether setups selected by the *full* chain — HTF alignment, fresh
catalyst, compression, genuine F&O participation — beat the unconditional base rate enough
to clear it. The probability model does not yet condition on those features, so this is not
yet proven either way. Resolving it needs labelled outcomes for spec-qualified setups,
which accumulate as the engine runs.

### Known data gaps

- **Consensus estimates are unavailable** on free sources, so §7's forward-revision and
  surprise-magnitude engine is not implemented. Earnings are detected as events, not scored
  against consensus.
- **15m/30m history is capped at ~81 days** by the upstream feed, which bounds intraday
  backtesting. 60m reaches ~3 years, weekly 5 years.
- Probability base rates are measured on **daily bars**, so they approximate a
  15-minute-triggered reality — erring pessimistic, since same-bar ambiguity books a loss.
