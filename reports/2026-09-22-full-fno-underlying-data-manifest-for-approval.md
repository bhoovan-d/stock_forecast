# Full FnO Underlying-Data Manifest — For Aditya's Review

**Status: proposed, inactive.** The accepted baseline covers the 210 stocks and 30,065 current option contracts seen on 22 September 2026. It is not the full historical data foundation. No token was accessed and no new market data was collected while preparing this proposal.

The exact machine-readable proposal is [fno_full_underlying_data_collection_v1.json](../systems/proposals/fno_full_underlying_data_collection_v1.json). Its separate proposed Upstox source definition is [fno_source_upstox_underlying_v1.json](../systems/proposals/fno_source_upstox_underlying_v1.json). Neither file is active.

For exact-version review, the collection manifest SHA-256 is `fd10f7f5e84bd17125456aefd929a3549880ed53a7afc2e7c9cc7d41b293ed5f`. The source-manifest file SHA-256 is `77d47130b0132cd4d9d66d9a284b5d32ee331db31f4079122f77e20ffbaca7c1`. Any change to either file requires a new review and hash.

## Proposed scope

| Data | Proposed request | Why this range | Limitation |
|---|---|---|---|
| Daily underlying OHLCV | 1 January 2000–21 September 2026, inclusive, for every current FnO stock | Requests the longest daily range Upstox documents | A stock may have a shorter listing/history; current membership must not be projected backward |
| 15-minute underlying OHLCV | 1 January 2022–21 September 2026, inclusive | Upstox documents minute history from January 2022 | Actual per-stock coverage must be measured, not assumed |
| 5-minute underlying OHLCV | 1 January 2022–21 September 2026, inclusive | Same provider limit | Actual per-stock coverage must be measured, not assumed |
| Ongoing underlying quote/volume | Every five minutes from 09:15 to 15:30 IST on observed NSE equity sessions, proposed first session 24 September 2026 | Covers all current underlying stocks with small snapshots | A quote every five minutes does not reconstruct trades or an OHLCV bar |
| Ongoing completed OHLCV | After each session, request completed daily, 15-minute and 5-minute provider candles | Completes the session's price/volume record | A missing or late provider candle remains missing; no synthetic bar |
| Universe and option reference | One instrument-master snapshot per observed session at or after 09:20 IST | Records later additions, removals, symbol/key changes and contract first/last seen | First/last observed is not an official eligibility date |

These dates and the five-minute cadence are **proposals**, not prior decisions. If approval or implementation misses 24 September, the proposed start date must be revised and its new file hash approved. No backdated or automatic start is allowed.

Upstox's [Historical Candle V3 documentation](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/) states that minute history is available from January 2022, daily history from January 2000, with one-month request windows for 1–15 minute candles and one-decade windows for daily candles. Its [Intraday Candle V3 documentation](https://upstox.com/developer/api-documentation/v3/get-intra-day-candle-data/) describes current-session daily and minute candles. The [Full Market Quotes V3 documentation](https://upstox.com/developer/api-documentation/get-full-market-quote-v3/) permits up to 500 keys per request; the accepted 210-stock baseline fits in one request today, though a larger future universe may need more than one. A proposed local ceiling of two requests per second, 100 per minute and 1,000 per 30 minutes is below Upstox's published [standard-API limits](https://upstox.com/developer/api-documentation/rate-limiting/). Provider limits may change and do not guarantee full historical availability.

## Data quality and audit

Each request will carry its universe-snapshot hash, instrument key, requested interval/window, collection order, request start/end, local UTC receipt time, provider timestamp and last-trade timestamp where supplied. Every raw response will be hashed. Coverage will be reported by stock, session and interval. Missing instruments, duplicate or malformed bars, schema changes, invalid or sentinel timestamps, stale quotes, outages and discontinuities will be recorded without silent retries, filling or substitution.

For each collection cycle, the system will report elapsed time from request start to final receipt, first-to-final local row processing, and the spread of provider timestamps. If all 210 stocks come in one response, it cannot measure their separate network-arrival times; the report will say so. No timing threshold will be used as a trading rule.

The proposed local cap is **250 MiB for the entire foundation directory**, including raw payloads, normalized data and audit files. Raw HTTP payloads would be compressed and kept for seven days; normalized versioned data, hashes, manifests, quality summaries, failure records and deletion records would remain. If a write would exceed the cap, collection stops and reports unfinished coverage. The cap may prove too small; that would require a new approval, not silent expansion or early deletion.

The deletion-audit procedure is now coded and tested. It defaults to dry-run, requires an exact raw file and matching source/quality/manifest evidence, refuses files younger than seven days or whose hash changed, records deletion intent before removing a file, and records completion or failure afterward. No existing raw data was deleted, and no cleanup schedule was started. The procedure must be connected to the future collector and tested again before any historical or ongoing raw collection begins.

The older RELIANCE pilot does not contain every code-version field required by this new procedure. Its seven-day raw cleanup needs a separate provenance check before applying the procedure; this proposal does not authorize or perform that cleanup.

## Separate source decisions still needed

This proposal identifies, but does not activate, [NSE Corporate Actions](https://www.nseindia.com/companies-listing/corporate-filings-actions) for corporate actions and symbol changes, and [NSE Indices Industry Classification](https://www.niftyindices.com/resources/industry-classification) for sectors. Their exact fields, access method, retention rights, publication/first-seen dates and change histories need separate source manifests and approval. Historical FnO eligibility also needs an approved dated NSE source; the current Upstox master alone cannot reconstruct it. No price adjustments or historical sector assignments will be inferred.

## Approval requested

Aditya Lakhotia should review the exact collection and source manifests and decide whether to approve their dates, endpoints, cadence, token boundary, 250 MiB cap, seven-day raw retention and failure behavior. Approval of this Upstox proposal would still **not** approve the separate NSE corporate-action, sector or historical-eligibility sources. Until the exact manifest hash is expressly approved, historical and ongoing collection remain inactive.

No strategy, indicators, AI, broad option BBO, paper P&L, alerts, Telegram, Source News input, orders or trading are part of this proposal.
