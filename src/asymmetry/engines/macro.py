"""Engine 4 — macro fair value.

A deliberately simplified sensitivity model, not a Quant Insight replication. For each
stock we regress daily returns on a panel of macro factor returns, then ask: given where
those factors are now, where *should* this stock be trading, and where is it actually?

The gap is only interesting when technicals and catalyst point the same way. On its own it
is not a "this is cheap" signal, and the R² guard below is what stops it becoming one.

Ridge rather than OLS because the factors are correlated (crude and the rupee, NIFTY and
the sector index); plain OLS hands back unstable, wildly-signed betas on collinear inputs.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
from loguru import logger

from ..config import settings
from ..data import universe as universe_mod, yahoo
from ..models import MacroGap

# Sector-specific factors layered on top of the common panel. Adding a commodity that
# actually drives the sector's margins is worth far more than a longer generic factor list.
SECTOR_FACTORS: dict[str, list[str]] = {
    "Oil Gas & Consumable Fuels": ["crude"],
    "Metals & Mining": ["gold", "dxy"],
    "Information Technology": ["usdinr", "nasdaq"],
    "Automobile and Auto Components": ["crude"],
    "Healthcare": ["usdinr"],
}

_COMMON_FACTORS = ["nifty", "usdinr", "us10y", "crude", "gold", "india_vix"]


def _returns(frame: pd.DataFrame) -> pd.Series:
    return frame["close"].pct_change().dropna()


def _to_daily_index(series: pd.Series) -> pd.Series:
    """Normalise to tz-naive calendar days, one observation per day.

    Yahoo appends a partial real-time bar for the current session alongside that day's
    settled bar, so normalising the timestamps yields duplicate labels and any subsequent
    join raises "cannot reindex on an axis with duplicate labels". Keep the latest.
    """
    series = series.copy()
    series.index = pd.to_datetime(series.index).tz_localize(None).normalize()
    return series[~series.index.duplicated(keep="last")]


def ridge_fit(
    X: np.ndarray, y: np.ndarray, alpha: float
) -> tuple[np.ndarray, float]:
    """Ridge regression on standardised inputs. Returns (betas, R²).

    Implemented directly rather than pulling in scikit-learn: it is a closed-form solve and
    the dependency would be the largest in the project for one line of linear algebra.
    """
    # Penalise the slopes but never the intercept, which is handled by centring.
    x_mean, y_mean = X.mean(axis=0), y.mean()
    Xc, yc = X - x_mean, y - y_mean

    identity = np.eye(Xc.shape[1])
    betas = np.linalg.solve(Xc.T @ Xc + alpha * identity, Xc.T @ yc)

    predicted = Xc @ betas
    ss_res = float(((yc - predicted) ** 2).sum())
    ss_tot = float((yc**2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return betas, r2


def _factor_panel(sector: str | None, as_of: date | None = None) -> dict[str, pd.Series]:
    """Daily returns for the common panel plus any sector-specific factors.

    Factors that cannot be fetched are omitted rather than zero-filled — India's 10Y yield
    is unavailable from our sources, and silently inserting zeros would bias every beta.
    """
    wanted = list(_COMMON_FACTORS)
    for extra in SECTOR_FACTORS.get(sector or "", []):
        if extra not in wanted:
            wanted.append(extra)

    frames = yahoo.fetch_macro(range_="2y", as_of=as_of)
    panel: dict[str, pd.Series] = {}
    for name in wanted:
        frame = frames.get(name)
        if frame is None or frame.empty:
            continue
        panel[name] = _to_daily_index(_returns(frame))

    missing = set(wanted) - set(panel)
    if missing:
        logger.debug(f"[macro] factors unavailable, omitted: {sorted(missing)}")
    return panel


def macro_gap(
    symbol: str, sector: str | None = None, as_of: date | None = None
) -> MacroGap:
    """Fair value implied by macro conditions, versus where the stock actually trades."""
    if sector is None:
        universe = universe_mod.load_universe()
        stock = universe.get(symbol)
        sector = stock.sector if stock else None

    stock_frame = yahoo.fetch_chart(
        yahoo.to_yahoo_symbol(symbol), range_="2y", interval="1d", as_of=as_of
    )
    if stock_frame is None or len(stock_frame) < 120:
        return MacroGap()

    stock_returns = _to_daily_index(_returns(stock_frame))

    panel = _factor_panel(sector, as_of)
    if not panel:
        return MacroGap()

    aligned = pd.DataFrame({"stock": stock_returns, **panel}).dropna()
    lookback = min(settings.macro_lookback_days, len(aligned))
    if lookback < 90:
        return MacroGap()
    aligned = aligned.tail(lookback)

    factor_names = [c for c in aligned.columns if c != "stock"]
    X = aligned[factor_names].to_numpy(dtype=float)
    y = aligned["stock"].to_numpy(dtype=float)

    # Standardise so the ridge penalty applies evenly and betas stay comparable.
    scale = X.std(axis=0)
    scale[scale == 0] = 1.0
    betas, r2 = ridge_fit(X / scale, y, settings.macro_ridge_alpha)

    reliable = r2 >= settings.macro_min_r2

    # Fair value: compound the model's predicted returns from the start of the window and
    # compare with the price path the stock actually took.
    predicted = (X / scale) @ betas + y.mean()
    anchor_price = float(stock_frame["close"].iloc[-lookback])
    fair_value = anchor_price * float(np.exp(np.log1p(predicted).sum()))
    actual = float(stock_frame["close"].iloc[-1])
    gap_pct = (actual / fair_value - 1) * 100 if fair_value > 0 else None

    order = np.argsort(-np.abs(betas))[:3]
    drivers = [(factor_names[i], round(float(betas[i]), 4)) for i in order]

    return MacroGap(
        fair_value=round(fair_value, 2),
        gap_pct=round(gap_pct, 2) if gap_pct is not None else None,
        r_squared=round(float(r2), 3),
        reliable=bool(reliable),
        top_drivers=drivers,
    )
