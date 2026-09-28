from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pandas as pd

from asymmetry.config import settings
from asymmetry.universe_snapshot import freeze, load, sample, save


def test_freeze_is_point_in_time(monkeypatch, tmp_path):
    cutoff = date(2026, 1, 30)
    rows = []
    for offset in range(25):
        day = cutoff - timedelta(days=24 - offset)
        rows.extend([
            {"date": day, "symbol": "ALWAYS", "turnover": settings.min_median_turnover_inr * 2,
             "volume": settings.min_median_volume * 2, "close": 100},
            {"date": day, "symbol": "LATE", "turnover": 1, "volume": 1, "close": 100},
        ])
    for offset in range(1, 25):
        rows.append({"date": cutoff + timedelta(days=offset), "symbol": "LATE",
                     "turnover": settings.min_median_turnover_inr * 10,
                     "volume": settings.min_median_volume * 10, "close": 100})
    monkeypatch.setattr("asymmetry.storage.load_history", lambda **_: pd.DataFrame(rows))

    symbols = freeze(cutoff, lookback=20)
    assert symbols == ["ALWAYS"]
    path = tmp_path / "universe.json"
    save(symbols, path)
    assert load(path) == ["ALWAYS"]


def test_sample_is_uniform_seeded_and_non_mutating():
    symbols = [f"S{i}" for i in range(20)]
    assert sample(symbols, 5, 7) == sample(symbols, 5, 7)
    assert len(sample(symbols, 5, 7)) == 5
    assert symbols == [f"S{i}" for i in range(20)]


def test_explicit_symbols_bypass_hindsight_stage_one(monkeypatch):
    import asymmetry.v3_backtest as module

    dates = pd.date_range("2025-01-01", periods=120, freq="B")
    history = pd.concat([
        pd.DataFrame({"date": dates.date, "symbol": symbol, "high": 101.0, "low": 99.0,
                      "close": 100.0, "volume": 1000, "turnover": 100000})
        for symbol in ("AAA", "BBB")
    ], ignore_index=True)
    tested = []
    monkeypatch.setattr("asymmetry.storage.load_history", lambda **_: history)
    monkeypatch.setattr("asymmetry.data.nse_archive.last_trading_day", lambda: date(2026, 1, 1))
    monkeypatch.setattr("asymmetry.engines.v3_scan.stage_one",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("hindsight")))
    monkeypatch.setattr(module, "CatalystHistory", lambda **_: SimpleNamespace(empty=True))
    monkeypatch.setattr(module, "backtest_symbol",
                        lambda symbol, *_a, **_k: tested.append(symbol) or [])

    result = module.run_v3_backtest(symbols=["BBB", "AAA"])
    assert tested == ["BBB", "AAA"]
    assert result.symbols_tested == 2


def test_v3_cost_is_per_trade_and_typical_basis_remains():
    from asymmetry.v3_backtest import BacktestResult, Trade

    def trade(stop_pct):
        return Trade("X", "long", "reclaim", pd.Timestamp("2026-01-01"),
                     100, 99, 104, stop_pct, outcome="target", realised_r=4)

    tight, wide = trade(0.5), trade(1.5)
    result = BacktestResult([tight, wide])
    assert tight.cost_r == 3 * wide.cost_r
    expected_typical = (settings.cost_roundtrip_pct + settings.slippage_pct) / 1.0
    assert result.typical_cost_r == expected_typical
    assert result.net_expectancy_r == (tight.net_r + wide.net_r) / 2
