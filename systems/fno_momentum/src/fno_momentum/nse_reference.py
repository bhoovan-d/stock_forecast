"""Read-only use of the approved NSE archive: calendar, listing dates, daily cross-check.

Nothing here edits a stored Upstox value. The calendar is file existence: a published
bhavcopy is a session, an HTTP 404 is no session, anything else (failed request, date
mismatch, parse failure, not yet fetched) leaves the day UNRESOLVED and the calendar
unverified over any range containing it.
"""
from __future__ import annotations
import gzip, json, sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from .full_underlying_collection import IST, digest
from .historical_db import weekly_from_daily

SERIES_PREFERENCE = ('EQ', 'BE', 'BZ')    # equity series only; bonds and others share symbols
PRICE_TOLERANCE = 0.011                  # the same tolerance as the 29 Sep 5m-vs-15m check


@dataclass
class Reference:
    days: dict                                        # ISO date -> collector day status
    stock_rows: dict                                  # our symbol -> ISO date -> chosen NSE row
    listing: dict                                     # our symbol -> EQUITY_L row
    errors: list = field(default_factory=list)
    catalog_sha256: str | None = None
    ledger_path: str | None = None


def pick_row(rows):
    ranked = [r for r in rows if r.get('series') in SERIES_PREFERENCE]
    return min(ranked, key=lambda r: SERIES_PREFERENCE.index(r['series'])) if ranked else None


def load_reference(catalog_path: Path) -> Reference | None:
    catalog_path = Path(catalog_path)
    if not catalog_path.exists():
        return None
    sha = digest(catalog_path.read_bytes())
    db = sqlite3.connect(catalog_path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        meta = dict(db.execute('SELECT key, value FROM meta'))
        wanted = json.loads(meta.get('wanted', '{}'))
        by_isin = {isin: sym for sym, isin in wanted.items()}
        days, grouped, errors = {}, {}, []
        for day, status, path, nsha in db.execute('SELECT day, status, normalized_path, normalized_sha256 FROM days'):
            days[day] = status
            if status != 'PUBLISHED':
                continue
            try:
                content = gzip.decompress(Path(path).read_bytes())
            except (OSError, EOFError, TypeError) as exc:
                errors.append(f'{day}: NSE extract unreadable ({type(exc).__name__})'); days[day] = 'UNREADABLE'; continue
            if digest(content) != nsha:
                errors.append(f'{day}: NSE extract hash mismatch'); days[day] = 'UNREADABLE'; continue
            for row in json.loads(content)['rows']:
                sym = row['symbol'] if row['symbol'] in wanted else by_isin.get(row['isin'])
                if sym:
                    grouped.setdefault(sym, {}).setdefault(day, []).append(row)
        stock_rows = {s: {d: r for d, rs in per.items() if (r := pick_row(rs))} for s, per in grouped.items()}
        listing = {}
        if 'listing' in meta:
            info = json.loads(meta['listing'])
            content = gzip.decompress(Path(info['path']).read_bytes())
            if digest(content) != info['sha256']:
                errors.append('EQUITY_L extract hash mismatch')
            else:
                for r in json.loads(content)['rows']:
                    sym = r.get('SYMBOL') if r.get('SYMBOL') in wanted else by_isin.get(r.get('ISIN NUMBER'))
                    if sym and r.get('SERIES') in SERIES_PREFERENCE:
                        listing[sym] = r
    finally:
        db.close()
    return Reference(days=days, stock_rows=stock_rows, listing=listing, errors=errors, catalog_sha256=sha)


def listing_date(ref: Reference | None, symbol: str) -> str | None:
    raw = (ref.listing.get(symbol) or {}).get('DATE OF LISTING') if ref else None
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%d-%b-%Y').date().isoformat()
    except ValueError:
        return None


def _span(first: str, last: str):
    day, end = date.fromisoformat(first), date.fromisoformat(last)
    while day <= end:
        yield day.isoformat()
        day += timedelta(days=1)


def calendar_checks(stored: set, first: str | None, last: str | None, ref: Reference, symbol: str, quarantined=frozenset()):
    """Classify every calendar day between the first and last stored bar against the NSE calendar."""
    out = {k: [] for k in ('missing_sessions', 'untraded_sessions', 'conflicts', 'unresolved_days',
                           'confirmed_holidays', 'quarantined_sessions')}
    out['sessions'] = 0
    if not first:
        out['verified'] = False
        return out
    traded = ref.stock_rows.get(symbol, {})
    for day in _span(first, last):
        state = ref.days.get(day, 'UNFETCHED')
        weekday = date.fromisoformat(day).weekday() < 5
        if state == 'PUBLISHED':
            out['sessions'] += 1
            if day in stored:
                continue
            if day in quarantined:
                out['quarantined_sessions'].append(day)
            elif day in traded:
                out['missing_sessions'].append(day)
            else:
                out['untraded_sessions'].append(day)
        elif state == 'NOT_PUBLISHED':
            if day in stored:
                out['conflicts'].append(day)
            elif weekday:
                out['confirmed_holidays'].append(day)
        else:
            out['unresolved_days'].append(day)
    out['verified'] = not out['unresolved_days']
    return out


def _day(stamp):
    return datetime.fromisoformat(stamp).astimezone(IST).date().isoformat()


def compare_daily(stored_rows, nse_rows: dict):
    """Field-by-field agreement where both series have the day. Both values are kept; neither wins."""
    fields = ('open', 'high', 'low', 'close', 'volume')
    diffs = dict.fromkeys(fields, 0)
    result = {'compared': 0, 'identical': 0, 'mismatches': [], 'stored_without_nse_row': 0}
    for row in stored_rows:
        day = _day(row[0])
        nse = nse_rows.get(day)
        if nse is None:
            result['stored_without_nse_row'] += 1
            continue
        result['compared'] += 1
        bad = [f for i, f in enumerate(fields, start=1)
               if nse.get(f) is None or (abs(row[i] - nse[f]) > PRICE_TOLERANCE if f != 'volume' else row[i] != nse[f])]
        for f in bad:
            diffs[f] += 1
        if bad:
            result['mismatches'].append({'day': day, 'fields': bad, 'stored': row[1:6], 'nse': {f: nse.get(f) for f in fields} | {'series': nse.get('series')}})
        else:
            result['identical'] += 1
    result['field_diffs'] = {f: n for f, n in diffs.items() if n}
    return result


def _monday(day: str) -> str:
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def derive_weekly(daily_rows, sessions, unresolved=()):
    """Monday-anchored weeks from stored daily bars, only where every NSE session of the week is
    present and every day of the week is resolved. A week holding an unresolved day may hide a
    session, so it is left empty and named rather than derived."""
    rows = [[_day(r[0]), *r[1:6]] for r in daily_rows]
    result = weekly_from_daily(rows, sorted(sessions))
    blocked = sorted({_monday(d) for d in unresolved})
    result['rows'] = [r for r in result['rows'] if r[0] not in blocked]
    result['incomplete_weeks'] = [w for w in result['incomplete_weeks'] if w not in blocked]
    result['unresolved_weeks'] = blocked
    return result
