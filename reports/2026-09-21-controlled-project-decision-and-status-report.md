# Controlled Project Decision and Status Report

**Report date:** 21 September 2026  
**Approval authority:** Aditya Lakhotia  
**System:** FnO Momentum research track  
**Current phase:** Read-only data-foundation validation; no strategy or trading activity

## 1. Executive summary

The project has completed its first controlled technical objective: establishing that a
strictly read-only Upstox collector can obtain current NSE equity and stock-option market
data during market hours, preserve an auditable record, enforce a storage limit, and report
data-quality failures without silently filling gaps.

Upstox remains the selected initial technical source because its Analytics Token has the
clearest read-only boundary among the broker APIs reviewed and the required current option
fields were observed successfully. The planned request to Upstox support was not submitted.
Aditya decided that the system is private, local and non-distributed, and instructed the
project to proceed without seeking separate provider clarification. That decision removed
provider contact from the work plan; it did not change the documented limitation that
Upstox does not expose a distinct timestamp for the most recent best-bid or best-ask change.

An initially discussed plan to collect every NSE stock-option contract every minute was
found to be unnecessarily large for the current stage. Its estimated storage requirement
was roughly 0.8–2 TB for normalized data, plus raw data. After review, that plan was
superseded before activation. The project moved to an on-demand design: use provider history
and metadata when required, and locally collect point-in-time bid/ask evidence only for a
small approved test.

The approved market-hours pilot covered RELIANCE and all 220 of its currently listed option
contracts. Twenty one-minute snapshots were scheduled. Nineteen completed; snapshot 17
failed because the remote host reset the connection. Every successful snapshot returned all
221 requested instruments—the underlying plus 220 options. The pilot consumed only 6.98
MiB. It found meaningful limitations: 9.34% of returned instrument observations lacked a
positive two-sided market, seven observations were crossed, and 38 rows used a zero/sentinel
last-trade timestamp. These are factual data-quality findings, not trading conclusions.

The project is now ready for review of this evidence. It is not ready for alerts, paper P&L,
AI learning, orders or funded trading. The immediate next step is to approve preparation of
one narrowly defined research-hypothesis proposal. Preparation of that proposal would not
activate the hypothesis or start further collection.

## 2. Decisions taken and why

### 2.1 Controlled, approval-gated scope

Aditya established that no source, field, threshold, model, strategy, alert or trading
behavior may be introduced without explanation and explicit approval. This was implemented
through exact manifests, an immutable audit ledger and fail-closed collectors. The purpose
is to keep architecture, evidence and future research decisions separate, so technical
capability cannot silently become trading behavior.

The FnO Momentum and NIFTY500 Source News systems remain independent. No news, sentiment or
cross-system input was used in this work.

### 2.2 Upstox as the initial technical source

The provider comparison considered Dhan, Upstox, TrueData, Global Datafeeds, direct NSE and
Zerodha. Upstox was retained for the initial foundation because:

- its Analytics Token is expressly read-only and cannot place, modify or cancel orders;
- it supplies NSE equities, options, contract metadata, bid/ask, quantities, OI and volume;
- the token and initial access are free;
- a one-shot verification had already confirmed authentication and the required response
  fields.

Dhan did not provide equally strong public evidence of a credential technically incapable
of trading. TrueData and Global Datafeeds require separate commercial/compliance handling
for simulation-type use. Direct NSE is authoritative but contract-heavy and substantially
more expensive. Upstox therefore represented the smallest controlled technical starting
point—not proof that it is the final provider for every future use.

### 2.3 No provider-support request

A nine-question Upstox support ticket was prepared to clarify private retention, paper
research and timestamp semantics, but it was never submitted. Aditya decided that the
project is private, local, non-public and will not use Telegram or redistribution, and
instructed the project to proceed without asking permission.

The implementation records that decision while preserving two limitations: public material
does not provide a distinct BBO-change timestamp, and this project is not making a legal or
contractual finding about indefinite retention. Raw pilot data is therefore limited to a
seven-day retention window and is not shared externally.

### 2.4 Broad collection was considered, then superseded

Aditya initially approved all eligible NSE single-stock F&O underlyings, every listed CE/PE
strike and expiry, one-minute snapshots, and regular market hours. Once the storage impact
was calculated, Aditya questioned whether the entire option market needed to be stored.
That was the correct control point: the broad plan would have collected far more data than
the immediate technical objective required.

The broad manifest was marked superseded before activation. No all-market routine collection
occurred. The revised decision was to use Upstox on demand for historical candles, current
metadata and current option chains, while recognizing that historical point-in-time BBO
cannot be requested later. Local quote capture will therefore be limited to approved,
hypothesis-specific forward tests.

### 2.5 RELIANCE market-hours pilot

RELIANCE was selected only as a technical verification instrument because it had already
been used in the initial one-shot check. This was not a stock recommendation or research
candidate. The pilot covered the underlying and every currently listed RELIANCE call and
put to test contract discovery, batching, completeness, timing and storage behavior.

The approved pilot was initially 60 minutes. When the scheduled run did not activate and
the remaining market session was short, Aditya explicitly approved a reduced 20-minute run.
Only the duration changed. The one-minute cadence, instruments, endpoints, storage ceiling
and non-trading boundaries remained fixed.

Implementation choices included a 250 MiB hard storage ceiling, exact-manifest hash check,
GET-only host/path/query allowlist, Windows Credential Manager for the token, content hashes
for raw responses, and an immutable SQLite audit ledger. These controls were chosen so a
failure would stop or be recorded instead of silently changing scope.

## 3. Work completed

The following work is complete:

1. A neutral provider and rights comparison was documented using official evidence.
2. Upstox was selected as the initial read-only technical source.
3. The Analytics Token was stored in Windows Credential Manager rather than source, logs or
   environment files.
4. A one-shot out-of-hours verification confirmed authentication, the instrument master,
   an underlying quote and one option quote.
5. The proposed broad collector was evaluated for storage and superseded before activation.
6. A dedicated RELIANCE market-hours pilot manifest, source manifest and collector were
   created.
7. The collector enforced the approved market window, 20 snapshots, one-minute cadence,
   RELIANCE-only universe and 250 MiB limit.
8. All 64 FnO package tests passed after the implementation.
9. The audit ledger passed its integrity-chain verification.
10. A factual pilot report was produced with no strategy or P&L interpretation.

### Pilot evidence

| Measure | Observed result |
|---|---:|
| Scheduled snapshots | 20 |
| Successful snapshots | 19 |
| Failed snapshots | 1 |
| RELIANCE option contracts | 220 |
| Requested/returned observations | 4,199 / 4,199 |
| Missing or unexpected instruments | 0 / 0 |
| Malformed rows | 0 |
| Empty two-sided markets | 392 (9.34%) |
| Crossed observations | 7 |
| Zero/sentinel last-trade timestamps | 38 |
| Provider-to-local timestamp difference | approximately 1.00–3.32 seconds |
| Total pilot storage | 6.98 MiB |

Snapshot 17 failed with `WinError 10054`, indicating that the remote host forcibly closed
the connection. The collector recorded the gap and did not retry, extend the window or
substitute data. Snapshots 18–20 then completed normally. This proves failure visibility,
not long-run reliability.

## 4. Current standing

| Component | Current status | Exact position |
|---|---|---|
| Provider research | Completed | Upstox selected as initial technical source; alternatives documented. |
| Read-only security boundary | Validated for the pilot | GET-only allowlist and Analytics Token; no order/account routes. |
| Credential storage | Validated | Token remains in Windows Credential Manager. |
| Initial connectivity | Validated with limitations | Out-of-hours one-shot check completed. |
| Market-hours data pilot | Completed with one failure | 19/20 snapshots; connection reset recorded. |
| Broad all-market collector | Inactive and superseded | It was never activated or run. |
| Routine data collection | Not started | No continuing collector or schedule is active. |
| Data-quality monitoring | Validated for pilot scope | Completeness, empty/crossed markets, timestamps, failures and quota recorded. |
| Raw pilot retention | Active housekeeping obligation | 7.1 MB of raw responses should be removed after the approved seven-day window. |
| Research hypothesis | Not started | No rule, feature, threshold or candidate has been proposed. |
| Paper P&L and liquidity claims | Blocked | Requires an approved hypothesis and relevant forward BBO observations. |
| AI learning | Blocked | Data access, outputs, labels, baselines and validation are not approved. |
| Alerts, Telegram and execution | Prohibited/inactive | No implementation or activation is authorized. |
| NIFTY500 Source News integration | Prohibited | The system remains independent and did not participate in this work. |

The evidence proves that the limited collector works and exposes failures. It does not prove
that a quote was executable, that Upstox's timestamp marks a BBO change, that liquidity is
sufficient, that a strategy is profitable, or that any trading edge exists.

## 5. Immediate next step

The immediate next step is **review and acceptance of the completed data-quality evidence**.
If Aditya accepts the pilot as sufficient for the foundation stage, the next authorized work
should be preparation—not activation—of one narrowly defined research-hypothesis proposal.

That proposal must state, before any testing begins:

- the single factual market behavior being tested;
- the exact historical and forward data it may use;
- fixed entry/exit observations and outcome labels;
- realistic bid/ask, transaction-cost and failure treatment;
- a simple baseline it must beat;
- time-separated development, validation and untouched forward-paper periods;
- the exact contracts and storage needed for forward evidence;
- success, rejection and stop conditions;
- known timestamp, missing-market and reliability limitations.

Preparing the proposal would not authorize data collection, strategy testing, candidates,
alerts, AI, Telegram, orders or trading. Those would remain blocked until the exact proposal
is explained and separately approved by Aditya Lakhotia.

## 6. Reference artifacts

- `reports/2026-09-21-reliance-market-hours-data-quality-pilot.md`
- `systems/proposals/fno_upstox_reliance_quality_pilot_v1.json`
- `systems/proposals/fno_source_upstox_reliance_quality_pilot_v1.json`
- `systems/proposals/fno_upstox_collection_scope_v1.json` — superseded, never activated
- `systems/fno_momentum/src/fno_momentum/reliance_quality_pilot.py`
- `data/fno_momentum/upstox/reliance_quality_pilot_v1/events.sqlite`
