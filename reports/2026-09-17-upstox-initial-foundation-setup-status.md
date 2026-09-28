# Upstox Initial Data Foundation — Setup Status

**Date:** 17 September 2026  
**Decision authority:** Aditya Lakhotia  
**Authorized scope:** Upstox Analytics Token setup and one-underlying/one-option
connectivity and data-quality verification only  
**Current result:** Setup implemented and tested; live verification blocked because the
Analytics Token is not yet present in Windows Credential Manager

## Completed

- Upstox is recorded as the selected provider for the initial read-only FnO data
  foundation. This is not a finding about historical option executability or retention
  rights.
- Credential loading now accepts only Windows Credential Manager target
  `AdityaResearch/UpstoxAnalyticsToken`.
- Environment-variable credential loading is explicitly rejected. The code does not load
  the token from `.env`, source, arguments, logs, reports, browser code or remote storage.
- A fail-closed outbound policy permits only canonical HTTPS `GET` requests for:
  NSE instrument metadata, market quotes, historical candles, option contract metadata,
  option chain, and the market-data WebSocket authorization route.
- The policy denies other hosts, HTTP, non-GET methods, unexpected query fields,
  redirects, account routes, user/profile routes, portfolio, holdings, funds, P&L, order,
  trade-history and mutation routes.
- A one-shot verifier is implemented for exactly one underlying (`RELIANCE`) and one
  nearest-expiry call option. It keeps provider response bodies in memory and writes only
  a sanitized verification report containing route audit, schema names, selected market
  fields, provider/local timestamps and quality failures.
- Routine collection is not started. Existing collectors remain approval-gated.
- The FnO Momentum test suite passes: **56 passed**.

## Network-contact audit for this setup run

No Upstox route was contacted. The credential-presence check returned **ABSENT**, so the
verification failed closed before constructing or sending an authenticated request.

## Token status

No token value was obtained, displayed, logged, copied into the repository or stored by
this implementation. Aditya Lakhotia must generate the Analytics Token through the
[official Upstox Analytics Token page](https://upstox.com/developer/api-documentation/analytics-token/).
Do not configure a static IP.

After generation, store it through the hidden-input local command below from
`C:\adiproj2\systems\fno_momentum`:

```powershell
$env:PYTHONPATH='src'
& 'C:\adiproj2\.venv\Scripts\python.exe' -m fno_momentum.secret_store
```

The command prompts for the token without echoing it and stores it as a Windows generic
credential for the current Windows user. The token must not be pasted into chat.

## Verification command after the token is stored

The already-authorized one-shot verification can then be run from the same directory:

```powershell
$env:PYTHONPATH='src'
& 'C:\adiproj2\.venv\Scripts\python.exe' -m fno_momentum.upstox_verification
```

Its intended output is
`C:\adiproj2\reports\2026-09-17-upstox-tiny-verification-report.md`. The command exits
after that one report and does not schedule or start collection.

## What the completed setup proves

It proves that the local code boundary rejects environment credentials and non-allowlisted
routes under the tested cases. It also proves that the one-shot verifier cannot proceed
without the approved OS-stored credential.

It does not prove authentication, connectivity, provider schema, provider timestamps,
option bid/ask availability, quantities, OI, volume, freshness, completeness, latency,
retention rights, historical option BBO, executable fills, P&L or any trading edge. Those
connectivity/schema items remain pending the one-shot run.

## Exact blocker and next action

**Blocker:** Windows Credential Manager does not contain the Upstox Analytics Token at the
approved target.

**Required action by Aditya Lakhotia:** generate one Analytics Token, do not configure a
static IP, and store it with the hidden-input command above. This is credential setup only;
it does not approve routine collection or any later project stage.

## What remains prohibited

Routine collection, strategy or hypothesis work, candidates, historical option-P&L claims,
alerts, Telegram, AI, execution, trading, Source News activity and any FnO/Source News
cross-system flow remain blocked.
