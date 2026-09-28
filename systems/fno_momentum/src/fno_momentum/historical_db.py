"""Audited historical OHLCV partition database; no strategy functionality."""
from __future__ import annotations
import argparse, calendar, csv, gzip, hashlib, io, json, math, msvcrt, sqlite3, statistics, time, urllib.parse, urllib.request
from collections import Counter, defaultdict
from contextlib import contextmanager, ExitStack
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError
from .full_underlying_collection import Collector, StorageLimitError, IST, PROPOSALS, utc, digest, used_bytes, set_system_awake
from .upstox import UpstoxEndpointPolicy, _NoRedirectHandler, INSTRUMENTS_URL
from .secret_store import read_upstox_analytics_token

VERSION='fno-historical-db-v4'
MANIFEST=PROPOSALS/'fno_full_underlying_data_collection_v9.json'
SESSION_EXCEPTIONS_PATH=PROPOSALS/'fno_historical_session_exceptions_v2.json'
SESSION_EXCEPTIONS_CONTENT=SESSION_EXCEPTIONS_PATH.read_bytes()
SESSION_EXCEPTIONS_SHA256=digest(SESSION_EXCEPTIONS_CONTENT)
SESSION_EXCEPTIONS=json.loads(SESSION_EXCEPTIONS_CONTENT)['exceptions']
INTERVALS={'daily':('days',1,date(2000,1,1),3650),'15m':('minutes',15,date(2022,1,1),28),'5m':('minutes',5,date(2022,1,1),28)}


def session_windows(day, symbol):
    for rule in SESSION_EXCEPTIONS:
        if rule['date']==day and rule.get('symbol',symbol)==symbol:
            return rule.get('segments',[(rule.get('start'),rule.get('end'))]),rule['source']
    if date.fromisoformat(day).weekday()<5:
        return [('09:15','15:30')],None
    return None


def session_bounds(day, symbol):
    rule=session_windows(day,symbol)
    if rule is None: return None
    windows,source=rule
    return windows[0][0],windows[-1][1],source


def expected_session_starts(day, interval, symbol):
    rule=session_windows(day,symbol)
    if rule is None or interval=='daily': return None
    n=INTERVALS[interval][1]
    starts=[]
    for begin,finish in rule[0]:
        start=datetime.fromisoformat(f'{day}T{begin}:00+05:30')
        end=datetime.fromisoformat(f'{day}T{finish}:00+05:30')
        starts.extend((start+timedelta(minutes=i)).isoformat()
                      for i in range(0,int((end-start).total_seconds()//60),n))
    return starts


def request_windows(interval, start, end):
    step=INTERVALS[interval][3]
    while start<=end:
        finish=min(end,start+timedelta(days=step-1))
        yield start,finish
        start=finish+timedelta(days=1)


def validate_rows(rows, interval, start, end, symbol=None):
    valid=[]; issues=[]
    for idx,row in enumerate(rows):
        try:
            if not isinstance(row,list) or len(row)<6: raise ValueError('malformed_row')
            try:
                dt=datetime.fromisoformat(str(row[0]))
                if dt.tzinfo is None or dt.year<2000: raise ValueError()
                local=dt.astimezone(IST)
                if not start<=local.date()<=end or dt>utc(): raise ValueError()
            except (ValueError,TypeError,OverflowError): raise ValueError('invalid_timestamp')
            vals=row[1:6]
            if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in vals): raise ValueError('invalid_ohlcv')
            op,hi,lo,cl,vol=vals
            if min(op,hi,lo,cl)<=0 or vol<0 or hi<max(op,lo,cl) or lo>min(op,hi,cl): raise ValueError('invalid_ohlcv')
            if interval!='daily':
                minute=local.hour*60+local.minute
                rule=session_windows(str(local.date()),symbol)
                windows=rule[0] if rule else [('09:15','15:30')]
                # Preserve already stored provider zero-volume pre-open placeholders.
                # They are not expected continuous-trading starts in coverage.
                if windows==[('10:00','15:30')]: windows=[('09:15','15:30')]
                aligned=any((int(begin[:2])*60+int(begin[3:5]))<=minute<(int(finish[:2])*60+int(finish[3:5]))
                            and (minute-(int(begin[:2])*60+int(begin[3:5])))%INTERVALS[interval][1]==0
                            for begin,finish in windows)
                if local.second or local.microsecond or not aligned: raise ValueError('off_grid_timestamp')
            stamp=local.isoformat()
            identity=str(local.date()) if interval=='daily' else stamp
            valid.append((idx,identity,[stamp,*row[1:]]))
        except ValueError as exc:
            issues.append({'row_index':idx,'timestamp':str(row[0]) if isinstance(row,list) and row else None,'reason':str(exc)})
    output=[]; by_identity=defaultdict(list)
    for item in valid: by_identity[item[1]].append(item)
    for identity, group in by_identity.items():
        if len(group)==1:
            output.append(group[0][2]); continue
        if (interval=='daily' and identity=='2015-12-31' and len(group)==2
                and group[0][2][1:]==group[1][2][1:]):
            canonical=min(group,key=lambda item:item[2][0])
            output.append(canonical[2])
            for idx,_,row in group:
                if idx!=canonical[0]:
                    issues.append({'row_index':idx,'timestamp':row[0],
                                   'reason':'identical_daily_duplicate_collapsed'})
            continue
        for idx,_,row in group:
            issues.append({'row_index':idx,'timestamp':row[0],'reason':'duplicate_timestamp'})
    return sorted(output,key=lambda x:x[0]),issues


def weekly_from_daily(rows, expected_sessions):
    if expected_sessions is None: return {'status':'UNAVAILABLE_CALENDAR','rows':[],'incomplete_weeks':[]}
    groups=defaultdict(list)
    for d in expected_sessions:
        day=date.fromisoformat(d); groups[str(day-timedelta(days=day.weekday()))].append(d)
    byday={r[0][:10]:r for r in rows}; result=[]; incomplete=[]
    for week,days in sorted(groups.items()):
        if any(d not in byday for d in days): incomplete.append(week); continue
        part=[byday[d] for d in sorted(days)]
        result.append([week,part[0][1],max(r[2] for r in part),min(r[3] for r in part),part[-1][4],sum(r[5] for r in part),sorted(days)])
    return {'status':'DERIVED','rule':'nse-session-daily-to-weekly-v1','rows':result,'incomplete_weeks':incomplete}


@contextmanager
def exclusive_collector(root):
    root=Path(root); root.mkdir(parents=True,exist_ok=True)
    with ExitStack() as stack:
        for name in ('ongoing.lock','backfill.lock','historical-db.lock'):
            file=stack.enter_context((root/name).open('a+b')); file.seek(0)
            try: msvcrt.locking(file.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: raise RuntimeError('An active collector already holds the acquisition lock')
        yield


class HistoryDB:
    def __init__(self,path):
        self.path=Path(path); self.db=sqlite3.connect(self.path,timeout=30)
        self.db.row_factory=sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=DELETE;
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members(instrument TEXT PRIMARY KEY,symbol TEXT,reference_hash TEXT);
        CREATE TABLE IF NOT EXISTS windows(id TEXT PRIMARY KEY,instrument TEXT,symbol TEXT,interval TEXT,start TEXT,end TEXT,priority INTEGER,status TEXT DEFAULT 'UNFETCHED',reason TEXT,returned INTEGER DEFAULT 0,valid INTEGER DEFAULT 0,invalid INTEGER DEFAULT 0,duplicates INTEGER DEFAULT 0,first_bar TEXT,last_bar TEXT,normalized_path TEXT,normalized_hash TEXT,raw_hash TEXT,raw_path TEXT,quality_path TEXT,elapsed_ms REAL,manifest_hash TEXT,source_event TEXT,attempts INTEGER DEFAULT 0);
        CREATE INDEX IF NOT EXISTS idx_work ON windows(status,priority,start,instrument);
        CREATE INDEX IF NOT EXISTS idx_lookup ON windows(instrument,interval,start,end);
        CREATE TABLE IF NOT EXISTS sessions(window_id TEXT,session TEXT,received INTEGER,expected INTEGER,missing INTEGER,expectation_status TEXT,PRIMARY KEY(window_id,session));
        CREATE TABLE IF NOT EXISTS weekly_status(instrument TEXT PRIMARY KEY,status TEXT,rule TEXT);
        CREATE TABLE IF NOT EXISTS acquisition_scope(instrument TEXT PRIMARY KEY,symbol TEXT NOT NULL,manifest_hash TEXT NOT NULL,reference_hash TEXT NOT NULL);
        ''')
    def set_scope(self,members,manifest_hash,reference_hash):
        if not members or len({m['underlying_key'] for m in members})!=len(members):
            raise ValueError('Scope must contain distinct, verified members')
        with self.db:
            self.db.execute('DELETE FROM acquisition_scope')
            self.db.executemany('INSERT INTO acquisition_scope VALUES(?,?,?,?)',[(m['underlying_key'],m['symbol'],manifest_hash,reference_hash) for m in members])
    def plan(self,members,end,reference_hash):
        for m in sorted(members,key=lambda x:x['underlying_key']):
            self.db.execute('INSERT OR IGNORE INTO members VALUES(?,?,?)',(m['underlying_key'],m['symbol'],reference_hash))
            for interval,(_,_,start,_) in INTERVALS.items():
                # Retain the original 21 September terminal window to reuse prior observations exactly.
                boundary=min(end,date(2026,9,21)); windows=list(request_windows(interval,start,boundary))
                if end>boundary: windows+=list(request_windows(interval,boundary+timedelta(days=1),end))
                for a,b in windows:
                    key=f"history-db:{m['underlying_key']}:{interval}:{a}:{b}"
                    priority=0 if interval=='daily' else 1
                    self.db.execute('INSERT OR IGNORE INTO windows(id,instrument,symbol,interval,start,end,priority) VALUES(?,?,?,?,?,?,?)',(key,m['underlying_key'],m['symbol'],interval,str(a),str(b),priority))
            self.db.execute('INSERT OR IGNORE INTO weekly_status VALUES(?,?,?)',(m['underlying_key'],'UNAVAILABLE_CALENDAR','nse-session-daily-to-weekly-v1'))
        self.db.commit()
    def next_window(self):
        row=self.db.execute("SELECT w.* FROM windows w JOIN acquisition_scope a ON a.instrument=w.instrument WHERE w.status='UNFETCHED' ORDER BY w.priority,w.start,w.instrument,w.interval LIMIT 1").fetchone()
        return dict(row) if row else None
    def begin(self,key):
        self.db.execute("UPDATE windows SET status='IN_PROGRESS',attempts=attempts+1 WHERE id=?",(key,)); self.db.commit()
    def recover_interrupted(self):
        n=self.db.execute("UPDATE windows SET status='INTERRUPTED',reason='previous process ended before catalog commit; no automatic retry' WHERE status='IN_PROGRESS'").rowcount; self.db.commit(); return n
    def update(self,key,**values):
        self.db.execute('UPDATE windows SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),key)); self.db.commit()
    def rows(self,instrument,interval,start,end):
        for w in self.db.execute("SELECT * FROM windows WHERE instrument=? AND interval=? AND end>=? AND start<=? AND normalized_path IS NOT NULL ORDER BY start",(instrument,interval,start,end)):
            content=gzip.decompress(Path(w['normalized_path']).read_bytes())
            if digest(content)!=w['normalized_hash']: raise RuntimeError('Normalized hash mismatch')
            for r in json.loads(content)['rows']:
                if start<=r[0][:10]<=end: yield r


def reclassification_targets(db):
    """Selected intraday windows intersecting an explicitly documented exception."""
    targets=[]
    for rule in SESSION_EXCEPTIONS:
        rows=db.db.execute('''SELECT w.* FROM windows w JOIN acquisition_scope a
            ON a.instrument=w.instrument WHERE w.interval IN ('15m','5m')
            AND w.start<=? AND w.end>=? AND (? IS NULL OR w.symbol=?)''',
            (rule['date'],rule['date'],rule.get('symbol'),rule.get('symbol')))
        targets.extend(rows)
    return sorted({w['id']:w for w in targets}.values(),key=lambda w:(w['symbol'],w['interval'],w['start']))


class HistoricalCollector(Collector):
    def __init__(self,path=MANIFEST):
        super().__init__(path)
        self.history_code_hash=digest(Path(__file__).read_bytes())
    def event(self,kind,key,payload):
        return super().event(kind,key,{'historical_parser_version':VERSION,'historical_code_sha256':getattr(self,'history_code_hash',digest(Path(__file__).read_bytes())),**payload})
    def fetch(self,url,key,*,slot=None,historical=False):
        UpstoxEndpointPolicy.require_allowed(url,method='GET')
        self._budget(4*1024*1024)
        last=[x for x in self.ledger.events('historical-rate') if x.event_type=='historical_request_dispatched'][-1:]
        if last:
            delay=2.0-(utc()-datetime.fromisoformat(last[-1].occurred_at)).total_seconds()
            if delay>0: time.sleep(delay)
        if url!=INSTRUMENTS_URL and self.token is None: self.token=read_upstox_analytics_token()
        headers={'Accept':'application/json','User-Agent':'fno-historical-foundation/1'}
        if url!=INSTRUMENTS_URL: headers['Authorization']='Bearer '+self.token
        started=utc(); tick=time.monotonic()
        self.event('historical_request_dispatched','historical-rate',{'window':key,'url_path':urllib.parse.urlsplit(url).path,'request_start_utc':started.isoformat(),'retry':bool(getattr(self,'prior_source_failures',[]) or getattr(self,'prior_request_count',0)),'prior_request_count':getattr(self,'prior_request_count',0),'prior_source_failure_event_ids':getattr(self,'prior_source_failures',[]),'retry_authority':'Binding broad historical acquisition instruction 2026-09-26; previous failed window is recorded explicitly' if getattr(self,'prior_source_failures',[]) else None})
        body=None; status=None
        try:
            try:
                with urllib.request.build_opener(_NoRedirectHandler()).open(urllib.request.Request(url,headers=headers,method='GET'),timeout=30) as response:
                    body=response.read(16*1024*1024+1); status=response.status
            except HTTPError as exc:
                status=exc.code; body=exc.read(16*1024*1024+1)
            ended=utc()
            if len(body)>16*1024*1024: raise ValueError('response_exceeds_recorded_16MiB_limit')
            sha=digest(body); raw=gzip.compress(body,compresslevel=6,mtime=0); path=self.root/'raw'/sha[:2]/f'{sha}.http.gz'
            self.write_capped(path,raw,historical=historical)
            self.event('raw_response_captured',key,{'url_path':urllib.parse.urlsplit(url).path,'request_start_utc':started.isoformat(),'response_end_utc':ended.isoformat(),'local_receipt_utc':ended.isoformat(),'http_status':status,'elapsed_ms':round((time.monotonic()-tick)*1000,3),'raw_sha256':sha,'raw_path':str(path.resolve()),'raw_bytes':len(raw)})
            if status!=200: raise ValueError('provider_http_'+str(status))
            value=json.loads(gzip.decompress(body) if url==INSTRUMENTS_URL else body)
            if url!=INSTRUMENTS_URL and (not isinstance(value,dict) or value.get('status')!='success'): raise ValueError('malformed_provider_success_schema')
            return value
        except StorageLimitError:
            self.event('storage_limit_event',key,{'status':'UNFETCHED','bytes_used':used_bytes(self.root)}); raise
        except Exception as exc:
            self.event('source_failure',key,{'request_start_utc':started.isoformat(),'failed_at_utc':utc().isoformat(),'http_status':status,'error_type':type(exc).__name__,'reason':str(exc)[:180],'raw_response_retained':body is not None and len(body)<=16*1024*1024})
            if status in (401,403,429): raise RuntimeError(f'provider_http_{status}; manual review required')
            return None
    def _budget(self,extra):
        n=used_bytes(self.root)
        if n+extra+self.reserve>self.historical_soft_cap or n+extra+self.reserve>self.cap: raise StorageLimitError('Historical allocation or total storage cap; remaining windows UNFETCHED')


def put_json(c,path,value):
    content=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    c.write_capped(path,gzip.compress(content,mtime=0),historical=True)
    return digest(content)


def ingest(c,db,w,bars,raw,source_event=None):
    if not isinstance(bars,list): raise ValueError('candles must be a list')
    valid,issues=validate_rows(bars,w['interval'],date.fromisoformat(w['start']),date.fromisoformat(w['end']),w['symbol'])
    content=json.dumps({'key':w['id'],'kind':'ohlcv','parser_version':VERSION,'session_exceptions_sha256':SESSION_EXCEPTIONS_SHA256,'rows':valid},sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    sha=digest(content); target=c.root/'historical_db'/'partitions'/sha[:2]/f'{sha}.json.gz'
    c.write_capped(target,gzip.compress(content,mtime=0),historical=True)
    sessions=defaultdict(list)
    for row in valid: sessions[row[0][:10]].append(row[0])
    gaps=[]; pre_open_provider_rows=[]
    # Re-ingestion must remove session rows excluded by the latest validation.
    db.db.execute('DELETE FROM sessions WHERE window_id=?',(w['id'],))
    for day,observed in sessions.items():
        starts=expected_session_starts(day,w['interval'],w['symbol'])
        if starts is not None:
            missing=sorted(set(starts)-set(observed)); gaps.extend(missing); expected=len(starts)
        else: expected=None
        rule=session_bounds(day,w['symbol'])
        if rule and rule[0]=='10:00' and rule[1]=='15:30':
            pre_open_provider_rows.extend(sorted(set(observed)-set(starts or [])))
        state='DOCUMENTED_SESSION_EXCEPTION' if rule and rule[2] else ('ASSUMED_REGULAR_SESSION_NOT_OFFICIAL_CALENDAR' if expected is not None else 'UNKNOWN_CALENDAR')
        db.db.execute('INSERT OR REPLACE INTO sessions VALUES(?,?,?,?,?,?)',(w['id'],day,len(observed),expected,len(missing) if starts is not None else None,state))
    quality={'window':w['id'],'raw_sha256':raw.get('raw_sha256'),'normalized_sha256':sha,'session_exceptions_sha256':SESSION_EXCEPTIONS_SHA256,'row_count':len(bars),'valid_rows':len(valid),'issues':issues,'missing_bar_starts_against_known_or_assumed_session':gaps,'provider_preopen_rows_outside_continuous_session':pre_open_provider_rows,'expectations':'Documented exception windows apply only to named dates/stocks; other observed weekdays assume regular hours. No complete historical calendar or listing/eligibility history; missing whole sessions unresolved.','weekly_status':'UNAVAILABLE_CALENDAR'}
    qcontent=json.dumps(quality,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    qp=c.root/'historical_db'/'quality'/f'{digest(qcontent)}.json.gz'; qsha=put_json(c,qp,quality)
    counts=Counter(x['reason'] for x in issues)
    status='UNAVAILABLE' if not bars else ('PARTIAL' if issues else 'FETCHED')
    payload={'underlying_key':w['instrument'],'symbol':w['symbol'],'interval':w['interval'],'from_date':w['start'],'to_date':w['end'],'status':status,'returned':len(bars),'valid':len(valid),'issue_counts':dict(counts),'quality_path':str(qp),'quality_sha256':qsha,'session_exceptions_sha256':SESSION_EXCEPTIONS_SHA256,'raw_sha256':raw.get('raw_sha256'),'normalized_path':str(target),'normalized_sha256':sha,'source_event':source_event,'expected_full_history_bars':None,'reason':'empty_provider_window_not_proof_of_prelisting' if not bars else None}
    c.event('historical_db_window_recorded',w['id'],payload)
    db.update(w['id'],status=status,reason=payload['reason'],returned=len(bars),valid=len(valid),invalid=len(issues),duplicates=counts.get('duplicate_timestamp',0),first_bar=valid[0][0] if valid else None,last_bar=valid[-1][0] if valid else None,normalized_path=str(target),normalized_hash=sha,raw_hash=raw.get('raw_sha256'),raw_path=raw.get('raw_path'),quality_path=str(qp),elapsed_ms=raw.get('elapsed_ms'),manifest_hash=c.manifest_hash,source_event=source_event)
    db.db.commit()


def import_existing(c,db):
    events=c.ledger.events(); raw={e.aggregate_id:e for e in events if e.event_type=='raw_response_captured'}
    count=0
    for w0 in db.db.execute("SELECT w.* FROM windows w JOIN acquisition_scope a ON a.instrument=w.instrument WHERE w.status='UNFETCHED'").fetchall():
        w=dict(w0); unit,n,_,_=INTERVALS[w['interval']]
        old=f"{w['symbol']}:{n}{unit}:{w['start']}:{w['end']}"; observation=raw.get(old)
        if not observation: continue
        rp=Path(observation.payload['raw_path'])
        if not rp.exists(): continue
        data=gzip.decompress(rp.read_bytes())
        if digest(data)!=observation.payload['raw_sha256']: raise RuntimeError('Existing raw hash mismatch')
        decoded=json.loads(data)
        if decoded.get('status')!='success' or not isinstance(decoded.get('data',{}).get('candles'),list): continue
        c._budget(4*1024*1024)
        ingest(c,db,w,decoded['data']['candles'],observation.payload,observation.event_id); count+=1
        if count%100==0: print(json.dumps({'reused_windows':count}),flush=True)
    return count


def projection(c,members,end):
    events=c.ledger.events(); raw={e.aggregate_id:e.payload for e in events if e.event_type=='raw_response_captured'}; norm={e.aggregate_id:e.payload for e in events if e.event_type=='normalized_partition_recorded'}; samples=defaultdict(list)
    names={'1 day':'daily','15 minutes':'15m','5 minutes':'5m'}
    for e in events:
        if e.event_type!='history_window_recorded' or e.payload.get('row_count',0)<100: continue
        i=names[e.payload['interval']]; r=raw.get(e.aggregate_id); n=norm.get(e.aggregate_id)
        if not r or not n or len(samples[i])>=100: continue
        rp=Path(r['raw_path']); np=Path(n['path'])
        if not rp.exists() or not np.exists(): continue
        count=e.payload['row_count']; samples[i].append([rp.stat().st_size/count,np.stat().st_size/count,len(gzip.decompress(rp.read_bytes()))/count,len(gzip.decompress(np.read_bytes()))/count])
    lines=[]; total=0
    for i,(_,n,start,_) in INTERVALS.items():
        weekdays=sum((start+timedelta(days=d)).weekday()<5 for d in range((end-start).days+1))
        rows=weekdays*len(members)*(1 if i=='daily' else 375//n)
        mean=[statistics.mean(v[k] for v in samples[i]) for k in range(4)] if samples[i] else [30,30,100,100]
        estimate=[math.ceil(rows*v) for v in mean]; total+=sum(estimate[:2])
        lines.append({'interval':i,'from':str(start),'through':str(end),'weekday_upper_planning_rows':rows,'nonempty_sample_windows':len(samples[i]),'bytes_per_bar_raw_gzip_normalized_gzip_raw_json_normalized_json':mean,'projected_bytes_raw_gzip_normalized_gzip_raw_json_normalized_json':estimate})
    return {'as_of_utc':utc().isoformat(),'manifest_sha256':c.manifest_hash,'universe_count':len(members),'intervals':lines,'projected_raw_plus_normalized_gzip_bytes':total,'planning_bytes_with_35_percent_margin_and_existing_store':math.ceil(total*1.35)+used_bytes(c.root),'bytes_now':used_bytes(c.root),'cap':c.cap,'historical_soft_cap':c.historical_soft_cap,'caveat':'Weekdays are storage planning assumptions only, not verified exchange sessions. Actual listing dates, holidays, special sessions and compressibility differ. Margin allows quality/catalog/audit overhead. Not a coverage claim.'}


def report(c,db):
    folder=c.root/'historical_db'/'reports'; folder.mkdir(parents=True,exist_ok=True)
    selected=' FROM windows w JOIN acquisition_scope a ON a.instrument=w.instrument'
    summary={'generated_at_utc':utc().isoformat(),'manifest_sha256':c.manifest_hash,'source_manifest_sha256':c.source_hash,'parser_version':VERSION,'parser_code_sha256':c.history_code_hash,'ledger_events':len(c.ledger.events()),'storage_bytes':used_bytes(c.root),'cap':c.cap,'states':dict(db.db.execute('SELECT w.status,count(*)'+selected+' GROUP BY w.status')),'valid_rows':db.db.execute('SELECT coalesce(sum(w.valid),0)'+selected).fetchone()[0],'universe_count':db.db.execute('SELECT count(*) FROM acquisition_scope').fetchone()[0],'preserved_unselected_windows':db.db.execute('SELECT count(*) FROM windows w WHERE NOT EXISTS (SELECT 1 FROM acquisition_scope a WHERE a.instrument=w.instrument)').fetchone()[0],'unproven':['Complete historical exchange calendar','Historical FnO eligibility and listing dates','Provider publication completeness and corporate-action adjustment basis','Weekly completeness: UNAVAILABLE_CALENDAR','Reliable always-on forward host and five complete sessions']}
    times=[r[0] for r in db.db.execute('SELECT w.elapsed_ms'+selected+' WHERE w.elapsed_ms IS NOT NULL ORDER BY w.elapsed_ms')]
    summary['request_duration_ms']={k:times[min(len(times)-1,int((len(times)-1)*q))] if times else None for k,q in [('p50',.5),('p95',.95),('p99',.99),('max',1)]}
    summary['reports']={}
    # Compressed current inventory files; immutable per-window quality and each
    # summary snapshot provide history without retaining repeated large exports.
    for name,sql in [('coverage_by_stock_interval.csv.gz',"SELECT w.symbol,w.instrument,w.interval,count(*) planned_windows,sum(w.status='UNFETCHED') unfetched,sum(w.status='FETCHED') fetched,sum(w.status='UNAVAILABLE') unavailable,sum(w.status='SOURCE_FAILURE') failed,sum(w.status='PARTIAL') partial,sum(w.status='INTERRUPTED') interrupted,sum(w.valid) valid_bars,sum(w.invalid) invalid_rows,min(w.first_bar) first_bar,max(w.last_bar) last_bar"+selected+' GROUP BY w.instrument,w.interval ORDER BY w.symbol,w.interval'),('window_inventory.csv.gz','SELECT w.*'+selected+' ORDER BY w.symbol,w.interval,w.start'),('session_coverage.csv.gz','SELECT w.symbol,w.interval,s.* FROM sessions s JOIN windows w ON s.window_id=w.id JOIN acquisition_scope a ON a.instrument=w.instrument ORDER BY w.symbol,w.interval,s.session')]:
        buffer=io.StringIO(newline=''); cur=db.db.execute(sql)
        writer=csv.writer(buffer); writer.writerow(x[0] for x in cur.description); writer.writerows(cur)
        payload=gzip.compress(buffer.getvalue().encode(),mtime=0)
        c._budget(len(payload)+1048576)
        temp=folder/(name+'.tmp'); c.write_capped(temp,payload,historical=True); temp.replace(folder/name)
        summary['reports'][name]={'sha256':digest(payload),'bytes':len(payload)}
    blob=json.dumps(summary,indent=2).encode(); c._budget(len(blob)+1048576)
    snapshot=folder/'snapshots'/f'{digest(blob)}.json.gz'; c.write_capped(snapshot,gzip.compress(blob,mtime=0),historical=True)
    temp=folder/'status.json.tmp'; c.write_capped(temp,blob,historical=True); temp.replace(folder/'status.json')
    c.event('historical_coverage_report_written','history-db-report',{'status_sha256':digest(blob),'snapshot_path':str(snapshot),'path':str(folder/'status.json'),'states':summary['states'],'storage_bytes':summary['storage_bytes']})
    return summary


def run(c,db,max_windows,*,manage_awake=True):
    if not c.manifest['activation'].get('historical_bulk_job_enabled'): raise PermissionError('bulk acquisition not authorized')
    expected=set(c.manifest['scope']['historical_selected_symbols'])
    actual={r[0] for r in db.db.execute('SELECT symbol FROM acquisition_scope')}
    if actual!=expected or len(actual)!=len(expected): raise RuntimeError('Historical scope is not prepared against selected manifest symbols')
    if any(r[0]!=c.manifest_hash for r in db.db.execute('SELECT DISTINCT manifest_hash FROM acquisition_scope')): raise RuntimeError('Catalog scope uses a different manifest')
    blocked=db.db.execute("SELECT value FROM meta WHERE key='blocked'").fetchone()
    if blocked: raise RuntimeError('Historical lane blocked: '+blocked[0])
    if db.next_window() is None:
        print('All planned windows have terminal statuses; see coverage report',flush=True); return
    n=db.recover_interrupted()
    if n: c.event('historical_run_interrupted','history-db',{'windows':n,'retry':False})
    previous=c.ledger.events(); prior_raw={e.aggregate_id:e for e in previous if e.event_type=='raw_response_captured'}
    prior_failures=defaultdict(list)
    for e in previous:
        if e.event_type=='source_failure': prior_failures[e.aggregate_id].append(e.event_id)
    before=used_bytes(c.root); started=utc(); count=0; first_request=None; last_receipt=None; consecutive_failures=0; awake=set_system_awake(True) if manage_awake else False
    c.event('historical_run_started','history-db',{'started_at':started.isoformat(),'max_windows':max_windows,'awake_request':awake,'awake_managed_by_continuous_run':not manage_awake})
    try:
        while count<max_windows:
            w=db.next_window()
            if w is None: break
            c._budget(20*1024*1024)
            if used_bytes(c.root)-before>=c.batch_cap: break
            unit,n,_,_=INTERVALS[w['interval']]
            old=f"{w['symbol']}:{n}{unit}:{w['start']}:{w['end']}"
            source=prior_raw.get(old)
            if source and Path(source.payload['raw_path']).exists():
                body=gzip.decompress(Path(source.payload['raw_path']).read_bytes())
                if digest(body)!=source.payload['raw_sha256']: raise RuntimeError('Existing raw hash mismatch')
                decoded=json.loads(body)
                if decoded.get('status')=='success' and isinstance(decoded.get('data',{}).get('candles'),list):
                    ingest(c,db,w,decoded['data']['candles'],source.payload,source.event_id); count+=1
                    continue
            c.prior_source_failures=prior_failures.get(old,[])
            c.prior_request_count=w['attempts']
            db.begin(w['id'])
            url=f"https://api.upstox.com/v3/historical-candle/{urllib.parse.quote(w['instrument'],safe='')}/{unit}/{n}/{w['end']}/{w['start']}"
            try:
                data=c.fetch(url,w['id'],historical=True)
                if data is None:
                    db.update(w['id'],status='SOURCE_FAILURE',reason='see immutable source_failure event'); consecutive_failures+=1
                    if consecutive_failures>=3: raise RuntimeError('Three consecutive source failures; manual review required')
                elif not isinstance(data.get('data'),dict) or not isinstance(data['data'].get('candles'),list):
                    c.event('malformed_response',w['id'],{'reason':'missing candles list'}); db.update(w['id'],status='MALFORMED',reason='missing candles list')
                else:
                    raw=[x for x in c.ledger.events(w['id']) if x.event_type=='raw_response_captured'][-1]
                    ingest(c,db,w,data['data']['candles'],raw.payload,raw.event_id)
                    first_request=first_request or datetime.fromisoformat(raw.payload['request_start_utc'])
                    last_receipt=datetime.fromisoformat(raw.payload['response_end_utc']); consecutive_failures=0
            except StorageLimitError:
                db.update(w['id'],status='UNFETCHED',reason='storage_limit_partial_request; inspect audit before retry'); raise
            except Exception as exc:
                db.update(w['id'],status='SOURCE_FAILURE',reason=str(exc)[:200]); raise
            count+=1
            if count%50==0: print(json.dumps({'processed':count,'last_symbol':w['symbol'],'interval':w['interval']}),flush=True)
        c.event('historical_batch_finished','history-db',{'processed_windows':count,'started_at':started.isoformat(),'ended_at':utc().isoformat(),'cycle_first_request_to_final_receipt_ms':round((last_receipt-first_request).total_seconds()*1000) if first_request and last_receipt else None,'batch_wall_duration_ms':round((utc()-started).total_seconds()*1000),'bytes_added':used_bytes(c.root)-before})
    except StorageLimitError as exc:
        db.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('blocked',str(exc))); db.db.commit()
        c.event('storage_limit_event','history-db',{'reason':str(exc),'processed_windows':count,'remaining_status':'UNFETCHED'})
    except Exception as exc:
        db.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('blocked',str(exc)[:180])); db.db.commit()
        c.event('historical_batch_failed','history-db',{'error_type':type(exc).__name__,'reason':str(exc)[:180],'processed_windows':count}); raise
    finally:
        if awake: set_system_awake(False)
        report(c,db)


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('action',choices=['prepare','prepare-scope','import','run','run-all','report','query']); ap.add_argument('--max-windows',type=int,default=250); ap.add_argument('--max-batches',type=int,default=12); ap.add_argument('--symbol'); ap.add_argument('--interval',choices=list(INTERVALS)); ap.add_argument('--from-date'); ap.add_argument('--to-date'); a=ap.parse_args()
    c=HistoricalCollector(); c.ledger.verify()
    with exclusive_collector(c.root):
        folder=c.root/'historical_db'; folder.mkdir(exist_ok=True); db=HistoryDB(folder/'catalog.sqlite')
        if a.action in ('prepare','prepare-scope'):
            if a.action=='prepare' and db.db.execute("SELECT 1 FROM meta WHERE key='reference_path'").fetchone(): raise RuntimeError('Historical universe already frozen; use prepare-scope for an explicitly authorized narrowed scope')
            members=c.refresh_master()
            if not members: raise RuntimeError('Fresh universe master unavailable; bulk requests not started')
            reference=[x for x in c.ledger.events() if x.event_type=='universe_reference_observed'][-1]
            wanted=c.manifest['scope']['historical_selected_symbols']
            if len(wanted)!=len(set(wanted)): raise ValueError('Duplicate symbol in manifest scope')
            matched={m['symbol']:m for m in members if m['symbol'] in wanted}
            absent=sorted(set(wanted)-set(matched))
            if absent:
                c.event('historical_scope_validation_failed','history-db',{'requested_symbols':wanted,'absent_symbols':absent,'reference_sha256':reference.payload['reference_sha256']})
                raise RuntimeError('Requested symbols absent from fresh active FnO master: '+','.join(absent))
            members=[matched[s] for s in wanted]
            end=date.fromisoformat(c.manifest['scope']['historical_ohlcv'][0]['to_date_inclusive'])
            projected=projection(c,members,end); target=folder/f'storage_projection_v{c.manifest["version"]}.json'; c.write_capped(target,json.dumps(projected,indent=2).encode(),historical=True)
            c.event('historical_storage_projection_recorded','history-db',{'projection':projected,'file_sha256':digest(target.read_bytes())})
            db.plan(members,end,reference.payload['reference_sha256'])
            db.set_scope(members,c.manifest_hash,reference.payload['reference_sha256'])
            db.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('reference_path',reference.payload['reference_path']))
            db.db.execute("DELETE FROM meta WHERE key='blocked' AND value LIKE 'User explicitly instructed STOP%'")
            db.db.commit()
            c.event('historical_scope_activated','history-db',{'symbols':wanted,'underlying_keys':[m['underlying_key'] for m in members],'reference_sha256':reference.payload['reference_sha256'],'projection_file_sha256':digest(target.read_bytes()),'preserved_unselected_windows':db.db.execute('SELECT count(*) FROM windows w WHERE NOT EXISTS (SELECT 1 FROM acquisition_scope a WHERE a.instrument=w.instrument)').fetchone()[0],'authorization':'User instruction 2026-09-28: do it for these only'})
            print(json.dumps(projected,indent=2))
        elif a.action=='import': print(json.dumps({'reused_windows':import_existing(c,db)})); print(json.dumps(report(c,db)))
        elif a.action in ('run','run-all'):
            if not (folder/f'storage_projection_v{c.manifest["version"]}.json').exists() or not db.db.execute("SELECT 1 FROM meta WHERE key='reference_path'").fetchone(): raise RuntimeError('Prepare fresh reference and storage projection before bulk requests')
            if a.max_windows<1 or a.max_windows>250: raise ValueError('Each historical batch must be 1 to 250 windows')
            if a.action=='run': run(c,db,a.max_windows)
            else:
                if a.max_batches<1 or a.max_batches>12: raise ValueError('Continuous run must be 1 to 12 bounded batches')
                awake=set_system_awake(True)
                c.event('historical_continuous_run_started','history-db',{'max_batches':a.max_batches,'max_windows_per_batch':a.max_windows,'awake_request':awake})
                try:
                    completed=0
                    for batch in range(a.max_batches):
                        if db.next_window() is None or db.db.execute("SELECT 1 FROM meta WHERE key='blocked'").fetchone(): break
                        run(c,db,a.max_windows,manage_awake=False)
                        completed+=1
                    c.event('historical_continuous_run_finished','history-db',{'completed_batches':completed,'remaining_unfetched':db.db.execute("SELECT count(*) FROM windows w JOIN acquisition_scope a ON a.instrument=w.instrument WHERE w.status='UNFETCHED'").fetchone()[0]})
                finally:
                    if awake: set_system_awake(False)
        elif a.action=='report': print(json.dumps(report(c,db)))
        elif a.action=='query':
            member=db.db.execute('SELECT instrument FROM members WHERE symbol=?',(a.symbol.upper(),)).fetchone()
            if not member: raise ValueError('Unknown underlying')
            print(json.dumps(list(db.rows(member[0],a.interval,a.from_date,a.to_date))))

if __name__=='__main__': main()
