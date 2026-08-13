"""Dealer gamma exposure (GEX) from the option chain.

Regime context only. Per the system brief, gamma tells you what *kind* of tape you are
trading — it is never an entry signal, and nothing downstream may use it to pick a stock.

Interpretation:
* **Positive net GEX** — dealers are long gamma and hedge against the move (sell rallies,
  buy dips). Volatility is suppressed; breakouts tend to stall. Be more selective.
* **Negative net GEX** — dealers are short gamma and hedge with the move. Moves extend,
  volatility expands. A good breakout has room to run.
* **Gamma flip** — the spot level where net GEX crosses zero, i.e. the boundary between
  those two regimes.

The chain is the settled EOD file (or the live Upstox chain), which carries price and open
interest but no implied vol, so IV is solved per contract from the option's own close.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from loguru import logger
from scipy.optimize import brentq
from scipy.stats import norm

from ..config import settings

# Trading days per year: IV and gamma should be measured on the calendar the market
# actually trades, not 365 days.
_YEAR = 252.0
_MIN_T = 1.0 / (_YEAR * 6.5 * 60)  # one minute, to keep expiry-day maths finite


def _d1(spot: float, strike: float, t: float, vol: float, rate: float) -> float:
    return (np.log(spot / strike) + (rate + 0.5 * vol**2) * t) / (vol * np.sqrt(t))


def bs_price(spot: float, strike: float, t: float, vol: float, rate: float, is_call: bool) -> float:
    if t <= 0 or vol <= 0:
        intrinsic = (spot - strike) if is_call else (strike - spot)
        return max(0.0, intrinsic)
    d1 = _d1(spot, strike, t, vol, rate)
    d2 = d1 - vol * np.sqrt(t)
    disc = np.exp(-rate * t)
    if is_call:
        return spot * norm.cdf(d1) - strike * disc * norm.cdf(d2)
    return strike * disc * norm.cdf(-d2) - spot * norm.cdf(-d1)


def bs_gamma(spot: float, strike: float, t: float, vol: float, rate: float) -> float:
    """Gamma is identical for calls and puts at the same strike (put-call parity)."""
    if t <= 0 or vol <= 0 or spot <= 0:
        return 0.0
    d1 = _d1(spot, strike, t, vol, rate)
    return float(norm.pdf(d1) / (spot * vol * np.sqrt(t)))


def implied_vol(
    price: float, spot: float, strike: float, t: float, rate: float, is_call: bool
) -> float | None:
    """Solve IV by bisection. Returns None where no sane root exists.

    Deep-OTM contracts priced at the tick floor carry no usable vol information, and
    forcing a root there produces absurd gammas that would swamp the GEX total.
    """
    if price <= 0.05 or t <= 0 or spot <= 0:
        return None
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if price < intrinsic:  # arbitrage/stale print
        return None

    def objective(vol: float) -> float:
        return bs_price(spot, strike, t, vol, rate, is_call) - price

    try:
        if objective(0.01) * objective(5.0) > 0:
            return None
        return float(brentq(objective, 0.01, 5.0, maxiter=100, xtol=1e-6))
    except (ValueError, RuntimeError):
        return None


def _prepare(chain: pd.DataFrame, as_of: date) -> pd.DataFrame:
    """Attach time-to-expiry, IV and per-contract gamma to each option row."""
    frame = chain.dropna(subset=["strike", "close", "expiry"]).copy()
    if frame.empty:
        return frame

    spot = float(frame["underlying"].dropna().iloc[0]) if frame["underlying"].notna().any() else np.nan
    if not np.isfinite(spot):
        return pd.DataFrame()

    frame["t"] = frame["expiry"].map(
        lambda e: max((e - as_of).days / 365.0, _MIN_T)
    )
    frame["is_call"] = frame["option_type"] == "CE"
    frame["spot"] = spot

    frame["iv"] = [
        implied_vol(r.close, spot, r.strike, r.t, settings.risk_free_rate, r.is_call)
        for r in frame.itertuples()
    ]
    frame = frame.dropna(subset=["iv"])
    if frame.empty:
        return frame

    frame["gamma"] = [
        bs_gamma(spot, r.strike, r.t, r.iv, settings.risk_free_rate) for r in frame.itertuples()
    ]
    return frame


def _gex_at(frame: pd.DataFrame, spot: float) -> float:
    """Net dealer GEX if spot were at ``spot``, holding IV and open interest fixed.

    Open interest in the NSE bhavcopy is expressed in **units of the underlying**, not in
    contracts — verified: every strike's OI divides exactly by its lot size. So there is
    deliberately no lot-size multiplier here; applying one would inflate GEX by ~65x.

    Sign convention: call OI contributes positive dealer gamma, put OI negative. The sign
    of the total is what the regime read and the flip level depend on.
    """
    total = 0.0
    for row in frame.itertuples():
        gamma = bs_gamma(spot, row.strike, row.t, row.iv, settings.risk_free_rate)
        # Notional gamma per 1% move in spot.
        exposure = gamma * row.open_interest * spot * spot * 0.01
        total += exposure if row.is_call else -exposure
    return total


def compute_gex(
    chain: pd.DataFrame, as_of: date, *, near_expiries: int = 2
) -> dict[str, float | None]:
    """Net GEX, gamma flip level, and spot.

    Only the nearest expiries are used: far-dated open interest carries little gamma and
    mostly adds noise to the flip solve.
    """
    if chain is None or chain.empty:
        return {"net_gex": None, "gamma_flip": None, "spot": None}

    expiries = sorted(chain["expiry"].dropna().unique())[:near_expiries]
    near = chain[chain["expiry"].isin(expiries)]

    frame = _prepare(near, as_of)
    if frame.empty:
        logger.warning("[gamma] no contracts survived the IV solve")
        return {"net_gex": None, "gamma_flip": None, "spot": None}

    spot = float(frame["spot"].iloc[0])
    net_gex = _gex_at(frame, spot)

    # Walk spot across a +/-10% grid; the flip is where the sign of net GEX changes.
    grid = np.linspace(spot * 0.90, spot * 1.10, 41)
    profile = [_gex_at(frame, float(s)) for s in grid]

    flip = None
    for i in range(1, len(profile)):
        if profile[i - 1] == 0 or profile[i - 1] * profile[i] < 0:
            lo, hi = grid[i - 1], grid[i]
            y0, y1 = profile[i - 1], profile[i]
            flip = float(lo + (hi - lo) * abs(y0) / max(abs(y0) + abs(y1), 1e-9))
            break

    return {
        "net_gex": float(net_gex),
        "gamma_flip": flip,
        "spot": spot,
        "contracts": float(len(frame)),
    }


def gamma_regime_score(net_gex: float | None, spot: float | None, flip: float | None) -> tuple[int, str]:
    """Translate GEX into a regime sub-score (-1/0/+1) plus a readable note.

    Negative gamma scores +1 for a *breakout* system: moves extend rather than mean-revert,
    which is exactly the environment where a good setup pays. This is context weighting,
    not a trade trigger.
    """
    if net_gex is None:
        return 0, "gamma unavailable"

    billions = net_gex / 1e9
    if net_gex < 0:
        note = f"negative gamma ({billions:,.1f}bn) — moves extend, breakouts have room"
        score = 1
    else:
        note = f"positive gamma ({billions:,.1f}bn) — dealers dampen moves, expect chop"
        score = -1

    if flip is not None and spot is not None:
        distance = (spot / flip - 1) * 100
        note += f"; flip {flip:,.0f} ({distance:+.1f}% from spot)"
    return score, note
