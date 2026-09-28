# Governance remediation — approved 2026-09-16

Aditya Lakhotia approved a governance-remediation patch only. This approval does not
approve a market-data provider, token, endpoint activation, collection run, research
hypothesis, model, alert, paper fill, execution, or trading behavior.

## Binding state

- `aditya-lakhotia` is the only approval and activation identity.
- Events recorded under `benefactor-authority` remain immutable evidence but confer no
  current approval or activation authority.
- All observations collected under those delegated events are quarantined. They may be
  inventoried and integrity-checked, but may not support research or performance claims.
- FnO market-data collection is frozen.
- No credential may be requested, generated, configured, or used at this stage.
- A future connector may accept only `UPSTOX_ANALYTICS_TOKEN`; a general OAuth access token
  is forbidden.

## Proposed boundary awaiting separate approval

Provider selection remains a proposal: Upstox Analytics Token, subject to written
confirmation of private local retention and derived-research rights.

Proposed data-only routes:

- NSE BOD instrument JSON from `assets.upstox.com`.
- GET `/v3/historical-candle/...`.
- GET `/v3/historical-candle/intraday/...`.
- GET `/v2/option/contract`.
- GET `/v2/option/chain`.
- GET `/v3/market-quote/quotes`.
- WebSocket `/feed/market-data-feed` in `full` mode for an explicitly approved instrument
  set.

Proposed local storage is content-addressed raw payloads plus a hash-chained SQLite event
ledger containing request metadata, provider timestamps, local receipt timestamps, parser
versions, and content hashes. Retention duration is deliberately unset pending written
rights confirmation.

Orders, GTT, holdings, positions, funds, margins, payments, account mutation, alerts,
Telegram delivery, strategy publication, paper P&L, AI learning, and funded trading remain
outside scope and prohibited.
