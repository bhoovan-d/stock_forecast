"""Per-stock F&O positioning (Brief §11).

Confirmation only. The brief is explicit that options positioning is context, never a
standalone entry signal, so nothing here can qualify a trade on its own — it can only
strengthen or weaken a thesis that price and catalyst already support.

The distinction that matters most is **long buildup vs short covering**. Both send price
up, but they are not the same trade:

* price up + open interest up   → new longs, fresh money, moves tend to extend
* price up + open interest down → shorts closing, a move that dies when they finish

A catalyst accompanied only by short covering is a much weaker setup than the same
catalyst with genuine futures participation, and the brief asks for exactly that check.

Everything is computed from the settled F&O bhavcopy, which carries per-stock futures and
options with open interest — so this needs no paid derivatives feed.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..spec import FnOState
from .gamma import implied_vol

# Instrument type codes in the NSE UDiFF F&O file.
_STOCK_FUT = "STF"
_STOCK_OPT = "STO"


def _nearest_expiry(frame: pd.DataFrame) -> date | None:
    expiries = sorted(frame["expiry"].dropna().unique())
    return expiries[0] if expiries else None


def assess_fno(
    symbol: str,
    as_of: date,
    spot: float,
    *,
    fo_today: pd.DataFrame | None = None,
    fo_prev: pd.DataFrame | None = None,
) -> FnOState:
    """Futures buildup and options positioning for one stock.

    ``fo_today``/``fo_prev`` are whole-market F&O frames; passing them in lets a scan parse
    the file once rather than per symbol.
    """
    from ..data import nse_archive

    if fo_today is None:
        fo_today = nse_archive.fetch_fo_bhavcopy(as_of)
    if fo_today is None:
        return FnOState(has_fno=False, note="F&O file unavailable")

    rows = fo_today[fo_today["symbol"] == symbol]
    if rows.empty:
        return FnOState(has_fno=False, note="not in the F&O segment")

    state = FnOState(has_fno=True)

    # ── Futures: buildup classification ───────────────────────────────────────
    futures = rows[rows["instrument"] == _STOCK_FUT]
    if not futures.empty:
        expiry = _nearest_expiry(futures)
        near = futures[futures["expiry"] == expiry]
        if not near.empty:
            row = near.iloc[0]
            fut_price = float(row["close"])
            oi = float(row["open_interest"]) if pd.notna(row["open_interest"]) else np.nan
            oi_change = (
                float(row["oi_change"]) if pd.notna(row["oi_change"]) else np.nan
            )

            state.futures_oi = oi if np.isfinite(oi) else None
            if np.isfinite(oi) and np.isfinite(oi_change) and (oi - oi_change) > 0:
                state.oi_change_pct = round(oi_change / (oi - oi_change) * 100, 2)

            if spot > 0 and np.isfinite(fut_price):
                # Futures above spot (positive basis) signals willingness to pay to be long.
                state.basis_pct = round((fut_price / spot - 1) * 100, 3)

            price_up = None
            if fo_prev is not None:
                prev = fo_prev[
                    (fo_prev["symbol"] == symbol)
                    & (fo_prev["instrument"] == _STOCK_FUT)
                    & (fo_prev["expiry"] == expiry)
                ]
                if not prev.empty:
                    prev_price = float(prev.iloc[0]["close"])
                    price_up = fut_price > prev_price

            if price_up is not None and state.oi_change_pct is not None:
                oi_up = state.oi_change_pct > 0
                state.buildup = {
                    (True, True): "long buildup",
                    (True, False): "short covering",
                    (False, True): "short buildup",
                    (False, False): "long unwinding",
                }[(price_up, oi_up)]
                # Only fresh longs count as genuine participation.
                state.genuine_participation = state.buildup == "long buildup"

    # ── Options: IV and the OI walls ──────────────────────────────────────────
    options = rows[
        (rows["instrument"] == _STOCK_OPT) & (rows["option_type"].isin(["CE", "PE"]))
    ]
    if not options.empty and spot > 0:
        expiry = _nearest_expiry(options)
        chain = options[options["expiry"] == expiry].copy()
        if not chain.empty:
            chain["distance"] = (chain["strike"] - spot).abs()

            # ATM implied vol, solved from the settled option price.
            atm = chain.nsmallest(2, "distance")
            ivs = []
            for row in atm.itertuples():
                if not np.isfinite(row.close) or row.close <= 0:
                    continue
                years = max((row.expiry - as_of).days / 365.0, 1 / 365)
                iv = implied_vol(
                    float(row.close), spot, float(row.strike), years,
                    settings.risk_free_rate, row.option_type == "CE",
                )
                if iv is not None:
                    ivs.append(iv)
            if ivs:
                state.atm_iv = round(float(np.mean(ivs)) * 100, 1)

            calls = chain[chain["option_type"] == "CE"]
            puts = chain[chain["option_type"] == "PE"]
            # The heaviest call OI above spot is where writers expect the move to stall —
            # option-implied resistance.
            calls_above = calls[calls["strike"] > spot]
            if not calls_above.empty:
                state.call_oi_wall = float(
                    calls_above.loc[calls_above["open_interest"].idxmax(), "strike"]
                )
            puts_below = puts[puts["strike"] < spot]
            if not puts_below.empty:
                state.put_oi_support = float(
                    puts_below.loc[puts_below["open_interest"].idxmax(), "strike"]
                )

    bits = []
    if state.buildup:
        bits.append(state.buildup)
    if state.oi_change_pct is not None:
        bits.append(f"OI {state.oi_change_pct:+.1f}%")
    if state.basis_pct is not None:
        bits.append(f"basis {state.basis_pct:+.2f}%")
    if state.atm_iv is not None:
        bits.append(f"ATM IV {state.atm_iv:.0f}%")
    state.note = " · ".join(bits) if bits else "F&O listed, no positioning read"
    return state


def iv_percentile_for(symbol: str, as_of: date, lookback_days: int = 60) -> float | None:
    """ATM IV percentile over recent sessions.

    Each day costs one F&O file parse, so this is intentionally called only for shortlisted
    names rather than the whole universe.
    """
    from ..data import nse_archive

    days = nse_archive.trading_days(
        as_of - pd.Timedelta(days=lookback_days * 2).to_pytimedelta(), as_of
    )[-lookback_days:]
    if len(days) < 20:
        return None

    history = []
    for day in days:
        fo = nse_archive.fetch_fo_bhavcopy(day)
        if fo is None:
            continue
        rows = fo[(fo["symbol"] == symbol) & (fo["instrument"] == _STOCK_OPT)]
        if rows.empty:
            continue
        underlying = rows["underlying"].dropna()
        if underlying.empty:
            continue
        spot = float(underlying.iloc[0])
        state = assess_fno(symbol, day, spot, fo_today=fo)
        if state.atm_iv is not None:
            history.append(state.atm_iv)

    if len(history) < 20:
        return None
    current = history[-1]
    return round(sum(v < current for v in history[:-1]) / (len(history) - 1) * 100, 1)


def fno_score(state: FnOState) -> float:
    """0-100 derivative confirmation. 50 is neutral — a non-F&O stock is not penalised.

    The brief says options data is not required for non-F&O equity candidates, so their
    absence must not push a good equity setup down the ranking.
    """
    if not state.has_fno:
        return 50.0

    score = 50.0
    if state.buildup == "long buildup":
        score += 25
    elif state.buildup == "short covering":
        # Price is up, but on closing shorts rather than new conviction.
        score += 5
    elif state.buildup == "short buildup":
        score -= 25
    elif state.buildup == "long unwinding":
        score -= 15

    if state.basis_pct is not None:
        score += float(np.clip(state.basis_pct * 20, -10, 10))
    if state.oi_change_pct is not None:
        score += float(np.clip(state.oi_change_pct / 2, -10, 10))

    return float(np.clip(score, 0, 100))
