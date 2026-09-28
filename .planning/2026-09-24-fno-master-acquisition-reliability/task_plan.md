# FnO master acquisition reliability

## Goal
Restore reliable daily current FnO universe acquisition and preserve fail-closed, audited full-universe forward capture under the 3 GiB cap.

## Next Step
Monitor v7 and the five-minute task repetition through the remaining 25 September quote slots and after-close candles, then verify ledger, storage and task state; preserve all 52 missed slots in the coverage report.

## Current Phase
Phase 4: Live verification

## Phases

### Phase 1: Diagnose transport
- **Status:** complete
- Reproduced intermittent same-host behavior; reviewed approved master path and audit.

### Phase 2: Choose recovery
- **Status:** complete
- Keep the official Upstox NSE BOD file and strict TLS; request at 09:10, retry failures once per minute before 10:00 and once per five minutes afterward, maximum 120 attempts.

### Phase 3: Implementation
- **Status:** complete
- Versioned v6 manifest and collector, updated tests and operational notes, recorded authorization in ledger. Replaced the PowerShell task wrapper with direct Python execution after reproducing an orphan process on task stop.

### Phase 4: Live verification
- **Status:** in_progress
- V7 passed 83 tests. The 25 September scheduled start was missed and the host later slept during capture; v7 now holds a bounded Windows system power request. A further unexplained process exit caused six more missed slots. The scheduled task now repeats every five minutes during the collection window, ignoring triggers while the collector runs. A fresh 210-member master and later 210/210 quote cycles were recorded; 52 slots remain missed. Sustained capture and after-close work remain to be verified.

### Phase 5: Delivery
- **Status:** pending
- Report live outcome and any remaining source failure after the next session begins.

## Decisions Made
| Decision | Rationale |
|---|---|
| Keep bulk history disabled | User changed historical capture to on-demand. |
| Keep strict TLS and no stale membership | Current universe must be verified. |
| Keep the approved Upstox public NSE master | Official full BOD source; evening strict GET succeeded. |
| Retry earlier and more often | A transient failure should not cost the whole session. |
| Use the enabled 09:10 Windows task tomorrow | No market capture is due overnight; the prior manual v5 process was stopped. |
| Launch Python directly from Task Scheduler | Stopping the former PowerShell wrapper left a child collector alive and the task state misleading. |
| Permit scheduled collection on battery and enable wake timer | Battery restrictions blocked a launch while the host was discharging; unattended session capture must not depend on AC power. |
| Hold a session-only Windows system power request | System idle sleep interrupted the collector at 13:17; the request prevents idle sleep while collection is due and releases afterward. |
| Repeat the session task every five minutes through 17:30 | An unexplained second task exit left a six-slot gap; repeated triggers recover an absent collector, while `IgnoreNew` prevents concurrent instances. |

## Errors Encountered
| Error | Attempt | Resolution |
|---|---:|---|
| 24 Sep TLS certificate failure, 76 missed slots | 1 | Bounded same-source recovery implemented; earlier certificate presenter remains unknown. |
| One diagnostic curl connection failure and one Python command resolution error | 1 | Used explicit Python path and separate strict TLS tests. |
| Task stop left Python child process running | 1 | Direct Python action; repeated start/stop test left zero collector processes. |
| 25 Sep 09:10 task did not run; no collector or ledger events by 12:46 | 1 | Manual collector started; battery start/stop restrictions removed, wake enabled. Exact 09:10 condition cannot be proven because task history was disabled. |
| 25 Sep 13:17 idle sleep interrupted forward capture | 1 | V7 bounded Windows system-awake request implemented and started; three more slots remain missed. |
| 25 Sep collector exited after 13:45 despite v7 power request | 1 | Restarted task, recorded six more missed slots, and enabled five-minute task repetition. Task Scheduler Operational history could not be enabled due to OS access denial; cause remains unproven. |
