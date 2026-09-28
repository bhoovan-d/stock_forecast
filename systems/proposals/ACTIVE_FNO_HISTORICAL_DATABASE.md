# Active FnO historical database: selected 20 symbols

Active manifest: `fno_full_underlying_data_collection_v9.json`, SHA-256 `096448cf0911facc8641262f2205fc1261144503124be9f1cd356ebf50f89377`. This records the user's 28 September 2026 instruction to acquire historical underlying OHLCV for only the 20 listed FnO stocks. It supersedes the earlier STOP only for this narrower historical lane. Strategy and backtest work remain unauthorized.

## Scope and reference

Metal: TATASTEEL, HINDALCO, JSWSTEEL, VEDL, HINDZINC, JINDALSTEL, SAIL, NMDC, NATIONALUM, APLAPOLLO.

IT: TCS, INFY, HCLTECH, WIPRO, TECHM, LTM, OFSS, PERSISTENT.

Other: HINDPETRO, IDEA.

A fresh strict-TLS Upstox NSE master on 28 September verified all 20 exact symbols as active FnO stock underlyings. The catalog's `acquisition_scope` table freezes their exact NSE_EQ keys, v9 manifest hash and reference hash. `next_window` joins this table, so the runner fails closed if no scope is prepared and cannot request a planned window for any other stock. The run command checks that catalog symbols and manifest symbols match exactly. Existing records and plans for the other 190 underlyings are preserved, excluded from active requests and excluded from active coverage denominators. Current membership does not prove historical FnO eligibility.

## Dates, storage and execution

Daily: 2000-01-01 to 2026-09-25. Fifteen-minute and five-minute: 2022-01-01 to 2026-09-25. Provider availability and individual listing dates can shorten actual coverage. There are 2,600 planned windows for the selected symbols. An empty provider window is UNAVAILABLE; an unrequested window remains UNFETCHED. No missing bar is filled or substituted. Weekly bars remain UNAVAILABLE_CALENDAR until expected exchange sessions can be verified under the versioned Monday Asia/Kolkata rule.

The v9 measured projection file is `historical_db/storage_projection_v9.json`: approximately 80.45 MB additional raw plus normalized gzip data across these intervals, approximately 250.32 MB with a 35% margin and the existing store as of preparation. It lists interval-specific compressed/uncompressed estimates and source samples. The 2 GiB historical soft allocation and 3 GiB total cap remain enforced. The 28 September preparation preserved approximately 141.71 MB of existing data. These are planning figures, not observed final sizes.

`FnO-Historical-Database` is the only enabled collector task. It runs at most 250 windows per batch, with no more than 12 batches in one task launch, and repeats every ten minutes when the machine is available. It holds one session-local system-awake request across the bounded batch sequence, uses exclusive locks, and records responses and quality in the hash-chained ledger. This change followed observed idle sleep between separate batches on 28 September; the power request cannot prevent manual sleep, lid closure, shutdown, or battery exhaustion. A one-window continuous-mode trial completed before the revised task was enabled. The forward session and legacy backfill tasks remain disabled. This Windows computer is not an always-on host, so forward-session coverage is still unproven.

## Evidence and reporting

Catalog: `C:\adiproj2\data\fno_momentum\upstox\full_underlying_foundation_v1\historical_db\catalog.sqlite`.

Active reports: `historical_db/reports/status.json`, `coverage_by_stock_interval.csv.gz`, `window_inventory.csv.gz`, and `session_coverage.csv.gz`. Immutable compressed snapshots and per-window raw, normalized, and quality evidence are retained under the capped root. The report identifies preserved unselected windows separately. Failures, malformed/invalid rows, duplicates, partial windows, storage events, timing, source hashes and manifest/code hashes remain auditable. Historical exchange-calendar coverage, old FnO eligibility and adjustment basis remain unproven. The original seven-day raw retention rule and deletion audit still govern retention; this runner does not delete data.

The hourly monitor is active for the selected 20 only and stays quiet while acquisition progresses. At selected-scope completion or cap deferral, submit a factual historical quality report. Do not proceed to strategy or backtesting without Aditya Lakhotia's separate explicit approval.
