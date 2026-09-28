# Controlled Research Foundation Report

**Date:** 17 September 2026  
**Owner and sole approval authority:** Aditya Lakhotia  
**Scope:** Factual build status and plans for neutral source research only  
**Current posture:** Both systems remain paper-research projects. No trading or
trading-like output is authorized.

## 1. Factual status matrix

| Component | Single status | Factual basis / exact blocker |
|---|---|---|
| Clean-room separation | **Validated for its stated purpose** | Boundary check passes; FnO Momentum, Source News, and legacy `asymmetry` imports/storage are separated. This proves code isolation only. |
| Approval enforcement | **Validated for its stated purpose** | Only `aditya-lakhotia` is recognized. Prior delegated approvals now confer no active status. |
| Market-data provider decision | **Blocked** | Neutral comparison and retention-rights evidence are not complete. No provider is approved. |
| Market-data credential | **Not started** | No approved manifest exists. No token may be requested, configured, inspected, or used. |
| Market-data access | **Blocked** | Current source and universe evaluate as inactive; provider, rights, endpoints, storage duration, and operating scope await approval. |
| Previously captured market data | **Quarantined** | 1,058 observations exist, but collection was based on non-authoritative delegated activation. They cannot support research or performance claims. |
| Content-addressed raw storage mechanism | **Validated for its stated purpose** | Hashing and content-addressed persistence are tested. This does not establish storage rights or provider-data accuracy. |
| Immutable event ledger | **Validated for its stated purpose** | The 3,219-event ledger passes integrity verification. This establishes internal integrity only. |
| Storage retention policy | **Blocked** | No written provider/contract evidence establishes permitted retention duration, deletion obligations, derived-data rights, or attribution requirements. |
| FnO instrument/universe discovery | **Coded but inactive** | Stock-option universe construction exists, but its prior activation is now inactive and no replacement policy is approved. |
| Historical underlying collector | **Coded but inactive** | Collector exists; routine or verification collection is not authorized. |
| Point-in-time underlying collector | **Coded but inactive** | REST snapshot and candle paths exist; no approved source or connectivity verification exists. |
| Point-in-time option BBO collector | **Coded but inactive** | BBO parsing exists, but reliable authorized capture has not been established. |
| Option-chain and contract metadata collector | **Coded but inactive** | Capture/parsing code exists; provider and endpoint use remain unapproved. |
| Data-quality monitoring | **Coded but inactive** | Two historical descriptive profiles exist, but their source data is quarantined and no acceptance thresholds are approved. |
| Cross-source reconciliation | **Not started** | No second approved reference source, matching rules, or tolerance policy exists. |
| FnO candidate logic | **Coded but inactive** | Candidate and pattern-related code exists; ledger contains zero candidate events. No hypothesis is approved. |
| Existing pattern proposal/results | **Quarantined** | They predate the required authorized point-in-time option evidence and cannot support activation or option-P&L conclusions. |
| Option contract selection | **Coded but inactive** | Contract-ranking structures exist; DTE, liquidity, quote-age, and ranking policies are not approved. |
| Option pricing/execution simulation | **Coded but inactive** | Paper fill and settlement mechanics exist; ledger contains zero execution events. |
| Costs, spread, and slippage | **Blocked** | No authorized point-in-time option BBO evidence or approved cost formula exists. |
| Stops, targets, and holding periods | **Blocked** | No research hypothesis or evidence-backed definitions are approved. |
| Paper P&L | **Blocked** | Executable entry/exit evidence, costs, hypothesis, and validation plan are absent. |
| FnO alerts | **Coded but inactive** | Alert structures exist; no alert events or active model/feature manifests exist. |
| Telegram delivery | **Not started** | No clean-room Telegram implementation exists. |
| Dashboard/publication delivery | **Blocked** | Legacy publication workflow is fail-closed; clean-room publication is not authorized. |
| Legacy daily brief | **Blocked** | GitHub job has a fail-closed guard. Local changes require separate commit/push approval to affect GitHub. |
| Legacy Trident forward record | **Blocked** | GitHub job has a fail-closed guard. Local changes require separate commit/push approval to affect GitHub. |
| FnO AI learning | **Not started** | No approved dataset, features, labels, baselines, prompt/model, training method, or evaluation design exists. |
| Broker execution/funded trading | **Not started** | Clean-room connector exposes no order endpoint; trading remains prohibited. |
| Source News clean-room foundation | **Validated for its stated purpose** | Independent ledger, governance, and isolation tests pass. |
| Source News provider decision | **Not started** | No completed neutral provider and rights comparison exists. |
| Source News collection | **Not started** | No approved source, credential, collector, runtime ledger, or stored observations exist. |
| Source News candidate logic | **Not started** | No approved candidate definition, label, feature, ranking, or hypothesis exists. |
| Source News AI | **Not started** | No AI design is proposed or approved. |
| Source News alerts/trading mechanics | **Not started** | No delivery, paper execution, or trading behavior exists. |
| FnO-to-Source-News data flow | **Validated for its stated purpose** | Clean-room enforcement prevents shared runtime imports. No cross-system flow is authorized. |

Passing tests, ledger counts, and hash-chain verification establish implementation or
integrity only. They do not establish source rights, market-data quality, market edge,
executable option P&L, or authorization.

## 2. Neutral market-data and rights research plan

### Providers in scope

The comparison will begin without a preferred provider:

1. DhanHQ Data API.
2. Upstox market-data APIs, including the Analytics Token.
3. TrueData as a data-only authorized-vendor candidate.
4. Global Datafeeds as another licensed data-vendor candidate.
5. Direct NSE/NSE Data products as the authoritative benchmark.
6. Zerodha Kite Connect only as a viability benchmark if its official terms and endpoints
   satisfy the required fields.

A provider will be removed only for an evidenced failure against a mandatory requirement.

### Evidence hierarchy

Use only:

1. Current official API documentation.
2. Current provider terms, subscription agreements, pricing schedules, support policies,
   and security documentation.
3. NSE/NSE Data policies and tariffs.
4. Written provider clarification for matters not resolved by published terms.

Marketing claims, forums, third-party comparisons, sample datasets, and prior repository
proposals will not establish rights or capabilities.

### Research matrix

| Area | Required evidence |
|---|---|
| Product | Exact product/tier name and whether an account or brokerage relationship is required. |
| Authentication | Token/key type, expiry, rotation, revocation, IP restrictions, and whether the credential is technically incapable of trading. |
| Endpoint authority | Exact market-data endpoints and whether the same credential can reach order, funds, portfolio, or account-mutation endpoints. |
| NSE equity coverage | Instruments, daily/intraday intervals, timestamps, volume, corporate-action treatment, and history depth. |
| Stock-option metadata | Underlying, instrument identifier, strike, right, expiry, lot size, tick size, contract lifecycle, and expired-contract discovery. |
| Option market data | Chain, provider-timestamped BBO, quantities, OI, volume, last-trade timestamp, snapshot versus streaming behavior, and depth. |
| Historical option data | Active/expired contracts, intervals, OHLCV/OI, BBO availability, survivorship limitations, and corporate-action handling. |
| Timing | Provider timestamp semantics, local receipt requirements, latency claims, session behavior, stale-data behavior, and outage reporting. |
| Limits | REST rates, WebSocket connections/subscriptions, record-window limits, concurrency, throttling, and overage behavior. |
| Reliability | Status reporting, service commitments, maintenance/deprecation policy, retries, and documented limitations. |
| Rights | Private research, raw storage, retention duration, derived datasets, deletion, attribution, audit, display, and redistribution. |
| Cost | Subscription, exchange fees, taxes, account charges, static-IP expense, add-ons, overages, renewal, and cancellation. |
| Secrets | Approved storage, rotation, revocation, logging restrictions, and incident procedure. |

### Mandatory acceptance conditions

A recommended provider must:

- Offer a genuinely enforceable read-only boundary.
- Cover NSE equities and stock options.
- Provide point-in-time, two-sided option quotes with usable timestamps.
- Provide OI, volume, quantities, and complete contract metadata.
- Permit the proposed private local research use and retention in writing.
- Have known total cost and operational limits.
- Support collection without enabling order or account-mutation behavior.

### Deliverable

Produce one concise evidence-linked comparison containing supported, unsupported, and
unknown for every field; exact source dates and versions; unresolved questions; security
and rights risks; expected annual cost; one provider recommendation; and a draft collection
manifest that remains inactive.

No token or connectivity test will occur during this research.

## 3. Independent NIFTY500 Source News provider-research plan

### Source categories to research separately

1. NSE and BSE official corporate announcements and filings.
2. NSE/NSE Data and BSE purchasable corporate-data products.
3. SEBI and other applicable regulator disclosures.
4. Company investor-relations releases.
5. Licensed news providers such as LSEG/Reuters, Bloomberg, FactSet, and comparable vendors
   with Indian-equity coverage.
6. India-focused licensed aggregators discovered through official documentation.
7. Lawful public RSS/API sources only where their terms explicitly permit automated private
   research and retention.

### Evidence required per source

| Area | Required evidence |
|---|---|
| Coverage | NIFTY500 company coverage, disclosure types, languages, attachments, corrections, and identifier quality. |
| Timing | Original publication timestamp, update/correction timestamp, retrieval latency, and point-in-time availability. |
| History | Earliest history, backfill method, attachment history, and delisted/renamed-company handling. |
| Delivery | API, feed, webhook, SFTP, RSS, or licensed file delivery; schema and rate limits. |
| Rights | Automated access, private storage, retention, derived labels, quotation, deletion, attribution, and redistribution. |
| Cost | License, taxes, exchange fees, user limits, request limits, archives, renewal, and cancellation. |
| Reliability | Status reporting, corrections, duplicate handling, outages, versioning, and support commitments. |
| Technical fit | Stable company identifiers, immutable raw capture, timestamps, content hashes, attachment retrieval, and revision tracking. |

### Research controls

- Do not define bullish/bearish rules, candidate scores, event thresholds, or execution
  vehicles.
- Do not use FnO data, labels, outcomes, models, or candidate lists.
- Do not allow Source News data to gate or alter FnO Momentum.
- Treat news articles and filings as source observations only.
- Preserve publication, correction, and local receipt times separately.
- Distinguish original documents from summaries and machine-generated text.
- Mark every unavailable or uncertain right explicitly.

### Deliverable

Produce a neutral source comparison, one recommended primary-source stack, a fallback/outage
proposal, exact rights and retention evidence, expected annual cost, an inactive source
manifest, and an explicit statement of what the proposed sources cannot prove.

No collection, candidates, AI, alerts, paper research, or cross-system connection will be
activated.

## 4. Exact approval required next

No approval is required to perform the read-only official-document research described in
sections 2 and 3.

Separate approval is required before contacting a provider for non-public contractual
clarification or pricing if that contact would identify or bind the project.

After the two research reports are delivered, the next possible approval would be:

> “Aditya Lakhotia approves the exact-hash read-only collection manifest for the selected
> provider, including the specified credential type, endpoint allowlist, instrument scope,
> storage method, retention duration, operating limits, and security controls. This
> approval authorizes only a separately defined tiny connectivity verification and does not
> authorize routine collection, research hypotheses, candidates, P&L, alerts, AI,
> execution, or trading.”
