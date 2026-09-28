# Approval packet

Nothing in this directory is active merely because it is written down. Each approved item
must be entered in its track's immutable ledger with the exact content hash and an approval
rationale from Aditya Lakhotia before activation.

## FnO source recommendation — superseded by free-source direction

The paid DhanHQ proposal is retained as an inactive comparison, but is no longer recommended
after the direction to use free sources.

The recommended first source is now `fno_source_upstox_free_v1.json`. It is intended only
for the private paper-research system and explicitly prohibits using any broker order,
portfolio, funds, or account-mutation endpoint.

Exact SourceManifest SHA-256:
`f47a860205a3a9ff922a4338bb1246a94ea56620f34167edf6c9311a5ba86aa1`

Why it is recommended:

- Upstox documents a free read-only Analytics Token.
- Its V3 feed documents current option bid/ask depth, volume, open interest, timestamps, and
  option metadata needed for forward paper fills.
- Its Historical Candle V3 API documents minute/hour data from January 2022 and daily data
  from January 2000 for supported instruments.
- Recurring API subscription cost is INR 0.

Approval still requires confirming that the operator owns or will open an Upstox account and
that current account terms permit private retention and analysis. Historical expired-option
BBO is not claimed; historical option P&L remains a separately approved estimate, while
forward paper fills require observed bid/ask data.

## FnO policies intentionally not proposed yet

Liquidity thresholds, quote-age limits, pattern thresholds, cost formulas, and model
publication thresholds require a clean-room data-quality study. Inventing those values before
the source is approved would violate the engineer direction. Test fixtures exercise the code,
but they are not production proposals.

## FnO first pattern proposal ready for review

`fno_pattern_prior_session_high_bearish_reclaim_v1.json` freezes one underlying-only
research pair: a bearish prior-session-high sweep/reclaim on completed 15-minute bars. The
bullish mirror was rejected because it failed both later chronological windows. This pattern
proposal does not activate paper alerts and does not approve contract liquidity, costs,
option execution, or a publication model.

Exact ChangeProposal SHA-256:
`79d6da6704d846112f98b3cc97c28bf9903f09523e855f61bdc06a1a93da0883`

Before activation it still requires explicit approval by Aditya Lakhotia, a fresh untouched
historical cohort, and option-BBO cost evidence after the read-only Analytics Token is
available.

## FnO universe policy ready for approval

`fno_universe_policy_v1.json` freezes the binding stock-only rule: current NSE_FO CE/PE
contracts whose underlying type is EQUITY, with INDEX and ETF excluded. The proposal is
supported by data-quality profile
`724673b046e2df21222242e10cd920d6c95376df073a1809447464094a304c76`.

Exact ChangeProposal SHA-256:
`9ea8663c50d7c80e7a3bc2b2adfc43cc87260b16b84f962226d7df25916fcf9c`

## NIFTY500 Source News design decision

The design approval mechanism is implemented, but the exact instrument policy needs a product
decision before an honest proposal can be submitted. The main options are:

1. Underlying event-study labels only for the first reference slice (lowest execution claims).
2. Cash-equity paper fills for bullish ideas and research-only labels for bearish ideas.
3. Long calls/puts where FnO contracts exist, with non-FnO names retained as research-only.

Option 1 is recommended for the first source/news slice because it establishes source timing,
candidate quality, and directional evidence without pretending that all NIFTY500 names have a
uniform executable vehicle.
