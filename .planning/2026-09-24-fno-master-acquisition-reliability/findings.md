# Findings & Decisions

## Requirements
-

## Research Findings
-

## Technical Decisions
| Decision | Rationale |
|----------|-----------|

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-
# Findings

- 24 September master request to `assets.upstox.com` failed Python TLS verification with a self-signed certificate in the chain. All 76 quote slots were audited as missed.
- Collector uses Python `urllib.request.build_opener(_NoRedirectHandler())` for the public master and authenticated API; no proxy or CA environment override is set. WinHTTP reports direct access.
- Same approved Upstox API host previously served authenticated historical requests. The issue appears specific to public asset transport, but that remains to be verified.
- Active v5 retries the master every 15 minutes during market hours, but a persistent trust failure will still lose the session.
- Current source/collection manifests allow the Upstox public NSE master only for current FnO reference. No fallback source is approved.
- A new direct Python GET on 24 September evening returned HTTP 200 with a 1,944,717-byte master using normal certificate verification. The fault is intermittent, not a permanent incompatibility of Python's CA store.
- A parallel `curl.exe` attempt reported connection failure; another parallel Python command was not resolved by that tool invocation although the executable exists. Repeated tests must use explicit Python path and separate diagnostic commands.
- Strict TLS handshakes to four currently resolved CloudFront IPs returned three valid Amazon certificates and one timeout. The earlier self-signed presenter remains unidentified.
- [Upstox's Instruments documentation](https://upstox.com/developer/api-documentation/instruments/) lists the NSE BOD JSON file on `assets.upstox.com`. The search API is paginated and query based, not a documented exhaustive full-master replacement.
- Recovery design: fetch the same approved file from 09:10 IST, retry failed requests once per minute through 10:00 and once per five minutes afterward, stop after the first verified current-day master, audit all failures and missed quote slots.
- The on-demand historical method deliberately requires `to_date` before the current IST date, so 24 September date-specific history cannot be requested until 25 September. The undated intraday endpoint can be sampled tonight as a diagnostic, but its rows must be checked for 24 September timestamps.
- A master fetched after close is effective only from retrieval; it cannot retroactively verify the 09:15 FnO membership or recreate the 76 missed quote snapshots. Keep any tonight master test under a diagnostic aggregate, rather than `master:2026-09-24`.
- Tonight's audited strict-TLS diagnostic master returned 210 FnO stock underlyings and 30,304 option contracts with zero malformed rows. A selected RELIANCE undated intraday request returned 75 valid 24 September five-minute bars. Neither result establishes full 24 September live quote coverage or market-open membership.
- Windows Task Scheduler successfully launched v6 via the PowerShell wrapper, but `Stop-ScheduledTask` left the `py.exe` launcher and `python.exe` worker alive. This can make task state misleading and allow an orphan to hold the collector lock across sessions. Replace the wrapper task action with direct Python execution and verify start/stop behavior.
- 25 September Windows System events show idle sleep at 13:17 IST and wake at 13:35 IST. The manual collector's last quote was 13:15; three slots during the interruption were audited as missed. A direct task restart captured another 210/210 slot but exited with `0xC000013A` after interruption.
- [Microsoft's SetThreadExecutionState documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate) states ES_SYSTEM_REQUIRED with ES_CONTINUOUS prevents idle system sleep until cleared, without requiring display power. This does not prevent manual sleep, lid close, shutdown, or battery exhaustion.
- V7 uses a bounded session-only system power request; the live process recorded successful activation and captured another 210/210 slot. `powercfg /requests` inspection was denied by Windows permissions, so sustained operation remains to be verified from the process and ledger.
