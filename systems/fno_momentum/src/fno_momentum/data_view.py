"""Read-only local data view of the selected historical OHLCV; no strategy functionality.

Builds a static site (one coverage table for the selected stocks, one page per
stock with charts and quality evidence) plus a Markdown report, from the
historical catalog, its partitions, quality records and the hash-chained ledger.

Every database is opened read-only and every hash shown is re-verified. Nothing
is filled, smoothed or substituted: a missing or quarantined bar is drawn as an
empty chart slot, and an interval with any exclusion is never labelled USABLE.
"""
from __future__ import annotations
import argparse, calendar, gzip, hashlib, html, json, sqlite3, statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from .full_underlying_collection import IST, digest
from .historical_db import MANIFEST, SESSION_EXCEPTIONS_SHA256, VERSION as PARSER_VERSION
from .ledger import sha256_json
from .nse_reference import calendar_checks, compare_daily, derive_weekly, listing_date, load_reference

VIEW_VERSION = 'fno-data-view-v2'
INTERVALS = ('daily', '15m', '5m')
REPO = Path(__file__).resolve().parents[4]
DEFAULT_OUT = REPO / 'data' / 'fno_momentum' / 'data_view'
NSE_CATALOG = REPO / 'data' / 'fno_momentum' / 'nse_archives_v1' / 'catalog.sqlite'
FAILED = ('SOURCE_FAILURE', 'MALFORMED', 'INTERRUPTED', 'UNFETCHED', 'IN_PROGRESS')
# Pinned so a rebuild renders identically; loaded from the CDN, never vendored.
CHART_LIB = 'https://unpkg.com/lightweight-charts@4.2.0/dist/lightweight-charts.standalone.production.js'
USABLE_MEANING = ('USABLE means every stored hash verified, every observed session is complete on its bar grid, '
                  'and no NSE session on which the NSE bhavcopy shows the stock trading is missing. The calendar is '
                  'NSE file existence (approved source nse-archives-public-eod-v1); any day it cannot resolve keeps '
                  'an interval from USABLE.')
DAILY_NOTE = ('Daily is an independent provider series. Its prices are adjusted for splits and bonuses (confirmed against '
              'the NSE bhavcopy, which is as traded). Its close and volume still differ from a 15-minute rollup on most dates '
              'for a reason not yet verified; do not combine the two.')


def _ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def _resolve(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else REPO / p


def _verified(path: Path, expected: str | None, label: str):
    """Decompressed bytes and an error string; the error is shown, never swallowed."""
    try:
        content = gzip.decompress(path.read_bytes())
    except FileNotFoundError:
        return None, f'{label} file missing: {path}'
    except (OSError, EOFError) as exc:
        return None, f'{label} unreadable: {type(exc).__name__}'
    if expected is not None and digest(content) != expected:
        return None, f'{label} hash mismatch: expected {expected[:12]}, found {digest(content)[:12]}'
    return content, None


# Parser v1 and v4 name the same evidence differently; both are live in the store
# (v1 holds all 24 of the 7 March 2022 five-minute gaps). Any list field not named
# here blocks the interval rather than being ignored.
MISSING_KEYS = ('missing_bar_starts_against_known_or_assumed_session',
                'missing_bar_starts_against_regular_session_assumption')
PREOPEN_KEYS = ('provider_preopen_rows_outside_continuous_session',)


def quality_lists(q: dict):
    """(missing starts, pre-open placeholders, unrecognised non-empty list fields)."""
    missing = [x for k in MISSING_KEYS for x in q.get(k, [])]
    preopen = [x for k in PREOPEN_KEYS for x in q.get(k, [])]
    unknown = sorted(k for k, v in q.items()
                     if isinstance(v, list) and v and k != 'issues' and k not in MISSING_KEYS + PREOPEN_KEYS)
    return missing, preopen, unknown


def composite_hash(pairs) -> str:
    """One hash over (window id, hash) pairs; an absent hash is spelled NONE, not blank."""
    lines = sorted(f'{k}:{"NONE" if v is None else v}' for k, v in pairs)
    return hashlib.sha256('\n'.join(lines).encode()).hexdigest()


def classify(s: dict):
    """(status, reasons, notes). Reasons downgrade and carry their numbers; notes do not."""
    if s['hash_errors']:
        return 'BLOCKED', [f"{len(s['hash_errors'])} stored file(s) failed verification"], []
    if s['valid_rows'] == 0:
        why = [f"{s['failed_windows']} planned window(s) not successfully fetched"] if s['failed_windows'] else []
        return 'BLOCKED', why or ['no validated rows stored'], []
    reasons, notes = [], []
    if s['invalid_rows']:
        reasons.append(f"{s['invalid_rows']} provider row(s) quarantined as invalid")
    if s['missing_starts']:
        reasons.append(f"{s['missing_starts']} bar start(s) absent inside observed sessions")
    if s['unavailable_windows']:
        reasons.append(f"{s['unavailable_windows']} request window(s) returned no provider rows")
    if s['failed_windows']:
        reasons.append(f"{s['failed_windows']} planned window(s) not successfully fetched")
    if s.get('prelisting_windows'):
        notes.append(f"{s['prelisting_windows']} empty request window(s) end before the NSE listing date; empty because the stock was not yet listed")
    if s['collapsed_duplicates']:
        notes.append(f"{s['collapsed_duplicates']} identical duplicate copy(ies) collapsed to one canonical row; no values lost")
    if s['preopen_rows']:
        notes.append(f"{s['preopen_rows']} zero-volume pre-open provider placeholder(s) retained and flagged; not continuous-trading bars")
    return ('PARTIALLY USABLE' if reasons else 'USABLE'), reasons, notes


def chart_time(stamp: str, interval: str):
    """Daily: the IST session date. Intraday: IST wall-clock seconds, because the chart renders UTC."""
    dt = datetime.fromisoformat(stamp)
    if dt.tzinfo is not None:
        dt = dt.astimezone(IST)
    if interval == 'daily':
        return dt.date().isoformat()
    return calendar.timegm(dt.replace(tzinfo=None).timetuple())


def merge_gaps(rows, interval, gaps):
    """Columnar series with an empty (null) slot at each gap. A gap never displaces an observed bar."""
    series = {chart_time(r[0], interval): r for r in rows}
    marks = []
    slots = dict.fromkeys(series)
    for stamp, kind, label in gaps:
        try:
            t = chart_time(stamp, interval)
        except (TypeError, ValueError):
            continue
        if t not in series:
            slots[t] = None
        marks.append({'t': t, 'k': kind, 'label': label})
    out = {k: [] for k in 'tohlcv'}
    for t in sorted(slots):
        r = series.get(t)
        out['t'].append(t)
        for i, k in enumerate('ohlcv', start=1):
            out[k].append(r[i] if r else None)
    out['marks'] = sorted(marks, key=lambda m: m['t'])
    return out


def _weekday_holes(dates: set[str]) -> list[str]:
    if not dates:
        return []
    day, last = date.fromisoformat(min(dates)), date.fromisoformat(max(dates))
    holes = []
    while day <= last:
        if day.weekday() < 5 and day.isoformat() not in dates:
            holes.append(day.isoformat())
        day += timedelta(days=1)
    return holes


def _ist(stamp: str | None) -> str | None:
    if not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp).astimezone(IST).strftime('%Y-%m-%d %H:%M IST')
    except ValueError:
        return stamp


def read_ledger(path: Path) -> dict:
    """Read-only chain replay plus the collection and normalization events per window."""
    info = {'status': 'ABSENT', 'events': 0, 'raw': {}, 'recorded': defaultdict(list), 'error': None, 'head': None}
    if not Path(path).exists():
        return info
    db = _ro(path)
    previous = ''
    try:
        for row in db.execute('SELECT * FROM ledger_event ORDER BY sequence'):
            payload = json.loads(row['payload_json'])
            body = {k: row[k] for k in ('event_id', 'track_id', 'event_type', 'aggregate_id', 'occurred_at', 'recorded_at',
                                         'decision_at', 'data_cutoff', 'payload_hash', 'previous_hash', 'idempotency_key')}
            if row['previous_hash'] != previous or sha256_json(payload) != row['payload_hash'] or sha256_json(body) != row['event_hash']:
                info.update(status='FAILED', error=f"ledger integrity failure at sequence {row['sequence']}")
                break
            previous = row['event_hash']
            info['events'] += 1
            if row['event_type'] == 'raw_response_captured':
                info['raw'][row['event_id']] = row['occurred_at']
            elif row['event_type'] == 'historical_db_window_recorded':
                info['recorded'][row['aggregate_id']].append({
                    'at': row['occurred_at'], 'normalized': payload.get('normalized_sha256'),
                    'quality': payload.get('quality_sha256'),
                    'parser': payload.get('historical_parser_version'), 'code': payload.get('historical_code_sha256')})
        else:
            info['status'] = 'VERIFIED'
        info['head'] = previous or None
    finally:
        db.close()
    return info


def _interval(windows, interval, ledger, verify_raw, session_missing):
    s = {'interval': interval, 'windows': [], 'hash_errors': [], 'valid_rows': 0, 'invalid_rows': 0,
         'collapsed_duplicates': 0, 'missing_starts': 0, 'unavailable_windows': 0, 'failed_windows': 0,
         'preopen_rows': 0, 'state_counts': Counter(), 'parser_versions': Counter(), 'parser_code': Counter(),
         'manifests': Counter(), 'exceptions_sha': Counter(), 'collected': [], 'normalized_at': [],
         'extra_reasons': [], 'extra_notes': [], 'prelisting_windows': 0, 'nse': None}
    rows, gaps, issues = [], [], []
    for w in windows:
        s['state_counts'][w['status']] += 1
        s['manifests'][w['manifest_hash']] += 1
        if w['status'] == 'UNAVAILABLE':
            s['unavailable_windows'] += 1
        if w['status'] in FAILED:
            s['failed_windows'] += 1
        rec = {k: w[k] for k in ('id', 'start', 'end', 'status', 'reason', 'returned', 'valid', 'invalid',
                                 'raw_hash', 'normalized_hash')}
        rec['collected_at'] = ledger['raw'].get(w['source_event'])
        if rec['collected_at']:
            s['collected'].append(rec['collected_at'])
        match = [e for e in ledger['recorded'].get(w['id'], []) if e['normalized'] == w['normalized_hash']]
        if match:
            s['normalized_at'].append(match[-1]['at'])
            s['parser_code'][match[-1]['code']] += 1
        if w['normalized_path']:
            content, err = _verified(_resolve(w['normalized_path']), w['normalized_hash'], 'normalized partition')
            if err:
                s['hash_errors'].append(f"{w['id']}: {err}")
            else:
                doc = json.loads(content)
                s['parser_versions'][doc.get('parser_version')] += 1
                s['exceptions_sha'][doc.get('session_exceptions_sha256')] += 1
                if len(doc['rows']) != (w['valid'] or 0):
                    s['hash_errors'].append(f"{w['id']}: partition holds {len(doc['rows'])} rows, catalog says {w['valid']}")
                rows.extend(doc['rows'])
        if w['quality_path']:
            qp = _resolve(w['quality_path'])
            # Parser v1 named quality files by a different serialization than it stored,
            # so the ledger's recorded content hash is the reference; the file name is
            # used only when no ledger event links the window.
            expected = match[-1]['quality'] if match and match[-1]['quality'] else qp.name.split('.')[0]
            content, err = _verified(qp, expected, 'quality record')
            if err:
                s['hash_errors'].append(f"{w['id']}: {err}")
            else:
                q = json.loads(content)
                for issue in q.get('issues', []):
                    issues.append({**issue, 'window': w['id']})
                    if issue['reason'] == 'identical_daily_duplicate_collapsed':
                        s['collapsed_duplicates'] += 1
                    else:
                        s['invalid_rows'] += 1
                        if issue.get('timestamp'):
                            gaps.append((issue['timestamp'], 'quarantined', f"quarantined: {issue['reason']}"))
                missing, preopen, unknown = quality_lists(q)
                for stamp in missing:
                    s['missing_starts'] += 1
                    gaps.append((stamp, 'missing', 'absent start'))
                for stamp in preopen:
                    s['preopen_rows'] += 1
                    gaps.append((stamp, 'preopen', 'pre-open placeholder'))
                if unknown:
                    s['hash_errors'].append(f"{w['id']}: quality record has unrecognised evidence field(s) {unknown}; not interpreted")
                if w['interval'] != 'daily' and session_missing.get(w['id'], 0) != len(missing):
                    s['hash_errors'].append(f"{w['id']}: sessions table counts {session_missing.get(w['id'], 0)} missing start(s), quality record lists {len(missing)}")
        elif w['status'] not in ('UNFETCHED',):
            s['hash_errors'].append(f"{w['id']}: no quality record linked")
        if verify_raw and w['raw_path']:
            _, err = _verified(_resolve(w['raw_path']), w['raw_hash'], 'raw response')
            if err:
                s['hash_errors'].append(f"{w['id']}: {err}")
        s['windows'].append(rec)
    rows.sort(key=lambda r: r[0])
    s['valid_rows'] = len(rows)
    s['first'] = rows[0][0] if rows else None
    s['last'] = rows[-1][0] if rows else None
    s['raw_hash'] = composite_hash((w['id'], w['raw_hash']) for w in windows)
    s['normalized_hash'] = composite_hash((w['id'], w['normalized_hash']) for w in windows)
    s['status'], s['reasons'], s['notes'] = classify(s)
    s['issues'] = issues
    s['gaps'] = sorted(({'stamp': g[0], 'kind': g[1], 'label': g[2]} for g in gaps), key=lambda g: g['stamp'])
    s['dates'] = {chart_time(r[0], 'daily') for r in rows}
    s['weekend_dates'] = sorted(d for d in s['dates'] if date.fromisoformat(d).weekday() >= 5)
    return s, rows


def _finalize(s):
    for key in ('state_counts', 'parser_versions', 'parser_code', 'manifests', 'exceptions_sha'):
        s[key] = dict(s[key])
    for key in ('collected', 'normalized_at'):
        vals = sorted(s.pop(key))
        s[key] = [vals[0], vals[-1]] if vals else None
    s.pop('dates')
    return s


def build(catalog: Path, ledger_path: Path, out: Path, *, symbols=None, verify_raw=True, nse_catalog=None) -> dict:
    catalog, out = Path(catalog), Path(out)
    catalog_sha = digest(catalog.read_bytes())
    ledger = read_ledger(ledger_path)
    ref = load_reference(nse_catalog) if nse_catalog else None
    db = _ro(catalog)
    try:
        scope = {r['symbol']: r['instrument'] for r in db.execute('SELECT symbol,instrument FROM acquisition_scope')}
        order = [x for x in (symbols or []) if x in scope] + sorted(set(scope) - set(symbols or []))
        weekly = {r['instrument']: dict(r) for r in db.execute('SELECT * FROM weekly_status')}
        meta = {r['key']: r['value'] for r in db.execute('SELECT key,value FROM meta')}
        (out / 'series').mkdir(parents=True, exist_ok=True)
        (out / 'stock').mkdir(parents=True, exist_ok=True)
        stocks = []
        for symbol in order:
            instrument = scope[symbol]
            stock = {'symbol': symbol, 'instrument': instrument, 'intervals': {},
                     'weekly': weekly.get(instrument, {'status': 'UNAVAILABLE_CALENDAR', 'rule': None})}
            date_sets, rows_by = {}, {}
            for interval in INTERVALS:
                windows = [dict(r) for r in db.execute(
                    'SELECT * FROM windows WHERE instrument=? AND interval=? ORDER BY start', (instrument, interval))]
                session_missing = {r[0]: r[1] or 0 for r in db.execute(
                    'SELECT s.window_id,sum(s.missing) FROM sessions s JOIN windows w ON w.id=s.window_id '
                    'WHERE w.instrument=? AND w.interval=? GROUP BY s.window_id', (instrument, interval))}
                s, rows = _interval(windows, interval, ledger, verify_raw, session_missing)
                date_sets[interval] = s['dates']
                rows_by[interval] = rows
                series = merge_gaps(rows, interval, [(g['stamp'], g['kind'], g['label']) for g in s['gaps']])
                name = f'{symbol}_{interval}.js'
                payload = ('window.FNO_SERIES=window.FNO_SERIES||{};window.FNO_SERIES[' + json.dumps(f'{symbol}|{interval}')
                           + ']=' + json.dumps(series, separators=(',', ':'), allow_nan=False) + ';').encode()
                (out / 'series' / name).write_bytes(payload)
                s['series_file'] = {'path': f'series/{name}', 'sha256': digest(payload), 'bytes': len(payload)}
                stock['intervals'][interval] = s
            _cross_series(stock, date_sets)
            _nse_checks(stock, rows_by, ref, out)
            _basis_check(stock, rows_by)
            for s in stock['intervals'].values():
                _settle(s)
                _finalize(s)
            rank = {'BLOCKED': 2, 'PARTIALLY USABLE': 1, 'USABLE': 0}
            statuses = [s['status'] for s in stock['intervals'].values()]
            if stock['weekly'].get('derived'):
                statuses.append(stock['weekly']['status'])
            stock['status'] = max(statuses, key=rank.get)
            stocks.append(stock)
            (out / 'stock' / f'{symbol}.html').write_text(stock_page(stock), encoding='utf-8')
    finally:
        db.close()
    summary = {
        'view_version': VIEW_VERSION, 'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'catalog': str(catalog), 'catalog_sha256': catalog_sha,
        'catalog_unchanged_by_build': digest(catalog.read_bytes()) == catalog_sha,
        'catalog_meta': meta, 'manifest_path': str(MANIFEST),
        'manifest_sha256': digest(MANIFEST.read_bytes()) if MANIFEST.exists() else None,
        'current_parser_version': PARSER_VERSION, 'current_session_exceptions_sha256': SESSION_EXCEPTIONS_SHA256,
        'raw_verified': verify_raw,
        'nse': _nse_summary(ref, nse_catalog),
        'ledger': {k: ledger[k] for k in ('status', 'events', 'error', 'head')},
        'stocks': stocks,
    }
    (out / 'eligibility.json').write_text(json.dumps(eligibility_manifest(stocks, ref, catalog_sha), indent=1), encoding='utf-8')
    (out / 'index.html').write_text(index_page(summary), encoding='utf-8')
    (out / 'report.md').write_text(report_md(summary), encoding='utf-8')
    blob = json.dumps(summary, indent=1, default=str).encode()
    (out / 'summary.json').write_bytes(blob)
    files = sorted(p for p in out.rglob('*') if p.is_file() and p.name != 'view_manifest.json')
    (out / 'view_manifest.json').write_text(json.dumps({
        'view_version': VIEW_VERSION, 'catalog_sha256': catalog_sha, 'ledger_head': ledger['head'],
        'files': {p.relative_to(out).as_posix(): digest(p.read_bytes()) for p in files}}, indent=1), encoding='utf-8')
    return summary


def _cross_series(stock, date_sets):
    """Checks that need no calendar: weekday holes, and dates one series has that another lacks."""
    intraday = date_sets['15m'] | date_sets['5m']
    daily = date_sets['daily']
    for interval, s in stock['intervals'].items():
        holes = _weekday_holes(date_sets[interval])
        s['weekday_holes'] = holes
        if interval == 'daily':
            start = min(intraday) if intraday else None
            s['cross_missing'] = sorted(d for d in intraday if d not in daily)
            s['cross_basis'] = 'intraday dates with no daily row'
            s['holes_note'] = 'weekdays between first and last daily row with no daily row (NSE holidays and any missing sessions; not separable without a verified calendar)'
            s['cross_window'] = start
        else:
            s['cross_missing'] = sorted(d for d in daily if d not in date_sets[interval] and d >= (s['first'] or '9')[:10])
            s['cross_basis'] = f'daily dates with no {interval} bars'
            s['holes_note'] = f'weekdays between first and last {interval} bar with no {interval} bars (NSE holidays and any missing sessions; not separable without a verified calendar)'
        if s['cross_missing']:
            s['extra_reasons'].append(f"{len(s['cross_missing'])} date(s) present in another series but absent here")


# Measured 30 Sep 2026: 16 of the 20 stocks read exactly 1.000 in every year, and
# HINDPETRO, TATASTEEL, TECHM and VEDL read 0.92-0.98 before 2025, so a 0.5% band
# separates the two groups with room on both sides.
BASIS_TOLERANCE = 0.005


def basis_by_year(daily_rows, intraday_rows):
    """Median of (first intraday open / stored daily open) per year, over days both series hold."""
    daily = {chart_time(r[0], 'daily'): r[1] for r in daily_rows}
    first = {}
    for r in intraday_rows:                              # rows arrive sorted, so the first seen is the open
        first.setdefault(chart_time(r[0], 'daily'), r[1])
    by = defaultdict(list)
    for day, op in first.items():
        if day in daily and daily[day]:
            by[day[:4]].append(op / daily[day])
    out = {}
    for year, vals in sorted(by.items()):
        m = round(statistics.median(vals), 3)
        out[year] = {'median_ratio': m, 'days': len(vals), 'differs': abs(m - 1) > BASIS_TOLERANCE}
    return out


def _basis_check(stock, rows_by):
    """Daily and intraday stored prices on different adjustment bases cannot be mixed; say where."""
    years = basis_by_year(rows_by['daily'], rows_by['15m'])
    stock['basis'] = years
    bad = {y: v['median_ratio'] for y, v in years.items() if v['differs']}
    if bad:
        text = ('stored 15-minute and daily prices are on different adjustment bases in ' + ', '.join(f'{y} (intraday/daily {r})' for y, r in bad.items())
                + '; each series is internally consistent, but do not mix daily price levels with intraday bars for those years')
        for iv in INTERVALS:
            stock['intervals'][iv]['extra_notes'].append(text)


def eligibility_manifest(stocks, ref, catalog_sha):
    """Every exclusion the view shows, keyed by session or week, for `eligible_data` to enforce."""
    def days(stamps):
        out = set()
        for s in stamps:
            try:
                out.add(chart_time(s, 'daily'))
            except (TypeError, ValueError):
                pass
        return out
    result = {'view_version': VIEW_VERSION, 'catalog_sha256': catalog_sha,
              'nse_catalog_sha256': ref.catalog_sha256 if ref else None,
              'nse_sessions': sorted(d for d, st in ref.days.items() if st == 'PUBLISHED') if ref else [],
              'nse_unresolved': sorted(d for d, st in ref.days.items() if st not in ('PUBLISHED', 'NOT_PUBLISHED')) if ref else [],
              'stocks': {}}
    for st in stocks:
        listed = (st.get('nse') or {}).get('listing_date')
        daily_first = st['intervals']['daily']['first']
        before = listed if listed and daily_first and chart_time(daily_first, 'daily') < listed else None
        entry = {'status': st['status'], 'listing_date': listed, 'basis': st.get('basis', {}), 'intervals': {},
                 'exclude_before': before}
        for iv, s in st['intervals'].items():
            ex = {}
            for kind, reason in (('missing', 'absent bar start(s) in the session'), ('preopen', 'pre-open placeholder rows in the session'),
                                 ('quarantined', 'provider row quarantined as invalid')):
                for d in days(g['stamp'] for g in s['gaps'] if g['kind'] == kind):
                    ex.setdefault(d, reason)
            c = s.get('nse') or {}
            for d in c.get('missing_sessions', []):
                ex.setdefault(d, 'NSE session with no stored bar')
            for d in c.get('untraded_sessions', []):
                ex.setdefault(d, 'NSE session with no NSE row for the stock')
            entry['intervals'][iv] = {'status': s['status'], 'excluded': dict(sorted(ex.items())),
                                      'first': chart_time(s['first'], 'daily') if s['first'] else None,
                                      'last': chart_time(s['last'], 'daily') if s['last'] else None}
        w = st['weekly']
        weekly = {'derived': bool(w.get('derived')), 'why': w.get('why'), 'excluded_weeks': {}}
        if w.get('derived'):
            ex = {wk: 'an NSE session in the week has no stored daily bar' for wk in w['incomplete_weeks']}
            ex |= {wk: 'a calendar day in the week is unresolved' for wk in w['unresolved_weeks']}
            if before:
                first_week = date.fromisoformat(chart_time(daily_first, 'daily'))
                d = first_week - timedelta(days=first_week.weekday())
                while d.isoformat() < before:
                    ex.setdefault(d.isoformat(), 'before the NSE listing date; security lineage unverified')
                    d += timedelta(days=7)
            weekly['excluded_weeks'] = dict(sorted(ex.items()))
        entry['weekly'] = weekly
        result['stocks'][st['symbol']] = entry
    return result


def _settle(s):
    """Final status: the base classification plus every cross-check reason, which can only downgrade."""
    status, reasons, notes = classify(s)
    reasons += s.pop('extra_reasons'); notes += s.pop('extra_notes')
    if reasons and status == 'USABLE':
        status = 'PARTIALLY USABLE'
    s['status'], s['reasons'], s['notes'] = status, reasons, notes


def _nse_summary(ref, path):
    if ref is None:
        return {'status': 'ABSENT', 'catalog': str(path) if path else None}
    return {'status': 'LOADED', 'catalog': str(path), 'catalog_sha256': ref.catalog_sha256,
            'days': dict(Counter(ref.days.values())), 'errors': ref.errors,
            'first_day': min(ref.days) if ref.days else None, 'last_day': max(ref.days) if ref.days else None}


def _nse_checks(stock, rows_by, ref, out):
    """Calendar, listing and daily cross-checks against the approved NSE archive. Nothing is replaced."""
    sym = stock['symbol']
    stock['nse'] = {'listing_date': listing_date(ref, sym), 'listing_row': ref.listing.get(sym) if ref else None}
    if ref is None:
        stock['weekly'] = {**stock['weekly'], 'derived': False, 'why': 'NSE archive reference not built'}
        return
    listed = stock['nse']['listing_date']
    traded = ref.stock_rows.get(sym, {})
    for iv, s in stock['intervals'].items():
        if not s['first']:
            s['nse'] = None
            continue
        first, last = chart_time(s['first'], 'daily'), chart_time(s['last'], 'daily')
        quarantined = set()
        for g in s['gaps']:
            if g['kind'] == 'quarantined':
                try:
                    quarantined.add(chart_time(g['stamp'], 'daily'))
                except (TypeError, ValueError):
                    pass
        c = calendar_checks(s['dates'], first, last, ref, sym, quarantined)
        s['nse'] = c
        if c['missing_sessions']:
            s['extra_reasons'].append(f"{len(c['missing_sessions'])} NSE session(s) on which NSE shows {sym} trading but no {LABEL[iv]} bars are stored")
        if c['conflicts']:
            s['extra_reasons'].append(f"{len(c['conflicts'])} stored day(s) on which NSE published no bhavcopy (conflict)")
        if c['unresolved_days']:
            s['extra_reasons'].append(f"NSE calendar unresolved for {len(c['unresolved_days'])} day(s) in range (file not fetched or fetch failed)")
        if c['confirmed_holidays']:
            s['extra_notes'].append(f"{len(c['confirmed_holidays'])} weekday hole(s) confirmed as NSE non-sessions (no bhavcopy published)")
        if c['untraded_sessions']:
            s['extra_notes'].append(f"{len(c['untraded_sessions'])} NSE session(s) with no NSE row for this stock (suspended, not traded, or an older symbol not matched); not counted as missing")
        if iv != 'daily':
            continue
        before = sorted(d for d in traded if d < first)
        if before:
            s['extra_notes'].append(f"NSE shows {len(before)} traded session(s) from {before[0]} before the first stored daily bar; the provider's history starts later")
        if listed:
            pre = [w for w in s['windows'] if w['status'] == 'UNAVAILABLE' and w['end'] < listed]
            s['prelisting_windows'] = len(pre)
            s['unavailable_windows'] -= len(pre)
        cmp_ = compare_daily(rows_by['daily'], traded)
        (out / 'compare').mkdir(exist_ok=True)
        lines = ['day,fields,stored_open,stored_high,stored_low,stored_close,stored_volume,nse_series,nse_open,nse_high,nse_low,nse_close,nse_volume']
        for m in cmp_['mismatches']:
            n = m['nse']
            lines.append(','.join(str(x) for x in [m['day'], '|'.join(m['fields']), *m['stored'], n['series'], n['open'], n['high'], n['low'], n['close'], n['volume']]))
        blob = ('\n'.join(lines) + '\n').encode()
        (out / 'compare' / f'{sym}_daily_vs_nse.csv').write_bytes(blob)
        s['nse_compare'] = {k: v for k, v in cmp_.items() if k != 'mismatches'} | {
            'recent': cmp_['mismatches'][-200:], 'csv': f'compare/{sym}_daily_vs_nse.csv', 'csv_sha256': digest(blob)}
        if cmp_['compared']:
            diffs = ', '.join(f'{k} {v:,}' for k, v in cmp_['field_diffs'].items()) or 'none'
            s['extra_notes'].append(f"Cross-checked with NSE on {cmp_['compared']:,} date(s): {cmp_['identical']:,} identical; field differences: {diffs}. Both values are kept; neither replaces the other")
        # A quarantined day may have no matched NSE row (e.g. VEDL in 2005 traded under an
        # earlier symbol and ISIN); it is still listed, with the absence stated.
        s['nse_side_by_side'] = [{'day': d, **traded.get(d, {'series': 'no NSE row matched for this stock'})}
                                 for d in c['quarantined_sessions']]
        sessions = [d for d, st in ref.days.items() if st == 'PUBLISHED' and first <= d <= last]
        w = derive_weekly(rows_by['daily'], sessions, unresolved=c['unresolved_days'])
        incomplete, unresolved = w['incomplete_weeks'], w['unresolved_weeks']
        if w['rows']:
            series = merge_gaps(w['rows'], 'daily', [(wk, 'incomplete', 'incomplete week: an NSE session has no stored daily bar') for wk in incomplete]
                                + [(wk, 'unresolved', 'unresolved week: a calendar day could not be resolved') for wk in unresolved])
            payload = ('window.FNO_SERIES=window.FNO_SERIES||{};window.FNO_SERIES[' + json.dumps(f'{sym}|weekly')
                       + ']=' + json.dumps(series, separators=(',', ':'), allow_nan=False) + ';').encode()
            (out / 'series' / f'{sym}_weekly.js').write_bytes(payload)
            stock['weekly'] = {'status': 'PARTIALLY USABLE' if incomplete or unresolved else 'USABLE', 'derived': True, 'rule': w['rule'],
                               'weeks': len(w['rows']), 'incomplete_weeks': incomplete, 'unresolved_weeks': unresolved,
                               'first': w['rows'][0][0] if w['rows'] else None,
                               'last': w['rows'][-1][0] if w['rows'] else None,
                               'series_file': {'path': f'series/{sym}_weekly.js', 'sha256': digest(payload), 'bytes': len(payload)}}
        else:
            stock['weekly'] = {**stock['weekly'], 'derived': False, 'why': 'no week could be derived'}


# ---------------------------------------------------------------- rendering
e = html.escape
PILL = {'USABLE': 'ok', 'PARTIALLY USABLE': 'part', 'BLOCKED': 'block', 'UNAVAILABLE_CALENDAR': 'na'}
LABEL = {'daily': 'Daily', '15m': '15-minute', '5m': '5-minute'}


def pill(status):
    return f'<span class="pill {PILL.get(status, "na")}">{e(status)}</span>'


def _range(s, fmt=True):
    if not s['first']:
        return '—'
    f = (lambda x: x[:10]) if s['interval'] == 'daily' else (lambda x: _ist(x))
    return f'{f(s["first"])} → {f(s["last"])}'


def _head(title, depth=''):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{e(title)}</title><style>{CSS}</style></head><body><main>')


def _banner():
    return ('<p class="banner">Data view only. Read-only over the stored historical catalog. No strategy, pattern, '
            'backtest, alert or trading function exists here, and none is authorized.</p>')


def index_page(summary):
    stocks = summary['stocks']
    tot = Counter(); states = Counter(); status_n = Counter()
    for st in stocks:
        status_n[st['status']] += 1
        for iv, s in st['intervals'].items():
            tot[iv] += s['valid_rows']
            states.update(s['state_counts'])
    ledger = summary['ledger']
    herr = sum(len(s['hash_errors']) for st in stocks for s in st['intervals'].values())
    out = [_head('FnO 20-stock data foundation'), '<header><p class="eyebrow">FnO clean-room track · historical underlying OHLCV</p>',
           f'<h1>Data foundation: {len(stocks)} selected stocks</h1>', _banner(),
           f'<p class="muted">Built {e(summary["generated_at_utc"][:19])} UTC by {VIEW_VERSION}. {e(USABLE_MEANING)}</p></header>']
    out.append('<section class="tiles">')
    for iv in INTERVALS:
        out.append(f'<div class="tile"><span>{LABEL[iv]} validated bars</span><b>{tot[iv]:,}</b></div>')
    derived = [st['weekly'] for st in stocks if st['weekly'].get('derived')]
    out.append(f'<div class="tile"><span>Weekly derived bars</span><b>{sum(w["weeks"] for w in derived):,}</b>'
               f'<small>{len(derived)} of {len(stocks)} stocks derivable; '
               f'{sum(len(w["incomplete_weeks"]) for w in derived):,} incomplete week(s) left empty</small></div>')
    nse = summary['nse']
    if nse['status'] == 'LOADED':
        d = nse['days']
        out.append(f'<div class="tile"><span>NSE calendar days</span><b>{sum(d.values()):,}</b><small>'
                   + ', '.join(f'{k} {v:,}' for k, v in sorted(d.items())) + '</small></div>')
    else:
        out.append('<div class="tile"><span>NSE calendar</span><b>—</b><small>archive reference not built</small></div>')
    out.append(f'<div class="tile"><span>Request windows</span><b>{sum(states.values()):,}</b><small>'
               + ', '.join(f'{k} {v:,}' for k, v in sorted(states.items())) + '</small></div>')
    out.append(f'<div class="tile"><span>Verification</span><b>{"PASS" if not herr and ledger["status"] == "VERIFIED" else "FAIL"}</b>'
               f'<small>{herr} stored-file error(s); ledger {e(ledger["status"])} ({ledger["events"]:,} events); '
               f'raw responses {"re-hashed" if summary["raw_verified"] else "NOT re-hashed this build"}</small></div>')
    out.append('</section>')
    out.append('<h2>Coverage and quality, all stocks</h2><div class="scroll"><table class="grid"><thead><tr><th>Stock</th><th>Overall</th>'
               + ''.join(f'<th>{LABEL[iv]}</th>' for iv in INTERVALS) + '<th>Weekly</th><th>Exclusions and gaps</th></tr></thead><tbody>')
    for st in stocks:
        cells = []
        for iv in INTERVALS:
            s = st['intervals'][iv]
            cells.append(f'<td>{pill(s["status"])}<div class="num">{s["valid_rows"]:,} rows</div><div class="muted small">{e(_range(s))}</div></td>')
        exc = []
        for iv in INTERVALS:
            for r in st['intervals'][iv]['reasons']:
                exc.append(f'<li><b>{LABEL[iv]}:</b> {e(r)}</li>')
            for n in st['intervals'][iv]['notes']:
                exc.append(f'<li class="muted"><b>{LABEL[iv]}:</b> {e(n)}</li>')
        out.append(f'<tr><td><a href="stock/{e(st["symbol"])}.html"><b>{e(st["symbol"])}</b></a><div class="muted small">{e(st["instrument"])}</div></td>'
                   f'<td>{pill(st["status"])}</td>{"".join(cells)}<td>{_weekly_cell(st["weekly"])}</td>'
                   f'<td><ul class="tight">{"".join(exc) or "<li>none recorded</li>"}</ul></td></tr>')
    out.append('</tbody></table></div>')
    out.append('<h2>Detail by stock and interval</h2><div class="scroll"><table class="grid small"><thead><tr>'
               '<th>Stock</th><th>Interval</th><th>Status</th><th>Rows</th><th>First observed</th><th>Last observed</th>'
               '<th>Windows</th><th>Invalid</th><th>Missing starts</th><th>Empty windows</th><th>Failed</th><th>Weekday holes</th>'
               '<th>Cross-series</th><th>NSE missing sessions</th><th>NSE conflicts</th><th>Calendar unresolved</th><th>NSE compared / identical</th><th>Collected (UTC)</th><th>Parser</th><th>Raw hash</th><th>Normalized hash</th></tr></thead><tbody>')
    for st in stocks:
        for iv in INTERVALS:
            s = st['intervals'][iv]
            coll = ' → '.join(x[:10] for x in s['collected']) if s['collected'] else 'unknown'
            out.append(f'<tr><td>{e(st["symbol"])}</td><td>{iv}</td><td>{pill(s["status"])}</td><td class="num">{s["valid_rows"]:,}</td>'
                       f'<td>{e(_ist(s["first"]) or "—")}</td><td>{e(_ist(s["last"]) or "—")}</td>'
                       f'<td>{e(", ".join(f"{k} {v}" for k, v in sorted(s["state_counts"].items())))}</td>'
                       f'<td class="num">{s["invalid_rows"]}</td><td class="num">{s["missing_starts"]}</td><td class="num">{s["unavailable_windows"]}</td>'
                       f'<td class="num">{s["failed_windows"]}</td><td class="num">{len(s["weekday_holes"])}</td><td class="num">{len(s["cross_missing"])}</td>'
                       + _nse_cells(s) + f'<td>{e(coll)}</td><td>{e(", ".join(str(k) for k in s["parser_versions"]))}</td>'
                       f'<td><code title="{s["raw_hash"]}">{s["raw_hash"][:12]}</code></td><td><code title="{s["normalized_hash"]}">{s["normalized_hash"][:12]}</code></td></tr>')
    out.append('</tbody></table></div>')
    out.append(_provenance(summary))
    out.append(_definitions())
    out.append('</main></body></html>')
    return ''.join(out)


def _provenance(summary):
    rows = [('Catalog', summary['catalog']), ('Catalog SHA-256', summary['catalog_sha256']),
            ('Catalog unchanged by this build', str(summary['catalog_unchanged_by_build'])),
            ('Active manifest', summary['manifest_path']), ('Manifest SHA-256', summary['manifest_sha256']),
            ('Parser version (current code)', summary['current_parser_version']),
            ('Session-exception calendar SHA-256', summary['current_session_exceptions_sha256']),
            ('Ledger', f'{summary["ledger"]["status"]}, {summary["ledger"]["events"]:,} events, head {summary["ledger"]["head"]}'),
            ('Catalog blocks', json.dumps(summary['catalog_meta'].get('blocked'))),
            ('NSE archive reference', json.dumps({k: v for k, v in summary['nse'].items() if k != 'errors'})),
            ('NSE reference errors', json.dumps(summary['nse'].get('errors', [])[:20]))]
    return ('<h2>Provenance</h2><table class="kv">' + ''.join(f'<tr><th>{e(k)}</th><td><code>{e(str(v))}</code></td></tr>' for k, v in rows)
            + '</table>')


def _definitions():
    return ('<h2>How to read this</h2><ul>'
            '<li><b>Raw hash / normalized hash</b> per stock and interval is SHA-256 over the sorted lines '
            '<code>window_id:sha256</code> of every planned window; a window with no stored file contributes <code>NONE</code>. '
            'Each window\'s own hashes are listed on the stock page and re-verified against the stored files on every build.</li>'
            '<li><b>Invalid</b> rows were returned by the provider and quarantined by the parser. They are drawn as empty slots, never replaced.</li>'
            '<li><b>Missing starts</b> are bar starts absent inside a session that has other bars, against the regular 09:15–15:30 grid '
            'or a documented session exception.</li>'
            '<li><b>Empty windows</b> (UNAVAILABLE) returned no provider rows. Empty is not proof the stock was unlisted.</li>'
            '<li><b>Weekday holes</b> are weekdays with no bars between the first and last observation. Each is checked against the NSE archive: '
            'no bhavcopy published = confirmed non-session; bhavcopy published with a row for the stock = <b>missing session</b> (downgrades); '
            'bhavcopy published without a row for the stock = untraded (suspension or unmatched older symbol; noted).</li>'
            '<li><b>Calendar</b> is NSE file existence from the approved source <code>nse-archives-public-eod-v1</code>. A 404 is a non-session; '
            'a failed or unfetched request resolves nothing and keeps the interval from USABLE.</li>'
            '<li><b>NSE conflicts</b> are stored bars on a day NSE published no bhavcopy.</li>'
            '<li><b>NSE compared / identical</b>: daily bars compared field by field with the NSE bhavcopy (prices within 0.011, volume exact). '
            'Differences are listed; both values are kept and neither replaces the other.</li>'
            '<li><b>Cross-series</b> counts dates that another interval of the same stock has and this one lacks. These downgrade status.</li>'
            '<li><b>Weekly</b> bars are derived from the stored daily bars (Monday-anchored, IST) only for weeks in which every day is resolved by '
            'the NSE calendar and every NSE session has a stored daily bar. Other weeks are left empty and listed.</li>'
            f'<li>{e(DAILY_NOTE)}</li></ul>')


def stock_page(st):
    sym = st['symbol']
    tabs = ''.join(f'<button class="tab" data-tab="{iv}">{LABEL[iv]} {pill(st["intervals"][iv]["status"])}</button>' for iv in INTERVALS)
    tabs += f'<button class="tab" data-tab="weekly">Weekly {pill(st["weekly"]["status"])}</button>'
    out = [_head(f'{sym} data', '../'), f'<header><p class="eyebrow"><a href="../index.html">← all stocks</a></p>',
           f'<h1>{e(sym)} {pill(st["status"])}</h1><p class="muted">{e(st["instrument"])} · NSE listing date: '
           f'{e((st.get("nse") or {}).get("listing_date") or "not in NSE EQUITY_L")}</p>', _banner(),
           f'<p class="muted small">{e(USABLE_MEANING)}</p></header>',
           f'<nav class="tabs">{tabs}</nav>']
    page = {'symbol': sym, 'series': {}}
    for iv in INTERVALS:
        s = st['intervals'][iv]
        page['series'][iv] = '../' + s['series_file']['path']
        out.append(f'<section class="panel" id="p-{iv}">')
        complete = s['status'] == 'USABLE'
        cls = 'ok' if complete else ('block' if s['status'] == 'BLOCKED' else 'part')
        lines = ''.join(f'<li>{e(r)}</li>' for r in s['reasons']) + ''.join(f'<li class="muted">{e(n)}</li>' for n in s['notes'])
        head = ('No exclusions recorded for this interval.' if complete else
                'This interval is NOT complete. The chart shows only stored rows; every gap below is an empty slot, not a value.')
        out.append(f'<div class="callout {cls}"><b>{LABEL[iv]}: {e(s["status"])}.</b> {head}<ul class="tight">{lines}</ul>'
                   + (f'<p class="small">{e(DAILY_NOTE)}</p>' if iv == 'daily' else '') + '</div>')
        out.append('<div class="facts">' + ''.join(f'<div><span>{e(k)}</span><b>{e(str(v))}</b></div>' for k, v in [
            ('Validated rows', f'{s["valid_rows"]:,}'), ('First observed', _ist(s['first']) or '—'), ('Last observed', _ist(s['last']) or '—'),
            ('Windows', ', '.join(f'{k} {v}' for k, v in sorted(s['state_counts'].items()))),
            ('Collected (UTC)', ' → '.join(x[:19] for x in s['collected']) if s['collected'] else 'unknown (no ledger link)'),
            ('Last normalized (UTC)', ' → '.join(x[:19] for x in s['normalized_at']) if s['normalized_at'] else 'unknown'),
            ('Parser version', ', '.join(f'{k} ×{v}' for k, v in s['parser_versions'].items()) or '—'),
            ('Weekend sessions observed', ', '.join(s['weekend_dates']) or 'none')]) + '</div>')
        out.append(f'<div class="controls"><label>Go to date <input type="date" data-goto="{iv}"></label>'
                   f'<span class="muted small">Scroll or pinch to zoom. Times are IST. Shaded full-height bands: '
                   f'<span class="key missing">absent start</span> <span class="key quarantined">quarantined row</span> '
                   f'<span class="key preopen">pre-open placeholder</span></span></div>')
        out.append(f'<div class="chart" id="chart-{iv}"><p class="muted">Select this tab to load the chart.</p></div>')
        out.append(_gap_table(s, iv))
        out.append(_details(f'Weekday holes ({len(s["weekday_holes"])})', s['holes_note'], s['weekday_holes']))
        out.append(_details(f'Cross-series: {s["cross_basis"]} ({len(s["cross_missing"])})',
                            'Dates another interval of this stock has and this interval lacks.', s['cross_missing']))
        out.append(_nse_block(s, iv))
        out.append(_window_table(s))
        if s['hash_errors']:
            out.append('<div class="callout block"><b>Verification errors</b><ul>' + ''.join(f'<li><code>{e(x)}</code></li>' for x in s['hash_errors']) + '</ul></div>')
        out.append(f'<table class="kv small"><tr><th>Raw-source hash (composite)</th><td><code>{s["raw_hash"]}</code></td></tr>'
                   f'<tr><th>Normalized-data hash (composite)</th><td><code>{s["normalized_hash"]}</code></td></tr>'
                   f'<tr><th>Parser code SHA-256 (from ledger)</th><td><code>{e(", ".join(str(k) for k in s["parser_code"]) or "not linked")}</code></td></tr>'
                   f'<tr><th>Session-exception calendar SHA-256</th><td><code>{e(", ".join(str(k) for k in s["exceptions_sha"]) or "—")}</code></td></tr>'
                   f'<tr><th>Manifest SHA-256 at ingest</th><td><code>{e(", ".join(str(k) for k in s["manifests"]))}</code></td></tr>'
                   f'<tr><th>Chart series file</th><td><code>{e(s["series_file"]["path"])} {s["series_file"]["sha256"]}</code></td></tr></table>')
        out.append('</section>')
    w = st['weekly']
    if w.get('derived'):
        page['series']['weekly'] = '../' + w['series_file']['path']
        cls = 'ok' if w['status'] == 'USABLE' else 'part'
        out.append(f'<section class="panel" id="p-weekly"><div class="callout {cls}"><b>Weekly: {e(w["status"])}.</b> '
                   f'{w["weeks"]:,} week(s) derived from stored daily bars under rule <code>{e(str(w["rule"]))}</code> (Monday, IST), '
                   f'{w["first"]} → {w["last"]}. {len(w["incomplete_weeks"]):,} week(s) have an NSE session with no stored daily bar and '
                   f'{len(w["unresolved_weeks"]):,} week(s) contain a calendar day NSE could not resolve; both are left empty, not estimated.<p class="small">{e(DAILY_NOTE)} Weekly inherits that basis.</p></div>'
                   f'<div class="controls"><label>Go to date <input type="date" data-goto="weekly"></label>'
                   f'<span class="muted small">Shaded bands: <span class="key missing">incomplete week</span> '
                   f'<span class="key preopen">unresolved week</span></span></div>'
                   f'<div class="chart" id="chart-weekly"><p class="muted">Select this tab to load the chart.</p></div>'
                   + _details(f'Incomplete weeks ({len(w["incomplete_weeks"])})', 'Week starting dates; each contains at least one NSE session with no stored daily bar.', w['incomplete_weeks'])
                   + _details(f'Unresolved weeks ({len(w["unresolved_weeks"])})', 'Week starting dates; each contains a day whose NSE file could not be fetched after three attempts.', w['unresolved_weeks'])
                   + f'<p class="small muted">Series file <code>{e(w["series_file"]["path"])} {w["series_file"]["sha256"]}</code></p></section>')
    else:
        out.append(f'<section class="panel" id="p-weekly"><div class="callout na"><b>Weekly: {e(w["status"])}.</b> No weekly bars are shown: '
                   f'{e(w.get("why") or "calendar not verified")}. Weekly bars are derived only for weeks whose every day the NSE calendar resolves '
                   f'(rule <code>{e(str(w.get("rule")))}</code>, Monday IST).</div></section>')
    out.append(f'<script>window.FNO_PAGE={json.dumps(page)};</script><script src="{CHART_LIB}"></script><script>{STOCK_JS}</script>')
    out.append('</main></body></html>')
    return ''.join(out)


def _weekly_cell(w):
    if w.get('derived'):
        return (f'{pill(w["status"])}<div class="num">{w["weeks"]:,} weeks</div><div class="muted small">{len(w["incomplete_weeks"])} incomplete, '
                f'{len(w["unresolved_weeks"])} unresolved</div>')
    return f'{pill(w["status"])}<div class="muted small">{e(w.get("why") or "")}</div>'


def _nse_cells(s):
    c = s.get('nse')
    if not c:
        return '<td>—</td>' * 4
    cmp_ = s.get('nse_compare')
    both = f'{cmp_["compared"]:,} / {cmp_["identical"]:,}' if cmp_ else '—'
    return (f'<td class="num">{len(c["missing_sessions"])}</td><td class="num">{len(c["conflicts"])}</td>'
            f'<td class="num">{len(c["unresolved_days"])}</td><td class="num">{both}</td>')


def _nse_block(s, iv):
    c = s.get('nse')
    if not c:
        return '<p class="muted small">NSE archive reference not available for this build.</p>'
    out = [f'<h3>NSE calendar check ({"verified" if c["verified"] else "NOT verified"})</h3><div class="facts">']
    for k, v in [('NSE sessions in range', f'{c["sessions"]:,}'), ('Missing sessions (NSE traded, nothing stored)', len(c['missing_sessions'])),
                 ('Conflicts (stored, NSE published nothing)', len(c['conflicts'])), ('Unresolved calendar days', len(c['unresolved_days'])),
                 ('Confirmed weekday non-sessions', len(c['confirmed_holidays'])), ('Sessions with no NSE row for stock', len(c['untraded_sessions']))]:
        out.append(f'<div><span>{e(k)}</span><b>{e(str(v))}</b></div>')
    out.append('</div>')
    for title, key, note in [('Missing sessions', 'missing_sessions', 'NSE bhavcopy has a row for this stock; no bars are stored for the day.'),
                             ('Conflicts', 'conflicts', 'Bars are stored for a day on which NSE published no bhavcopy.'),
                             ('Unresolved calendar days', 'unresolved_days', 'NSE file not yet fetched, request failed, or file date did not match.'),
                             ('Sessions with no NSE row for this stock', 'untraded_sessions', 'Suspension, no trades, or an older symbol not matched by symbol or ISIN.'),
                             ('Confirmed weekday non-sessions', 'confirmed_holidays', 'No bhavcopy published (HTTP 404).')]:
        out.append(_details(f'{title} ({len(c[key])})', note, c[key]))
    cmp_ = s.get('nse_compare')
    if cmp_:
        rows = ''.join(f'<tr><td><a href="#" data-jump="daily|{m["day"]}">{m["day"]}</a></td><td>{e(", ".join(m["fields"]))}</td>'
                       f'<td class="num">{" / ".join(str(x) for x in m["stored"])}</td>'
                       f'<td class="num">{" / ".join(str(m["nse"][f]) for f in ("open", "high", "low", "close", "volume"))} ({e(str(m["nse"]["series"]))})</td></tr>'
                       for m in reversed(cmp_['recent']))
        out.append(f'<details><summary>Stored daily vs NSE bhavcopy: {cmp_["compared"]:,} compared, {cmp_["identical"]:,} identical, '
                   f'{cmp_["compared"] - cmp_["identical"]:,} different</summary><p class="muted small">Prices within 0.011, volume exact. '
                   f'Field differences: {e(json.dumps(cmp_["field_diffs"]))}. Stored rows without an NSE row: {cmp_["stored_without_nse_row"]:,}. '
                   f'Full list: <a href="../{e(cmp_["csv"])}">{e(cmp_["csv"])}</a> <code>{cmp_["csv_sha256"][:12]}</code>. Most recent 200 below; '
                   f'both values are kept.</p><div class="scroll short"><table class="grid small"><thead><tr><th>Day</th><th>Fields</th>'
                   f'<th>Stored O / H / L / C / V</th><th>NSE O / H / L / C / V (series)</th></tr></thead><tbody>{rows}</tbody></table></div></details>')
    side = s.get('nse_side_by_side') or []
    if side:
        rows = ''.join(f'<tr><td>{r["day"]}</td><td>{e(str(r.get("series")))}</td>'
                       + ''.join(f'<td class="num">{r.get(f)}</td>' for f in ('open', 'high', 'low', 'close', 'volume')) + '</tr>' for r in side)
        out.append(f'<details open><summary>Quarantined stored days with an NSE row ({len(side)})</summary><p class="muted small">The stored provider row '
                   f'stays quarantined; NSE&#39;s bhavcopy values are shown beside it for inspection, not substituted.</p><div class="scroll short">'
                   f'<table class="grid small"><thead><tr><th>Day</th><th>Series</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th>'
                   f'</tr></thead><tbody>{rows}</tbody></table></div></details>')
    return ''.join(out)


def _gap_table(s, iv):
    if not s['gaps']:
        return '<p class="muted small">No absent starts, quarantined rows or flagged placeholders in this interval.</p>'
    rows = ''.join(f'<tr><td><a href="#" data-jump="{iv}|{e(str(chart_time(g["stamp"], iv)))}">{e(_ist(g["stamp"]) if iv != "daily" else g["stamp"][:10])}</a></td>'
                   f'<td><span class="key {g["kind"]}">{e(g["kind"])}</span></td><td>{e(g["label"])}</td></tr>' for g in s['gaps'])
    return (f'<details open><summary>Gaps and exclusions ({len(s["gaps"])}) — click a row to show it on the chart</summary>'
            f'<div class="scroll short"><table class="grid small"><thead><tr><th>Timestamp</th><th>Kind</th><th>Detail</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div></details>')


def _details(title, note, items):
    body = ', '.join(e(x) for x in items) if items else 'none'
    return f'<details><summary>{e(title)}</summary><p class="muted small">{e(note)}</p><p class="small mono">{body}</p></details>'


def _window_table(s):
    rows = ''.join(
        f'<tr class="{"" if w["status"] == "FETCHED" else "flag"}"><td>{e(w["start"])}</td><td>{e(w["end"])}</td><td>{e(w["status"])}</td>'
        f'<td class="num">{w["returned"]}</td><td class="num">{w["valid"]}</td><td class="num">{w["invalid"]}</td>'
        f'<td>{e((w["collected_at"] or "unknown")[:19])}</td><td><code title="{w["raw_hash"] or ""}">{(w["raw_hash"] or "NONE")[:12]}</code></td>'
        f'<td><code title="{w["normalized_hash"] or ""}">{(w["normalized_hash"] or "NONE")[:12]}</code></td><td>{e(w["reason"] or "")}</td></tr>'
        for w in s['windows'])
    return (f'<details><summary>Request windows ({len(s["windows"])})</summary><div class="scroll short"><table class="grid small"><thead><tr>'
            '<th>From</th><th>To</th><th>Status</th><th>Returned</th><th>Valid</th><th>Issues</th><th>Collected (UTC)</th><th>Raw SHA-256</th>'
            f'<th>Normalized SHA-256</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table></div></details>')


def report_md(summary):
    stocks = summary['stocks']
    tot = Counter(); states = Counter(); status_n = Counter()
    for st in stocks:
        status_n[st['status']] += 1
        for iv, s in st['intervals'].items():
            tot[iv] += s['valid_rows']; states.update(s['state_counts'])
    herr = [x for st in stocks for s in st['intervals'].values() for x in s['hash_errors']]
    L = [f'# FnO historical data foundation — {len(stocks)} selected stocks',
         '', f'Generated {summary["generated_at_utc"][:19]} UTC by `{VIEW_VERSION}` from the stored catalog, read-only. '
         'Data view only: no strategy, pattern, backtest, alert or trading function was run or added.', '',
         '## Totals', '', '| Interval | Validated bars |', '|---|--:|']
    derived = [st['weekly'] for st in stocks if st['weekly'].get('derived')]
    weekly_line = (f'| Weekly (derived) | {sum(w["weeks"] for w in derived):,} — {len(derived)} of {len(stocks)} stocks; '
                   f'{sum(len(w["incomplete_weeks"]) for w in derived):,} incomplete week(s) left empty |')
    L += [f'| {LABEL[iv]} | {tot[iv]:,} |' for iv in INTERVALS] + [weekly_line, '']
    L += [f'Request windows: ' + ', '.join(f'{k} {v:,}' for k, v in sorted(states.items())) + '.', '',
          f'Stock status: ' + ', '.join(f'{k} {v}' for k, v in sorted(status_n.items())) + '. ' + USABLE_MEANING, '',
          '## Verification', '',
          f'- Stored-file verification errors: **{len(herr)}** (normalized partitions, quality records'
          + (', raw responses' if summary['raw_verified'] else '; raw responses NOT re-hashed this build') + ').',
          f'- Ledger chain: **{summary["ledger"]["status"]}**, {summary["ledger"]["events"]:,} events, head `{summary["ledger"]["head"]}`.',
          f'- Catalog SHA-256 `{summary["catalog_sha256"]}`; unchanged by this build: **{summary["catalog_unchanged_by_build"]}**.',
          f'- NSE archive reference: **{summary["nse"]["status"]}**'
          + (f', calendar days {json.dumps(summary["nse"]["days"], sort_keys=True)}, catalog SHA-256 `{summary["nse"]["catalog_sha256"]}`, '
             f'{len(summary["nse"]["errors"])} extract error(s).' if summary['nse']['status'] == 'LOADED' else '.'), '']
    L += [f'  - `{x}`' for x in herr[:50]]
    L += ['## By stock', '', '| Stock | Overall | Daily | 15-minute | 5-minute | Weekly |', '|---|---|---|---|---|---|']
    for st in stocks:
        cells = [f'{s["status"]}: {s["valid_rows"]:,} rows, {_range(s)}' for s in (st['intervals'][iv] for iv in INTERVALS)]
        w = st['weekly']
        wcell = f'{w["status"]}: {w["weeks"]:,} weeks, {len(w["incomplete_weeks"])} incomplete' if w.get('derived') else f'{w["status"]} ({w.get("why", "")})'
        L.append(f'| {st["symbol"]} | {st["status"]} | ' + ' | '.join(cells) + f' | {wcell} |')
    L += ['', '## Exclusions, gaps and reasons', '']
    for st in stocks:
        items = []
        for iv in INTERVALS:
            s = st['intervals'][iv]
            items += [f'{LABEL[iv]}: {r}' for r in s['reasons']] + [f'{LABEL[iv]} (note): {n}' for n in s['notes']]
            if s['gaps'] and len(s['gaps']) <= 12:
                items.append(f'{LABEL[iv]} gap timestamps: ' + ', '.join(g['stamp'][:16] + ' ' + g['kind'] for g in s['gaps']))
            elif s['gaps']:
                items.append(f'{LABEL[iv]} gap timestamps: {len(s["gaps"])}, from {s["gaps"][0]["stamp"][:10]} to {s["gaps"][-1]["stamp"][:10]} (listed on the stock page)')
        L.append(f'- **{st["symbol"]}** — ' + ('; '.join(items) if items else 'no exclusions recorded'))
    L += ['', '## NSE calendar and daily cross-check', '',
          '| Stock | Listing date (NSE) | Daily: missing sessions / conflicts / unresolved | 15m | 5m | Daily vs NSE: compared / identical |',
          '|---|---|---|---|---|---|']
    for st in stocks:
        def trio(s):
            c = s.get('nse')
            return f'{len(c["missing_sessions"])} / {len(c["conflicts"])} / {len(c["unresolved_days"])}' if c else '—'
        cmp_ = st['intervals']['daily'].get('nse_compare')
        both = f'{cmp_["compared"]:,} / {cmp_["identical"]:,}' if cmp_ else '—'
        L.append(f'| {st["symbol"]} | {(st.get("nse") or {}).get("listing_date") or "—"} | '
                 + ' | '.join(trio(st['intervals'][iv]) for iv in INTERVALS) + f' | {both} |')
    L += ['', 'Calendar = NSE bhavcopy file existence (approved source `nse-archives-public-eod-v1`). Differences between the stored daily '
          'series and NSE are listed per stock in `compare/<SYMBOL>_daily_vs_nse.csv`; both values are kept and neither replaces the other.',
          '', '## Price basis: stored intraday vs stored daily', '',
          'Median of (first 15-minute open / stored daily open) per year. 1.000 means both series share one price basis. '
          f'Years beyond ±{BASIS_TOLERANCE} are flagged.', '',
          '| Stock | ' + ' | '.join(sorted({y for st in stocks for y in st.get('basis', {})})) + ' |',
          '|---|' + '--:|' * len({y for st in stocks for y in st.get('basis', {})})]
    years = sorted({y for st in stocks for y in st.get('basis', {})})
    for st in stocks:
        b = st.get('basis', {})
        L.append(f'| {st["symbol"]} | ' + ' | '.join((f'**{b[y]["median_ratio"]:.3f}**' if b[y]['differs'] else f'{b[y]["median_ratio"]:.3f}') if y in b else '—' for y in years) + ' |')
    L += ['', 'Stored daily and intraday prices are adjusted for splits and bonuses (e.g. TATASTEEL ÷10 before its 2022 split, WIPRO ÷2 '
          'before its 2024 bonus); NSE bhavcopy prices are as traded, which is why most daily-vs-NSE differences move in whole-series ratios. '
          'Where a stock is flagged above, the two stored series carry different adjustments; the provider has not documented which.',
          '', '## Not established by this data', '',
          '- Any calendar day the NSE archive could not resolve (failed or unfetched request) is counted per stock above and keeps that interval from USABLE.',
          f'- {DAILY_NOTE}',
          '- Historical FnO eligibility and early security lineage. HINDZINC in particular: NSE lists it from 2006-11-21, yet the provider '
          'returns daily bars from 2003 and NSE bhavcopies before 2006 carry no row matching it.',
          '- Which of the two stored series carries which adjustment where they disagree (price-basis table above), and why daily close '
          'and volume differ from a 15-minute rollup.',
          '- Why the provider returned invalid daily rows (VEDL 2003-2005, IDEA 2024-08-30); Upstox rechecks on 29 Sep 2026 returned the same. '
          'The provider-empty early windows of APLAPOLLO, LTM and PERSISTENT are now explained by their NSE listing dates.',
          '- Five calendar days NSE could not serve after three attempts (2010-03-08, 2010-03-13, 2013-04-10, 2016-10-02, 2018-08-19); weeks '
          'containing them are not derived. None falls in the 2022-2026 intraday period.',
          '', 'This view restates the stored evidence; it is not a clearance. The 29 Sep 2026 decision '
          '(`quality_clearance_decision_v9_2026-09-29.md`) stands: conditional validation of retained rows, full database not cleared. '
          "Pattern backtesting on these 20 stocks begins only after Aditya Lakhotia confirms the data is visible and acceptable.",
          '', '## Provenance', '',
          f'- Manifest `{summary["manifest_path"]}` SHA-256 `{summary["manifest_sha256"]}`',
          f'- Parser version (current code) `{summary["current_parser_version"]}`; session-exception calendar SHA-256 `{summary["current_session_exceptions_sha256"]}`',
          f'- Catalog `{summary["catalog"]}`', '- Per-window raw and normalized SHA-256 values: `summary.json` and each stock page.', '']
    return '\n'.join(L)


CSS = '''
:root{--bg:#f7f8fa;--surface:#ffffff;--ink:#14181f;--ink-2:#4a5260;--line:#e3e6eb;--accent:#2b7a8c;
--up:#1f8a5b;--down:#c2412d;--up-a:rgba(31,138,91,.45);--down-a:rgba(194,65,45,.45);--vol:#8a93a3;
--ok-bg:#e6f4ec;--ok:#1d6b46;--part-bg:#fdf1dc;--part:#8a5a00;--block-bg:#fbe5e1;--block:#9b2c1c;--na-bg:#eceef2;--na:#4a5260;
--warn:#d98a00;--info:#6b5bd6}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0f1216;--surface:#161a20;--ink:#e7eaef;--ink-2:#9aa3b2;--line:#262c35;
--accent:#5fb6c8;--up:#3fbf85;--down:#f06a55;--up-a:rgba(63,191,133,.4);--down-a:rgba(240,106,85,.4);--vol:#5c6574;
--ok-bg:#12291e;--ok:#6fd3a0;--part-bg:#2e2410;--part:#f0c060;--block-bg:#35170f;--block:#ff9a86;--na-bg:#20252d;--na:#aab3c2;--warn:#f0b040;--info:#a79bff}}
:root[data-theme="dark"]{--bg:#0f1216;--surface:#161a20;--ink:#e7eaef;--ink-2:#9aa3b2;--line:#262c35;--accent:#5fb6c8;--up:#3fbf85;--down:#f06a55;
--up-a:rgba(63,191,133,.4);--down-a:rgba(240,106,85,.4);--vol:#5c6574;--ok-bg:#12291e;--ok:#6fd3a0;--part-bg:#2e2410;--part:#f0c060;
--block-bg:#35170f;--block:#ff9a86;--na-bg:#20252d;--na:#aab3c2;--warn:#f0b040;--info:#a79bff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1400px;margin:0 auto;padding:24px 16px 64px}a{color:var(--accent)}h1{font-size:26px;margin:4px 0 8px}h2{font-size:18px;margin:32px 0 10px}
.eyebrow{color:var(--ink-2);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin:0}.muted{color:var(--ink-2)}.small{font-size:12px}
.banner{border-left:3px solid var(--accent);background:var(--surface);padding:8px 12px;margin:8px 0}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:10px;margin-top:16px}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px}.tile span{display:block;color:var(--ink-2);font-size:12px}
.tile b{font-size:22px;font-variant-numeric:tabular-nums}.tile small{display:block;color:var(--ink-2);font-size:11px;margin-top:4px}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--line);border-radius:8px}.scroll.short{max-height:360px;overflow-y:auto}
table{border-collapse:collapse;width:100%}.grid th,.grid td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.grid th{position:sticky;top:0;background:var(--surface);font-size:12px;color:var(--ink-2);white-space:nowrap}.grid.small td{font-size:12px;white-space:nowrap}
.num{font-variant-numeric:tabular-nums}td.num{text-align:right}tr.flag td{background:var(--part-bg)}
.kv th{text-align:left;color:var(--ink-2);font-weight:500;padding:4px 12px 4px 0;white-space:nowrap;vertical-align:top}.kv td{padding:4px 0;word-break:break-all}
code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:12px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:600;white-space:nowrap}
.pill.ok{background:var(--ok-bg);color:var(--ok)}.pill.part{background:var(--part-bg);color:var(--part)}.pill.block{background:var(--block-bg);color:var(--block)}.pill.na{background:var(--na-bg);color:var(--na)}
ul.tight{margin:4px 0;padding-left:18px}ul.tight li{margin:0}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin:16px 0}.tab{background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px 10px;cursor:pointer;font:inherit}
.tab.on{border-color:var(--accent);box-shadow:inset 0 -2px 0 var(--accent)}.panel{display:none}.panel.on{display:block}
.callout{border-radius:8px;padding:10px 12px;margin:8px 0;border:1px solid var(--line)}.callout.ok{background:var(--ok-bg)}.callout.part{background:var(--part-bg)}
.callout.block{background:var(--block-bg)}.callout.na{background:var(--na-bg)}
.facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:8px;margin:10px 0}.facts div{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:8px}
.facts span{display:block;font-size:11px;color:var(--ink-2)}.facts b{font-weight:600;font-size:13px;word-break:break-word}
.controls{display:flex;flex-wrap:wrap;gap:12px;align-items:center;margin:8px 0}.controls input{font:inherit;background:var(--surface);color:var(--ink);border:1px solid var(--line);border-radius:4px;padding:2px 6px}
.chart{height:480px;background:var(--surface);border:1px solid var(--line);border-radius:8px;position:relative}.chart>p{padding:16px;margin:0}
.key{display:inline-block;padding:0 6px;border-radius:4px;font-size:11px;border:1px solid var(--line)}.key.missing{color:var(--warn)}.key.quarantined{color:var(--down)}.key.preopen{color:var(--info)}
details{margin:10px 0}summary{cursor:pointer;font-weight:600}
@media (max-width:600px){.chart{height:360px}h1{font-size:21px}}
'''

STOCK_JS = r'''(function(){
const P=window.FNO_PAGE, store=(window.FNO_SERIES=window.FNO_SERIES||{}), charts={};
const css=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const markColor={missing:'--warn',quarantined:'--down',preopen:'--info',incomplete:'--warn',unresolved:'--info'};
function load(iv){const key=P.symbol+'|'+iv;return new Promise((ok,no)=>{if(store[key])return ok(store[key]);
 const s=document.createElement('script');s.src=P.series[iv];s.onload=()=>store[key]?ok(store[key]):no(new Error('series file loaded but empty'));
 s.onerror=()=>no(new Error('Could not load '+P.series[iv]));document.head.appendChild(s);});}
async function draw(iv){
 const box=document.getElementById('chart-'+iv);if(charts[iv]||box.dataset.busy)return charts[iv];
 if(!window.LightweightCharts){box.innerHTML='<p class="muted">The chart library did not load (it is fetched from unpkg.com; are you offline?). Every table on this page is complete without it.</p>';return;}
 box.dataset.busy='1';box.innerHTML='<p class="muted">Loading '+iv+' series…</p>';
 let d;try{d=await load(iv);}catch(err){box.innerHTML='<p class="muted">'+err.message+'</p>';return;}
 box.innerHTML='';
 const c=LightweightCharts.createChart(box,{autoSize:true,layout:{background:{color:css('--surface')},textColor:css('--ink-2')},
  grid:{vertLines:{color:css('--line')},horzLines:{color:css('--line')}},timeScale:{timeVisible:iv!=='daily',secondsVisible:false},
  rightPriceScale:{scaleMargins:{top:.06,bottom:.26}},localization:{locale:'en-IN'}});
 const cs=c.addCandlestickSeries({upColor:css('--up'),downColor:css('--down'),wickUpColor:css('--up'),wickDownColor:css('--down'),borderVisible:false});
 const vs=c.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},color:css('--vol')});
 c.priceScale('vol').applyOptions({scaleMargins:{top:.8,bottom:0}});
 const cd=new Array(d.t.length),vd=new Array(d.t.length),up=css('--up-a'),dn=css('--down-a');
 for(let i=0;i<d.t.length;i++){const t=d.t[i];
  if(d.o[i]===null){cd[i]={time:t};vd[i]={time:t};continue;}
  cd[i]={time:t,open:d.o[i],high:d.h[i],low:d.l[i],close:d.c[i]};vd[i]={time:t,value:d.v[i],color:d.c[i]>=d.o[i]?up:dn};}
 cs.setData(cd);vs.setData(vd);
 // Markers cannot sit on an empty slot (the library snaps them onto the nearest real
 // bar, which made valid bars look quarantined), so each gap is a full-height band.
 const kind=new Map(d.marks.map(m=>[m.t,m.k]));
 const gs=c.addHistogramSeries({priceScaleId:'gap',lastValueVisible:false,priceLineVisible:false});
 c.priceScale('gap').applyOptions({scaleMargins:{top:0,bottom:0},visible:false});
 gs.setData(d.t.map(t=>kind.has(t)?{time:t,value:1,color:css(markColor[kind.get(t)]||'--info')+'66'}:{time:t}));
 const n=d.t.length;c.timeScale().setVisibleLogicalRange({from:Math.max(0,n-(iv==='daily'?260:300)),to:n+2});
 charts[iv]={c,t:d.t};delete box.dataset.busy;return charts[iv];}
function index(ts,x){let lo=0,hi=ts.length-1;while(lo<hi){const m=(lo+hi)>>1;if(ts[m]<x)lo=m+1;else hi=m;}return lo;}
async function jump(iv,x){show(iv);const ch=await draw(iv);if(!ch)return;const i=index(ch.t,x);
 ch.c.timeScale().setVisibleLogicalRange({from:i-(iv==='daily'?40:60),to:i+(iv==='daily'?40:60)});
 document.getElementById('chart-'+iv).scrollIntoView({block:'center',behavior:'smooth'});}
function show(iv){document.querySelectorAll('.tab').forEach(b=>b.classList.toggle('on',b.dataset.tab===iv));
 document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('on',p.id==='p-'+iv));
 if(history.replaceState)history.replaceState(null,'','#'+iv);if(P.series[iv])draw(iv);}
document.addEventListener('click',ev=>{const tab=ev.target.closest('.tab');if(tab){show(tab.dataset.tab);return;}
 const a=ev.target.closest('[data-jump]');if(a){ev.preventDefault();const k=a.dataset.jump.indexOf('|'),iv=a.dataset.jump.slice(0,k),raw=a.dataset.jump.slice(k+1);
 jump(iv,iv==='daily'?raw:Number(raw));}});
document.addEventListener('change',ev=>{const inp=ev.target.closest('[data-goto]');if(!inp||!inp.value)return;const iv=inp.dataset.goto;
 jump(iv,iv==='daily'?inp.value:Date.parse(inp.value+'T00:00:00Z')/1000);});
const first=(location.hash||'').slice(1);show(['daily','15m','5m','weekly'].includes(first)?first:'daily');
})();'''


def main():
    ap = argparse.ArgumentParser(description='Build the read-only local data view of the selected historical stocks.')
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--skip-raw-verify', action='store_true', help='do not re-hash raw provider responses (faster; stated in the output)')
    a = ap.parse_args()
    manifest = json.loads(MANIFEST.read_bytes())
    root = Path(manifest['storage']['root'])
    summary = build(root / 'historical_db' / 'catalog.sqlite', root / 'events.sqlite', a.out,
                    symbols=manifest['scope']['historical_selected_symbols'], verify_raw=not a.skip_raw_verify,
                    nse_catalog=NSE_CATALOG if NSE_CATALOG.exists() else None)
    print(json.dumps({'out': str(a.out), 'stocks': len(summary['stocks']),
                      'status': dict(Counter(s['status'] for s in summary['stocks'])),
                      'ledger': summary['ledger']['status'], 'catalog_unchanged': summary['catalog_unchanged_by_build'],
                      'hash_errors': sum(len(i['hash_errors']) for s in summary['stocks'] for i in s['intervals'].values())}))


if __name__ == '__main__':
    main()
