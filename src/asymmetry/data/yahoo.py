"""Yahoo Finance chart API client.

Serves everything NSE's archives cannot: intraday bars, index levels, and the global/macro
series (S&P, Nasdaq, DXY, US 10Y, crude, gold, USD/INR). Verified working for NSE equities
(``RELIANCE.NS``), indices (``^NSEI``, ``^INDIAVIX``, ``^NSEBANK``, ``^CNX*``) and 5-minute
bars returned in ``Asia/Kolkata``.

Every call goes through a paced client and the disk cache — see cache.py for why.
"""

from __future__ import annotations

from datetime import date, time

import pandas as pd
from loguru import logger

from ..config import settings
from .cache import PacedClient, cache

_BASE = "https://query1.finance.yahoo.com/v8/finance/chart/"

# NSE's regular session, in exchange time. Bars are stamped with their *start*, so 09:15 to
# 15:30 is 25 fifteen-minute intervals — yet the feed returns 26 bars a session. The extra
# one is stamped 15:30 and is the closing print, not a 15-minute window: nothing trades
# between 15:30 and 15:45. Anything describing a bar's time span has to know this or it will
# quote an interval that does not exist.
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)

_client = PacedClient(
    min_interval_sec=settings.yahoo_min_interval_sec,
    max_retries=settings.yahoo_max_retries,
)

# Macro/global factor symbols. `^IN10YT=RR` (India 10Y) is deliberately absent: it 404s on
# Yahoo. The macro engine treats its factor list as advisory and omits what it cannot fetch
# rather than zero-filling, which would silently bias the regression.
MACRO_SYMBOLS: dict[str, str] = {
    "nifty": "^NSEI",
    "india_vix": "^INDIAVIX",
    "sp500": "^GSPC",
    "nasdaq": "^IXIC",
    "us_vix": "^VIX",
    "dxy": "DX-Y.NYB",
    "us10y": "^TNX",
    "usdinr": "USDINR=X",
    "crude": "CL=F",
    "gold": "GC=F",
}

# NSE sector indices, used for relative-strength-vs-sector in Engine 3.
SECTOR_INDEX_SYMBOLS: dict[str, str] = {
    "Financial Services": "^NSEBANK",
    "Information Technology": "^CNXIT",
    "Automobile and Auto Components": "^CNXAUTO",
    "Healthcare": "^CNXPHARMA",
    "Fast Moving Consumer Goods": "^CNXFMCG",
    "Metals & Mining": "^CNXMETAL",
    "Oil Gas & Consumable Fuels": "^CNXENERGY",
    "Realty": "^CNXREALTY",
}


def to_yahoo_symbol(nse_symbol: str) -> str:
    """NSE ticker -> Yahoo ticker (RELIANCE -> RELIANCE.NS)."""
    return nse_symbol if nse_symbol.startswith("^") else f"{nse_symbol}.NS"


def fetch_chart(
    symbol: str,
    *,
    range_: str = "1y",
    interval: str = "1d",
    as_of: date | None = None,
) -> pd.DataFrame | None:
    """Fetch OHLCV bars. Returns None if unavailable, so callers can degrade.

    The returned frame is indexed by tz-aware timestamps in the exchange's own timezone,
    which for NSE keeps intraday bars aligned to the 09:15-15:30 IST session.

    ``as_of`` truncates the frame to bars at or before that date. This is the single
    chokepoint that prevents lookahead bias: Yahoo always returns a window ending *today*,
    so a historical run without truncation silently ranks stocks against a benchmark that
    contains months of future data. Truncating here rather than in each caller means no
    engine can leak the future by forgetting to.
    """
    key = f"yahoo:{symbol}:{range_}:{interval}"
    ttl = (
        settings.cache_ttl_daily_sec
        if interval.endswith("d") or interval.endswith("k") or interval.endswith("o")
        else settings.cache_ttl_intraday_sec
    )
    payload = cache.get_json(key, ttl)

    if payload is None:
        resp = _client.get(_BASE + symbol, params={"range": range_, "interval": interval})
        if resp is None:
            logger.warning(f"[yahoo] no data for {symbol} ({range_}/{interval})")
            return None
        try:
            payload = resp.json()
        except ValueError:
            # Throttling returns an HTML error page rather than JSON.
            logger.warning(f"[yahoo] non-JSON response for {symbol} (likely throttled)")
            return None
        cache.set_json(key, payload)

    frame = _parse_chart(payload, symbol)
    return truncate(frame, as_of)


def truncate(frame: pd.DataFrame | None, as_of: date | None) -> pd.DataFrame | None:
    """Drop every bar after ``as_of``. Returns None if nothing survives."""
    if frame is None or as_of is None or frame.empty:
        return frame
    cutoff = pd.Timestamp(as_of).tz_localize(frame.index.tz) + pd.Timedelta(days=1)
    trimmed = frame[frame.index < cutoff]
    return trimmed if not trimmed.empty else None


def _parse_chart(payload: dict, symbol: str) -> pd.DataFrame | None:
    result = (payload.get("chart") or {}).get("result")
    if not result:
        return None
    node = result[0]
    timestamps = node.get("timestamp")
    if not timestamps:
        return None

    quote = node["indicators"]["quote"][0]
    tz = node.get("meta", {}).get("exchangeTimezoneName", "Asia/Kolkata")
    frame = pd.DataFrame(
        {
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        },
        index=pd.to_datetime(timestamps, unit="s", utc=True).tz_convert(tz),
    )
    frame.index.name = "ts"
    frame.attrs["symbol"] = symbol
    # Yahoo pads the current session with an all-null bar before the first print.
    return frame.dropna(subset=["close"])


def fetch_many(
    symbols: list[str],
    *,
    range_: str = "1y",
    interval: str = "1d",
    as_of: date | None = None,
) -> dict[str, pd.DataFrame]:
    """Fetch a batch. Pacing is handled by the shared client, so bursts are safe."""
    out: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        frame = fetch_chart(sym, range_=range_, interval=interval, as_of=as_of)
        if frame is not None and not frame.empty:
            out[sym] = frame
    if len(out) < len(symbols):
        missing = set(symbols) - set(out)
        logger.info(f"[yahoo] {len(out)}/{len(symbols)} fetched; missing: {sorted(missing)}")
    return out


def fetch_macro(range_: str = "2y", as_of: date | None = None) -> dict[str, pd.DataFrame]:
    """Fetch the macro factor panel, keyed by factor name rather than ticker."""
    frames = fetch_many(
        list(MACRO_SYMBOLS.values()), range_=range_, interval="1d", as_of=as_of
    )
    return {
        name: frames[sym] for name, sym in MACRO_SYMBOLS.items() if sym in frames
    }
