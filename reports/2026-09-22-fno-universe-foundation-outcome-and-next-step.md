# FnO Data Foundation: Outcome and Next Step

**Decision authority:** Aditya Lakhotia  
**Date:** 22 September 2026  
**Scope:** FnO Momentum data foundation only

## Where we stand

We completed the first universe-wide data-foundation step.

The system downloaded one current NSE instrument-master file from Upstox and used it to identify the complete active NSE stock-option universe at that point in time. This was a public, read-only request. The stored Upstox Analytics Token was not accessed or transmitted.

The accepted baseline contains:

- 210 active FnO stock underlyings;
- 30,065 current stock-option contracts; and
- 80,127 total instrument-master rows reviewed.

For every stock-option contract, the permanent reference file retains the contract identifier, underlying identifier and symbol, call or put designation, strike, expiry, lot size, and tick size.

The accepted run found:

- no missing required contract fields;
- no duplicate contract identifiers;
- no malformed rows;
- no invalid expiries;
- no invalid strikes;
- no invalid lot sizes; and
- no invalid tick sizes.

This gives us a reliable current starting point. It does not reconstruct which stocks were in the FnO universe in the past.

## Audit result

The collection is reproducible and auditable.

The system retained the approved manifest, source version, parser version, code hash, collection time, raw-file hash, universe hash, contract-reference hash, instrument counts, and quality results. The audit ledger contains 20 immutable, hash-linked events and passes its integrity check.

The first run exposed an expiry-format mismatch. Upstox supplied expiry as epoch milliseconds, while the first parser expected an ISO date. The system reported all affected values as invalid instead of silently changing them. We inspected the raw response, corrected the parser, tested it, and ran the baseline again. The first result and its replacement remain recorded in the ledger.

The final collector and related controls pass all 69 FnO tests.

## Storage

The complete baseline currently uses about **2.18 MiB**:

- 1.89 MiB for the raw provider file;
- 0.20 MiB for the permanent compressed universe and contract-reference file; and
- approximately 0.09 MiB for the audit ledger.

The raw file is covered by the existing seven-day retention rule. It is due for deletion after 29 September 2026. Before deletion, we still need to implement the deletion procedure that records what was removed, when it was removed, why it was removed, and which retention rule applied. The compact reference file and audit ledger will remain.

## What this work proves

It proves that we can identify the current full NSE FnO stock universe, preserve its option-contract reference data, detect data-format problems, and maintain a verifiable audit trail with very little storage.

It does not prove historical price coverage, historical universe membership, sector history, corporate-action handling, provider uptime, intraday timing quality, option liquidity, executable prices, costs, paper P&L, or a trading edge.

No strategy, indicator, threshold, hypothesis, AI model, alert, Telegram process, paper trade, broker order, or funded-trading function was introduced or activated.

## Immediate next step

The next step should be to approve one exact **underlying-market-data collection manifest**. We should not begin strategy research yet.

That manifest must settle the following points:

1. Start and end dates for daily OHLCV history.
2. Start and end dates for 15-minute OHLCV history.
3. Start and end dates for 5-minute OHLCV history.
4. The cadence and NSE session window for ongoing underlying quote and volume observations.
5. The compressed raw-storage format, maximum local-storage ceiling, and fail-closed behavior when the ceiling is reached.
6. Whether NSE Corporate Actions is approved as the source for corporate actions and symbol changes, together with the permitted fields and treatment of price adjustments.
7. Whether NSE Indices is approved for sector classification, together with the method for recording first-known dates and later classification changes.

Our recommended sequence is:

1. Approve the exact collection manifest.
2. Implement and test the storage and deletion controls.
3. Collect the historical underlying data in small, storage-capped batches.
4. Start the approved ongoing underlying-data collection.
5. Produce the full-universe coverage and data-quality report.
6. Review that report with Aditya Lakhotia.
7. Only after acceptance, propose one narrow research hypothesis for separate approval.

Until the manifest is approved, the correct project state is: **current universe and contract metadata complete; historical and ongoing underlying collection inactive; strategy research blocked.**
