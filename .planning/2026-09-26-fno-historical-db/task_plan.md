# Historical FnO database
## Authorized objective
Build auditable historical underlying OHLCV for the complete active Upstox NSE FnO stock universe. User binding instruction on 2026-09-26 supersedes the on-demand-only historical policy. Historical database first; no strategy work. Existing forward task remains disabled until reliable host work is separately completed.
## Design
Extend the existing approved collector with a versioned v8 historical manifest, frozen current master, deterministic oldest-first requests spanning daily 2000-01-01 and intraday 2022-01-01 through 2026-09-25, and a SQLite partition/quality catalog. Raw and normalized compressed partitions remain authoritative; the catalog supplies searchable per-stock/date/interval coverage without duplicating every bar in SQLite. Reuse verified existing responses, log every attempt and interruption, and use exclusive collector locks. Measure raw/normalized sizes before new bulk history. Stop at the existing 2 GiB historical allocation within the 3 GiB total cap. Weekly derivation requires a verified session calendar; do not invent holiday/missing-day expectations.
## Phases
1. Inspect existing collector and constraints - complete
2. Calculate and record storage projection - complete
3. Implement catalog, validation and resumable capped runner with targeted tests - complete (96 tests passed)
4. Refresh reference, activate v8, import existing evidence and collect approved history - in progress; collector running
5. Verify database, report coverage and remaining work - initial verification complete; bulk coverage pending
## Next Step
Monitor the historical batches until all windows are terminal or explicitly storage-deferred, then publish factual historical coverage. Weekly calendar and reliable forward hosting remain unresolved data tasks.
## Limits
Provider documented earliest bounds are daily January 2000 and minute January 2022, not a claim that every instrument exists back to those dates. Historical eligibility, listing dates and official historical session calendar remain unverified. Do not change retention/deletion policy.

## 28 September scope and quality-review update
The user narrowed acquisition to 20 named symbols under manifest v9. Their 2,600 windows are terminal and the historical task is disabled. A separate read-only quality review is recorded at `data/fno_momentum/upstox/full_underlying_foundation_v1/historical_db/reports/quality_review_v9_2026-09-28.md`. Next data-only work: version an official calendar, audit special-session re-normalization, investigate 24 remaining absent five-minute starts and daily/intraday basis, and establish historical instrument continuity. Do not resume live collection or begin strategy work.

## 29 September clearance result
The known special-session and weekend dates are versioned in exception calendar v2. Audited reclassification restored 960 valid intraday candles and resolved 17 exact duplicate daily pairs, with prior payloads retained. Upstox rechecks confirmed that all 24 five-minute gaps and 473 invalid daily source rows persist. Integrity checks pass, but full historical clearance remains blocked by those source issues, incomplete official calendar, unresolved historical lineage and daily/intraday basis. See `quality_clearance_decision_v9_2026-09-29.md`. No strategy work is authorized.
