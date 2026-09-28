# Controlled next steps

**Owner and sole approver:** Aditya Lakhotia  
**Current state:** Governance remediation completed locally; all collection, research,
AI, alerting, publication, paper execution, and funded trading remain frozen.

This roadmap is sequential. Starting or approving one step does not authorize any later
step. Every proposed source, endpoint, retention period, dataset, feature, label, rule,
threshold, model, output, or behavior requires its own evidence and explicit approval from
Aditya Lakhotia.

## Non-negotiable boundaries

- Do not request, generate, configure, inspect, or use a market-data token until Step 3 is
  explicitly approved.
- Do not generate candidates, alerts, paper trades, P&L, strategy conclusions, Telegram
  messages, broker orders, position sizes, or funded trades.
- Do not activate AI learning or allow AI output to affect either system.
- Keep FnO Momentum and NIFTY500 Source News independent. Neither may feed, gate, rank,
  score, or alter the other.
- Preserve quarantined observations unchanged. They may be inventoried and integrity-checked
  but may not support research or performance claims.
- Treat repository code, passing tests, historical documentation, and prior results as
  evidence of implementation only—not proof of data quality, authorization, or edge.

## Step 1 — Review and publish the governance freeze

**Status:** Ready for review; implemented locally, not yet published to GitHub.

### What to do

1. Review the remediation diff.
2. Confirm that only `aditya-lakhotia` is an authorized approver.
3. Confirm that prior `benefactor-authority` events confer no active status.
4. Confirm that only `UPSTOX_ANALYTICS_TOKEN` could be accepted in a future approved run.
5. Confirm that the legacy daily-brief and Trident GitHub jobs are fail-closed.
6. After review, separately authorize committing and pushing the remediation if desired.

### Why it is needed now

The local safeguards do not affect GitHub-hosted workflows until the changes are committed
and pushed.

### Evidence available

- FnO tests: 44 passed.
- Source News tests: 4 passed.
- Clean-room boundary check passed.
- Existing FnO ledger: 3,219 events; hash-chain integrity verified.
- Existing source and universe activations now evaluate as inactive.
- Both frozen workflow YAML files parse successfully.

### What this does not prove

It does not approve Upstox, a credential, data retention, collection, research, AI, alerts,
paper execution, or trading.

### What remains blocked

All external and research activity remains blocked.

### Exact approval required

> “Aditya Lakhotia approves committing and pushing the reviewed governance-remediation
> changes. This approval does not activate any source, collector, strategy, alert, AI, paper
> execution, or trading behavior.”

## Step 2 — Obtain written market-data rights clarification

**Status:** Blocked pending external written evidence.

### What to do

Ask Upstox for written clarification covering only:

- Whether an individual account may privately store raw NSE equity and F&O observations
  obtained through the Analytics Token.
- The permitted retention duration for raw observations.
- Whether internally derived quality statistics and research datasets may be retained.
- Whether timestamped best bid/ask, OI, volume, candles, option-chain data, and contract
  metadata may be used for private, non-distributed paper research.
- Whether any deletion, attribution, audit, display, or redistribution restrictions apply.

Do not request or generate a token as part of this clarification.

### Why it is needed now

Provider documentation establishes read-only access but does not clearly grant the proposed
local retention duration. NSE policy makes usage and handling dependent on the applicable
agreement.

### Evidence required

- A dated written response or applicable contract/terms document.
- The exact document version or URL.
- A short mapping from each proposed stored field to the granted use.

### What this does not prove

Provider permission does not prove feed accuracy, completeness, latency, availability, or
suitability for executable paper fills.

### What remains blocked

Provider selection, storage duration, token generation, and collection remain blocked.

### Exact approval required

No project approval is needed merely to ask the rights questions. After the response is
received, Aditya Lakhotia must review it before Step 3 can be proposed.

## Step 3 — Approve one exact Upstox collection manifest

**Status:** Not approved; may be proposed only after Step 2.

### Proposed provider

Upstox Analytics Token only. Do not use a general OAuth access token or Upstox trading API.

### Proposed data scope

- Current NSE equity instruments that are underlyings of current stock-option contracts.
- Current NSE stock-option contract metadata.
- Completed underlying candles.
- Point-in-time underlying quotes.
- Point-in-time option-chain observations.
- Provider-timestamped option best bid/ask, quantities, OI, and volume.

### Proposed endpoint allowlist

- NSE BOD instrument JSON from `assets.upstox.com`.
- GET `/v3/historical-candle/...`.
- GET `/v3/historical-candle/intraday/...`.
- GET `/v2/option/contract`.
- GET `/v2/option/chain`.
- GET `/v3/market-quote/quotes`.
- WebSocket `/feed/market-data-feed` in `full` mode for an explicitly approved instrument
  set.

Everything else remains denied, including orders, GTT, holdings, positions, funds, margins,
payments, portfolio data, account mutation, and broker execution.

### Proposed storage method

- Raw responses/ticks stored locally by SHA-256 content address.
- Hash-chained SQLite metadata ledger.
- Record provider timestamp, local receipt timestamp, request metadata, parser version,
  source version, and content hash.
- Store no credential in the repository, `.env` files, logs, raw payloads, or ledger.
- Set retention only to the duration supported by the Step 2 written evidence.
- No external display or redistribution.

### Why it is needed

This establishes the factual point-in-time evidence required before claims about executable
option-paper P&L, spread, slippage, liquidity, cost, stop, target, or holding period.

### What this does not prove

The manifest does not prove data quality or authorize a research hypothesis.

### Exact approval required

An exact-hash source and collection manifest must be presented with the written rights
evidence. Aditya Lakhotia must explicitly approve that hash before a token is requested or
used.

## Step 4 — Controlled credential setup and tiny connectivity verification

**Status:** Blocked until Step 3 approval.

### What to do

1. Generate one Upstox Analytics Token only after approval.
2. Store it in an approved operating-system secret mechanism and inject it only at runtime.
3. Run a tiny, time-bounded verification against a few explicitly approved instruments.
4. Capture request status, timestamps, schema, and hashes only.
5. Stop after the verification report; do not expand automatically.

### Acceptance evidence

- Credential is demonstrably read-only.
- Only allowlisted hosts, methods, and routes were contacted.
- No credential appears in files or logs.
- Raw payload hashes match ledger records.
- Provider and local timestamps parse correctly.
- No order, alert, candidate, P&L, or model event is created.

### What remains blocked

Routine collection remains blocked until the verification report is reviewed.

### Exact approval required

Aditya Lakhotia must approve the verification cohort, instruments, time window, and command
before it runs, then separately approve progression to Step 5.

## Step 5 — Build and run data collection plus data-quality monitoring only

**Status:** Blocked until Step 4 review.

### What to build

- Resumable local capture for underlying candles and quotes.
- Point-in-time stock-option contract reference snapshots.
- Option-chain and provider-timestamped BBO capture.
- Completeness, duplicate, schema, timestamp, clock-drift, stale-quote, crossed-market,
  missing-side, OI, volume, contract-reference, and outage monitoring.
- Daily immutable quality reports and explicit failure records.

No production threshold may be invented. Initial reports must show distributions and
failures descriptively. Any acceptance threshold requires a later proposal and approval.

### What this does not include

No alerts, Telegram, candidate rules, strategy tests, paper orders, fills, P&L, costs,
position sizing, AI, or funded trading.

### Evidence required before proceeding

- Sufficient coverage across different sessions and market conditions.
- Documented missingness and outage behavior.
- Timestamp and quote-freshness distributions.
- Independent spot checks against an approved reference.
- Stable contract-reference and expiry handling.
- Evidence that raw observations can be reproduced from their hashes.

### Exact approval required

Aditya Lakhotia must approve the collection schedule, instrument scope, quality checks,
storage retention, and operating limits before routine collection starts.

## Step 6 — Submit one narrow research hypothesis

**Status:** Blocked until sufficient Step 5 evidence is reviewed.

### What to do

Submit exactly one hypothesis containing:

- One precisely defined underlying-market observation.
- One direction and one timeframe.
- Fixed eligibility and exclusion rules.
- Fixed outcome labels.
- One primary evaluation measure and stated baselines.
- A chronological development, validation, untouched test, and forward-paper plan.
- Data requirements, limitations, uncertainty, and failure conditions.
- An explicit statement that it does not authorize alerts or trades.

Do not submit multiple strategies, mirrors, parameter grids, or alternative thresholds at
the same time.

### What this does not prove

A plausible mechanism or historical result does not establish executable option edge.

### Exact approval required

Aditya Lakhotia must approve the exact hypothesis hash before any hypothesis-specific code
or test is run.

## Step 7 — Paper research for the approved hypothesis only

**Status:** Blocked until Step 6 approval.

### What to do

- Implement only the approved hypothesis.
- Keep development and evaluation periods time-separated.
- Preserve an untouched final test and forward-paper cohort.
- Use observed point-in-time option BBO for executable-price research.
- Report gross and net results separately with uncertainty, missingness, concentration,
  and sensitivity.
- Review results with Aditya Lakhotia before proposing any change.

### Prohibited shortcuts

- No favorable same-bar fill assumptions.
- No use of closing price as an executable option price.
- No silent spread, slippage, cost, stop, target, or holding-period assumptions.
- No threshold tuning on the untouched test or forward cohort.
- No activation of alerts or execution after a positive result.

### Exact approval required

Each implementation version, dataset version, label formula, evaluation plan, and change
requires exact-hash approval. Expansion requires a new proposal after joint evidence review.

## Step 8 — AI research design, only if later requested

**Status:** Not started and not authorized.

Before any AI model is used, submit one separate design specifying:

- Exact data visible to the AI.
- Permitted outputs: observations, rankings, explanations, or testable hypotheses only.
- Prohibited outputs: live decisions, alerts, orders, autonomous rules, or silent changes.
- Fixed labels, evaluation measures, and baselines.
- Time-separated validation and untouched forward-paper testing.
- Dataset, feature, prompt/model, training, result, and change versioning.
- Required explanations, evidence, uncertainty, and limitations.
- Isolation from the NIFTY500 Source News system.

No AI-related work may begin without explicit approval from Aditya Lakhotia.

## NIFTY500 Source News track

The Source News system remains independently frozen. Its future roadmap must start with its
own source-rights review and exact source proposal. It must have separate datasets, labels,
hypotheses, models, evaluation, forward testing, and approvals. Nothing in the FnO roadmap
authorizes or designs Source News behavior.

## Immediate next action

Review Step 1 and decide whether to authorize committing and pushing the governance freeze.
In parallel, the rights questions in Step 2 may be sent to Upstox without requesting a token.
No later step should begin.
