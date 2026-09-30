import io, json, zipfile
from datetime import date, datetime, timezone
import pytest
from fno_momentum import nse_archives as na
from fno_momentum.ledger import ImmutableLedger
from fno_momentum.source_registry import SourceRegistry, SourceManifest

OLD = ('SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,\n'
       'TCS,EQ,100,110,95,105,104,99,1000,105000,12-JAN-2005,50,INE467B01029,\n'
       'TCS,BE,1,1,1,1,1,1,1,1,12-JAN-2005,1,INE467B01029,\n'
       'OTHER,EQ,1,2,1,2,2,1,5,10,12-JAN-2005,1,INE000000000,\n')
NEW = ('TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,'
       'OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,'
       'TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4\n'
       '2024-08-30,2024-08-30,CM,NSE,STK,1,INE669E01016,IDEA,EQ,,,,,VODAFONE IDEA,16,16.5,15.8,16.2,16.2,15.9,,16.2,,,900000,14580000,4000,F1,1,,,,,\n')


def _zip(name, text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z: z.writestr(name, text)
    return b.getvalue()


def test_url_switches_to_udiff_on_the_cutover_date():
    assert na.bhavcopy_urls(date(2005, 1, 12))[0].endswith('/content/historical/EQUITIES/2005/JAN/cm12JAN2005bhav.csv.zip')
    assert na.bhavcopy_urls(date(2024, 8, 30))[0].endswith('/content/cm/BhavCopy_NSE_CM_0_0_0_20240830_F_0000.csv.zip')
    # Near the cutover both formats are tried, primary first.
    assert len(na.bhavcopy_urls(na.UDIFF_FROM)) == 2


def test_both_formats_parse_to_one_schema_and_select_by_isin_or_symbol():
    wanted = {'TCS': 'INE467B01029', 'IDEA': 'INE669E01016'}
    day, total, rows = na.parse_bhavcopy(_zip('cm12JAN2005bhav.csv', OLD), wanted)
    assert day == '2005-01-12' and total == 3
    # Every series is kept: a stock moved to BE (trade-for-trade) has no EQ row that day.
    assert [r['series'] for r in rows] == ['EQ', 'BE']
    assert rows[:1] == [{'symbol': 'TCS', 'isin': 'INE467B01029', 'series': 'EQ', 'open': 100.0, 'high': 110.0, 'low': 95.0,
                     'close': 105.0, 'last': 104.0, 'prev_close': 99.0, 'volume': 1000, 'turnover': 105000.0, 'trades': 50,
                     'match': 'isin+symbol'}]
    day, total, rows = na.parse_bhavcopy(_zip('x.csv', NEW), wanted)
    assert day == '2024-08-30' and rows[0]['volume'] == 900000 and rows[0]['close'] == 16.2


def test_legacy_two_digit_year_is_read():
    # NSE's 13 Jul 2020 file stamps its rows '13-Jul-20'.
    text = OLD.replace('12-JAN-2005', '13-Jul-20')
    assert na.parse_bhavcopy(_zip('cm13JUL2020bhav.csv', text), {'TCS': 'INE467B01029'})[0] == '2020-07-13'


def test_fetch_outcomes_are_distinct():
    assert na.classify_response(200, b'PK..') == 'PUBLISHED'
    assert na.classify_response(404, b'') == 'NOT_PUBLISHED'
    for status in (500, 503, None):
        assert na.classify_response(status, b'') == 'SOURCE_FAILURE'
    # A 200 that is not a zip is an error page, never an empty trading day.
    assert na.classify_response(200, b'<html>') == 'SOURCE_FAILURE'


def test_blocking_statuses_halt_the_run():
    for status in (401, 403, 429):
        assert na.halts(status)
    assert not na.halts(404)


def test_collector_refuses_without_active_approval(tmp_path):
    with pytest.raises(PermissionError):
        na.require_approved(tmp_path / 'events.sqlite')


def test_only_transient_failures_are_retried_and_attempts_are_capped(tmp_path):
    _approve(tmp_path)
    c = na.Collector(tmp_path)
    with c.catalog.db:
        c.catalog.db.executemany('INSERT INTO days(day,status,http_status,attempts) VALUES(?,?,?,?)', [
            ('2000-01-01', 'SOURCE_FAILURE', None, 1), ('2000-01-02', 'SOURCE_FAILURE', 503, 2),
            ('2000-01-03', 'SOURCE_FAILURE', None, na.MAX_ATTEMPTS), ('2000-01-04', 'SOURCE_FAILURE', 400, 1)])
    assert sorted(na.retry_transient_failures(c)) == ['2000-01-01', '2000-01-02']
    assert [e.event_type for e in c.ledger.events('bhavcopy:2000-01-01')] == ['day_retry_authorized']


def _approve(tmp_path):
    ledger = ImmutableLedger(tmp_path / 'events.sqlite')
    d = json.loads(na.MANIFEST.read_bytes()); d['fields'] = tuple(d['fields'])
    m = SourceManifest(**d); reg = SourceRegistry(ledger); now = datetime.now(timezone.utc)
    h = reg.propose(m, actor_id='op', at=now)
    reg.approve(m.source_id, h, actor_id='aditya-lakhotia', rationale='test', at=now)
    reg.activate(m.source_id, h, actor_id='aditya-lakhotia', at=now)
    return h


def test_collector_accepts_the_exact_approved_manifest(tmp_path):
    ledger = ImmutableLedger(tmp_path / 'events.sqlite')
    d = json.loads(na.MANIFEST.read_bytes()); d['fields'] = tuple(d['fields'])
    m = SourceManifest(**d); reg = SourceRegistry(ledger); now = datetime.now(timezone.utc)
    h = reg.propose(m, actor_id='op', at=now)
    reg.approve(m.source_id, h, actor_id='aditya-lakhotia', rationale='test', at=now)
    reg.activate(m.source_id, h, actor_id='aditya-lakhotia', at=now)
    assert na.require_approved(tmp_path / 'events.sqlite') == h


def test_every_calendar_day_is_planned_including_weekends(tmp_path):
    cat = na.Catalog(tmp_path / 'c.sqlite')
    cat.plan(date(2024, 1, 19), date(2024, 1, 22))
    days = [r[0] for r in cat.db.execute('select day from days order by day')]
    assert days == ['2024-01-19', '2024-01-20', '2024-01-21', '2024-01-22']
    assert {r[0] for r in cat.db.execute('select status from days')} == {'UNFETCHED'}
