"""Earnings calendar — who just reported, and who is about to.

Both directions matter, in opposite ways:

* **Just reported** is a catalyst. The market's reaction over the following days is the
  surprise measure we cannot read from the filing text itself.
* **About to report** is a *risk*, not an opportunity. Holding a technical breakout through
  an earnings print converts a structured trade with a defined stop into a coin flip: the
  gap can open straight through the invalidation level, so the R:R the trade engine
  computed no longer holds. Trade plans carry a warning when a result is imminent.

Source: BSE board-meeting notices, which announce the date a company will consider results.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from loguru import logger

from .filings import _API, _client, _scrip_to_isin

# A result within this many sessions makes an entry hazardous.
EARNINGS_BLACKOUT_DAYS = 3

_DATE_PATTERNS = [
    re.compile(r"(\d{1,2})(?:st|nd|rd|th)?\s+(\w+),?\s+(\d{4})", re.I),
    re.compile(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})"),
]

_MONTHS = {
    m.lower(): i
    for i, m in enumerate(
        ["January", "February", "March", "April", "May", "June", "July",
         "August", "September", "October", "November", "December"], 1
    )
}


@dataclass
class EarningsEvent:
    symbol: str
    event_date: date
    reported: bool  # True = results are out, False = scheduled
    source_text: str = ""


def _parse_date(text: str) -> date | None:
    """Pull a date out of board-meeting prose like "held on 21st August, 2026"."""
    for pattern in _DATE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        try:
            a, b, c = match.groups()
            if b.lower() in _MONTHS:
                return date(int(c), _MONTHS[b.lower()], int(a))
            return date(int(c), int(b), int(a))
        except (ValueError, KeyError):
            continue
    return None


def fetch_earnings_events(
    as_of: date | None = None, *, lookback_days: int = 21
) -> list[EarningsEvent]:
    """Recent results filings and upcoming board meetings called to consider results."""
    from ..data import universe as universe_mod

    end = as_of or date.today()
    start = end - timedelta(days=lookback_days)

    universe = universe_mod.load_universe()
    isin_to_symbol = {s.isin: sym for sym, s in universe.items() if s.isin}
    scrip_to_isin = _scrip_to_isin()
    if not scrip_to_isin:
        return []

    events: list[EarningsEvent] = []
    seen: set[tuple[str, date]] = set()

    for category in ("Result", "Board Meeting"):
        for page in (1, 2, 3):
            resp = _client.get(
                f"{_API}/AnnSubCategoryGetData/w",
                params={
                    "pageno": page, "strCat": category,
                    "strPrevDate": f"{start:%Y%m%d}", "strToDate": f"{end:%Y%m%d}",
                    "strScrip": "", "strSearch": "P", "strType": "C", "subcategory": "-1",
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
                isin = scrip_to_isin.get(str(row.get("SCRIP_CD", "")).strip())
                symbol = isin_to_symbol.get(isin) if isin else None
                if symbol is None:
                    continue

                subject = f"{row.get('NEWSSUB', '')} {row.get('MORE', '') or ''}"
                try:
                    filed = datetime.fromisoformat(
                        str(row.get("NEWS_DT", "")).replace("Z", "")
                    ).date()
                except ValueError:
                    continue

                if category == "Result":
                    event = EarningsEvent(symbol, filed, reported=True, source_text=subject[:200])
                else:
                    # Only board meetings actually convened to consider results count.
                    if not re.search(r"financial result|unaudited|audited result", subject, re.I):
                        continue
                    scheduled = _parse_date(subject) or filed
                    # A parsed date far in the past is a misparse, not a meeting.
                    if scheduled < filed - timedelta(days=2):
                        scheduled = filed
                    event = EarningsEvent(
                        symbol, scheduled, reported=False, source_text=subject[:200]
                    )

                key = (event.symbol, event.event_date)
                if key in seen:
                    continue
                seen.add(key)
                events.append(event)

    logger.info(
        f"[earnings] {len(events)} events for {len({e.symbol for e in events})} tickers"
    )
    return events


def earnings_flags(
    as_of: date | None = None, *, blackout: int = EARNINGS_BLACKOUT_DAYS
) -> tuple[dict[str, date], set[str]]:
    """(upcoming results by symbol, symbols that just reported).

    ``upcoming`` only includes results due within the blackout window, since that is the
    set a trade plan needs to warn about.
    """
    today = as_of or date.today()
    upcoming: dict[str, date] = {}
    reported: set[str] = set()

    for event in fetch_earnings_events(today):
        if event.reported:
            if 0 <= (today - event.event_date).days <= 3:
                reported.add(event.symbol)
        elif today <= event.event_date <= today + timedelta(days=blackout):
            # Keep the nearest scheduled date per symbol.
            existing = upcoming.get(event.symbol)
            if existing is None or event.event_date < existing:
                upcoming[event.symbol] = event.event_date
    return upcoming, reported
