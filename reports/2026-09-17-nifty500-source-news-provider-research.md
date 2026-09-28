# Independent NIFTY500 Source News Provider Research

**Research date:** 17 September 2026  
**Approval authority:** Aditya Lakhotia  
**Status:** Completed official-document desk research; no source stack selected  
**System boundary:** This report concerns the NIFTY500 Source News system only. It does
not use, feed, gate, rank, score or modify FnO Momentum data, candidates, features, labels,
models, outcomes, alerts or behavior.

No collection, feed trial, vendor contact, account, token, source manifest, candidate,
label, AI process, alert, paper research, execution or trading action was performed.

## 1. Decision state

The public evidence is sufficient to identify viable source classes but not to approve a
stack. Exchange-origin corporate disclosures are the strongest primary records. NSE offers
an official real-time corporate-data feed and an end-of-day SFTP product with public list
prices, while BSE offers corporate information products through its data-feed portal and
tariff. Global Datafeeds offers an India-focused normalized corporate-announcement API.
LSEG and Bloomberg offer machine-readable news with extensive metadata and history.

However, no examined public source resolves all of: exact NIFTY500 coverage, attachment
and correction behavior, automated private storage, retention duration, derived labels,
deletion/audit duties, and total cost. No primary-source stack or fallback is selected.

## 2. Evidence labels

- **Supported** — expressly stated in a current official document.
- **Unsupported** — expressly contradicted or absent from the documented product.
- **Unknown** — no explicit current official evidence found; unknown is not permission.

Page sources without a stated revision are recorded as **page version not stated;
accessed 17 September 2026**.

## 3. Neutral source comparison

| Source/product | Coverage and origin | Timing / correction evidence | Delivery and history | Rights and retention | Cost evidence | Current result |
|---|---|---|---|---|---|---|
| NSE public corporate-filings pages and RSS | **Supported:** issuer-uploaded corporate announcements, filings and attachments; company/symbol and broadcast date are visible. Exact NIFTY500 completeness and identifier mapping are **unknown**. | **Supported:** website says information is displayed immediately on receipt and carries broadcast date. Revision/correction chain and immutable prior versions are **unknown**. | Public web, CSV views and RSS are documented. Stable API, rate limits, full archive and attachment backfill are **unknown**. | Website visibility does not itself grant automated bulk capture or indefinite storage. NSE policy says corporate data is governed as Market Data. | Public viewing is free; lawful automated research cost is **unknown**. | **Blocked for automation**; useful as a human verification source only until rights/delivery are explicit. |
| NSE real-time Corporate Data feed | **Supported:** company fundamentals, corporate announcements, results and shareholding data from NSE filings. | **Supported:** online corporate announcements; technical specification exists. Correction/revision semantics need specification review. | Dedicated leased line from an NSE POP; official technical spec. Historical/backfill and attachments need confirmation. | **Contract-controlled:** Relevant Agreement defines use, handling and dissemination; retention/derived labels are **unknown** until reviewed. | **Supported headline:** ₹1,060,000/year domestic, excluding line, taxes and other costs. Page updated 11 June 2026. | **Viable but blocked** on agreement, retention, full cost and correction details. |
| NSE EOD Corporate Announcement | Same official-origin disclosure class, delivered after market hours. Exact NIFTY500 mapping/attachment completeness are **unknown**. | Available after 8:00 PM IST daily; not suitable for proving real-time receipt. Correction history is **unknown**. | SFTP, daily. Official technical spec; archive/backfill scope is **unknown**. | Contract-controlled; retention, derived labels, quotation and deletion are **unknown**. | **Supported headline:** ₹500,000/year domestic, taxes/other costs unknown. | **Viable EOD fallback candidate**, not selected. |
| BSE Self Data Feed / Corporate Information Products | **Supported generally:** financials, disclosures, announcements and actions. BSE is an original exchange venue; exact overlap/completeness after single-filing changes is **unknown**. | Public tariff and portal identify announcement products. Publication/correction timestamps and revision chain require product manuals/agreement. | Feed portal advertises API and manuals; exact corporate API schema, history, limits and attachments remain **unknown** without enrollment materials. | Tariff distinguishes internal/datafeed/redistribution uses, but exact private-storage and derived-label rights require contract. | February 2025 domestic tariff exists, but the exact package/current payable total is **unknown**. | **Viable but blocked** on current product specification, agreement and quote. |
| SEBI public releases/orders/circulars | **Supported:** regulator-origin documents, useful for market-wide and enforcement events. Not a complete issuer-announcement source. | Publication date is visible; correction/version mechanics vary by document and are **unknown**. | Public website; no general official ingestion API or complete NIFTY500 mapping established. | Automated access, retention and derived-label rights not established by the materials reviewed. | Public viewing free; authorized automated use cost **unknown**. | **Supplementary human-verification source only**; not a primary automated feed. |
| Company investor-relations sites | **Supported in principle:** issuer-origin releases and presentations. Coverage, schema and quality vary across 500 companies. | Issuer timestamps may be present; corrections and removals are inconsistent and not centrally governed. | Heterogeneous web/RSS/email formats; no uniform API, limits, archive or identifiers. | Must be established site by site; 500 separate terms create high legal and operational risk. | Public viewing often free; compliant automation cost is **unknown**. | **Unsuitable as an initial uniform collector**; possible later verification layer only. |
| Global Datafeeds Fundamental/Corporate Data API | **Supported:** real-time and historical Indian corporate announcements, actions, results, governance and related fields; provider says data is sourced directly from exchanges. Exact NIFTY500 and attachment coverage are **unknown**. | Corporate announcements update continuously. Original exchange timestamp, provider receipt time, revisions and corrections need schema evidence. | REST APIs with public endpoint descriptions/code samples. Rate limits, archive start, attachment retrieval and revision history are **unknown** publicly. | Automated API access is inherent, but raw retention, derived labels, quotation, deletion, attribution and audit are **unknown**. | Quote-only; taxes, limits, archive and renewal costs **unknown**. | **Viable India-focused normalization candidate**, blocked on rights/schema/quote. |
| TrueData Corporate Announcements / News APIs | **Supported generally:** real-time exchange announcements, corporate filings and a tagged market-news API. Exact NIFTY500, attachments and correction chain are **unknown**. | Described as real-time; source/original/update timestamps and point-in-time revision behavior are **unknown**. | API access advertised; exact schema, limits, history and attachment support are not publicly established. | Internal-use language exists for market data, but Source News-specific retention/derived-label terms are **unknown**. | Separately quoted after compliance review. | **Viable candidate**, blocked on official detailed docs, rights and quote. |
| LSEG Machine Readable News (MRN) | **Supported:** Reuters, third-party and exchange news; normalized textual news, metadata and optional analytics. Exact NIFTY500 issuer/filing completeness is **unknown**. | **Supported:** low-latency real-time delivery and point-in-time news; relevance/significance/confidence and historical archives. Correction-chain fields need entitlement/schema confirmation. | Real-Time Platform/API/WebSocket and SFTP; textual archive to 1996, analytics history to 2003. Attachments and original filing PDFs are **unknown**. | Enterprise license controls storage, NLP/LLM use, derived labels, quotation and redistribution; public pages do not grant them. | Quote-only. | **Viable secondary news/enrichment candidate**, not a proven exchange-filing system of record. |
| Bloomberg Event-Driven Feeds / Data License | **Supported:** structured textual news, company/topic/person metadata, analytics, corporate events/actions and third-party sources. Exact NIFTY500 filing completeness is **unknown**. | Real-time machine-readable event feeds are supported; detailed correction/revision semantics require licensed schema. | Enterprise feed/API/SFTP/cloud options; Data License bulk says 20+ years for datasets generally. Exact selected-news history and attachments are **unknown**. | Enterprise license controls internal use, storage and redistribution; public pages do not grant project rights. | Quote-only. | **Viable secondary news/enrichment candidate**, blocked on coverage, license and cost. |
| FactSet data/news products | Public official brochures establish enterprise real-time/historical data-feed capability, but the reviewed public evidence did not establish the exact Indian issuer-news product, NIFTY500 completeness, attachments or corrections. | **Unknown** for the proposed source. | Enterprise feeds/APIs exist generally; exact source delivery/history unknown. | Contract-controlled and publicly unresolved. | Quote-only/unknown. | **Insufficient public evidence; hold**, not rejected. |

## 4. Primary-source findings

### 4.1 NSE disclosures

The NSE public corporate-filings page exposes issuer announcements and attachments and says
they are displayed immediately after receipt. It also warns that the issuer is responsible
for accuracy and that NSE does not verify adequacy, accuracy or veracity. That makes an
announcement an authoritative record of what the issuer filed—not proof that the filing's
content is true.

NSE's paid Corporate Data product is the clearest official machine-delivery option found.
The real-time product costs ₹1,060,000 per year for domestic clients and requires a customer-
owned leased line from an NSE point of presence. The EOD SFTP product costs ₹500,000 per
year and is available after 8:00 PM IST. Those are headline subscription fees; taxes,
connectivity, installation, archive/backfill and any non-display charges are not resolved.

NSE's Data Sharing & Usage Policy is decisive on rights: a subscriber must execute a
Relevant Agreement describing intended use and how data is received, transmitted, handled
and disseminated; redistribution is not allowed unless agreed. The public policy does not
itself grant indefinite raw retention, attachment storage or derived-label rights.

Official evidence: [NSE Corporate Filings](https://www.nseindia.com/companies-listing/corporate-filings-announcements),
[Paid Corporate Data](https://www.nseindia.com/static/market-data/corporate-data-subscription),
[Corporate Data technical specification](https://nsearchives.nseindia.com/content/press/CorporateData.pdf),
[NSE RSS](https://www.nseindia.com/static/rss-feed), and
[NSE Data Sharing & Usage Policy](https://www.nseindia.com/static/market-data/nse-data-policy).

### 4.2 BSE disclosures

BSE's official Self Data Feed portal advertises company financials, disclosures, corporate
announcements and actions, with API/feed materials and trial access. The February 2025
domestic tariff distinguishes announcement and corporate-data products, including
redistribution licenses. The public evidence reviewed did not unambiguously establish the
current exact internal-use package, API schema, revision/correction model, attachment
history, limits, NIFTY500 coverage or payable 2026 total.

BSE remains important because dual-listed issuers and exchange-specific corrections may
not be safely inferred from NSE alone. At the same time, NSE circulars document an expanding
single-filing API integration between exchanges; that reduces duplicate filing burden but
does not prove both dissemination streams are identical in time, metadata or corrections.

Official evidence: [BSE Self Data Feed](https://marketdata.bseindia.com/),
[BSE Information Products Domestic Tariff, February 2025](https://www.bseindia.com/downloads1/Information_Products_Pricing_Sheet.pdf),
and [NSE single-filing circular, 28 February 2025](https://nsearchives.nseindia.com/web/sites/default/files/inline-files/CML_API_Integrated%20filing%20for%20governance.pdf).

### 4.3 India-focused licensed aggregators

Global Datafeeds publishes the most detailed India-focused corporate API evidence found.
It lists continuous corporate announcements plus actions, financial results, voting,
governance and other data and says the data is sourced directly from exchanges. This could
reduce schema and issuer-mapping work, but public documentation did not settle original
attachment preservation, all publication/correction timestamps, immutable revision IDs,
historical start, limits or retention/derived-label rights.

TrueData advertises a real-time corporate-announcement API and a separate tagged news API,
but the current public page did not provide enough source-specific technical and licensing
detail for an evidence-complete comparison.

Official evidence: [Global Datafeeds Corporate API overview](https://globaldatafeeds.in/fundamental-data-apis/),
[Global Datafeeds API list](https://docs.globaldatafeeds.in/list-of-apis-923685m0),
[Global Datafeeds documentation introduction](https://docs.globaldatafeeds.in/), and
[TrueData API suite](https://www.truedata.in/products/marketdataapi).

### 4.4 Licensed global news feeds

LSEG MRN is explicitly designed for algorithmic/NLP consumption and offers normalized
Reuters, third-party and exchange news through real-time APIs/WebSockets and SFTP. Textual
archives reach 1996 and analytics history reaches 2003. Bloomberg Event-Driven Feeds offer
structured textual news, metadata, analytics and corporate event/action datasets; Data
License supports REST, SFTP and cloud delivery, with 20+ years for bulk datasets generally.

These products are credible secondary news/enrichment candidates, but neither public page
proves complete NSE/BSE issuer-filing coverage, original attachment capture or the exact
license needed for indefinite local raw storage and derived labels. They must not silently
replace exchange-origin documents as the system of record.

Official evidence: [LSEG News catalogue](https://www.lseg.com/en/data-catalogue/news),
[LSEG Machine Readable News](https://www.lseg.com/en/data-analytics/financial-news-services/machine-readable-news),
[LSEG News Analytics](https://www.lseg.com/en/data-analytics/financial-data/financial-news-coverage/political-news-feeds-analysis/news-analytics),
[Bloomberg Event-Driven Feeds](https://professional.bloomberg.com/products/data/enterprise-catalog/event-driven-feeds/),
and [Bloomberg Data License](https://professional.bloomberg.com/products/data/data-license/).

## 5. Required source architecture properties (inactive research requirements)

These properties describe what any later source-manifest proposal must contain; they do
not authorize implementation.

| Property | Required evidence before approval |
|---|---|
| Stable identity | ISIN plus NSE/BSE symbol and effective-dated issuer mapping, including renames, mergers and delistings. |
| Three times | Original publisher time, correction/update time, and local UTC receipt time stored separately. |
| Original evidence | Raw payload plus original attachment bytes, MIME type, source URL/object ID and cryptographic hash. |
| Revision chain | Provider revision/cancel/correct identifier or a documented deterministic revision-linking rule. |
| Source role | Explicit `primary_exchange`, `regulator`, `issuer`, `licensed_news`, or `aggregator`; summaries never overwrite originals. |
| Rights record | Contract/version, permitted automation, retention period, derived-label permission, deletion, quotation, attribution and audit terms. |
| Reliability | Rate/concurrency limits, replay/backfill procedure, outage status, duplicate semantics and correction SLA. |
| Independence | A dedicated Source News data root, ledger, credentials and runtime; no FnO imports, tables, candidates, scores or outputs. |

## 6. Fallback/outage design question—not a proposal

A safe future design would need independent primary and fallback channels, but this report
does not select them. Before approval, evidence must answer:

1. Whether the fallback is legally allowed to retain the same payload and attachments.
2. How the system distinguishes late primary arrival from a true primary outage.
3. Whether timestamps are source publication times or aggregator receipt times.
4. How duplicate filings from NSE/BSE single-filing integration are linked without deleting
   venue-specific metadata.
5. How corrections and withdrawn documents are preserved without rewriting history.
6. Whether an outage permits delayed ingestion only; it must never create candidates,
   alerts or links to FnO Momentum.

## 7. What sources cannot prove

Corporate filings prove what an issuer submitted and when the venue/provider says it was
published; they do not prove the filing is accurate, complete or economically material.
News feeds prove delivery of a provider's story and metadata; they do not prove truth,
causality, market impact, tradability, or future returns. Provider sentiment/relevance
fields are vendor outputs, not approved project labels. No source in this report proves a
candidate rule, threshold, prediction, P&L, alert, holding period or trading decision.

## 8. Remaining blockers

- Exact NIFTY500 membership history and effective-date license.
- Complete NSE/BSE announcement, attachment and correction coverage.
- Rights for automated access, raw/attachment storage, retention duration, backups,
  derived labels, quotation, deletion, attribution and audit.
- Stable issuer identifiers and historical rename/delist mappings.
- Original, update and provider-receipt timestamp semantics.
- Historical backfill start, cancelled/replaced filings and replay behavior.
- Rate limits, support obligations, outage notification and duplicate rules.
- Binding total annual costs, taxes, line/setup fees, archives, renewal and cancellation.

## 9. Exact approval required next

No source manifest or collection should be approved yet. The smallest next step is a fixed,
non-binding clarification request to the exchange/data providers; it must remain separate
from FnO Momentum and must not disclose or seek strategy behavior.

Suggested approval text:

> Aditya Lakhotia authorizes written, non-binding clarification requests for the NIFTY500
> Source News system to NSE/NSE Data, BSE, and Global Datafeeds only. Questions are limited
> to exact issuer/attachment/correction coverage, timestamps, history/backfill, API/feed
> delivery, limits, private automated research, raw and derived retention, deletion,
> attribution, audit, outage support, and an itemized quote. This does not authorize an
> account, contract, purchase, token, trial, connectivity, collection, source selection,
> source manifest, candidates, labels, AI, alerts, paper research, execution, trading, or
> any connection to FnO Momentum.

