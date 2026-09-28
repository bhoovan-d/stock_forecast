# Draft — Upstox Market-Data Rights and Timestamp Clarification

**Status:** Draft only; not submitted  
**Intended destination:** Upstox private support ticket  
**Subject:** Analytics Token — private research retention and quote timestamp clarification

## Message

Hello Upstox Support,

I am evaluating the Upstox Analytics Token for a private, local, read-only market-data
research project. The system has no order placement, execution, alerts, public display,
redistribution, client access, or third-party data delivery. It uses only approved
market-data GET/WebSocket functions and does not configure a static IP.

Before beginning routine collection, please provide written clarification on the following:

1. Is automated collection of NSE equity and F&O market data permitted for private,
   non-display research using the Analytics Token?
2. Is private forward-paper research permitted when it produces no public alerts, broker
   orders, execution, or redistribution?
3. May raw REST and WebSocket market-data payloads be stored locally? If yes, what retention
   period applies?
4. May normalized or derived datasets be retained? If yes, what retention period and
   restrictions apply?
5. What deletion, backup, audit, attribution, or data-return obligations apply to private
   local research storage?
6. Do different rules apply to option-chain data, full market quotes, historical candles,
   instrument metadata, or WebSocket data?
7. In Full Market Quotes V3, what exactly does the top-level per-instrument `timestamp`
   represent: response generation time, last market-data update time, exchange time, or
   another event?
8. Is there a field in REST or Market Data Feed V3 that timestamps the most recent best-bid
   or best-ask change? If not, should consumers treat local receipt time as the only
   available observation time for BBO?
9. Please identify the applicable current terms, policy, agreement, or exchange rule that
   governs these permissions and restrictions.

This request is for clarification only. It is not a request to enable trading, static-IP
account APIs, order access, portfolio access, or any account mutation.

Thank you.
