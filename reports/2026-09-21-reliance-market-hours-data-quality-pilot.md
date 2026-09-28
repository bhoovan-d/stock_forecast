# RELIANCE Market-Hours Data-Quality Pilot

**Run date:** 21 September 2026  
**Observation window:** 15:10:34–15:29:34 IST  
**Provider:** Upstox Analytics Token, read-only market-data routes only  
**Approved scope:** RELIANCE underlying plus all currently listed RELIANCE CE/PE contracts  
**Result:** Completed with one missing snapshot caused by a connection reset

## Executive result

The pilot attempted 20 one-minute snapshots. Nineteen completed and snapshot 17 failed
when the remote host forcibly closed the connection (`WinError 10054`). The collector did
not retry, change endpoints, extend beyond the approved window, or broaden its scope.

The current instrument master identified 220 RELIANCE option contracts. Each successful
snapshot requested and returned the underlying plus all 220 contracts. This establishes
that the approved REST path can return the intended current contract set during the sampled
market window. It does not establish uninterrupted reliability because one of 20 scheduled
observations failed.

## Completion and coverage

| Measure | Result |
|---|---:|
| Planned snapshots | 20 |
| Successful snapshots | 19 |
| Failed snapshots | 1 |
| Successful snapshot numbers | 1–16 and 18–20 |
| RELIANCE option contracts discovered | 220 |
| Instruments requested per successful snapshot | 221 |
| Total instrument observations requested | 4,199 |
| Total instrument observations returned | 4,199 |
| Missing returned instruments | 0 |
| Unexpected returned instruments | 0 |
| Malformed rows | 0 |
| Collection gaps other than the recorded failed snapshot | 0 |

## Market-field quality observations

| Measure | Result |
|---|---:|
| Observations without a positive two-sided market | 392 of 4,199 (9.34%) |
| Distinct instruments empty at least once | 24 |
| Instruments empty in all 19 successful snapshots | 19 |
| Crossed-market observations | 7 of 4,199 (0.17%) |
| Distinct crossed instruments | 1 |
| Negative OI observations | 0 |
| Negative volume observations | 0 |
| Zero/sentinel last-trade timestamps | 38 |

The provider timestamp preceded local receipt by approximately 1.00–3.32 seconds across
the captured rows; the median of the per-snapshot medians was approximately 1.09 seconds.
This is an observed provider-to-local timestamp difference, not a verified network-latency
or best-bid/best-ask age measurement.

Last-trade ages ranged from approximately 1.26 seconds to an unusable epoch-derived value.
The extreme value is explained by 38 rows carrying a zero/sentinel last-trade timestamp.
Last-trade time must therefore be validated before it is used, and it must not be treated
as the timestamp of a bid/ask change.

## Reliability finding

Snapshot 17 recorded:

> `URLError: [WinError 10054] An existing connection was forcibly closed by the remote host`

The next three scheduled snapshots completed successfully. This is evidence of one
transient failure in a 20-observation window. It is not enough evidence to estimate a
long-run outage rate or uptime percentage.

## Storage and controls

| Control | Result |
|---|---:|
| Pilot files | 21 |
| Raw provider responses | 20 files, including the instrument master |
| Raw storage | 7,106,003 bytes |
| Total pilot storage | 7,314,899 bytes (6.98 MiB) |
| Hard storage ceiling | 262,144,000 bytes (250 MiB) |
| Raw retention | 7 days |
| Ledger integrity | Verified |

- The Analytics Token remained in Windows Credential Manager.
- Only the approved instrument-master and full-market-quote GET routes were available.
- No account, funds, portfolio, order, alert, Telegram, strategy, P&L, AI, or trading
  function was contacted or activated.
- No cloud backup or external sharing occurred.

## What this proves

This pilot proves only that, during the recorded 19-minute market-hours window, the
approved read-only collector discovered 220 current RELIANCE option contracts, returned
complete instrument rows for 19 scheduled observations, preserved content hashes and local
receipt times, stayed below its storage ceiling, and recorded one network failure without
silently filling the gap.

## What this does not prove

It does not prove that empty or crossed markets were executable, that provider timestamps
represent the last bid/ask change, that historical quotes can be reconstructed, or that
the feed will have similar quality at other times or for other stocks. It does not prove
liquidity, slippage, transaction costs, paper profitability, a hypothesis, a trading edge,
or future performance.

## Remaining blockers

1. No research hypothesis has been proposed or approved.
2. No contract-selection rule, outcome label, cost model, fill rule, baseline, validation
   partition, or forward-paper protocol is approved.
3. The single connection reset and observed empty/crossed markets must be treated as data
   limitations; no reliability threshold has been approved.
4. No further collection is authorized by this completed pilot.

## Exact next approval

The next controlled step is review of this report. Only after review should one narrowly
defined research hypothesis be drafted for approval. That draft must state its permitted
data, fixed rules, outcome labels, costs, baseline, time-separated validation, forward
paper-test design, uncertainty, and limitations. Drafting it must not activate collection,
alerts, AI, orders, or trading.
