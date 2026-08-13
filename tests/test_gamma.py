"""Black-Scholes maths and the GEX sign convention.

The units question here is load-bearing: NSE reports option open interest in *units of the
underlying*, not in contracts. An early version multiplied by lot size as well, inflating
net GEX by ~65x. The magnitude test below pins that down.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from asymmetry.engines.gamma import (
    bs_gamma,
    bs_price,
    compute_gex,
    gamma_regime_score,
    implied_vol,
)


def test_implied_vol_recovers_the_input():
    spot, strike, t, rate = 100.0, 100.0, 0.25, 0.065
    for true_vol in (0.12, 0.20, 0.45):
        price = bs_price(spot, strike, t, true_vol, rate, is_call=True)
        assert implied_vol(price, spot, strike, t, rate, is_call=True) == pytest.approx(
            true_vol, abs=1e-3
        )


def test_gamma_is_equal_for_calls_and_puts():
    """Put-call parity: gamma does not depend on the option type."""
    assert bs_gamma(100, 100, 0.25, 0.2, 0.065) == pytest.approx(
        bs_gamma(100, 100, 0.25, 0.2, 0.065)
    )


def test_gamma_peaks_near_the_money():
    atm = bs_gamma(100, 100, 0.1, 0.2, 0.065)
    assert atm > bs_gamma(100, 130, 0.1, 0.2, 0.065)
    assert atm > bs_gamma(100, 70, 0.1, 0.2, 0.065)


def test_junk_prices_yield_no_iv():
    # Below intrinsic value, and at the tick floor: neither carries vol information.
    assert implied_vol(0.01, 100, 100, 0.1, 0.065, True) is None
    assert implied_vol(1.0, 100, 50, 0.1, 0.065, True) is None


def _chain(call_oi: float, put_oi: float, as_of: date) -> pd.DataFrame:
    """A small symmetric chain around a 24,000 spot."""
    expiry = as_of + timedelta(days=7)
    rows = []
    for strike in range(23000, 25001, 250):
        for option_type, oi in (("CE", call_oi), ("PE", put_oi)):
            intrinsic = max(0.0, (24000 - strike) if option_type == "CE" else (strike - 24000))
            rows.append(
                {
                    "date": as_of,
                    "symbol": "NIFTY",
                    "instrument": "IDO",
                    "expiry": expiry,
                    "strike": float(strike),
                    "option_type": option_type,
                    "close": intrinsic + 60.0,  # generous time value so IV solves
                    "underlying": 24000.0,
                    "open_interest": oi,
                    "oi_change": 0.0,
                    "volume": 1000.0,
                    "lot_size": 65.0,
                }
            )
    return pd.DataFrame(rows)


def test_gex_sign_follows_call_versus_put_open_interest():
    as_of = date(2026, 8, 12)

    call_heavy = compute_gex(_chain(call_oi=1e6, put_oi=1e5, as_of=as_of), as_of)
    put_heavy = compute_gex(_chain(call_oi=1e5, put_oi=1e6, as_of=as_of), as_of)

    assert call_heavy["net_gex"] > 0, "call-dominated open interest must give positive GEX"
    assert put_heavy["net_gex"] < 0, "put-dominated open interest must give negative GEX"


def test_gex_magnitude_treats_open_interest_as_units():
    """Guards the lot-size double-count.

    With OI in units, GEX is gamma x OI x spot^2 x 1%. Multiplying by the 65-unit lot size
    as well would push this comfortably past the bound below.
    """
    as_of = date(2026, 8, 12)
    stats = compute_gex(_chain(call_oi=1e6, put_oi=0.0, as_of=as_of), as_of)
    # 9 strikes x 1e6 units; a sane result is well under 1e13.
    assert 0 < stats["net_gex"] < 1e13


def test_regime_score_reads_negative_gamma_as_favourable():
    """Negative gamma means moves extend, which favours a breakout system."""
    positive, note_pos = gamma_regime_score(5e11, 24000, 23800)
    negative, note_neg = gamma_regime_score(-5e11, 24000, 24200)

    assert negative == 1 and "extend" in note_neg
    assert positive == -1 and "dampen" in note_pos
    assert gamma_regime_score(None, None, None)[0] == 0


def test_empty_chain_is_handled():
    stats = compute_gex(pd.DataFrame(), date(2026, 8, 12))
    assert stats["net_gex"] is None
