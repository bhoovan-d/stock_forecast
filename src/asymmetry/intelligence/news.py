"""News ingestion and headline-to-ticker resolution.

Feed choice is constrained by what is actually reachable from here: Moneycontrol's RSS
returns 403, and NSE/BSE corporate-announcement APIs are blocked, so the official filings
stream is not available. Economic Times, Business Standard and Livemint all serve clean
RSS and are used instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import feedparser
from loguru import logger

from ..data.cache import BROWSER_UA

FEEDS: dict[str, str] = {
    "ET Markets": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "ET Stocks": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    "Livemint Markets": "https://www.livemint.com/rss/markets",
    "Livemint Companies": "https://www.livemint.com/rss/companies",
}

# Only corporate-form suffixes are stripped. An earlier, far broader stopword list removed
# meaningful words too — it reduced "Indian Energy Exchange" to "Exchange" and "Federal
# Bank" to "Federal", which then matched "Securities and Exchange Board" and "Federal
# Reserve". The distinctive part of a company name is usually its first two words.
_CORPORATE_SUFFIXES = {
    "ltd", "ltd.", "limited", "corp", "corp.", "corporation", "company", "co", "co.",
    "plc", "inc", "inc.", "pvt", "private", "and", "of", "the", "&",
}


@dataclass
class NewsItem:
    headline: str
    summary: str
    url: str
    source: str
    published: datetime


def fetch_news(max_age_hours: int = 48) -> list[NewsItem]:
    """Pull recent items from every configured feed."""
    from ..data.cache import PacedClient

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[NewsItem] = []
    seen: set[str] = set()
    # Fetch the bytes ourselves rather than letting feedparser do it: ET and Livemint
    # return empty when feedparser fetches them, but serve fine to a normal browser
    # client with retries.
    client = PacedClient(min_interval_sec=0.5, max_retries=3)

    for source, url in FEEDS.items():
        try:
            response = client.get(url)
            if response is None:
                logger.warning(f"[news] {source} unreachable")
                continue
            parsed = feedparser.parse(response.content)
        except Exception as exc:  # noqa: BLE001 — a dead feed must not stop ingestion
            logger.warning(f"[news] {source} failed: {exc}")
            continue

        if not parsed.entries:
            logger.warning(f"[news] {source} returned no entries")
            continue

        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            if not title or title.lower() in seen:
                continue

            published = datetime.now(timezone.utc)
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if published < cutoff:
                continue

            seen.add(title.lower())
            items.append(
                NewsItem(
                    headline=title,
                    summary=re.sub(r"<[^>]+>", "", entry.get("summary", ""))[:500],
                    url=entry.get("link", ""),
                    source=source,
                    published=published,
                )
            )

    logger.info(f"[news] {len(items)} items from {len(FEEDS)} feeds")
    return items


def _name_tokens(company: str) -> list[str]:
    """Company name reduced to its identifying words, corporate suffixes removed."""
    cleaned = re.sub(r"[^\w\s]", " ", company.lower())
    return [token for token in cleaned.split() if token and token not in _CORPORATE_SUFFIXES]


@dataclass
class AliasMap:
    """Three alias classes, because each needs a different matching rule.

    * ``tickers`` — many NSE tickers are ordinary English words (ACE, ONE, IDEA, BEST), so
      these match case-**sensitively**: "ACE" is the company, "ace" is prose.
    * ``phrases`` — two-or-more-token company names are unambiguous; match case-insensitively.
    * ``proper`` — single-token names left after stopword stripping are often common words
      ("Persistent Systems" -> "persistent", "Federal Bank" -> "federal"). Matching those
      case-insensitively tagged "persistent inflation" and "US Fed", so they match only in
      their capitalised, proper-noun form.
    """

    tickers: dict[str, str]
    phrases: dict[str, str]
    proper: dict[str, str]


def build_alias_map(universe: dict) -> AliasMap:
    tickers: dict[str, str] = {}
    phrases: dict[str, str] = {}
    proper: dict[str, str] = {}

    for symbol, stock in universe.items():
        tickers[symbol] = symbol
        tokens = _name_tokens(stock.company)
        if not tokens:
            continue
        if len(tokens) >= 2:
            # "Bharat Electronics" from "Bharat Electronics Ltd."
            phrases.setdefault(" ".join(tokens[:2]), symbol)
        elif len(tokens[0]) > 4:
            proper.setdefault(tokens[0].capitalize(), symbol)
    return AliasMap(tickers=tickers, phrases=phrases, proper=proper)


def resolve_tickers(item: NewsItem, aliases: AliasMap) -> set[str]:
    """Which universe tickers this item is about.

    An item naming many companies is usually a market wrap rather than a company-specific
    catalyst; the caller drops those.
    """
    raw = f"{item.headline} {item.summary}"
    lowered = raw.lower()
    hits: set[str] = set()

    for alias, symbol in aliases.tickers.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            hits.add(symbol)
    for alias, symbol in aliases.phrases.items():
        if re.search(rf"\b{re.escape(alias)}\b", lowered):
            hits.add(symbol)
    for alias, symbol in aliases.proper.items():
        if re.search(rf"\b{re.escape(alias)}\b", raw):
            hits.add(symbol)
    return hits
