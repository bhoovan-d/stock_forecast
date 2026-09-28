> Historical policy update, 26 September 2026: v8 now authorizes the full historical database first. See ACTIVE_FNO_HISTORICAL_DATABASE.md. The v7 forward lane documented below remains disabled; its prior observations are retained.

# Controlled FnO underlying acquisition

The active collection specification is `fno_full_underlying_data_collection_v7.json`,
SHA-256 `d251eb3681dd0ded46c27eff8402110d99cf26639bbf55130eea51518a7e1ff9`.
It supersedes v1-v6 without rewriting their historical hashes. The Upstox source
manifest is `fno_source_upstox_underlying_v1.json`, SHA-256
`77d47130b0132cd4d9d66d9a284b5d32ee331db31f4079122f77e20ffbaca7c1`.
Each new audit event carries both hashes.

The storage root is `C:\adiproj2\data\fno_momentum\upstox\full_underlying_foundation_v1`.
The 3 GiB hard cap counts every file beneath it, including raw responses, normalized
partitions, references, logs, locks, and the ledger. Payload writes are serialized
between processes and check current full-root use before writing, leaving 32 MiB for
audit growth. Historical capture stops at 2 GiB of total use, preserving room for
the ongoing lane. Old windows that do not fit remain unfetched and are available
only through later, scoped on-demand collection. No option history is authorized.

The active Windows task is `FnO-Underlying-Data-Collection` at 09:10 IST on weekdays.
Its trigger repeats every five minutes through 17:30 IST so Windows restarts the
approved collector if it exits during the session. Running instances ignore
new triggers; the collector also holds a single-instance file lock.
`FnO-Underlying-History-Backfill` was disabled on 2026-09-24 at user direction.
The session task launches `python.exe` directly through
`scripts/run_fno_underlying_collection.py`; a 2026-09-24 launch/stop smoke test
confirmed that stopping the task stops its collector process. The earlier
PowerShell wrapper left an orphaned Python process after a task stop.
The session lane begins the current-day instrument master at
09:10 before quote snapshots, fetches completed five-minute, fifteen-minute and daily
candles after close, and performs audited retention after close. Needed historical
OHLCV windows may be requested by stock, interval and date range through the approved
Upstox historical API. Unrequested windows are recorded as UNFETCHED, not as missing
provider bars. A separate scraper or option-history source has not been approved.

The first live collection began during the 2026-09-23 session. Earlier scheduled
slots were recorded as missed, not backfilled as snapshots. Initial v1/v2 records
remain in the ledger with their original manifest hashes. The ledger also records
transport failures and an explicit operator resume after collector restarts.

A factual full-universe coverage report is due after at least five complete forward
sessions have been observed. It must show which historical windows were fetched and
which remain UNFETCHED under the on-demand policy. This phase authorizes only underlying market data and
current option-contract reference metadata; no indicators, hypothesis, candidates,
option bid/ask sweep, AI, paper P&L, alerts, Telegram, execution, or trading.

## 2026-09-23 coverage incident

The first session has 60 explicitly missed five-minute quote slots. There are 16
recorded quote cycles, but the snapshot associated with the 14:25 IST slot was
requested about an hour later. Its request and receipt times remain in the audit
ledger; it must not count as on-time coverage. Fifteen cycles began within their
five-minute slot deadline. The gap between late morning and afternoon
has no verified root cause in the available records. The collector now rejects a
quote request if its slot is already five minutes old and records the slot as
missed. The ongoing lane was resumed with that guard, and the scheduled task's
execution limit was extended to fifteen hours so after-close candle capture can
finish. No missed quote snapshot has been synthesized or filled.

The first after-close candle pass continued into the next calendar day. Upstox
documents its undated intraday candle endpoint as returning the current trading
day. Of 630 stock/interval requests, 436 returned bars for the target session,
166 returned no target-session bars, and 28 failed. The original 166 empty
responses had been counted as 5,425 missing bar starts; append-only
`session_response_classification_corrected` events now classify them as
unavailable target-session coverage. No missing bars were found within the 436
observed, valid session windows. Two separate date-specific diagnostic requests
for 2026-09-23 five-minute history returned zero rows early on 2026-09-24,
including RELIANCE, whose same-day intraday response had 75 rows. Historical
publication timing remains unverified. Future empty intraday responses are
classified as unavailable coverage without asserting that the session's bars
did not exist.

## 2026-09-24 instrument-master outage

The 09:20 IST public NSE master GET failed Python TLS verification with a
self-signed certificate in the chain. No current-day master was verified, so all
76 scheduled quote slots were marked missed and no 2026-09-24 session candles
were collected. The 3 GiB cap was not approached and the ledger verified.
Diagnostic requests found intermittent connection resets on the public asset
host; the authenticated historical API remained reachable. A current-day
master was not substituted with the previous day's snapshot. Version 6 begins
the same approved public master request at 09:10 IST, retries failures no more
often than once per minute before 10:00 and once per five minutes afterward,
and stops after 120 attempts or market close. Every failure and affected quote
slot remains in the ledger. Strict TLS verification remains required. An
evening diagnostic GET succeeded with strict TLS, so the next live session is
needed to verify that this recovery schedule works during market hours.
An audited after-close diagnostic master parsed 210 stock underlyings and 30,304
option contracts with no malformed rows. A separate RELIANCE 5-minute intraday
diagnostic returned 75 valid 2026-09-24 bars. These observations were not
registered as a market-open 2026-09-24 master or as full-universe session
coverage; the 76 missed live quote slots remain missed. Date-specific 2026-09-24
historical requests become eligible through the approved on-demand method on
2026-09-25.

## 2026-09-25 scheduled-start incident and recovery

At 12:46 IST the session task was Ready with no 2026-09-25 ledger events or
collector process, so the 09:10 scheduled start had not captured the market.
Windows task history was disabled. The host was discharging and the task's
settings prohibited starting on battery, stopped it on battery, and did not wake
the host. Those settings could prevent a start; without task history the exact
09:10 condition is unproven. The battery start/stop restrictions were removed
and WakeToRun enabled without changing the collector or source manifest.

A manually started direct Python collector obtained a fresh 2026-09-25 master:
210 stock underlyings and 30,746 option contracts. It recorded 43 missed quote
slots through 12:45 IST, with no synthetic catch-up. The 12:50 slot then
returned 210 of 210 underlying quotes, with zero missing or invalid rows;
request start was 54.9 seconds after the slot. The session remains partial and
after-close candle collection is still pending.

At 13:35 IST a subsequent check found the manually started collector gone after
its 13:15 quote cycle. Windows System events show entry into idle sleep at
13:17 and wake at 13:35. Three later quote slots were recorded as missed.
The direct scheduled task was restarted and captured another 210/210 quote
cycle, but then exited with Windows code `0xC000013A` after an interruption.
Version 7 now requests Windows system-awake state only while session collection
is due (09:10â€“17:30 IST on weekdays), without holding the display on or changing
the global power plan. It releases the request afterward; manual sleep, lid
close, shutdown, and battery exhaustion can still interrupt collection.
The v7 task was started and captured a 210/210 quote cycle with a recorded
successful system-awake request. Continued capture and after-close completion
remain to be verified. The missed-slot count is now 46.

At 14:22 IST the v7 task was again Ready with no collector after the 13:45
quote cycle. No sleep event or Python traceback explained that exit. The task
was restarted and the 14:20 cycle returned 210/210 quotes, albeit roughly
three minutes late. The 13:50â€“14:15 slots were recorded as six additional
missed cycles, bringing the day's missed count to 52. The task now repeats
every five minutes during the bounded collection window, with running
instances ignored. This recovery setting and its timestamp correction were
appended to the hash-chained ledger. Windows Task Scheduler Operational history
could not be enabled because the OS denied access, so the exact exit cause
remains unknown.
At 14:30 the repeat trigger left the existing collector running, and the
14:30 quote cycle returned 210/210. A later process exit and after-close
candle collection remain to be verified.

At 15:21 IST a monitor found the session task Disabled, with no collector
process. The task file was last modified at 14:59:27 IST, after the 14:50
quote cycle. At that check the ledger verified, storage was 62,062,475 bytes,
and 15 quote cycles plus 53 missed slots were recorded for 25 September.
No new manifest or ledger authorization for an on-demand-only mode was found.
The monitor recorded the observed task state in the ledger and will not
override the external disable while its intent remains unclear. After-close
candle collection is therefore not assured.
