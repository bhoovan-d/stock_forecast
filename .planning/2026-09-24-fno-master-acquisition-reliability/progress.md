# Progress Log

## 2026-09-24 repair

- Read planning-with-files skill and created this task plan.
- Verified 24 Sep outage: 76 missed quote slots, TLS failure of public master, no current-day reference; ledger intact.
- Verified strict Python GET of the approved master now returns HTTP 200; four resolved edges gave three valid certificates and one TLS timeout. Root cause of earlier self-signed presenter remains unproven.
- Reviewed official Upstox Instruments documentation and retained the same full NSE BOD file; did not add a scraper or disable certificate checks.
- Versioned collection manifest to v6 (SHA-256 `7eb274171e8a1dc1a44eab76da2156d42e31e99fa8e8f89417fb6c8712bf4ab6`). Collector now begins at 09:10 IST, retries failure every minute before 10:00 and every five minutes afterward, maximum 120 attempts; each attempt/failure remains audited.
- Added schedule and pre-open collector tests. Full FnO test suite: 82 passed.
- Stopped old v5 manual collector after close. Windows task is enabled and due 2026-09-25 09:10 IST; bulk history task remains disabled.
- V6 authorization event appended; ledger verifies through 7,722 events. Storage 57,055,321 bytes of 3 GiB.
- Changed monitoring to hourly during NSE hours plus after close, with quiet normal operation and prompt failure notification.

## Remaining live check

- The 25 September session must prove a fresh master and quote capture. If strict TLS fails through retries, diagnose the certificate presenter or upstream host with new evidence; do not use a stale universe or bypass TLS.

## 2026-09-24 immediate checks

- After-close strict-TLS master diagnostic succeeded and validated 210 stock underlyings, 30,304 option contracts, zero malformed rows. It was not registered as the 24 September market-open membership reference.
- RELIANCE 5-minute intraday diagnostic returned 75 rows for 24 September, no invalid timestamps. It does not recover the missed quote snapshots or establish full-universe coverage.
- Scheduled-task smoke test exposed orphaned Python processes after stopping the PowerShell wrapper. Stopped the orphans, added `scripts/run_fno_underlying_collection.py`, changed the task to execute Python directly, and repeated the test. Task returned to Ready with zero collector processes, next run 25 September 09:10 IST.

## 2026-09-25 existing-data replay

- At 01:49 IST the session task was Ready with next run 09:10 IST; no live market session had begun.
- Added repeatable read-only `scripts/verify_fno_existing_data.py` and ran it twice after tightening per-event raw hash comparison.
- Ledger chain verified across 7,728 events; checked 2,436 raw observations, 2,052 normalized observations, replayed 2 instrument masters, 16 quote batches, and 602 candle windows against source payloads. Zero integrity or recorded-count failures.
- This verifies existing data handling and audit consistency. It cannot prove 25 September provider connectivity or live quote timing before the session starts.

## 2026-09-25 live incident

- At 12:46 IST the scheduled task was Ready, no collector was running, and the ledger had no 25 September events. The 09:10 start had been missed.
- Manual task start queued while the host was discharging. The task prohibited starts on battery, stopped on battery, and had WakeToRun disabled. Task history was disabled, so the exact 09:10 cause is unproven.
- Started the approved direct Python collector manually to recover the remaining session. It obtained a fresh master with 210 stock underlyings and 30,746 option contracts.
- The collector recorded 43 missed slots through 12:45 IST. Its 12:50 slot returned 210/210 quotes, zero missing or invalid rows, request lateness 54,899 ms. No missed snapshot was synthesized.
- Changed the existing task to allow starting and continuing on battery and enabled WakeToRun. Confirmed it remains enabled with next weekday trigger. Bulk history task remains disabled.
- Remaining: verify subsequent slots and after-close candles, and confirm the scheduled trigger works on the next trading day.

## 2026-09-25 idle-sleep recovery

- At about 13:35 IST the collector was absent after its 13:15 quote cycle. Windows System events show idle sleep at 13:17 and wake at 13:35. Three subsequent quote slots were recorded as missed, increasing the day to 46 missed slots.
- A direct scheduled task restart returned 210/210 quotes but exited with `0xC000013A`. No Python traceback was recorded.
- Versioned collection manifest to v7, SHA-256 `d251eb3681dd0ded46c27eff8402110d99cf26639bbf55130eea51518a7e1ff9`, adding a session-only Windows system-awake request. No source, data, historical mode, or cap expansion.
- Full FnO suite: 83 passed. V7 authorization event was appended; task started and recorded `system_awake_request_changed` enabled plus a 210/210 quote cycle.
- `powercfg /requests` inspection returned a Windows permissions error. Subsequent slots and after-close completion will provide operational verification.

## 2026-09-25 second collector interruption

- At 14:22 IST the v7 task was Ready and the collector absent after a 13:45 210/210 quote cycle. System logs did not show sleep after v7 activation; no Python traceback was recorded. The cause remains unproven.
- Restarted the approved task. Its 14:20 slot returned 210/210 quotes after a roughly three-minute delay. The six slots at 13:50–14:15 were recorded as missed, increasing the day to 52 missed slots. No gaps were filled.
- Changed the existing weekday 09:10 task to repeat at five-minute intervals for 8 hours 20 minutes, ending 17:30 IST. `MultipleInstances=IgnoreNew` and the collector lock prevent overlapping collectors. Verified the task remains Running and next repetition is scheduled.
- Appended the recovery configuration to the hash-chained ledger. A correction event identifies an imprecise hand-entered timestamp in the first event payload; the immutable event timestamp is authoritative.
- Attempted to enable Task Scheduler Operational history for failure diagnosis, but Windows denied access. Live repeat/recovery and after-close completion remain to be verified.
- At the 14:30 repetition trigger, the existing collector remained running with the same PID, and the 14:30 quote cycle returned 210/210. The task reported an ignored overlapping launch while it stayed Running. Recovery after a future process exit and after-close candles remain unverified.

## 2026-09-25 external task disable observed

- At 15:21 IST the session task was Disabled and no collector was running. Its Windows task file showed a 14:59:27 IST modification, after the last recorded 14:50 quote cycle. The reason and actor are unknown.
- Ledger verified through 7,860 events before the observation event; 15 complete quote cycles and 53 missed slots were recorded for today, with a fresh 210-member master and 30,746 option contracts. Storage was 62,062,475 bytes of 3 GiB.
- Appended `forward_task_state_observed` to the ledger, then verified the chain through 7,861 events. Because the user is discussing an on-demand-only switch, the monitor will not override the external disable without clarified mode authorization. Bulk backfill remains disabled.
- Updated the heartbeat prompt to avoid automatically restarting a disabled task while the mode transition is unresolved. After-close candles and the five-session forward report are now at risk of not completing.
