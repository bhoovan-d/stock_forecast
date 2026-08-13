"""Upstox market-data client — READ ONLY.

This module deliberately implements **only** market-data endpoints. There is no order
placement, modification or cancellation code anywhere in this repo: the system produces
trade plans, and the user places every order themselves.

Auth: the user completes the OAuth login in their own browser and pastes the resulting
access token into ``.env`` as ``UPSTOX_ACCESS_TOKEN``. We never handle their credentials.
Upstox tokens expire daily (~03:30 IST), so an expired token is an expected condition,
not an error — it simply degrades the session to the archive/delayed tiers.

Instrument keys are ISIN-based (``NSE_EQ|INE002A01018``), and we already carry ISINs from
the bhavcopy and constituent files, so no extra lookup service is needed.
"""

from __future__ import annotations

import gzip
import io
import json
from datetime import date, timedelta

import pandas as pd
from loguru import logger

from ..config import settings
from .cache import PacedClient, cache

_API = "https://api.upstox.com/v2"
_INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"

# Upstox v2 serves 1-minute intraday candles; we resample up to whatever the trade engine
# asks for. This is strictly better than the delayed tier, which cannot produce 3m bars.
_RESAMPLE = {"1m": "1min", "3m": "3min", "5m": "5min", "15m": "15min", "1h": "60min"}


def _load_instruments() -> dict[str, str]:
    """symbol -> instrument_key for NSE equities. Cached daily; needs no auth."""
    cached = cache.get_json("upstox:instruments", ttl_sec=86400)
    if cached is None:
        client = PacedClient(min_interval_sec=0.2, max_retries=2)
        resp = client.get(_INSTRUMENTS_URL)
        client.close()
        if resp is None:
            logger.warning("[upstox] instrument master unavailable")
            return {}
        raw = json.loads(gzip.decompress(resp.content).decode())
        cached = {
            row["trading_symbol"]: row["instrument_key"]
            for row in raw
            if row.get("segment") == "NSE_EQ" and row.get("instrument_type") == "EQ"
        }
        cache.set_json("upstox:instruments", cached)
    return cached


class UpstoxClient:
    """Live tier. Every method returns None when unauthenticated or on failure."""

    def __init__(self, token: str | None = None):
        self.token = token or settings.upstox_access_token
        self._client = PacedClient(
            min_interval_sec=0.25,
            max_retries=2,
            headers={
                "Accept": "application/json",
                **({"Authorization": f"Bearer {self.token}"} if self.token else {}),
            },
        )
        self._instruments: dict[str, str] | None = None
        self._auth_checked = False
        self._auth_ok = False

    # ── auth ──────────────────────────────────────────────────────────────────

    @property
    def authenticated(self) -> bool:
        """True only if a token exists *and* the API accepts it.

        Checked once per process: a token that expired overnight must degrade the session
        rather than fail every call individually.
        """
        if not self.token:
            return False
        if not self._auth_checked:
            self._auth_checked = True
            resp = self._client.get(f"{_API}/user/profile")
            self._auth_ok = resp is not None
            if not self._auth_ok:
                logger.warning(
                    "[upstox] token missing or expired — falling back to archive/delayed. "
                    "Run `asymmetry auth` for refresh instructions."
                )
        return self._auth_ok

    def instrument_key(self, symbol: str) -> str | None:
        if self._instruments is None:
            self._instruments = _load_instruments()
        return self._instruments.get(symbol)

    # ── quotes ────────────────────────────────────────────────────────────────

    def ltp(self, symbol: str) -> float | None:
        key = self.instrument_key(symbol)
        if key is None or not self.authenticated:
            return None
        resp = self._client.get(f"{_API}/market-quote/ltp", params={"instrument_key": key})
        if resp is None:
            return None
        payload = resp.json().get("data", {})
        for entry in payload.values():
            price = entry.get("last_price")
            if price is not None:
                return float(price)
        return None

    # ── candles ───────────────────────────────────────────────────────────────

    def intraday(
        self, symbol: str, *, interval: str = "5m", days: int = 5
    ) -> pd.DataFrame | None:
        """Intraday bars, resampled from 1-minute candles.

        Upstox splits "today" (intraday endpoint) from prior sessions (historical
        endpoint), so for multi-day windows we stitch the two together.
        """
        key = self.instrument_key(symbol)
        if key is None or not self.authenticated:
            return None
        rule = _RESAMPLE.get(interval)
        if rule is None:
            logger.warning(f"[upstox] unsupported interval {interval}")
            return None

        frames = []
        if days > 1:
            to_day = date.today()
            from_day = to_day - timedelta(days=days + 4)  # pad for weekends/holidays
            resp = self._client.get(
                f"{_API}/historical-candle/{key}/1minute/{to_day:%Y-%m-%d}/{from_day:%Y-%m-%d}"
            )
            if resp is not None:
                frames.append(self._to_frame(resp.json()))

        resp = self._client.get(f"{_API}/historical-candle/intraday/{key}/1minute")
        if resp is not None:
            frames.append(self._to_frame(resp.json()))

        frames = [f for f in frames if f is not None and not f.empty]
        if not frames:
            return None

        minutes = pd.concat(frames).sort_index()
        minutes = minutes[~minutes.index.duplicated(keep="last")]
        return self._resample(minutes, rule)

    def daily(self, symbol: str, *, days: int = 400) -> pd.DataFrame | None:
        key = self.instrument_key(symbol)
        if key is None or not self.authenticated:
            return None
        to_day = date.today()
        from_day = to_day - timedelta(days=days)
        resp = self._client.get(
            f"{_API}/historical-candle/{key}/day/{to_day:%Y-%m-%d}/{from_day:%Y-%m-%d}"
        )
        return self._to_frame(resp.json()) if resp is not None else None

    @staticmethod
    def _to_frame(payload: dict) -> pd.DataFrame | None:
        """Upstox candles: [timestamp, open, high, low, close, volume, open_interest]."""
        candles = (payload.get("data") or {}).get("candles")
        if not candles:
            return None
        frame = pd.DataFrame(
            candles, columns=["ts", "open", "high", "low", "close", "volume", "oi"]
        )
        frame["ts"] = pd.to_datetime(frame["ts"], format="ISO8601")
        return frame.set_index("ts").sort_index().drop(columns=["oi"]).astype(float)

    @staticmethod
    def _resample(minutes: pd.DataFrame, rule: str) -> pd.DataFrame:
        out = minutes.resample(rule, label="left", closed="left").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        )
        # Resampling spans the overnight gap; drop the empty bars it invents.
        return out.dropna(subset=["close"])

    # ── option chain ──────────────────────────────────────────────────────────

    def option_chain(self, underlying: str = "NIFTY") -> pd.DataFrame | None:
        """Live option chain, normalised to the same shape as the archive chain.

        Returning an identical schema means Engine 1's gamma maths is written once and
        works against either tier.
        """
        if not self.authenticated:
            return None
        index_key = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}.get(
            underlying
        )
        if index_key is None:
            return None

        contracts = self._client.get(
            f"{_API}/option/contract", params={"instrument_key": index_key}
        )
        if contracts is None:
            return None
        expiries = sorted({c["expiry"] for c in contracts.json().get("data", [])})
        if not expiries:
            return None

        resp = self._client.get(
            f"{_API}/option/chain",
            params={"instrument_key": index_key, "expiry_date": expiries[0]},
        )
        if resp is None:
            return None

        rows = []
        for node in resp.json().get("data", []):
            spot = node.get("underlying_spot_price")
            expiry = pd.to_datetime(node.get("expiry")).date()
            for side, tag in (("call_options", "CE"), ("put_options", "PE")):
                leg = node.get(side) or {}
                market = leg.get("market_data") or {}
                rows.append(
                    {
                        "date": date.today(),
                        "symbol": underlying,
                        "instrument": "IDO",
                        "expiry": expiry,
                        "strike": node.get("strike_price"),
                        "option_type": tag,
                        "close": market.get("ltp"),
                        "settle": market.get("ltp"),
                        "underlying": spot,
                        "open_interest": market.get("oi"),
                        "oi_change": (market.get("oi") or 0) - (market.get("prev_oi") or 0),
                        "volume": market.get("volume"),
                        "lot_size": None,
                    }
                )
        frame = pd.DataFrame(rows)
        return frame.dropna(subset=["strike", "close"]) if not frame.empty else None


def auth_url() -> str | None:
    """The URL the user opens themselves to obtain a token. We never log them in."""
    if not settings.upstox_api_key:
        return None
    return (
        f"{_API}/login/authorization/dialog?response_type=code"
        f"&client_id={settings.upstox_api_key}"
        f"&redirect_uri={settings.upstox_redirect_uri}"
    )
