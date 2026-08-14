"""Parsing and tiering. Network tests are marked and skipped when unreachable."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from asymmetry.data import DataTier
from asymmetry.data.nse_archive import _read_zipped_csv
from asymmetry.engines.indicators import normalise_rank, rolling_percentile, vwap

# A trimmed sample of the real UDiFF bhavcopy layout, including the non-EQ row that the
# parser must drop.
GOLDEN_CM_CSV = """TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
2026-08-12,2026-08-12,CM,NSE,STK,2885,INE002A01018,RELIANCE,EQ,,,,,RELIANCE,1323.90,1329.00,1309.20,1329.00,1328.50,1323.90,,,,,7901012,10400335523.90,129875,F1,1,,,,,
2026-08-12,2026-08-12,CM,NSE,STK,1594,INE009A01021,INFY,EQ,,,,,INFY,1450.00,1465.00,1445.00,1460.00,1459.00,1448.00,,,,,3000000,4380000000.00,50000,F1,1,,,,,
2026-08-12,2026-08-12,CM,NSE,ETF,9999,INE000A01000,NIFTYBEES,EQ,,,,,NIFTYBEES,250.00,251.00,249.00,250.50,250.40,250.00,,,,,100000,25050000.00,900,F1,1,,,,,
2026-08-12,2026-08-12,CM,NSE,STK,1111,INE111A01011,SOMEBOND,N1,,,,,SOMEBOND,100.00,101.00,99.00,100.50,100.40,100.00,,,,,500,50250.00,10,F1,1,,,,,
"""


def _zipped(csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("BhavCopy.csv", csv_text)
    return buffer.getvalue()


def test_bhavcopy_parses_and_filters_series():
    rows = _read_zipped_csv(_zipped(GOLDEN_CM_CSV))
    assert len(rows) == 4

    frame = pd.DataFrame(rows)
    equities = frame[frame["SctySrs"].str.strip() == "EQ"]
    # The N1-series bond must be excluded; the EQ-series ETF survives here and is removed
    # later by the NIFTY 500 universe filter.
    assert len(equities) == 3
    assert "SOMEBOND" not in set(equities["TckrSymb"])

    reliance = equities[equities["TckrSymb"] == "RELIANCE"].iloc[0]
    assert float(reliance["ClsPric"]) == pytest.approx(1329.00)
    assert float(reliance["TtlTradgVol"]) == pytest.approx(7901012)


def test_data_tier_ordering_picks_the_worst_tier():
    """The brief must report the weakest tier used, so min() ordering matters."""
    assert DataTier.LIVE > DataTier.ARCHIVE > DataTier.DELAYED > DataTier.UNAVAILABLE
    assert min([DataTier.LIVE, DataTier.DELAYED]) is DataTier.DELAYED
    assert "DELAYED" in DataTier.DELAYED.label


def test_normalise_rank_is_a_percentile():
    ranked = normalise_rank(pd.Series([10.0, 20.0, 30.0, 40.0]))
    assert ranked.iloc[0] < ranked.iloc[-1]
    assert ranked.max() == pytest.approx(100.0)


def test_normalise_rank_resists_outliers():
    """Ranking is used precisely so one outlier cannot compress the rest of the field."""
    ranked = normalise_rank(pd.Series([1.0, 2.0, 3.0, 4.0, 10_000.0]))
    assert ranked.iloc[3] - ranked.iloc[0] == pytest.approx(60.0)


def test_rolling_percentile_bounds():
    series = pd.Series(range(100), dtype=float)
    pct = rolling_percentile(series, 50)
    assert pct.dropna().between(0, 100).all()
    # A rising series ends at its own maximum.
    assert pct.iloc[-1] == pytest.approx(100.0)


def test_vwap_resets_each_session():
    """VWAP is a session reference; carrying it across days makes intraday stops wrong."""
    index = pd.to_datetime(
        [
            "2026-08-11 09:15", "2026-08-11 09:20",
            "2026-08-12 09:15", "2026-08-12 09:20",
        ]
    )
    frame = pd.DataFrame(
        {
            "high": [100.0, 102.0, 200.0, 202.0],
            "low": [100.0, 102.0, 200.0, 202.0],
            "close": [100.0, 102.0, 200.0, 202.0],
            "volume": [1000.0, 1000.0, 1000.0, 1000.0],
        },
        index=index,
    )
    result = vwap(frame)
    assert result.iloc[1] == pytest.approx(101.0)
    # Day two starts fresh rather than blending in day one.
    assert result.iloc[2] == pytest.approx(200.0)
    assert result.iloc[3] == pytest.approx(201.0)


@pytest.mark.network
def test_archive_universe_is_reachable():
    from asymmetry.data.nse_archive import fetch_index_constituents

    frame = fetch_index_constituents("nifty50")
    if frame is None:
        pytest.skip("NSE archives unreachable from this environment")
    assert len(frame) >= 45
    assert {"symbol", "company", "sector", "isin"} <= set(frame.columns)


def test_session_tier_ignores_failed_fetches():
    """One optional miss must not relabel a whole run as UNAVAILABLE.

    A spec scan reported "Data tier: UNAVAILABLE" because that day's option chain was not
    yet published, even though every price had come from the archive — which tells the
    reader their brief was built on nothing.
    """
    from asymmetry.data.provider import MarketData

    data = MarketData()
    data._record(DataTier.ARCHIVE)
    data._record(DataTier.UNAVAILABLE)   # optional fetch that failed
    data._record(DataTier.DELAYED)

    assert data.session_tier is DataTier.DELAYED
    assert data.failed_fetches == 1


def test_session_tier_is_unavailable_only_when_nothing_served():
    from asymmetry.data.provider import MarketData

    data = MarketData()
    assert data.session_tier is DataTier.UNAVAILABLE
    data._record(DataTier.UNAVAILABLE)
    assert data.session_tier is DataTier.UNAVAILABLE
