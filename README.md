# Asymmetry Engine

Find the right stock → at the right time → because of the right catalyst → in the right
market regime → with enough upside/downside asymmetry to justify the trade.

An Indian-equities (NSE) scanner built as five engines that compose into one daily brief.
**Decision support only** — it never places, modifies or cancels an order, and contains no
order-placement code at all. You place every trade yourself.

```bash
uv venv && uv pip install -e ".[dev]"
cp .env.example .env          # optional — it runs with no keys
uv run asymmetry doctor       # check every data source
uv run asymmetry backfill     # ~600k daily bars, a few minutes
uv run asymmetry brief --html # the daily output, Markdown + dashboard
uv run asymmetry backtest     # does the ranking actually predict anything?
```

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

### Scheduling

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_schedule.ps1
```

Registers a weekday 18:45 task: backfill → settle journal → brief + dashboard. NSE closes
15:30 IST and the bhavcopy lands ~18:00 IST, so anything earlier just 404s and builds a
brief from stale data. Logs to `data/logs/`.

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
