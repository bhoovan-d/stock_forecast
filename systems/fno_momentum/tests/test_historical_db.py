from datetime import date
import math
import pytest
from fno_momentum.historical_db import validate_rows, request_windows, weekly_from_daily, exclusive_collector, HistoryDB


def test_windows_cover_range_without_overlap():
    w=list(request_windows('5m',date(2022,1,1),date(2026,9,25)))
    assert w[0][0]==date(2022,1,1) and w[-1][1]==date(2026,9,25)
    assert all((b-a).days<28 for a,b in w)
    assert sum((b-a).days+1 for a,b in w)==(date(2026,9,25)-date(2022,1,1)).days+1


def test_invalid_sentinel_duplicate_and_nan_are_quarantined():
    good=['2026-09-24T09:15:00+05:30',100,102,99,101,10,0]
    rows=[good,good,['1970-01-01T00:00:00Z',1,2,1,2,3],['2026-09-24T09:20:00+05:30',100,float('nan'),99,101,10],['2026-09-24T09:22:00+05:30',100,102,99,101,10]]
    valid,issues=validate_rows(rows,'5m',date(2026,9,24),date(2026,9,24))
    assert valid==[]
    assert {'duplicate_timestamp','invalid_timestamp','invalid_ohlcv','off_grid_timestamp'} <= {x['reason'] for x in issues}


def test_no_weekly_without_calendar_or_with_missing_daily():
    rows=[['2026-09-21T00:00:00+05:30',1,3,1,2,10]]
    assert weekly_from_daily(rows,None)['status']=='UNAVAILABLE_CALENDAR'
    result=weekly_from_daily(rows,['2026-09-21','2026-09-22'])
    assert result['rows']==[] and result['incomplete_weeks']


def test_weekly_uses_approved_monday_boundary_and_sources():
    rows=[['2026-09-21T00:00:00+05:30',1,3,1,2,10],['2026-09-22T00:00:00+05:30',2,4,2,3,20]]
    result=weekly_from_daily(rows,['2026-09-21','2026-09-22'])
    assert result['rows'][0][:6]==['2026-09-21',1,4,1,3,30]


def test_locks_prevent_overlapping_collectors(tmp_path):
    with exclusive_collector(tmp_path):
        with pytest.raises(RuntimeError,match='already'):
            with exclusive_collector(tmp_path):
                pass


def test_unfetched_is_not_missing_and_interruptions_are_visible(tmp_path):
    db=HistoryDB(tmp_path/'catalog.sqlite')
    db.plan([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],date(2026,9,25),'refhash')
    db.set_scope([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],'manifest','refhash')
    assert db.db.execute("select count(*) from windows where status='UNFETCHED'").fetchone()[0]>0
    first=db.next_window()
    db.begin(first['id'])
    assert db.recover_interrupted()==1
    assert db.db.execute("select status from windows where id=?",(first['id'],)).fetchone()[0]=='INTERRUPTED'

def test_query_detects_corrupted_partition(tmp_path):
    import gzip, json
    db=HistoryDB(tmp_path/'catalog.sqlite')
    db.plan([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],date(2026,9,25),'r')
    db.set_scope([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],'manifest','r')
    w=db.next_window(); path=tmp_path/'bad.gz'
    path.write_bytes(gzip.compress(b'{"rows":[]}'))
    db.update(w['id'],normalized_path=str(path),normalized_hash='wrong',status='FETCHED')
    with pytest.raises(RuntimeError,match='hash mismatch'):
        list(db.rows('NSE_EQ|X','daily','2000-01-01','2026-09-25'))


def test_ingest_preserves_invalid_and_duplicate_quality(tmp_path):
    import gzip,json
    from fno_momentum.historical_db import HistoricalCollector,ingest,MANIFEST
    manifest=json.loads(MANIFEST.read_text()); manifest['storage']['root']=str(tmp_path/'store')
    p=tmp_path/'manifest.json'; p.write_text(json.dumps(manifest)); c=HistoricalCollector(p)
    db=HistoryDB(c.root/'catalog.sqlite'); db.plan([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],date(2026,9,25),'r')
    db.set_scope([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],c.manifest_hash,'r')
    w=db.next_window(); row=['2001-01-02T00:00:00+05:30',10,12,9,11,100]
    ingest(c,db,w,[row,row,['1970-01-01T00:00:00Z',1,2,1,2,10]],{'raw_sha256':'raw','raw_path':'stored-source'})
    result=dict(db.db.execute('select * from windows where id=?',(w['id'],)).fetchone())
    assert result['status']=='PARTIAL' and result['valid']==0 and result['duplicates']==2
    quality=json.loads(gzip.decompress(__import__('pathlib').Path(result['quality_path']).read_bytes()))
    assert len(quality['issues'])==3
    c.ledger.verify()

def test_storage_measure_counts_nested_files_and_ledger(tmp_path):
    from fno_momentum.full_underlying_collection import used_bytes
    (tmp_path/'raw').mkdir(); (tmp_path/'raw'/'x').write_bytes(b'abc')
    (tmp_path/'events.sqlite').write_bytes(b'12345')
    assert used_bytes(tmp_path)==8

def test_http_auth_failure_blocks_subsequent_batches_without_retry(tmp_path,monkeypatch):
    import json
    import fno_momentum.historical_db as h
    manifest=json.loads(h.MANIFEST.read_text()); manifest['storage']['root']=str(tmp_path/'store')
    path=tmp_path/'manifest.json'; path.write_text(json.dumps(manifest)); c=h.HistoricalCollector(path)
    db=h.HistoryDB(c.root/'catalog.sqlite'); db.plan([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],date(2026,9,25),'r')
    manifest['scope']['historical_selected_symbols']=['ABC']
    path.write_text(json.dumps(manifest)); c=h.HistoricalCollector(path)
    db.set_scope([{'symbol':'ABC','underlying_key':'NSE_EQ|X'}],c.manifest_hash,'r')
    called=[]
    def fail(*args,**kw):
        called.append(1); raise RuntimeError('provider_http_401; manual review required')
    monkeypatch.setattr(c,'fetch',fail); monkeypatch.setattr(h,'set_system_awake',lambda value:True)
    with pytest.raises(RuntimeError,match='401'): h.run(c,db,2)
    with pytest.raises(RuntimeError,match='blocked'): h.run(c,db,2)
    assert len(called)==1
    assert db.db.execute("select count(*) from windows where status='SOURCE_FAILURE'").fetchone()[0]==1
    c.ledger.verify()


def test_malformed_json_http_body_is_preserved(tmp_path,monkeypatch):
    import json,gzip
    from pathlib import Path
    import fno_momentum.historical_db as h
    manifest=json.loads(h.MANIFEST.read_text()); manifest['storage']['root']=str(tmp_path/'store')
    path=tmp_path/'manifest.json'; path.write_text(json.dumps(manifest)); c=h.HistoricalCollector(path); c.token='test-only'
    class Response:
        status=200
        def read(self,*args): return b'{malformed'
        def __enter__(self): return self
        def __exit__(self,*args): pass
    class Opener:
        def open(self,*args,**kw): return Response()
    monkeypatch.setattr(h.urllib.request,'build_opener',lambda *a:Opener())
    assert c.fetch('https://api.upstox.com/v3/historical-candle/NSE_EQ%7CINE002A01018/days/1/2020-01-02/2020-01-01','test',historical=True) is None
    e=c.ledger.events('test'); raw=next(x for x in e if x.event_type=='raw_response_captured')
    assert gzip.decompress(Path(raw.payload['raw_path']).read_bytes())==b'{malformed'
    assert any(x.event_type=='source_failure' for x in e)

def test_daily_candles_keep_provider_midnight_or_market_open_timestamp():
    rows=[['2018-11-29T09:15:00+05:30',100,102,99,101,10],['2018-12-03T00:00:00+05:30',100,102,99,101,10]]
    valid,issues=validate_rows(rows,'daily',date(2018,1,1),date(2018,12,31))
    assert len(valid)==2 and issues==[]


def test_daily_duplicate_session_with_different_times_is_quarantined():
    rows=[['2018-11-29T09:15:00+05:30',100,102,99,101,10],['2018-11-29T00:00:00+05:30',100,102,99,101,10]]
    valid,issues=validate_rows(rows,'daily',date(2018,1,1),date(2018,12,31))
    assert valid==[] and all(x['reason']=='duplicate_timestamp' for x in issues)


def test_identical_2015_provider_daily_duplicate_has_one_canonical_bar():
    rows=[['2015-12-31T09:15:00+05:30',26,26.1,25.7,26,44466900,0],
          ['2015-12-31T00:00:00+05:30',26,26.1,25.7,26,44466900,0]]
    valid,issues=validate_rows(rows,'daily',date(2015,1,1),date(2015,12,31),'TATASTEEL')
    assert len(valid)==1 and valid[0][0]=='2015-12-31T00:00:00+05:30'
    assert [x['reason'] for x in issues]==['identical_daily_duplicate_collapsed']


def test_documented_muhurat_candles_use_session_calendar():
    from fno_momentum.historical_db import expected_session_starts
    cases=[('2022-10-24','18:15:00',4,12),('2023-11-12','18:15:00',4,12),
           ('2024-11-01','18:00:00',4,12),('2025-10-21','13:45:00',4,12)]
    for day,start,n15,n5 in cases:
        for interval,n in [('15m',n15),('5m',n5)]:
            stamp=f'{day}T{start}+05:30'
            rows=[[stamp,100,102,99,101,10]]
            valid,issues=validate_rows(rows,interval,date.fromisoformat(day),date.fromisoformat(day),'TATASTEEL')
            assert len(valid)==1 and issues==[]
            starts=expected_session_starts(day,interval,'TATASTEEL')
            assert len(starts)==n and starts[0]==stamp


def test_documented_special_preopens_shift_expected_start_for_one_stock():
    from fno_momentum.historical_db import expected_session_starts
    assert expected_session_starts('2022-10-27','5m','NMDC')[0]=='2022-10-27T10:00:00+05:30'
    assert expected_session_starts('2026-04-30','15m','VEDL')[0]=='2026-04-30T10:00:00+05:30'
    assert expected_session_starts('2022-10-27','5m','TATASTEEL')[0]=='2022-10-27T09:15:00+05:30'


def test_provider_preopen_placeholder_is_retained_but_not_expected_as_continuous_trading():
    from fno_momentum.historical_db import expected_session_starts
    day=date(2022,10,27)
    row=['2022-10-27T09:15:00+05:30',27.88,27.88,27.88,27.88,0]
    valid,issues=validate_rows([row],'15m',day,day,'NMDC')
    assert len(valid)==1 and issues==[]
    assert row[0] not in expected_session_starts(str(day),'15m','NMDC')


def test_documented_weekend_sessions_have_exact_expected_windows():
    from fno_momentum.historical_db import expected_session_starts
    for day,n in [('2024-01-20',25),('2024-03-02',7),('2024-05-18',7),
                  ('2025-02-01',25),('2026-02-01',25)]:
        starts=expected_session_starts(day,'15m','TATASTEEL')
        assert len(starts)==n
        if n==7:
            assert f'{day}T10:00:00+05:30' not in starts
            assert f'{day}T11:30:00+05:30' in starts
    valid,issues=validate_rows([['2024-03-02T10:15:00+05:30',1,2,1,2,10]],
                                '15m',date(2024,3,2),date(2024,3,2),'TATASTEEL')
    assert valid==[] and issues[0]['reason']=='off_grid_timestamp'


def test_reclassification_targets_only_selected_exception_windows(tmp_path):
    from fno_momentum.historical_db import reclassification_targets
    db=HistoryDB(tmp_path/'catalog.sqlite')
    members=[{'symbol':'TATASTEEL','underlying_key':'NSE_EQ|A'},
             {'symbol':'OTHER','underlying_key':'NSE_EQ|B'}]
    db.plan(members,date(2026,9,25),'reference')
    db.set_scope(members[:1],'manifest','reference')
    targets=reclassification_targets(db)
    assert all(w['symbol']=='TATASTEEL' and w['interval']!='daily' for w in targets)
    assert len(targets)==18  # four Muhurat plus five weekend dates, two intervals each


def test_catalog_scope_excludes_old_unselected_windows(tmp_path):
    db=HistoryDB(tmp_path/'catalog.sqlite')
    members=[{'symbol':'ABC','underlying_key':'NSE_EQ|A'},{'symbol':'OTHER','underlying_key':'NSE_EQ|B'}]
    db.plan(members,date(2026,9,25),'ref')
    assert db.next_window() is None
    db.set_scope(members[:1],'manifest','ref')
    assert db.next_window()['symbol']=='ABC'
    assert db.db.execute('SELECT count(*) FROM windows WHERE symbol=?',('OTHER',)).fetchone()[0]>0
