"""The only sanctioned way for research code to read the selected historical OHLCV.

Returns valid retained bars as contiguous *segments*: a segment never spans an excluded
session, a session with no stored bar, an unresolved calendar day or an excluded week, so
nothing computed inside one segment can silently bridge a gap. Exclusions come from
`eligibility.json`, which the data view writes from the same verified evidence it shows;
the manifest is refused if either catalog has changed since it was built.

Combining daily or weekly with intraday is allowed only in years whose stored price bases
agree (see `basis_by_year` in data_view), and only on dates every requested interval holds.

This module contains no pattern, signal, indicator or backtest.
"""
from __future__ import annotations
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from .full_underlying_collection import digest
from .data_view import DEFAULT_OUT, NSE_CATALOG, _resolve, _ro, _verified, chart_time
from .nse_archives import HISTORICAL_CATALOG
from .nse_reference import derive_weekly

ELIGIBILITY = DEFAULT_OUT / 'eligibility.json'
INTRADAY = ('15m', '5m')


class IneligibleRequest(RuntimeError):
    """A request the retained data cannot answer without crossing an exclusion."""


def check_fresh(elig: dict, catalog_sha: str, nse_sha: str | None, view_version: str | None = None) -> None:
    if elig.get('catalog_sha256') != catalog_sha:
        raise IneligibleRequest('historical catalog changed since eligibility.json was built; rebuild the data view')
    if elig.get('nse_catalog_sha256') != nse_sha:
        raise IneligibleRequest('NSE archive catalog changed since eligibility.json was built; rebuild the data view')
    if view_version is not None and elig.get('view_version') != view_version:
        raise IneligibleRequest('eligibility.json was written by a different data-view version; rebuild it')


def split_segments(rows, units, excluded: dict, key, breaks=()):
    """Contiguous runs of rows over ordered units (sessions or weeks). A unit that is excluded or
    holds no rows ends the run, and so does any break falling between two units."""
    breaks = set(breaks)
    by_unit = {}
    for r in rows:
        if key(r) in breaks:              # a bar on an unresolved day is dropped with the day
            continue
        by_unit.setdefault(key(r), []).append(r)
    stray = set(by_unit) - set(units)
    if stray:
        raise IneligibleRequest(f'{len(stray)} stored unit(s) fall outside the expected calendar, e.g. {sorted(stray)[:3]}')
    events = sorted([(u, 1) for u in units if u not in breaks] + [(b, 0) for b in breaks])
    segments, current = [], []
    for unit, is_unit in events:
        if not is_unit or unit in excluded or unit not in by_unit:
            if current:
                segments.append(current)
            current = []
            continue
        current.extend(by_unit[unit])
    if current:
        segments.append(current)
    return segments


def split_within_sessions(segments, minutes: int):
    """Split wherever two bars of the same day are more than one bar apart. After the missing-start
    exclusions, the only such jumps are documented split sessions (2 Mar and 18 May 2024), where
    the market was shut between blocks; nothing may be computed across that break."""
    out = []
    for seg in segments:
        run = [seg[0]]
        for a, b in zip(seg, seg[1:]):
            ta, tb = datetime.fromisoformat(a[0]), datetime.fromisoformat(b[0])
            if ta.date() == tb.date() and tb - ta != timedelta(minutes=minutes):
                out.append(run); run = []
            run.append(b)
        out.append(run)
    return out


def aligned_years(basis: dict, years) -> list[str]:
    """Years in which stored daily and intraday prices share one basis. Unmeasured years are refused."""
    return [y for y in years if y in basis and not basis[y]['differs']]


def _mondays(first: str, last: str):
    d = date.fromisoformat(first); d -= timedelta(days=d.weekday())
    end = date.fromisoformat(last)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=7)


def _restrict(segments, keep):
    """Keep only rows passing `keep`, splitting a segment wherever a row is removed."""
    out = []
    for s in segments:
        run = []
        for r in s:
            if keep(r):
                run.append(r)
            elif run:
                out.append(run); run = []
        if run:
            out.append(run)
    return out


class EligibleData:
    def __init__(self, eligibility: Path = ELIGIBILITY, catalog: Path = HISTORICAL_CATALOG, nse_catalog: Path = NSE_CATALOG):
        eligibility = Path(eligibility)
        if not eligibility.exists():
            raise IneligibleRequest('eligibility.json not found; build the data view first')
        self.elig = json.loads(eligibility.read_bytes())
        self.catalog = Path(catalog)
        nse_sha = digest(Path(nse_catalog).read_bytes()) if nse_catalog and Path(nse_catalog).exists() else None
        check_fresh(self.elig, digest(self.catalog.read_bytes()), nse_sha)
        self.sessions = self.elig['nse_sessions']
        self.unresolved = self.elig['nse_unresolved']

    def _stock(self, symbol):
        st = self.elig['stocks'].get(symbol)
        if st is None:
            raise IneligibleRequest(f'{symbol} is not in the selected 20-stock scope')
        return st

    def _rows(self, symbol, interval):
        db = _ro(self.catalog)
        try:
            windows = list(db.execute(
                'SELECT w.normalized_path, w.normalized_hash, w.id FROM windows w JOIN acquisition_scope a ON a.instrument=w.instrument '
                'WHERE a.symbol=? AND w.interval=? AND w.normalized_path IS NOT NULL ORDER BY w.start', (symbol, interval)))
        finally:
            db.close()
        rows = []
        for path, sha, wid in windows:
            content, err = _verified(_resolve(path), sha, 'normalized partition')
            if err:
                raise IneligibleRequest(f'{wid}: {err}')
            rows.extend(json.loads(content)['rows'])
        return sorted(rows, key=lambda r: r[0])

    def load(self, symbol: str, interval: str) -> dict:
        """Valid retained bars for one stock and interval, as gap-free segments, plus every exclusion applied."""
        st = self._stock(symbol)
        if interval == 'weekly':
            return self._weekly(symbol, st)
        iv = st['intervals'].get(interval)
        if iv is None or iv['status'] == 'BLOCKED':
            raise IneligibleRequest(f'{symbol} {interval} is not eligible: {iv and iv["status"]}')
        before = st.get('exclude_before') if interval == 'daily' else None
        day = lambda r: chart_time(r[0], 'daily')
        rows = [r for r in self._rows(symbol, interval) if not (before and day(r) < before)]
        first, last = max(iv['first'], before or ''), iv['last']
        units = [d for d in self.sessions if first <= d <= last]
        breaks = [d for d in self.unresolved if first <= d <= last]
        segments = split_segments(rows, units, iv['excluded'], day, breaks)
        if interval in INTRADAY:
            segments = split_within_sessions(segments, int(interval[:-1]))
        return {'symbol': symbol, 'interval': interval, 'segments': segments, 'excluded': iv['excluded'],
                'exclude_before': before, 'breaks': breaks, 'bars': sum(len(s) for s in segments)}

    def _weekly(self, symbol, st):
        w = st['weekly']
        if not w.get('derived'):
            raise IneligibleRequest(f'{symbol} weekly is not derivable: {w.get("why")}')
        daily = self._rows(symbol, 'daily')
        iv = st['intervals']['daily']
        sessions = [d for d in self.sessions if iv['first'] <= d <= iv['last']]
        derived = derive_weekly(daily, sessions, unresolved=[d for d in self.unresolved if iv['first'] <= d <= iv['last']])
        units = list(_mondays(iv['first'], iv['last']))
        segments = split_segments(derived['rows'], units, w['excluded_weeks'], key=lambda r: r[0])
        return {'symbol': symbol, 'interval': 'weekly', 'segments': segments, 'excluded': w['excluded_weeks'],
                'bars': sum(len(s) for s in segments)}

    def load_aligned(self, symbol: str, intervals) -> dict:
        """Several intervals restricted to the same eligible dates. Daily or weekly with intraday is limited
        to years on one price basis; a date missing or excluded in any requested interval is dropped from all."""
        intervals = list(intervals)
        st = self._stock(symbol)
        loaded = {iv: self.load(symbol, iv) for iv in intervals}
        mixed = any(iv in INTRADAY for iv in intervals) and any(iv in ('daily', 'weekly') for iv in intervals)
        day = lambda r: chart_time(r[0], 'daily')
        dates = [{day(r) for s in loaded[iv]['segments'] for r in s} for iv in intervals if iv != 'weekly']
        common = set.intersection(*dates) if dates else set()
        years = sorted({d[:4] for d in common})
        keep_years = set(aligned_years(st['basis'], years)) if mixed else set(years)
        if mixed and not keep_years:
            raise IneligibleRequest(f'{symbol}: no year where stored daily and intraday prices share one basis')
        allowed = {d for d in common if d[:4] in keep_years}
        weeks = {(date.fromisoformat(d) - timedelta(days=date.fromisoformat(d).weekday())).isoformat() for d in allowed}
        out = {}
        for iv in intervals:
            keep = (lambda r: r[0] in weeks) if iv == 'weekly' else (lambda r: day(r) in allowed)
            out[iv] = {**loaded[iv], 'segments': _restrict(loaded[iv]['segments'], keep)}
        return {'symbol': symbol, 'intervals': out, 'years': sorted(keep_years),
                'refused_years': sorted(set(years) - keep_years), 'dates': len(allowed)}
