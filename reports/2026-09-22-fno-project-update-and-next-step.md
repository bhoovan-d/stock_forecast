# FnO Momentum Project Update

**Date:** 22 September 2026  
**Current position:** The universe baseline is complete. Historical and ongoing market-data collection has not started.

## What we did

We first established the current NSE FnO stock universe. The purpose was to create a reliable starting list before collecting large amounts of price data or discussing any trading idea.

We downloaded the current NSE instrument file from Upstox and identified every stock with active NSE stock-option contracts. We also captured the reference details for those contracts, including the contract ID, underlying stock, call or put type, strike, expiry, lot size and tick size.

The first run found that Upstox supplied expiry dates in a different format from the one expected by our parser. The system reported the problem instead of silently changing the data. We checked the original file, corrected the parser and ran the process again. The original result, the correction and the final result remain recorded in the audit ledger.

We then created a permanent, compressed reference file containing the current universe and contract information. This file remains available even after the larger raw provider file reaches the end of its retention period.

We also prepared the proposed plan for the next data stage. It covers historical daily, 15-minute and 5-minute price and volume data for all current FnO stocks, followed by ongoing collection during market hours. This plan is still a proposal. It has not been activated.

Finally, we built the raw-data deletion control required before any larger collection begins. It checks the exact file, its recorded hash, its age, the approved manifest and the related quality report. It records the intention to delete before removing a file and records either completion or failure afterward. It will not delete a file early, delete an altered file or delete something outside the approved storage folder. No existing data was deleted while testing it.

## Result

The accepted baseline contains:

- 210 active NSE FnO stock underlyings;
- 30,065 current stock-option contracts; and
- 80,127 instrument records checked in the Upstox file.

The final run found no missing required contract fields, duplicate contract IDs, malformed rows, invalid expiries, invalid strikes, invalid lot sizes or invalid tick sizes.

The baseline currently uses about 2.18 MiB of local storage. The raw provider file accounts for about 1.89 MiB. The permanent compressed reference file is about 0.20 MiB, with the balance used by the audit ledger.

The full FnO test suite now contains 77 tests, and all 77 pass. The tests cover the universe baseline, provider boundaries, data-quality controls, proposed manifest restrictions and deletion-audit safeguards.

This work proves that we can identify the current full FnO stock universe, preserve its current option-contract details, detect format problems and retain a verifiable record of what happened.

It does not prove historical FnO membership, complete historical price coverage, correct corporate-action adjustments, historical sector classification, option liquidity, executable prices, paper profit or loss, or a trading edge.

No strategy, indicator, threshold, AI model, alert, Telegram process, paper trade, broker order or funded-trading function was introduced or activated.

## Next step

The next step is to review and approve one exact underlying-market-data collection plan. We should not start strategy research yet.

The proposed plan requests:

- daily price and volume data from 1 January 2000 to 21 September 2026;
- 15-minute and 5-minute data from 1 January 2022 to 21 September 2026;
- one current price and volume snapshot every five minutes during NSE market hours;
- completed daily, 15-minute and 5-minute candles after each market session;
- one updated universe and contract-reference snapshot for each trading session;
- a 250 MiB limit for the complete local data folder; and
- seven-day retention for raw provider responses, with permanent audit and quality records.

These dates, the five-minute schedule and the storage limit are proposals. They need Aditya Lakhotia's approval before we use the stored Upstox token or collect any additional data.

Corporate actions, symbol changes, sector classifications and historical FnO eligibility still need separate source decisions. NSE sources have been identified, but they have not been approved or connected. We will not adjust historical prices, assign old sector classifications or invent historical membership dates without approved evidence.

If the collection plan is approved, the work should proceed in this order:

1. Connect the approved manifest to the collector and deletion control.
2. Test a small storage-capped historical batch without changing the approved scope.
3. Run the historical collection in controlled batches and report every gap or provider failure.
4. Start the approved ongoing underlying-data collection.
5. Produce a full coverage and data-quality report for all FnO stocks.
6. Review that report before proposing one narrow research hypothesis.

Until the plan is approved, the correct status is:

> **Current FnO universe and contract-reference baseline complete; full-universe historical and ongoing underlying-market-data collection inactive; strategy research blocked.**
