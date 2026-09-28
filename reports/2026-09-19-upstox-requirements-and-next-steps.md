# Upstox Data Foundation — Requirements and Next Steps

**Date:** 19 September 2026  
**Decision authority:** Aditya Lakhotia  
**System:** FnO Momentum only  
**Current posture:** Read-only paper-research foundation; routine collection remains inactive

## 1. Current position

The authorized initial Upstox setup and tiny verification are complete.

- The Analytics Token is stored in Windows Credential Manager and was not disclosed.
- Static IP was not configured.
- The outbound boundary permits approved market-data routes only and denies account,
  portfolio, holdings, funds, order, trade-history, P&L and mutation routes.
- One RELIANCE underlying and one RELIANCE option contract were verified.
- The response contained bid, ask, bid/ask quantities, OI, volume, provider general
  timestamp, last-trade timestamp and local receipt timestamp.
- The run occurred outside market hours. It did not prove live BBO freshness.
- Upstox did not expose a distinct timestamp for the last bid/ask change in the verified
  response.
- Routine collection, research hypotheses, candidates, P&L, alerts, AI, execution and
  trading were not started.
- NIFTY500 Source News remains completely separate and inactive.

Verification evidence is recorded in
`C:\adiproj2\reports\2026-09-19-upstox-tiny-verification-report.md`.

## 2. What is needed from Aditya Lakhotia

### Immediate decision: permission to resolve the remaining rights questions

The provider decision authorizes Upstox as the initial technical source, but expressly
does not establish legal rights beyond the published documentation. Before a final
routine-collection manifest can state a retention policy as fact, the following questions
need written answers from Upstox:

1. Is automated collection for private, non-display research permitted?
2. Is forward paper research permitted when it produces no public alerts or orders?
3. May raw market-data responses be stored locally, and for how long?
4. May normalized/derived datasets be retained, and for how long?
5. What deletion, backup, audit and attribution conditions apply?
6. Does the V3 quote `timestamp` represent response generation, a market-data update, or
   something else?
7. Is there any field that timestamps an individual best-bid/best-ask update?
8. Are there additional restrictions on option-chain or WebSocket data retention?

No account change, contract, purchase, token change or technical trial is required to ask
these questions.

### Later decisions required for the exact collection manifest

After the rights response is available, Aditya must approve each of the following as one
versioned, exact-hash manifest:

| Manifest area | Decision required |
|---|---|
| Collection purpose | Data foundation and data-quality monitoring only. No candidate, prediction, alert or trading output. |
| Instrument scope | Exact underlying list and exact option-contract discovery boundary. No universe may be inferred by the collector. |
| Approved routes | Exact REST and WebSocket host/path/method allowlist. All account and trading routes remain denied. |
| Fields | Exact underlying, contract metadata, BBO, quantities, OI, volume and timestamp fields to retain. |
| Cadence | Exact polling/streaming frequency and provider-rate-limit budget. |
| Operating window | Exact market-session times, startup/shutdown rules and holiday behavior. |
| Storage | Exact local directory, raw/normalized separation, encryption/access, hashing and backup policy. |
| Retention | Provider-supported duration for raw payloads, normalized data, hashes, logs and backups. |
| Quality monitoring | Exact checks for missing fields, stale observations, crossed/empty markets, timestamp lag, duplicates, gaps and schema drift. |
| Failure behavior | Conditions that stop collection, quarantine data and require review. No automatic expansion or fallback source. |
| Security | Credential target, process identity, logging exclusions, revocation and incident procedure. |
| Version control | Manifest hash, approver, approval time, code version and change process. |

## 3. Proposed controlled next steps

### Step 1 — Obtain written rights and timestamp clarification

**What:** Send Upstox the fixed questions in Section 2.  
**Why now:** The exact retention policy and timestamp interpretation cannot be truthfully
completed from the public documentation or one verification response.  
**Evidence:** The provider decision itself states that it does not establish legal rights;
the verification observed a general timestamp but no distinct BBO-change timestamp.  
**What it does not prove:** A provider response will not prove data quality, latency,
completeness, executable fills or a research edge.  
**What remains blocked:** Routine collection and all downstream research behavior.  
**Approval required:** The exact approval text in Section 5.1.

### Step 2 — Draft the inactive exact-hash routine-collection manifest

**What:** Convert the confirmed rights and Aditya's scope choices into a complete,
versioned manifest.  
**Why:** The system must know precisely what it may collect and how it must stop before any
routine run.  
**Evidence:** The initial verification confirms basic technical connectivity and observed
schema only.  
**What it does not prove:** Drafting a manifest does not validate collection reliability or
authorize execution.  
**What remains blocked:** Routine collection until the exact manifest hash is approved.  
**Approval required:** Approval to draft is not operational authorization; the completed
manifest must be separately reviewed and approved.

### Step 3 — Review and approve one exact manifest version

**What:** Aditya reviews every field and approves the exact content hash.  
**Why:** This prevents silent changes to instruments, endpoints, cadence, storage,
retention or quality rules.  
**What remains blocked:** Any scope not explicitly present in that manifest.  
**Approval required:** The exact approval text will contain the final manifest hash and
cannot be produced until Step 2 is complete.

### Step 4 — Build and test only the approved collector and quality monitor

**What:** Implement routine read-only collection and quality monitoring exactly as
approved. Use fixtures and local tests first.  
**Why:** This establishes point-in-time evidence needed before later research claims.  
**What it does not prove:** Collection alone does not prove executable P&L, a strategy,
liquidity, slippage, stops, targets or holding periods.  
**What remains blocked:** Hypotheses, candidates, scoring, alerts, AI, Telegram, execution
and trading.

### Step 5 — Run the approved collection foundation and review evidence sufficiency

**What:** Begin only the approved data capture and monitoring period. Report gaps, stale
quotes, schema changes, outages and timestamp limitations.  
**Why:** Point-in-time underlying and option evidence must exist before proposing research
about executable options.  
**What it does not authorize:** No hypothesis is created automatically. After sufficient
evidence exists, exactly one narrow hypothesis would be proposed for separate approval.

## 4. Items that are not being requested or approved

- No second provider or silent fallback source.
- No additional token, OAuth credential or static-IP configuration.
- No account, portfolio, holdings, funds, order, trade-history or P&L access.
- No historical option bid/ask reconstruction or executable historical option-P&L claim.
- No strategy, pattern, threshold, candidate, score, stop, target or holding period.
- No alerts, Telegram, dashboard publication, execution or funded trading.
- No AI dataset, feature, label, model, prompt or learning behavior.
- No Source News or sentiment-source work.
- No information flow between FnO Momentum and NIFTY500 Source News.

## 5. Exact approvals

### 5.1 Approval needed now

> Aditya Lakhotia authorizes one non-binding written clarification request to Upstox,
> limited to private automated market-data collection, forward-paper research use, raw and
> derived retention, backup and deletion obligations, audit and attribution conditions,
> option-chain/WebSocket retention restrictions, and V3 quote/BBO timestamp semantics.
> This approval does not authorize an account change, contract, purchase, token change,
> connectivity test, routine collection, strategy, hypothesis, candidate, P&L, alert, AI,
> Telegram, execution, trading, Source News activity, or cross-system information flow.

### 5.2 Approval required later

After the written response and inactive manifest are complete, the later approval must
identify the exact manifest hash and authorize only the described routine read-only data
collection and quality monitoring. It must continue to prohibit all strategy, candidate,
P&L, alert, AI, execution, trading and Source News activity.

## 6. Recommended immediate action

Approve Section 5.1 only. After that approval, prepare the fixed Upstox clarification
message for Aditya's review before it is sent. No provider contact or other action should
occur merely because this report exists.
