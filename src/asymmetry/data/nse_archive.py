"""NSE archives client — the official EOD backbone.

IMPORTANT: ``www.nseindia.com`` (the live site and its ``/api/*`` endpoints) is
Akamai-blocked from this environment — verified 403 on the homepage and 404 on
``/api/option-chain-indices`` even with a full browser header set and cookie warm-up.
Do not build against it.

``nsearchives.nseindia.com`` is *not* blocked and serves everything we need:

* ``/content/indices/ind_nifty500list.csv``      — universe + sector labels
* ``/content/cm/BhavCopy_NSE_CM_...csv.zip``     — EOD OHLCV for every stock (UDiFF)
* ``/content/fo/BhavCopy_NSE_FO_...csv.zip``     — full options chain with open interest
* ``/archives/equities/mto/MTO_DDMMYYYY.DAT``    — delivery quantity / delivery %

Settled bhavcopies are immutable, so they are cached forever (ttl -1).
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from .cache import PacedClient, cache

_HOST = "https://nsearchives.nseindia.com"

# Archives tolerate faster access than Yahoo, but a Referer is required.
_client = PacedClient(
    min_interval_sec=0.4, max_retries=3, headers={"Referer": "https://www.nseindia.com/"}
)

INDEX_CSV = {
    "nifty50": "ind_nifty50list.csv",
    "nifty100": "ind_nifty100list.csv",
    "nifty200": "ind_nifty200list.csv",
    "nifty500": "ind_nifty500list.csv",
}


# ── Universe ──────────────────────────────────────────────────────────────────


def fetch_index_constituents(index: str = "nifty500") -> pd.DataFrame | None:
    """Constituents with their NSE ``Industry`` sector label.

    Columns: symbol, company, sector, isin.
    """
    filename = INDEX_CSV.get(index.lower())
    if filename is None:
        raise ValueError(f"unknown index {index!r}; known: {sorted(INDEX_CSV)}")

    key = f"nse:index:{index}"
    raw = cache.get_bytes(key, ttl_sec=86400)
    if raw is None:
        resp = _client.get(f"{_HOST}/content/indices/{filename}")
        if resp is None:
            logger.error(f"[nse] could not fetch constituents for {index}")
            return None
        raw = resp.content
        cache.set_bytes(key, raw)

    frame = pd.read_csv(io.BytesIO(raw))
    frame.columns = [c.strip() for c in frame.columns]
    return pd.DataFrame(
        {
            "symbol": frame["Symbol"].str.strip(),
            "company": frame["Company Name"].str.strip(),
            "sector": frame["Industry"].str.strip(),
            "isin": frame["ISIN Code"].str.strip(),
        }
    )


# ── Cash-market bhavcopy ──────────────────────────────────────────────────────


def _cm_url(day: date) -> str:
    return (
        f"{_HOST}/content/cm/BhavCopy_NSE_CM_0_0_0_"
        f"{day:%Y%m%d}_F_0000.csv.zip"
    )


def _read_zipped_csv(raw: bytes) -> list[dict[str, str]]:
    archive = zipfile.ZipFile(io.BytesIO(raw))
    text = archive.read(archive.namelist()[0]).decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def fetch_cm_bhavcopy(day: date) -> pd.DataFrame | None:
    """EOD OHLCV for every NSE cash-market instrument on ``day``.

    Returns only the ``EQ`` series (~2,459 rows) — that is the tradeable equity universe;
    the rest is ETFs, bonds and other series we do not scan.
    Returns None on a holiday/weekend, when no file exists.
    """
    key = f"nse:cm:{day:%Y%m%d}"
    raw = cache.get_bytes(key)
    if raw is None:
        resp = _client.get(_cm_url(day))
        if resp is None:
            return None
        raw = resp.content
        cache.set_bytes(key, raw)

    rows = _read_zipped_csv(raw)
    frame = pd.DataFrame(rows)
    frame = frame[frame["SctySrs"].str.strip() == "EQ"].copy()
    if frame.empty:
        return None

    out = pd.DataFrame(
        {
            "date": pd.to_datetime(frame["TradDt"]).dt.date,
            "symbol": frame["TckrSymb"].str.strip(),
            "isin": frame["ISIN"].str.strip(),
            "open": pd.to_numeric(frame["OpnPric"], errors="coerce"),
            "high": pd.to_numeric(frame["HghPric"], errors="coerce"),
            "low": pd.to_numeric(frame["LwPric"], errors="coerce"),
            "close": pd.to_numeric(frame["ClsPric"], errors="coerce"),
            "prev_close": pd.to_numeric(frame["PrvsClsgPric"], errors="coerce"),
            "volume": pd.to_numeric(frame["TtlTradgVol"], errors="coerce"),
            "turnover": pd.to_numeric(frame["TtlTrfVal"], errors="coerce"),
            "trades": pd.to_numeric(frame["TtlNbOfTxsExctd"], errors="coerce"),
        }
    )
    return out.dropna(subset=["close"]).reset_index(drop=True)


# ── F&O bhavcopy (options open interest -> dealer gamma) ──────────────────────


def _fo_url(day: date) -> str:
    return (
        f"{_HOST}/content/fo/BhavCopy_NSE_FO_0_0_0_"
        f"{day:%Y%m%d}_F_0000.csv.zip"
    )


def fetch_fo_bhavcopy(day: date) -> pd.DataFrame | None:
    """Full derivatives EOD file, including every option's strike and open interest.

    ``FinInstrmTp`` values: IDO/IDF = index options/futures, STO/STF = stock options/futures.
    This is what makes dealer gamma computable without a paid options feed.
    """
    key = f"nse:fo:{day:%Y%m%d}"
    raw = cache.get_bytes(key)
    if raw is None:
        resp = _client.get(_fo_url(day))
        if resp is None:
            return None
        raw = resp.content
        cache.set_bytes(key, raw)

    frame = pd.DataFrame(_read_zipped_csv(raw))
    if frame.empty:
        return None

    return pd.DataFrame(
        {
            "date": pd.to_datetime(frame["TradDt"]).dt.date,
            "symbol": frame["TckrSymb"].str.strip(),
            "instrument": frame["FinInstrmTp"].str.strip(),
            "expiry": pd.to_datetime(frame["XpryDt"], errors="coerce").dt.date,
            "strike": pd.to_numeric(frame["StrkPric"], errors="coerce"),
            "option_type": frame["OptnTp"].str.strip(),
            "close": pd.to_numeric(frame["ClsPric"], errors="coerce"),
            "settle": pd.to_numeric(frame["SttlmPric"], errors="coerce"),
            "underlying": pd.to_numeric(frame["UndrlygPric"], errors="coerce"),
            "open_interest": pd.to_numeric(frame["OpnIntrst"], errors="coerce"),
            "oi_change": pd.to_numeric(frame["ChngInOpnIntrst"], errors="coerce"),
            "volume": pd.to_numeric(frame["TtlTradgVol"], errors="coerce"),
            "lot_size": pd.to_numeric(frame["NewBrdLotQty"], errors="coerce"),
        }
    )


def fetch_option_chain(day: date, underlying: str = "NIFTY") -> pd.DataFrame | None:
    """Option rows for one underlying, nearest expiry first."""
    fo = fetch_fo_bhavcopy(day)
    if fo is None:
        return None
    chain = fo[
        (fo["symbol"] == underlying) & (fo["option_type"].isin(["CE", "PE"]))
    ].copy()
    return chain.sort_values(["expiry", "strike"]).reset_index(drop=True) if not chain.empty else None


# ── Delivery data ─────────────────────────────────────────────────────────────


def fetch_delivery(day: date) -> pd.DataFrame | None:
    """Security-wise delivery positions (MTO file).

    Delivery % is our institutional-participation proxy: a turnover spike that is *also*
    a delivery spike means real positioning, not intraday churn. This stands in for the
    insider/13F data the brief explicitly rules out.

    The file has 4 preamble lines, then fixed CSV records prefixed by record type ``20``.
    """
    key = f"nse:mto:{day:%Y%m%d}"
    raw = cache.get_bytes(key)
    if raw is None:
        resp = _client.get(f"{_HOST}/archives/equities/mto/MTO_{day:%d%m%Y}.DAT")
        if resp is None:
            return None
        raw = resp.content
        cache.set_bytes(key, raw)

    records = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        parts = [p.strip() for p in line.split(",")]
        # Record layout: 20, srno, symbol, series, traded qty, delivered qty, pct
        if len(parts) >= 7 and parts[0] == "20":
            try:
                records.append(
                    {
                        "date": day,
                        "symbol": parts[2],
                        "series": parts[3],
                        "traded_qty": float(parts[4]),
                        "delivered_qty": float(parts[5]),
                        "delivery_pct": float(parts[6]),
                    }
                )
            except ValueError:
                continue

    if not records:
        return None
    frame = pd.DataFrame(records)
    return frame[frame["series"] == "EQ"].reset_index(drop=True)


# ── Trading-day helpers ───────────────────────────────────────────────────────


def last_trading_day(reference: date | None = None, max_lookback: int = 10) -> date | None:
    """Most recent day with a published bhavcopy, walking back from ``reference``.

    Probing the archive is the reliable way to skip weekends *and* NSE holidays without
    maintaining a holiday calendar.
    """
    day = reference or date.today()
    for _ in range(max_lookback):
        if day.weekday() < 5 and fetch_cm_bhavcopy(day) is not None:
            return day
        day -= timedelta(days=1)
    return None


def trading_days(start: date, end: date) -> list[date]:
    """Weekdays in range that actually have a bhavcopy (cheap once cached)."""
    days, cursor = [], start
    while cursor <= end:
        if cursor.weekday() < 5 and fetch_cm_bhavcopy(cursor) is not None:
            days.append(cursor)
        cursor += timedelta(days=1)
    return days
