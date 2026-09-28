# Neutral Market-Data and Rights Research

**Research date:** 17 September 2026  
**Approval authority:** Aditya Lakhotia  
**Status:** Completed official-document desk research; no provider selected  
**Scope boundary:** Documentation research only. No account, contract, token,
credential, endpoint call, connectivity test, data capture, manifest activation,
hypothesis, paper P&L, alert, AI, execution, or trading action was performed.

## 1. Decision state

No provider can yet be approved from public evidence alone. Upstox has the clearest
technical read-only boundary, but its public documents do not establish the project's
required raw-data retention, derived-data, deletion, and audit rights. Dhan covers many
required fields but does not document a credential technically incapable of trading.
TrueData and Global Datafeeds are data-only vendors, but their published terms expressly
restrict virtual/simulation use, and their API pricing and project-specific retention
rights are not public. Direct NSE is authoritative and data-only, but requires a specific
agreement and has materially heavier commercial/connectivity requirements. Zerodha's
credential includes order capabilities and therefore fails the mandatory technical
read-only condition.

This is an eligibility finding, not a recommendation or selection.

## 2. Evidence labels

- **Supported** — the cited current official document expressly supports the field.
- **Unsupported** — an official document contradicts the requirement, or the documented
  product lacks it.
- **Unknown** — no sufficiently explicit current official evidence was found. Unknown is
  not treated as permission.

Unless a source states its own revision date, the version is recorded as **page version
not stated; accessed 17 September 2026**.

## 3. Mandatory-condition comparison

| Mandatory condition | DhanHQ Data API | Upstox Analytics Token | TrueData API | Global Datafeeds API | Direct NSE/NSE Data | Zerodha Kite Connect |
|---|---|---|---|---|---|---|
| Technically enforceable read-only credential | **Unknown** — Data APIs use a Dhan access token; no public document found says that token cannot call order/account-mutation endpoints. | **Supported** — one-year Analytics Token is expressly read-only, GET-only, and cannot place/modify/cancel orders. | **Supported at provider-product level** — provider says it offers data, not execution; exact credential scope is not publicly documented. | **Supported at provider-product level** — market-data key and no broker execution product identified; exact credential scope is not publicly documented. | **Supported** — subscription is a data feed governed by an agreement, not a brokerage/order API. | **Unsupported** — the same authenticated product expressly supports real-time order execution. |
| NSE equities and stock options | **Supported** | **Supported** | **Supported** | **Supported** | **Supported** | **Supported** |
| Point-in-time two-sided option quotes | **Supported for current top BBO**; BBO-specific provider timestamp is **unknown**. | **Supported for current BBO/depth**; feed has a current timestamp, but an independent timestamp attached to each BBO change is **unknown**. | **Supported for current L1 BBO**; quote timestamp semantics are **unknown**. | **Supported for current L1 BBO** and fields include server/last-trade time; BBO-change timestamp semantics are **unknown**. | **Supported**, with depth depending on subscribed product; exact proposed feed is not selected. | **Supported**, five bid/offer levels with exchange timestamp. |
| OI, volume, quantities, contract metadata | **Supported** | **Supported** | **Supported in product description**; full metadata schema is **unknown**. | **Supported in API descriptions**; lifecycle/completeness guarantees are **unknown**. | **Supported** subject to exact feed/specification. | **Supported for live contracts**; expired option discovery is materially limited. |
| Historical NSE equity/intraday | **Supported** — daily to inception; minute history for active instruments for five years. | **Supported** — minute/hour from January 2022 and daily from January 2000. | **Supported generally**; exact depth and adjustment policy are **unknown**. | **Supported** — intraday backfill windows and daily history are published. | **Supported** through paid EOD/historical products. | **Supported** — several years of candles, exact universal depth not stated. |
| Historical expired stock options | **Supported in a bounded form** — rolling five years, minute records, strike-window limitations; no historical BBO. | **Supported only through Plus endpoints and bounded expiry availability**; no historical BBO. | **Unknown** for expired-contract completeness and BBO history. | **Unsupported for the required long research horizon** — published option contract daily history is one month and intraday backfill is at most six months; tick backfill one week. | **Potentially supported** through historical order/trade products, subject to exact licensed product/agreement. | **Unsupported for a robust expired-option universe** — only live option tokens are exposed; continuous history is for futures, not options. |
| Private local raw storage and retention in writing | **Unknown** — docs recommend local storage, but no public license term grants duration, derived-data, deletion, or audit rights. | **Unknown** | **Unknown** — internal analysis is described, but project-specific raw retention/duration is not. | **Unknown** | **Unknown until Relevant Agreement is reviewed** | **Unknown/restricted** — instrument caching is recommended, but broader market-data retention rights are not established. |
| Paper-research/simulation use permitted | **Unknown** | **Unknown** | **Unsupported by published standard terms** absent NSE/SEBI approvals; virtual trading/simulation is expressly restricted. | **Unsupported by published terms**; virtual trading/simulation applications are expressly restricted. | **Unknown until intended use is accepted in the Relevant Agreement** | **Unknown**; the public API product is trading-enabled and does not establish this project's controlled paper-research rights. |
| Known total annual cost | **Partly supported** — ₹499 plus taxes per 30 days for Data APIs; brokerage/account/static-IP/other charges and renewal/cancellation total remain **unknown**. | **Partly supported** — Analytics Token is free; brokerage account and Plus/other add-on costs relevant to expired data remain **unknown**. | **Unknown** — API is separately quoted after compliance review; displayed Velocity prices explicitly exclude API access. | **Unknown** — quote-based API price. | **Partly supported** — current exchange tariffs are public, but final product, line, taxes, agreement and operating costs are not fixed. | **Partly supported** — ₹500/month per app for live and historical data; account/taxes/other total remain **unknown**. |
| Overall public-evidence eligibility | **Blocked** | **Blocked** | **Blocked** | **Blocked** | **Blocked** | **Fails mandatory read-only condition** |

## 4. Detailed provider findings

### 4.1 DhanHQ Data API

**Product and access.** Dhan publishes separate Trading and Data API areas. Its support
page prices Data APIs at **₹499 plus taxes every 30 days**. The same documentation family
uses an access token and client ID across API calls. No public document found establishes
a distinct market-data credential that is technically incapable of order submission.

**Coverage and fields.** The official option-chain endpoint supplies best bid and ask,
quantities, OI, volume, implied volatility, greeks, price and a security identifier across
NSE/BSE/MCX options. The option-chain limit is one unique request every three seconds.
The live feed permits up to 5,000 subscribed instruments on a connection and publishes
trade, volume, OI and five-depth packets. Instrument masters expose exchange/segment,
security identifiers and derivative metadata.

**History.** Daily OHLCV is documented back to inception. Intraday OHLCV/OI for active
instruments is available in 1, 5, 15, 25 and 60-minute intervals for five years, with a
90-day maximum request window. Dhan's expired-option product documents rolling five-year
minute data with OHLC, IV, volume, strike, OI and spot, but only bounded strike ranges.
Historical bid/ask is not documented.

**Timing limitation.** Live packets and option-chain fields expose last-trade timing, but
the public documentation does not establish a provider timestamp that specifically marks
when the displayed best bid/ask was valid or changed. Local receipt time would therefore
be required, but that does not prove exchange-time BBO synchrony.

**Rights and security.** A recommendation to store historical data locally is technical
guidance, not a license grant. Public material did not resolve raw retention duration,
derived datasets, required deletion, audit, attribution, or redistribution. Release 2.4
(22 September 2025) says access tokens last 24 hours and a one-year key/secret can generate
daily tokens; order APIs require a static IP. This does not create a hard read-only token.

**Unresolved blocker:** written confirmation of a data-only credential and the complete
private-research/retention schedule.

Official evidence: [Option Chain, DhanHQ v2](https://dhanhq.co/docs/v2/option-chain/),
[Live Market Feed, DhanHQ v2](https://dhanhq.co/docs/v2/live-market-feed/),
[Historical Data, DhanHQ v2](https://dhanhq.co/docs/v2/historical-data/),
[Instrument List, DhanHQ v2](https://dhanhq.co/docs/v2/instruments/),
[DhanHQ releases](https://dhanhq.co/docs/v2/releases/), and
[Dhan API access pricing](https://dhan.co/support/platforms/dhanhq-api/how-to-access-dhan-api/).

### 4.2 Upstox Analytics Token

**Product and boundary.** Upstox's Analytics Token is valid for one year, limited to one
per account, revocable, free, GET-only, and expressly cannot place, modify, or cancel
orders. It covers market quote, historical data, option chain, market information and
WebSocket categories without static-IP configuration. This is the strongest documented
read-only boundary among the broker APIs reviewed.

**Coverage and fields.** The V3 market feed offers LTPC, option-chain and full modes. Full
mode includes five-depth data, metadata and option greeks. The payload documents current
timestamp, last-trade time, bid/offer depth, volume and OI. The REST option chain includes
bid/ask prices and quantities, volume, OI/previous OI, greeks and stable instrument keys.
The daily instrument master includes underlying, strike, right, expiry, lot size and tick
size, and warns that exchange tokens may be reused.

**History and limits.** V3 historical candles document minute/hour records from January
2022 and daily records from January 2000; fine intervals are request-window limited.
Expired-instrument endpoints are separately documented and some require Upstox Plus;
public expired-option discovery is bounded. Historical BBO is not documented. Standard
non-order APIs are limited to 50/second, 500/minute and 2,000/30 minutes. Normal WebSocket
limits include two connections and mode-specific subscription caps; the full-mode cap is
2,000 instruments, with lower combined-mode limits.

**Rights and security.** The token page tells users to store the token securely and offers
revocation. Public materials did not establish private raw quote retention duration,
derived-data rights, deletion/return obligations, attribution, audit, or whether paper
research is an authorized use.

**Unresolved blocker:** written licensing/retention terms plus exact Plus cost and expired
option scope, if those endpoints are needed.

Official evidence: [Analytics Token](https://upstox.com/developer/api-documentation/analytics-token/),
[Market Data Feed V3](https://upstox.com/developer/api-documentation/v3/get-market-data-feed/),
[Put/Call Option Chain](https://upstox.com/developer/api-documentation/get-pc-option-chain/),
[Instruments](https://upstox.com/developer/api-documentation/instruments/),
[Historical Candle Data V3](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/),
[Expired Instruments](https://upstox.com/developer/api-documentation/expired-instruments/),
and [Rate Limits](https://upstox.com/developer/api-documentation/rate-limiting/).

### 4.3 TrueData Market Data API

**Product and boundary.** TrueData describes itself as an authorized NSE/BSE/MCX market
data vendor and states that it does not provide execution or broker services. API access
requires a separate application and compliance approval; consumer Velocity plan prices
do not include API access.

**Coverage and fields.** Official product material describes NSE cash, indices and F&O;
WebSocket and REST access; current L1 bid/ask, quantities, cumulative volume, OI, option
chain and greeks. It advertises a 99.99% uptime SLA and less than 1 ms server-side
processing, but no service-level agreement text was publicly available for verification.
The detailed contract metadata schema, BBO timestamp semantics, request/subscription
limits, historical expired-option completeness and corporate-action adjustment policy
remain unknown.

**Rights conflict.** TrueData states that internal analysis is allowed and redistribution
requires written permission. The same official page says it does **not** provide data for
gaming, virtual trading or simulation use without NSE/SEBI approval. Because this project
explicitly contemplates later forward-paper research, the standard published terms do not
support the intended use.

**Cost.** API cost and exchange fees are quote-based and therefore unknown. No contact was
made because provider contact was outside this authorization.

**Unresolved blocker:** prior written NSE/SEBI/provider acceptance of the exact controlled
paper-research use, full license/retention language, schema/limits and a binding quote.

Official evidence: [TrueData Market Data API](https://www.truedata.in/products/marketdataapi),
[TrueData compliance and usage description](https://www.truedata.in/market-data-apis), and
[TrueData pricing](https://www.truedata.in/price).

### 4.4 Global Datafeeds API

**Product and boundary.** Global Datafeeds identifies itself as an authorized real-time
vendor and offers market-data-only keys through REST, WebSocket, .NET and COM APIs. Its
public API pricing is tailored by requirements rather than published as a fixed amount.

**Coverage and fields.** It documents NSE stocks, indices and NFO; one-second L1 best
bid/ask; entire option chain; greeks; tick/minute/EOD history; and exchange snapshots.
Published historical availability is narrow for the required option research: tick data
one week, intraday data three to six months depending on interval, and contract-wise option
daily/weekly/monthly data one month. One API key permits one active streaming session.

**Rights conflict.** The official API page states that NSE/SEBI rules prevent provision
for virtual-trading or simulation applications. That conflicts with the project's later
paper-research purpose unless separately approved. Public terms did not resolve raw
retention, derived data, deletion, audit or attribution.

**Unresolved blocker:** intended-use approval, a full license/retention schedule, a quote,
and evidence that required point-in-time history can be built and retained lawfully.

Official evidence: [API overview and restrictions](https://globaldatafeeds.in/apis/),
[API pricing and limits](https://globaldatafeeds.in/global-datafeeds-apis/global-datafeeds-apis/pricing-sales/api-pricing/),
and [published data availability](https://globaldatafeeds.in/global-datafeeds-apis/global-datafeeds-apis/introduction/type-of-data-available/).

### 4.5 Direct NSE/NSE Data products

**Product and authority.** NSE offers direct real-time Level 1/2/3 and tick-by-tick feeds,
plus paid EOD and historical order/trade products for Capital Market and F&O. This is the
authoritative source and has no broker-order capability.

**Delivery and cost.** Depending on product, delivery requires a dedicated line/authorized
vendor or SFTP/online access. The official tariff page says current market-data pricing is
effective 1 April 2026 and was updated 22 July 2026. Total cost cannot be computed until
the exact depth, non-display use, connectivity, historical products, taxes and agreement
are fixed.

**Rights.** NSE's Data Sharing & Usage Policy is explicit that the Relevant Agreement must
define intended use, receipt, transmission, handling and dissemination. Data remains NSE's
property; redistribution is not allowed except as agreed. Research access is case-specific.
Therefore public policy establishes that rights are contract-controlled, but does not
grant this project permission or a retention duration.

**Unresolved blocker:** identify an exact data product and obtain the Relevant Agreement,
permitted-use/retention schedule and complete quote. Doing so requires separate approval
before contacting or binding the project.

Official evidence: [Real-time data subscription](https://www.nseindia.com/static/market-data/real-time-data-subscription),
[EOD/historical data](https://www.nseindia.com/static/market-data/eod-historical-data-subscription),
[Products Tariff](https://www.nseindia.com/static/market-data/products-tariff), and
[NSE Data Sharing & Usage Policy](https://www.nseindia.com/static/market-data/nse-data-policy).

### 4.6 Zerodha Kite Connect viability benchmark

**Product and boundary.** Kite Connect is a trading API that expressly supports real-time
orders, portfolio access and market data using an API key/access token. It therefore fails
the mandatory condition that the credential be technically incapable of trading.

**Data capabilities.** The paid Connect tier is ₹500/month per app and includes WebSocket
market data and historical candles. WebSocket full mode carries last trade time, exchange
timestamp, volume, OI and five bid/offer levels; one key permits three connections and up
to 3,000 instruments per connection. Full REST quotes cover up to 500 instruments. The
instrument master contains strike, expiry, lot and tick size.

**History limitation.** Historical candles include OHLCV/OI, but the instrument master
only provides live derivative contracts. The docs explicitly say expired option tokens
cannot be retrieved unless the user cached them, while continuous history applies to
futures. This is not a reliable source for complete expired-option research.

**Result:** removed from further eligibility because one evidenced mandatory condition
fails; no account/token action is authorized.

Official evidence: [Kite Connect introduction](https://kite.trade/docs/connect/v3/),
[WebSocket streaming](https://kite.trade/docs/connect/v3/websocket/),
[Market quotes and instruments](https://kite.trade/docs/connect/v3/market-quotes/),
[Historical candles](https://kite.trade/docs/connect/v3/historical/), and
[official pricing](https://support.zerodha.com/category/trading-and-markets/general-kite/kite-api/articles/what-are-the-charges-for-kite-apis).

## 5. Cross-provider gaps that remain blocked

| Gap | Why it matters | What would close it |
|---|---|---|
| BBO timestamp semantics | Last-trade time is not the time at which a bid/ask was valid. Without quote-time evidence, executable spread/slippage claims are unsafe. | Provider field specification plus sample schema or contractual clarification identifying exchange/provider quote timestamps. |
| Raw retention duration | Technical ability to save data is not legal permission to retain it. | Written license clause specifying raw and derived retention, deletion/return, audit and backup treatment. |
| Paper-research permission | Several vendors explicitly restrict virtual/simulation use. | Written provider/exchange approval for the exact private, non-alerting, non-trading research design. |
| Corporate actions | Unadjusted equity history can create false returns and joins. | Documented adjustment flags/methodology and correction history. |
| Expired-option completeness | Survivorship and strike-window truncation can bias findings. | Contract-level archive scope, delisted/adjusted handling, and unavailable-series disclosure. |
| Reliability obligations | Marketing uptime is not necessarily an enforceable SLA. | Applicable SLA, incident/status history, support window and deprecation notice policy. |
| Full cost | Free/retail headline prices omit add-ons, exchange fees, taxes, lines and static IP. | Binding quote for the exact inactive scope; no purchase or commitment. |

## 6. Security baseline for any future proposal

This section is a research requirement, not an active configuration.

- Use only a credential proven incapable of order, funds, portfolio mutation or account
  mutation.
- Store it in an OS-protected secret store, never in source, logs, reports or test fixtures.
- Enforce an outbound allowlist containing only specifically approved GET/WebSocket market
  endpoints; deny order/account hosts and methods.
- Run under a non-administrator local service identity with write access only to the
  approved data root.
- Record token issue/expiry/revocation metadata without recording the token value.
- Define immediate revocation and local quarantine steps for suspected disclosure.
- Hash immutable raw payloads and separately record provider timestamp, exchange/last-trade
  timestamp, and local monotonic/UTC receipt time.

## 7. What this research proves—and does not prove

It proves what the cited public official documents said on the research date and identifies
where a mandatory condition is met, contradicted or undocumented. It does **not** prove
actual data quality, latency, completeness, timestamp behavior, uptime, executable fills,
slippage, liquidity, cost, P&L, suitability, or legal permission beyond the text cited.

## 8. Exact approval required next

No token, credential, endpoint, storage manifest or connectivity test should be approved
yet. The smallest next approval is permission to request **non-binding written
clarification** from a short list of providers, using a fixed questionnaire, without
opening an account, accepting terms, buying a plan, generating a token, or disclosing
strategy logic.

Suggested approval text:

> Aditya Lakhotia authorizes written, non-binding clarification requests to Upstox,
> DhanHQ, and NSE/NSE Data only, limited to read-only credential scope, BBO timestamp
> semantics, private paper-research permission, raw and derived retention, deletion,
> audit, attribution, operational limits, and an itemized quote. This does not authorize
> account opening, contract acceptance, purchase, token generation, connectivity, data
> collection, a provider selection, a manifest, research hypotheses, candidates, P&L,
> alerts, AI, execution, or trading.

