"""NSE public-archive end-of-day files for the selected stocks; no strategy functionality.

Approved source `nse-archives-public-eod-v1` (systems/proposals/fno_source_nse_archives_v1.json),
for three purposes only: a trading-session calendar, listing dates, and an independent
cross-check of the stored Upstox daily series. Nothing here edits, replaces or fills an
Upstox value; NSE rows are a separate series kept side by side.

Every calendar day is planned, weekends included, because special sessions exist. Each
day ends in exactly one state: PUBLISHED (a bhavcopy whose own trade date matches),
NOT_PUBLISHED (HTTP 404), DATE_MISMATCH, PARSE_FAILURE or SOURCE_FAILURE. Only the first
is session evidence, and only a 404 is evidence of no session - a failed request is not.
"""
from __future__ import annotations
import argparse, csv, gzip, io, json, msvcrt, sqlite3, time, urllib.request, zipfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from .full_underlying_collection import digest, used_bytes
from .ledger import ImmutableLedger, sha256_json
from .source_registry import SourceRegistry

VERSION = 'fno-nse-archives-v1'
SOURCE_ID = 'nse-archives-public-eod-v1'
REPO = Path(__file__).resolve().parents[4]
MANIFEST = REPO / 'systems' / 'proposals' / 'fno_source_nse_archives_v1.json'
ROOT = REPO / 'data' / 'fno_momentum' / 'nse_archives_v1'
HISTORICAL_CATALOG = REPO / 'data' / 'fno_momentum' / 'upstox' / 'full_underlying_foundation_v1' / 'historical_db' / 'catalog.sqlite'
HOST = 'https://nsearchives.nseindia.com'
CAP_BYTES = 1024 ** 3                    # the manifest's fail-closed cap for this root
PACE_SECONDS = 1.0                       # the manifest's "at most one request per second"
# NSE replaced the legacy bhavcopy with the UDiFF file in July 2024. Within CUTOVER_SLACK
# of the switch both names are tried, so an unconfirmed cutover date cannot manufacture a 404.
UDIFF_FROM = date(2024, 7, 8)
CUTOVER_SLACK = timedelta(days=14)
START, END = date(2000, 1, 1), date(2026, 9, 25)
FINAL = ('PUBLISHED', 'NOT_PUBLISHED', 'DATE_MISMATCH', 'PARSE_FAILURE', 'SOURCE_FAILURE')


def require_approved(ledger_path: Path) -> str:
    """The exact manifest content hash, or PermissionError unless approved and activated."""
    manifest = json.loads(MANIFEST.read_bytes())
    content_hash = sha256_json(manifest)
    if not Path(ledger_path).exists():
        raise PermissionError('no source ledger: nse-archives source was never approved')
    SourceRegistry(ImmutableLedger(ledger_path)).require_active(SOURCE_ID, content_hash)
    return content_hash


def bhavcopy_urls(day: date) -> list[str]:
    legacy = f"{HOST}/content/historical/EQUITIES/{day.year}/{day.strftime('%b').upper()}/cm{day.strftime('%d%b%Y').upper()}bhav.csv.zip"
    udiff = f"{HOST}/content/cm/BhavCopy_NSE_CM_0_0_0_{day:%Y%m%d}_F_0000.csv.zip"
    first, second = (udiff, legacy) if day >= UDIFF_FROM else (legacy, udiff)
    return [first, second] if abs(day - UDIFF_FROM) <= CUTOVER_SLACK else [first]


def classify_response(status, body: bytes) -> str:
    if status == 404:
        return 'NOT_PUBLISHED'
    if status == 200 and body[:2] == b'PK':
        return 'PUBLISHED'
    return 'SOURCE_FAILURE'


def halts(status) -> bool:
    return status in (401, 403, 429)


def _num(value, kind=float):
    value = (value or '').strip()
    if value in ('', '-'):
        return None
    return kind(float(value)) if kind is int else kind(value)


def _legacy_date(value: str) -> str:
    # At least one legacy file (13 Jul 2020) stamps a two-digit year.
    for fmt in ('%d-%b-%Y', '%d-%b-%y'):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f'unrecognised legacy TIMESTAMP {value!r}')


def parse_bhavcopy(content: bytes, wanted: dict[str, str]):
    """(file trade date, total rows, rows for the wanted securities). Matched by ISIN or symbol."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        names = [n for n in z.namelist() if n.lower().endswith('.csv')]
        if len(names) != 1:
            raise ValueError(f'expected one csv in archive, found {len(names)}')
        text = z.read(names[0]).decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text))
    fields = set(reader.fieldnames or [])
    isins = {v: k for k, v in wanted.items()}
    total, rows, dates = 0, [], set()
    for raw in reader:
        total += 1
        if 'TckrSymb' in fields:
            dates.add(raw['TradDt'].strip())
            sym, isin, series = raw['TckrSymb'].strip(), raw['ISIN'].strip(), raw['SctySrs'].strip()
            vals = ('OpnPric', 'HghPric', 'LwPric', 'ClsPric', 'LastPric', 'PrvsClsgPric', 'TtlTradgVol', 'TtlTrfVal', 'TtlNbOfTxsExctd')
        elif 'SYMBOL' in fields:
            dates.add(_legacy_date(raw['TIMESTAMP']))
            sym, isin, series = raw['SYMBOL'].strip(), (raw.get('ISIN') or '').strip(), raw['SERIES'].strip()
            vals = ('OPEN', 'HIGH', 'LOW', 'CLOSE', 'LAST', 'PREVCLOSE', 'TOTTRDQTY', 'TOTTRDVAL', 'TOTALTRADES')
        else:
            raise ValueError('unrecognised bhavcopy header: ' + ','.join(sorted(fields))[:200])
        by_isin, by_symbol = isin in isins, sym in wanted
        if not (by_isin or by_symbol):
            continue
        o, h, l, c, last, prev, vol, turnover, trades = (raw.get(k) for k in vals)
        rows.append({'symbol': sym, 'isin': isin, 'series': series, 'open': _num(o), 'high': _num(h), 'low': _num(l),
                     'close': _num(c), 'last': _num(last), 'prev_close': _num(prev), 'volume': _num(vol, int),
                     'turnover': _num(turnover), 'trades': _num(trades, int),
                     'match': 'isin+symbol' if by_isin and by_symbol and isins[isin] == sym else ('isin' if by_isin else 'symbol')})
    if len(dates) != 1:
        raise ValueError(f'expected one trade date in file, found {sorted(dates)[:5]}')
    return dates.pop(), total, rows


class Catalog:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path, timeout=30)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS days(day TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'UNFETCHED', url TEXT,
            http_status INTEGER, raw_sha256 TEXT, file_date TEXT, rows_total INTEGER, rows_selected INTEGER,
            normalized_path TEXT, normalized_sha256 TEXT, reason TEXT, attempts INTEGER DEFAULT 0, event_id TEXT);
        ''')

    def plan(self, start: date, end: date):
        day = start
        with self.db:
            while day <= end:
                self.db.execute('INSERT OR IGNORE INTO days(day) VALUES(?)', (day.isoformat(),))
                day += timedelta(days=1)

    def next_day(self):
        row = self.db.execute("SELECT day FROM days WHERE status='UNFETCHED' ORDER BY day LIMIT 1").fetchone()
        return row[0] if row else None

    def update(self, day, **values):
        with self.db:
            self.db.execute('UPDATE days SET ' + ','.join(k + '=?' for k in values) + ' WHERE day=?', (*values.values(), day))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PermissionError('HTTP redirects are denied for the NSE archive source')


@contextmanager
def exclusive(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    with (root / 'collector.lock').open('a+b') as f:
        f.seek(0)
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('another NSE archive collector holds the lock')
        yield


class Collector:
    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.ledger_path = self.root / 'events.sqlite'
        self.manifest_hash = require_approved(self.ledger_path)
        self.ledger = ImmutableLedger(self.ledger_path)
        self.code_hash = digest(Path(__file__).read_bytes())
        self.catalog = Catalog(self.root / 'catalog.sqlite')
        self.last_request = 0.0
        self.pace = PACE_SECONDS

    def event(self, kind, key, payload):
        now = datetime.now(timezone.utc)
        return self.ledger.append(kind, key, {'parser_version': VERSION, 'code_sha256': self.code_hash,
                                              'source_id': SOURCE_ID, 'source_manifest_sha256': self.manifest_hash, **payload},
                                  occurred_at=now, idempotency_key=f'{kind}:{key}:{now.isoformat()}')

    def store(self, folder: str, data: bytes, suffix: str) -> Path:
        path = self.root / folder / digest(data)[:2] / f'{digest(data)}{suffix}'
        if not path.exists():
            if used_bytes(self.root) + len(data) > CAP_BYTES:
                raise RuntimeError('storage cap reached; remaining days stay UNFETCHED')
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix('.tmp'); tmp.write_bytes(data); tmp.replace(path)
        return path

    def fetch(self, url: str, key: str):
        wait = self.pace - (time.monotonic() - self.last_request)
        if wait > 0:
            time.sleep(wait)
        self.last_request = time.monotonic()
        started = datetime.now(timezone.utc); tick = time.monotonic(); status = None; body = b''; error = None
        try:
            request = urllib.request.Request(url, method='GET', headers={'User-Agent': 'Mozilla/5.0 (fno-momentum research; read-only)', 'Accept': '*/*'})
            with urllib.request.build_opener(_NoRedirect()).open(request, timeout=30) as r:
                status, body = r.status, r.read(64 * 1024 * 1024)
        except HTTPError as exc:
            status, body = exc.code, exc.read(1024 * 1024)
        except (URLError, TimeoutError, OSError, PermissionError) as exc:
            error = f'{type(exc).__name__}: {str(exc)[:160]}'
        path = self.store('raw', body, '.bin') if body else None
        ev = self.event('raw_response_captured', key, {
            'url': url, 'http_status': status, 'error': error, 'raw_sha256': digest(body) if body else None,
            'raw_bytes': len(body), 'raw_path': str(path) if path else None, 'request_start_utc': started.isoformat(),
            'elapsed_ms': round((time.monotonic() - tick) * 1000, 1)})
        return status, body, ev.event_id

    def collect_day(self, day: str, wanted: dict[str, str]):
        self.catalog.update(day, attempts=self.catalog.db.execute('SELECT attempts FROM days WHERE day=?', (day,)).fetchone()[0] + 1)
        outcome = None
        for url in bhavcopy_urls(date.fromisoformat(day)):
            status, body, event_id = self.fetch(url, f'bhavcopy:{day}')
            if halts(status):
                self.catalog.update(day, status='SOURCE_FAILURE', url=url, http_status=status, reason=f'HTTP {status}; run halted for manual review')
                raise RuntimeError(f'HTTP {status} from NSE archive; halted for manual review')
            outcome = (classify_response(status, body), url, status, body, event_id)
            if outcome[0] != 'NOT_PUBLISHED':
                break
        return self._record(day, wanted, *outcome)

    def reparse(self, day: str, wanted: dict[str, str]):
        """Re-read a PARSE_FAILURE day from its stored raw response after a parser fix; no request."""
        url, status, sha, event_id = self.catalog.db.execute(
            'SELECT url, http_status, raw_sha256, event_id FROM days WHERE day=?', (day,)).fetchone()
        body = (self.root / 'raw' / sha[:2] / f'{sha}.bin').read_bytes()
        if digest(body) != sha:
            raise RuntimeError(f'stored raw response for {day} fails its hash')
        self.event('day_reparse_authorized', f'bhavcopy:{day}', {'day': day, 'raw_sha256': sha,
                                                                  'reason': f'parser fix ({VERSION}); stored raw response re-read, no new request'})
        return self._record(day, wanted, 'PUBLISHED', url, status, body, event_id)

    def _record(self, day, wanted, state, url, status, body, event_id):
        values = {'status': state, 'url': url, 'http_status': status, 'event_id': event_id,
                  'raw_sha256': digest(body) if body else None, 'reason': None}
        if state == 'PUBLISHED':
            try:
                file_date, total, rows = parse_bhavcopy(body, wanted)
            except (ValueError, KeyError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
                values.update(status='PARSE_FAILURE', reason=f'{type(exc).__name__}: {str(exc)[:160]}')
            else:
                content = json.dumps({'day': day, 'file_date': file_date, 'url': url, 'raw_sha256': values['raw_sha256'],
                                      'parser_version': VERSION, 'rows_total': total, 'rows': rows},
                                     sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
                path = self.store('normalized', gzip.compress(content, mtime=0), '.json.gz')
                values.update(file_date=file_date, rows_total=total, rows_selected=len(rows),
                              normalized_path=str(path), normalized_sha256=digest(content))
                if file_date != day:
                    values.update(status='DATE_MISMATCH', reason=f'requested {day}, file trades {file_date}')
        elif state == 'SOURCE_FAILURE':
            values['reason'] = f'HTTP {status}' if status else 'request failed; see raw_response_captured event'
        self.catalog.update(day, **values)
        self.event('bhavcopy_day_recorded', f'bhavcopy:{day}', {k: v for k, v in values.items() if k != 'normalized_path'} | {'day': day})
        return values['status']

    def collect_listing(self, wanted):
        url = f'{HOST}/content/equities/EQUITY_L.csv'
        status, body, event_id = self.fetch(url, 'equity-listing')
        if status != 200 or not body:
            raise RuntimeError(f'EQUITY_L.csv not retrieved: HTTP {status}')
        rows = list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))
        norm = {k.strip(): k for k in (rows[0].keys() if rows else [])}
        picked = [{k.strip(): (v or '').strip() for k, v in r.items()} for r in rows
                  if r[norm['SYMBOL']].strip() in wanted or r[norm['ISIN NUMBER']].strip() in wanted.values()]
        content = json.dumps({'url': url, 'raw_sha256': digest(body), 'parser_version': VERSION, 'rows_total': len(rows), 'rows': picked},
                             sort_keys=True, separators=(',', ':')).encode()
        path = self.store('normalized', gzip.compress(content, mtime=0), '.json.gz')
        with self.catalog.db:
            self.catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('listing', json.dumps(
                {'path': str(path), 'sha256': digest(content), 'event_id': event_id, 'rows_total': len(rows), 'selected': len(picked)})))
        self.event('equity_listing_recorded', 'equity-listing', {'raw_sha256': digest(body), 'normalized_sha256': digest(content),
                                                                  'rows_total': len(rows), 'selected': len(picked)})
        return picked


# Two retries: the first full run (30 Sep 2026) left 678 connection resets, one retry
# resolved 640, and the 38 left would each keep every stock's range unresolved.
MAX_ATTEMPTS = 3


def retry_transient_failures(c: Collector) -> list[str]:
    """Re-queue days that failed on a dropped connection or a server error, up to MAX_ATTEMPTS. Never a 4xx."""
    rows = list(c.catalog.db.execute(
        "SELECT day, attempts FROM days WHERE status='SOURCE_FAILURE' AND attempts<? AND (http_status IS NULL OR http_status>=500)",
        (MAX_ATTEMPTS,)))
    for day, attempts in rows:
        c.catalog.update(day, status='UNFETCHED', reason=None)
        c.event('day_retry_authorized', f'bhavcopy:{day}', {
            'day': day, 'attempt': attempts + 1, 'max_attempts': MAX_ATTEMPTS,
            'reason': 'retry of a transient failure (connection reset or HTTP 5xx) under the manifest rule '
                      '"no retry of a failed date without a recorded reason"'})
    return [d for d, _ in rows]


def wanted_securities() -> dict[str, str]:
    """Symbol -> current ISIN for the frozen 20-stock scope, read-only from the historical catalog."""
    db = sqlite3.connect(HISTORICAL_CATALOG.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        return {s: i.split('|', 1)[1] for s, i in db.execute('SELECT symbol, instrument FROM acquisition_scope ORDER BY symbol')}
    finally:
        db.close()


def status_counts(catalog: Catalog):
    return dict(catalog.db.execute('SELECT status, count(*) FROM days GROUP BY status'))


def main():
    ap = argparse.ArgumentParser(description='Collect NSE archive EOD files for the approved purposes.')
    ap.add_argument('action', choices=['probe', 'run', 'listing', 'status', 'retry-failures', 'reparse-failures'])
    ap.add_argument('--days', nargs='*', default=[], help='probe: specific ISO dates')
    ap.add_argument('--max-days', type=int, default=100000)
    ap.add_argument('--pace', type=float, default=PACE_SECONDS, help='seconds between requests; never below the manifest limit')
    a = ap.parse_args()
    with exclusive(ROOT):
        c = Collector()
        if a.pace < PACE_SECONDS:
            raise ValueError(f'--pace below the manifest limit of {PACE_SECONDS}s')
        c.pace = a.pace
        wanted = wanted_securities()
        if len(wanted) != 20:
            raise RuntimeError(f'expected the 20-stock scope, found {len(wanted)}')
        with c.catalog.db:
            c.catalog.db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('wanted', json.dumps(wanted, sort_keys=True)))
        c.catalog.plan(START, END)
        if a.action == 'status':
            print(json.dumps(status_counts(c.catalog)))
        elif a.action == 'reparse-failures':
            days = [d for d, in c.catalog.db.execute("SELECT day FROM days WHERE status='PARSE_FAILURE'")]
            print(json.dumps({d: c.reparse(d, wanted) for d in days}))
        elif a.action == 'retry-failures':
            print(json.dumps({'requeued': retry_transient_failures(c)}))
        elif a.action == 'listing':
            print(json.dumps(c.collect_listing(wanted), indent=1))
        elif a.action == 'probe':
            for d in a.days:
                print(d, c.collect_day(d, wanted), flush=True)
        else:
            c.event('run_started', 'bhavcopy-run', {'max_days': a.max_days})
            done = 0
            try:
                while done < a.max_days and (day := c.catalog.next_day()):
                    c.collect_day(day, wanted); done += 1
                    if done % 100 == 0:
                        print(json.dumps({'processed': done, 'last': day, **status_counts(c.catalog)}), flush=True)
            finally:
                c.event('run_finished', 'bhavcopy-run', {'processed': done, 'states': status_counts(c.catalog)})
            print(json.dumps({'processed': done, **status_counts(c.catalog)}))


if __name__ == '__main__':
    main()
