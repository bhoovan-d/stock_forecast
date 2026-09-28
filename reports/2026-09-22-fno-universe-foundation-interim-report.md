# FnO Universe-Wide Data Foundation — Interim Execution Report

**Decision authority:** Aditya Lakhotia  
**Execution date:** 22 September 2026  
**Track:** FnO Momentum only  
**Status:** Current-universe and option-contract metadata baseline completed; historical and ongoing underlying-data foundation not yet authorized at an executable level.

## 1. Outcome

The first low-storage, read-only phase of the new authorization is complete. A single public Upstox NSE instrument-master response established an effective-dated baseline for the complete current NSE stock-option universe. The capture found:

- **210 active FnO stock underlyings**;
- **30,065 current stock-option contracts**;
- **80,127 total rows** in the provider instrument master;
- no duplicate stock-option contract identifiers;
- no missing required contract identifiers, underlying identifiers, option side, strike, expiry, lot size, or tick size;
- no malformed master rows; and
- no invalid expiry, strike, lot-size, or tick-size values after the observed provider expiry encoding was correctly versioned.

The final accepted run is `45dd93bf7d961a42dc9db88e8e4fbc19`, captured at `2026-09-22T12:07:22.789862+00:00` (17:37:22 IST). It used only:

`GET https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz`

The Upstox Analytics Token was not requested, read, transmitted, or used. No quote, candle, option-chain, account, portfolio, order, or trading endpoint was called.

## 2. Audit and reproducibility evidence

The approved collection manifest SHA-256 is:

`10082c33466254ff331351b54febb0f26a30b9f22feab5606d8af02ce4136833`

The source-manifest content hash is:

`38e3abf09368f1bc6fd05c02326e572c2625436d878744107bfa53aa5660e516`

The raw provider payload SHA-256 is:

`a33d0c167c01dc5f6cbf5936f50b6c7b18d9a9e0178296ce3d6c764140941f0e`

The effective-dated universe hash is:

`56627fd32bbc5662f9d7fcf9117f7c558d93a0311fec651e0b3657108932ac40`

The normalized contract-reference hash is:

`fffede21f8851a8244071586ac336a9d990fbd2791aee89fa82e6e946999c973`

A permanent compressed reference snapshot contains the 210 members and all 30,065 contracts with contract ID, underlying ID and symbol, call/put side, strike, expiry, lot size, and tick size. Its normalized snapshot hash is `f9b31c98ded420e3a003ba172c61cf993c220bf29a7fa7d00986e9113954fe9d`; its compressed-file hash is `905b4d3efaf399bf5233a5f1f6d51bc42895feb2877d95dbe15918351e469ac1`.

The immutable ledger contains 20 hash-chained audit events and passes integrity verification. It records source proposal/approval/activation, each request and observation, manifest/parser/code identity, coverage and quality results, and both supersession events described below.

## 3. Parser issue found and resolved openly

The first run incorrectly expected ISO-formatted expiry dates and therefore reported all 30,065 expiries as invalid. Inspection of the retained raw payload showed that Upstox supplies expiry as epoch milliseconds representing the contract date in IST. No data was silently filled or accepted.

Parser version 2 added explicit epoch-millisecond handling and produced zero invalid expiries. That clean run was then superseded by parser version 3 only because version 3 also writes a permanent, content-addressed normalized reference snapshot. The underlying values did not change. Both earlier runs and the reasons they were superseded remain in the immutable ledger.

## 4. Storage and retention

Current total storage for this foundation is **2.18 MiB**:

- raw provider payload: **1.89 MiB**;
- permanent normalized universe/contract reference: **0.20 MiB**; and
- immutable SQLite audit ledger: approximately **0.09 MiB**.

The raw payload is governed by the existing seven-day policy and is due for deletion after 29 September 2026. No deletion has occurred yet. When deletion occurs, the permanent ledger must record the exact file/hash, deletion time, reason, and retention rule. The normalized reference snapshot and audit ledger remain permanent, so deleting the raw payload will not erase the membership, contract coverage, hashes, counts, or quality outcome.

## 5. Component status matrix

| Component | Status | Evidence or exact blocker |
|---|---|---|
| Read-only provider boundary | Validated for this phase | One public GET endpoint only; no credential used; all prohibited behaviors stayed off. |
| Current FnO stock universe | Validated as a point-in-time baseline | 210 unique stock underlyings, effective from the recorded retrieval time. |
| Historical universe membership | Blocked | A current master cannot reconstruct past additions/removals or eligibility dates; an approved historical membership source/method is absent. |
| Current option-contract reference | Validated as a point-in-time baseline | 30,065 contracts; permanent normalized snapshot plus raw lineage. |
| Contract lifecycle over time | Active only for data collection after future snapshots | The first observation establishes `first_seen`; removals/expiries require later dated snapshots and an approved cadence. |
| Daily underlying OHLCV | Coded but inactive | Upstox route exists; start/end dates and storage ceiling are not approved. |
| 15-minute underlying OHLCV | Coded but inactive | Upstox route exists; start/end dates and storage ceiling are not approved. |
| 5-minute underlying OHLCV | Coded but inactive | Upstox route exists; start/end dates and storage ceiling are not approved. |
| Ongoing underlying quote/volume observations | Coded but inactive | Exact cadence, market-session window, start date, and storage ceiling are not approved. |
| Timestamp and collection-order audit | Coded but inactive for ongoing collection | Requires the approved recurring collection design. |
| Timing-skew distribution | Not started | Requires a multi-request collection cycle; no cadence or cycle design is approved. |
| Sector mapping | Blocked | No approved sector source or effective-date methodology. |
| Corporate actions and symbol changes | Blocked | No approved source, event schema, or adjustment policy. |
| Option-chain OI/volume capability | Coded but inactive | Detailed option observation remains limited to a future approved cohort/study. |
| Broad option BBO collection | Explicitly prohibited | The authorization excludes continuous all-contract/all-strike/all-expiry BBO capture. |
| Retention deletion audit | Not started operationally | The policy and required deletion-event fields are defined, but raw data is not yet old enough to delete and cleanup is not automated. |
| Strategy, indicators, hypotheses, AI, alerts, P&L, orders | Not started and prohibited | Outside this step. |

## 6. Evidence supporting the next collection design

Upstox’s official Historical Candle V3 documentation states that minute data is available from January 2022 and daily data from January 2000. It limits 1–15 minute requests to one month per request and daily requests to one decade. The official rate-limit page states that standard APIs, including historical candles, are limited per user to 50 requests per second, 500 per minute, and 2,000 per 30 minutes. These are capability limits, not authorization to collect any particular range. See [Upstox Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) and [Upstox API rate limits](https://upstox.com/developer/api-documentation/rate-limiting/).

For the two unresolved reference datasets, official sources exist but are not yet approved. NSE publishes a downloadable corporate-actions view with symbol, purpose, ex-date, record date, and book-closure fields on its [Corporate Actions page](https://www.nseindia.com/companies-listing/corporate-filings-actions). NSE Indices publishes a four-tier company classification—macro-economic sector, sector, industry, and basic industry—on its [Industry Classification page](https://www.niftyindices.com/resources/industry-classification). Their availability does not by itself establish permitted automated collection, historical completeness, effective dating, or the correct price-adjustment policy.

## 7. What this proves—and does not prove

This phase proves that the system can reproducibly identify the current full NSE FnO stock-underlying universe and preserve complete current stock-option reference metadata with raw lineage, immutable audit events, a fixed parser version, and minimal storage.

It does **not** prove historical universe membership, daily/15-minute/5-minute coverage, intraday freshness, timestamp skew, outage rates, effective-dated sectors, corporate-action correctness, adjusted-price correctness, option-chain quality, liquidity, executable fills, costs, P&L, a strategy edge, or future performance.

## 8. Immediate next approval required from Aditya Lakhotia

No further collection should begin until Aditya approves one exact collection manifest containing:

1. the daily OHLCV start and end dates;
2. the 15-minute OHLCV start and end dates;
3. the 5-minute OHLCV start and end dates;
4. the ongoing underlying quote/volume cadence, NSE session window, and start date;
5. the historical raw-storage format (including whether content-addressed gzip storage is approved), a maximum temporary raw-storage ceiling, and whether collection must pause when that ceiling is reached;
6. whether NSE Corporate Actions is approved as the corporate-action/symbol-change source, including the exact fields and a policy that records events without silently adjusting prices; and
7. whether NSE Indices’ four-tier classification is approved for sector mapping, including how first-known dates and later classification changes will be represented.

Until those seven items are approved, the correct immediate state is **metadata baseline complete; historical and ongoing collection inactive**. No strategy proposal is permitted because the required full-universe data-foundation report has not yet been achieved.
