import gzip, hashlib, json
from datetime import date
from fno_momentum.historical_db import HistoryDB
from fno_momentum.data_view import classify, merge_gaps, composite_hash, build, chart_time


def _sha(b): return hashlib.sha256(b).hexdigest()


def test_status_never_promotes_an_interval_with_exclusions():
    clean={'valid_rows':10,'hash_errors':[],'invalid_rows':0,'collapsed_duplicates':0,'missing_starts':0,'unavailable_windows':0,'failed_windows':0,'preopen_rows':0}
    assert classify(clean)[0]=='USABLE'
    # A collapsed identical copy loses no data: noted, not downgraded.
    status,reasons,notes=classify({**clean,'collapsed_duplicates':1})
    assert status=='USABLE' and notes and not reasons
    for key in ('invalid_rows','missing_starts','unavailable_windows','failed_windows'):
        status,reasons,_=classify({**clean,key:3})
        assert status=='PARTIALLY USABLE' and any('3' in r for r in reasons)
    assert classify({**clean,'valid_rows':0})[0]=='BLOCKED'
    assert classify({**clean,'hash_errors':['x']})[0]=='BLOCKED'


def test_gaps_are_empty_slots_never_values():
    rows=[['2022-03-07T09:25:00+05:30',1,2,1,2,5],['2022-03-07T09:35:00+05:30',2,3,2,3,6]]
    s=merge_gaps(rows,'5m',[('2022-03-07T09:30:00+05:30','missing','absent start')])
    assert s['t']==sorted(s['t']) and len(s['t'])==3
    i=s['t'].index(chart_time('2022-03-07T09:30:00+05:30','5m'))
    assert s['o'][i] is None and s['c'][i] is None and s['v'][i] is None
    assert s['marks'][0]['k']=='missing'
    # A gap may never overwrite an observed bar.
    s=merge_gaps(rows,'5m',[('2022-03-07T09:25:00+05:30','quarantined','x')])
    assert len(s['t'])==2 and s['o'][0]==1


def test_basis_check_flags_years_where_intraday_and_daily_prices_disagree():
    from fno_momentum.data_view import basis_by_year
    daily = [['2022-01-03T00:00:00+05:30', 100, 1, 1, 1, 1], ['2025-01-02T00:00:00+05:30', 100, 1, 1, 1, 1]]
    intraday = [['2022-01-03T09:15:00+05:30', 96.7, 1, 1, 1, 1], ['2022-01-03T09:30:00+05:30', 50, 1, 1, 1, 1],
                ['2025-01-02T09:15:00+05:30', 100, 1, 1, 1, 1]]
    years = basis_by_year(daily, intraday)
    assert years == {'2022': {'median_ratio': 0.967, 'days': 1, 'differs': True},
                     '2025': {'median_ratio': 1.0, 'days': 1, 'differs': False}}


def test_intraday_chart_time_is_ist_wall_clock():
    assert chart_time('2022-03-07T09:15:00+05:30','15m')%86400==9*3600+15*60
    assert chart_time('2015-12-31T00:00:00+05:30','daily')=='2015-12-31'


def test_composite_hash_is_order_independent_and_marks_absence():
    assert composite_hash([('a','1'),('b','2')])==composite_hash([('b','2'),('a','1')])
    assert composite_hash([('a',None)])!=composite_hash([('a','')])


def test_every_quality_schema_reports_its_gaps_and_unknown_fields_block():
    from fno_momentum.data_view import quality_lists
    # Parser v1 wrote missing starts under a different key; reading only the v4 key
    # hid all 24 real five-minute gaps on the first build.
    v1={'issues':[],'missing_bar_starts_against_regular_session_assumption':['2022-03-07T09:30:00+05:30']}
    v4={'issues':[],'missing_bar_starts_against_known_or_assumed_session':['x'],'provider_preopen_rows_outside_continuous_session':['y']}
    assert quality_lists(v1)==(['2022-03-07T09:30:00+05:30'],[],[])
    assert quality_lists(v4)==(['x'],['y'],[])
    assert quality_lists({'issues':[],'bars_we_have_not_seen_before':['z']})[2]==['bars_we_have_not_seen_before']


def _fixture(tmp_path):
    db=HistoryDB(tmp_path/'catalog.sqlite')
    member={'symbol':'ABC','underlying_key':'NSE_EQ|X'}
    db.plan([member],date(2022,1,5),'ref'); db.set_scope([member],'manifest','ref')
    rows=[['2022-01-03T09:15:00+05:30',10,11,9,10.5,100],['2022-01-03T09:25:00+05:30',10.5,11,10,10.8,90]]
    body=json.dumps({'status':'success','data':{'candles':rows}}).encode()
    raw=tmp_path/'raw.gz'; raw.write_bytes(gzip.compress(body))
    content=json.dumps({'parser_version':'v-test','rows':rows}).encode()
    norm=tmp_path/'norm.gz'; norm.write_bytes(gzip.compress(content))
    quality={'issues':[],'missing_bar_starts_against_known_or_assumed_session':['2022-01-03T09:20:00+05:30'],'provider_preopen_rows_outside_continuous_session':[]}
    q=json.dumps(quality,sort_keys=True,separators=(',',':')).encode(); qp=tmp_path/f'{_sha(q)}.json.gz'; qp.write_bytes(gzip.compress(q))
    w=db.db.execute("select id from windows where interval='5m'").fetchone()[0]
    db.update(w,status='FETCHED',returned=2,valid=2,normalized_path=str(norm),normalized_hash=_sha(content),raw_path=str(raw),raw_hash=_sha(body),quality_path=str(qp),first_bar=rows[0][0],last_bar=rows[-1][0])
    db.db.execute("insert into sessions values(?,?,?,?,?,?)",(w,'2022-01-03',2,3,1,'ASSUMED_REGULAR_SESSION_NOT_OFFICIAL_CALENDAR'))
    db.db.commit()
    db.db.close()
    return tmp_path/'catalog.sqlite', norm


def test_build_is_read_only_and_shows_gaps(tmp_path):
    catalog,_=_fixture(tmp_path)
    before=catalog.read_bytes()
    summary=build(catalog,tmp_path/'absent-ledger.sqlite',tmp_path/'view',symbols=['ABC'])
    assert catalog.read_bytes()==before
    iv=summary['stocks'][0]['intervals']['5m']
    assert iv['status']=='PARTIALLY USABLE' and iv['missing_starts']==1
    assert summary['stocks'][0]['weekly']['status']=='UNAVAILABLE_CALENDAR'
    # Planned daily/15m windows that were never fetched are failures, not "no data".
    assert summary['stocks'][0]['intervals']['daily']['status']=='BLOCKED'
    page=(tmp_path/'view'/'stock'/'ABC.html').read_text(encoding='utf-8')
    assert '2022-01-03 09:20' in page and 'UNAVAILABLE_CALENDAR' in page
    assert summary['ledger']['status']=='ABSENT'


def test_corrupted_partition_blocks_the_interval(tmp_path):
    catalog,norm=_fixture(tmp_path)
    norm.write_bytes(gzip.compress(b'{"rows":[]}'))
    summary=build(catalog,tmp_path/'absent.sqlite',tmp_path/'view',symbols=['ABC'])
    iv=summary['stocks'][0]['intervals']['5m']
    assert iv['status']=='BLOCKED' and any('hash' in e for e in iv['hash_errors'])


def _nse(tmp_path, day_status):
    import sqlite3
    from fno_momentum.nse_archives import Catalog
    cat = Catalog(tmp_path / 'nse.sqlite')
    with cat.db:
        cat.db.execute('INSERT INTO meta VALUES(?,?)', ('wanted', json.dumps({'ABC': 'X'})))
        for day, status in day_status.items():
            cat.db.execute('INSERT INTO days(day,status) VALUES(?,?)', (day, status))
    cat.db.close()
    return tmp_path / 'nse.sqlite'


def test_nse_calendar_conflict_and_unresolved_days_downgrade(tmp_path):
    catalog, _ = _fixture(tmp_path)
    nse = _nse(tmp_path, {'2022-01-03': 'NOT_PUBLISHED'})
    iv = build(catalog, tmp_path / 'a.sqlite', tmp_path / 'v1', symbols=['ABC'], nse_catalog=nse)['stocks'][0]['intervals']['5m']
    assert any('conflict' in r for r in iv['reasons'])
    (tmp_path / 'b').mkdir()
    nse2 = _nse(tmp_path / 'b', {})          # the calendar has not resolved the day at all
    iv = build(catalog, tmp_path / 'a.sqlite', tmp_path / 'v2', symbols=['ABC'], nse_catalog=nse2)['stocks'][0]['intervals']['5m']
    assert any('unresolved' in r for r in iv['reasons']) and iv['status'] != 'USABLE'


def test_catalog_session_gap_count_must_agree_with_quality_records(tmp_path):
    catalog,_=_fixture(tmp_path)
    db=HistoryDB(catalog)
    w=db.db.execute("select id from windows where interval='5m'").fetchone()[0]
    db.db.execute("insert or replace into sessions values(?,?,?,?,?,?)",(w,'2022-01-03',2,5,3,'ASSUMED_REGULAR_SESSION_NOT_OFFICIAL_CALENDAR'))
    db.db.commit(); db.db.close()
    iv=build(catalog,tmp_path/'absent.sqlite',tmp_path/'view',symbols=['ABC'])['stocks'][0]['intervals']['5m']
    assert iv['status']=='BLOCKED' and any('sessions table' in e for e in iv['hash_errors'])
