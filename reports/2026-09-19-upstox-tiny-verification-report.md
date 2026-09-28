# Upstox Tiny Connectivity and Data-Quality Verification

**Run time:** 2026-09-19T15:34:06.154341+00:00  
**Scope:** One underlying (RELIANCE) and one option contract only  
**Result:** Completed with an out-of-hours freshness limitation  
**Routine collection:** Not started; remains blocked

## Boundary verification

- Every network request passed the in-process HTTPS host/method/path/query allowlist.
- Only `GET` requests were permitted. Redirects were denied.
- No static IP was configured.
- No account, portfolio, holdings, funds, order, trade-history, P&L or mutation route was contacted.
- The token was read in-process from Windows Credential Manager target
  `AdityaResearch/UpstoxAnalyticsToken`. Its value was not written to this report, source, an
  environment variable, `.env`, log, payload or repository artifact.
- Provider response bodies were held in memory only. This report retains selected observed
  values and schema names, not raw payloads.
- An initial preflight attempt contacted only the same allowlisted instrument-master and
  one-underlying quote routes. It stopped before any option request because the current
  instrument master supplied expiry as epoch milliseconds. The parser was corrected and
  covered by a regression test before this completed run. No non-allowlisted route was
  contacted in either attempt.

| Method | Host | Path | Query keys only | HTTP | Local receipt time (UTC) |
|---|---|---|---|---:|---|
| GET | `assets.upstox.com` | `/market-quote/instruments/exchange/NSE.json.gz` | none | 200 | 2026-09-19T15:34:05.210836+00:00 |
| GET | `api.upstox.com` | `/v3/market-quote/quotes` | instrument_key | 200 | 2026-09-19T15:34:05.957718+00:00 |
| GET | `api.upstox.com` | `/v3/market-quote/quotes` | instrument_key | 200 | 2026-09-19T15:34:06.153343+00:00 |

## Verified instruments

| Role | Instrument | Expiry | Strike | Type |
|---|---|---|---:|---|
| Underlying | `NSE_EQ|INE002A01018` (RELIANCE) | n/a | n/a | NSE equity |
| Option | `NSE_FO|106362` | 2026-09-29 | 1230.0 | CE |

## Observed option market fields

| Field | Observed value |
|---|---:|
| Provider general response timestamp (`timestamp`) | 2026-09-19T21:04:05.542+05:30 |
| Provider last-trade timestamp | 2026-09-18T10:09:58.124000+00:00 |
| Local receipt timestamp | 2026-09-19T15:34:06.153343+00:00 |
| Best bid | 22.5 |
| Best-bid quantity | 500 |
| Best ask | 22.65 |
| Best-ask quantity | 500 |
| Open interest | 538500.0 |
| Volume | 2188500 |

## Observed response schema

- `data`
- `data.NSE_FO:RELIANCE26SEP1230CE`
- `data.NSE_FO:RELIANCE26SEP1230CE.average_price`
- `data.NSE_FO:RELIANCE26SEP1230CE.cas_eligible`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.buy`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.buy[].orders`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.buy[].price`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.buy[].quantity`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.sell`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.sell[].orders`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.sell[].price`
- `data.NSE_FO:RELIANCE26SEP1230CE.depth.sell[].quantity`
- `data.NSE_FO:RELIANCE26SEP1230CE.indicative_equilibrium_price`
- `data.NSE_FO:RELIANCE26SEP1230CE.indicative_equilibrium_quantity`
- `data.NSE_FO:RELIANCE26SEP1230CE.indicative_imbalance_quantity_market`
- `data.NSE_FO:RELIANCE26SEP1230CE.indicative_imbalance_quantity_total`
- `data.NSE_FO:RELIANCE26SEP1230CE.instrument_token`
- `data.NSE_FO:RELIANCE26SEP1230CE.last_price`
- `data.NSE_FO:RELIANCE26SEP1230CE.last_trade_time`
- `data.NSE_FO:RELIANCE26SEP1230CE.lower_circuit_limit`
- `data.NSE_FO:RELIANCE26SEP1230CE.net_change`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.close`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.high`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.low`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.open`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.ts`
- `data.NSE_FO:RELIANCE26SEP1230CE.ohlc.volume`
- `data.NSE_FO:RELIANCE26SEP1230CE.oi`
- `data.NSE_FO:RELIANCE26SEP1230CE.oi_day_high`
- `data.NSE_FO:RELIANCE26SEP1230CE.oi_day_low`
- `data.NSE_FO:RELIANCE26SEP1230CE.prev_close_price`
- `data.NSE_FO:RELIANCE26SEP1230CE.previous_oi`
- `data.NSE_FO:RELIANCE26SEP1230CE.reference_price`
- `data.NSE_FO:RELIANCE26SEP1230CE.symbol`
- `data.NSE_FO:RELIANCE26SEP1230CE.timestamp`
- `data.NSE_FO:RELIANCE26SEP1230CE.total_buy_quantity`
- `data.NSE_FO:RELIANCE26SEP1230CE.total_sell_quantity`
- `data.NSE_FO:RELIANCE26SEP1230CE.upper_circuit_limit`
- `data.NSE_FO:RELIANCE26SEP1230CE.volume`
- `data.NSE_FO:RELIANCE26SEP1230CE.year_high`
- `data.NSE_FO:RELIANCE26SEP1230CE.year_low`
- `status`

## Data-quality findings

- The run occurred outside market hours. The last-trade timestamp preceded local receipt
  by approximately 29 hours 24 minutes, so this observation does not establish live quote
  freshness.
- The response contained a current general `timestamp`, but no distinct timestamp for the
  last bid/ask change. It therefore does not establish when the displayed BBO became valid.
- No structural failure was observed in this single response: bid and ask were positive,
  ask was not below bid, quantities were positive, and OI/volume were non-negative.

## What this proves

This one-shot run proves only that the OS-held Analytics Token authenticated against the
listed allowlisted market-data route at the recorded time, the instrument master could be
read, one underlying and one option quote could be returned, and the displayed fields had
the values shown. It also proves which routes this process attempted.

## What remains unproven

It does not prove long-run completeness, uptime, latency, quote freshness during market
hours, exchange-time synchronization of BBO changes (no distinct BBO-change timestamp was
observed), correction behavior, historical
option BBO, retention rights, corporate-action treatment, executable fills, liquidity,
slippage, costs, stops, targets, holding periods, P&L, any hypothesis, or any trading edge.

## Exact separate approval required before routine collection

> Aditya Lakhotia approves the exact-hash Upstox routine-collection manifest, including
> its instrument universe, REST/WebSocket endpoint allowlist, fields, cadence, operating
> window, local storage layout, raw and derived retention period, deletion obligations,
> data-quality checks, failure behavior and security controls. This approval authorizes
> read-only routine data collection and quality monitoring only. It does not authorize a
> research hypothesis, candidates, historical option P&L, alerts, AI, Telegram, execution,
> trading, or any connection to NIFTY500 Source News.
