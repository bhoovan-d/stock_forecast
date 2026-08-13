"""BSE corporate filings — the hard-catalyst source.

RSS alone gave catalyst coverage of ~4% of the universe (20 of 473 stocks), which left the
highest-weighted factor nearly constant and therefore inert. Filings fix that: order wins,
results, board decisions, capital raises and stake changes are disclosed here first, and
they are exactly the events that move forward earnings.

Two things had to be discovered by probing, because neither is documented:

1. ``www.nseindia.com`` is blocked from this environment, but ``api.bseindia.com`` is not.
2. The announcements endpoint returns an **empty result for the "all categories" wildcard**
   (``strCat=-1``) — you must ask for each category by name. Silently getting ``{}`` from
   the obvious query is why this looked unavailable at first.

Ticker resolution is exact rather than name-based: BSE's scrip master carries ISIN, and the
NSE constituent list carries ISIN, so filings join to tickers on ISIN with no fuzzy matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from loguru import logger

from ..data.cache import PacedClient, cache

_API = "https://api.bseindia.com/BseIndiaAPI/api"

# Every category must be requested by name; the wildcard returns nothing.
CATEGORIES = [
    "Result",
    "Board Meeting",
    "Company Update",
    "Corp. Action",
    "Insider Trading / SAST",
    "AGM/EGM",
    "New Listing",
    "Others",
]

# Categories whose filings are most likely to change forward numbers. Used to prioritise
# which filings are worth spending an LLM call on when the daily budget is limited.
HIGH_VALUE = {"Result", "Board Meeting", "Company Update", "Corp. Action"}

# ── Subcategory routing ───────────────────────────────────────────────────────
# Measured over ~500 live filings: the overwhelming majority are procedural. "Record Date"
# (51), "Newspaper Publication" (41) and "Book Closure" (29) change nothing about forward
# earnings, and sending them to an LLM only burns calls to be told they score zero.
#
# So each subcategory is routed:
#   SKIP  — procedural; never scored.
#   EVENT — the fact it happened is the signal, and the magnitude comes from the market's
#           own reaction (price/volume/delivery), not from the filing text. Results are the
#           main case: the subject line says results were approved but the numbers live in
#           a multi-megabyte PDF attachment, so no text scorer can read the surprise.
#   LLM   — genuinely informative prose worth interpreting.

SKIP_SUBCATS = {
    "Record Date", "Book Closure", "Newspaper Publication", "Closure of Trading Window",
    "Investor Presentation", "Analyst / Investor Meet", "Change in Registered Office Address",
    "Monitoring Agency Report", "Reg. 32 (1), (3) - Statement of Deviation & Variation",
    "Resignation of Statutory Auditors", "General", "Earnings Call Transcript",
}

EVENT_SUBCATS = {
    # subcategory -> (catalyst type, durability hint)
    "Financial Results": ("earnings_surprise", 2),
    "Dividend": ("guidance", 1),
    "Sub-division / Stock Split": ("guidance", 0),
}

# Substantial-acquisition disclosures: someone crossed a shareholding threshold. This is
# the promoter/institutional activity signal, sourced from a primary disclosure rather than
# the slow 13F-style filings the brief rules out.
SAST_SUBCATS = {
    "Disclosures under Reg. 29(2) of SEBI (SAST) Regulations, 2011",
    "Disclosures under Reg. 29(1) of SEBI (SAST) Regulations, 2011",
    "Disclosures under Reg. 31(1) and 31(2) of SEBI (SAST) Regulations, 2011",
    "Disclosures under Reg. 10(7) of SEBI (SAST) Regulations, 2011",
}


def route(subcategory: str, category: str) -> str:
    """How to handle a filing: 'skip', 'event', 'sast' or 'llm'."""
    if subcategory in SKIP_SUBCATS:
        return "skip"
    if subcategory in EVENT_SUBCATS:
        return "event"
    if subcategory in SAST_SUBCATS:
        return "sast"
    # "Outcome of Board Meeting" is generic on its own, but its text sometimes carries the
    # substance (orders, fundraising, approvals), so it is worth reading.
    return "llm"

_client = PacedClient(
    min_interval_sec=0.5,
    max_retries=3,
    headers={
        "Referer": "https://www.bseindia.com/corporates/ann.html",
        "Origin": "https://www.bseindia.com",
        "Accept": "application/json, text/plain, */*",
    },
)


@dataclass
class Filing:
    symbol: str
    headline: str
    category: str
    published: datetime
    critical: bool
    url: str = ""
    subcategory: str = ""
    body: str = ""

    @property
    def route(self) -> str:
        return route(self.subcategory, self.category)

    @property
    def summary(self) -> str:
        """Context handed to the LLM alongside the headline.

        The ``MORE`` body is included when present — it is populated on only a minority of
        filings, but where it exists it is the only prose that says what actually happened.
        """
        flag = " Flagged by the exchange as critical." if self.critical else ""
        base = f"BSE filing. Category: {self.category} / {self.subcategory}.{flag}"
        return f"{base} {self.body}".strip() if self.body else base


def _scrip_to_isin() -> dict[str, str]:
    """BSE scrip code -> ISIN, from the exchange's own equity master."""
    cached = cache.get_json("bse:scripmaster", ttl_sec=7 * 86400)
    if cached is None:
        resp = _client.get(
            f"{_API}/ListofScripData/w",
            params={
                "Group": "", "Scripcode": "", "industry": "",
                "segment": "Equity", "status": "Active",
            },
        )
        if resp is None:
            logger.warning("[filings] BSE scrip master unavailable")
            return {}
        try:
            payload = resp.json()
        except ValueError:
            return {}
        rows = payload if isinstance(payload, list) else payload.get("Table", [])
        cached = {
            str(r["SCRIP_CD"]).strip(): str(r.get("ISIN_NUMBER", "")).strip()
            for r in rows
            if r.get("SCRIP_CD") and r.get("ISIN_NUMBER")
        }
        cache.set_json("bse:scripmaster", cached)
    return cached


def _clean_headline(subject: str, company: str = "") -> str:
    """BSE subjects arrive as "Company - 500068 - Actual subject". Keep the substance.

    The company name and scrip code are already known from the join, so leaving them in
    just wastes prompt tokens and biases the model toward the name.
    """
    parts = [p.strip() for p in subject.split(" - ")]
    if len(parts) >= 3 and re.fullmatch(r"\d{6}", parts[1]):
        text = " - ".join(parts[2:])
    else:
        text = subject.strip()
    return f"{company}: {text}" if company else text


def fetch_filings(
    as_of: date | None = None,
    *,
    lookback_days: int = 2,
    categories: list[str] | None = None,
    pages: int = 2,
) -> list[Filing]:
    """Recent filings mapped to NSE tickers.

    Only universe members survive the ISIN join, so unrelated small caps are dropped for
    free rather than needing a filter.
    """
    from ..data import universe as universe_mod

    end = as_of or date.today()
    start = end - timedelta(days=lookback_days)

    universe = universe_mod.load_universe()
    isin_to_symbol = {stock.isin: symbol for symbol, stock in universe.items() if stock.isin}
    scrip_to_isin = _scrip_to_isin()
    if not scrip_to_isin:
        return []

    filings: list[Filing] = []
    seen: set[str] = set()

    for category in categories or CATEGORIES:
        for page in range(1, pages + 1):
            resp = _client.get(
                f"{_API}/AnnSubCategoryGetData/w",
                params={
                    "pageno": page,
                    "strCat": category,
                    "strPrevDate": f"{start:%Y%m%d}",
                    "strToDate": f"{end:%Y%m%d}",
                    "strScrip": "",
                    "strSearch": "P",
                    "strType": "C",
                    "subcategory": "-1",
                },
            )
            if resp is None:
                break
            try:
                rows = resp.json().get("Table", [])
            except ValueError:
                break
            if not rows:
                break

            for row in rows:
                news_id = str(row.get("NEWSID", ""))
                if news_id in seen:
                    continue
                isin = scrip_to_isin.get(str(row.get("SCRIP_CD", "")).strip())
                symbol = isin_to_symbol.get(isin) if isin else None
                if symbol is None:
                    continue  # not in our universe

                seen.add(news_id)
                raw = str(row.get("NEWSSUB", ""))
                try:
                    published = datetime.fromisoformat(
                        str(row.get("NEWS_DT", "")).replace("Z", "")
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    published = datetime.now(timezone.utc)

                filings.append(
                    Filing(
                        symbol=symbol,
                        headline=_clean_headline(raw, universe[symbol].company),
                        category=category,
                        published=published,
                        critical=str(row.get("CRITICALNEWS", "0")).strip() == "1",
                        subcategory=(row.get("SUBCATNAME") or "").strip(),
                        body=(row.get("MORE") or "").strip()[:600],
                        url=(
                            f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/"
                            f"{row.get('ATTACHMENTNAME', '')}"
                            if row.get("ATTACHMENTNAME")
                            else ""
                        ),
                    )
                )

    # High-value categories and exchange-flagged criticals first, so a capped LLM budget is
    # spent on the filings most likely to matter.
    filings.sort(
        key=lambda f: (f.category not in HIGH_VALUE, not f.critical, -f.published.timestamp())
    )
    logger.info(
        f"[filings] {len(filings)} filings mapped to {len({f.symbol for f in filings})} "
        f"universe tickers ({start} → {end})"
    )
    return filings
