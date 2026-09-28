"""Reclassify documented NSE sessions from retained raw responses; no HTTP calls."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'systems' / 'fno_momentum' / 'src'))

from fno_momentum.historical_db import (  # noqa: E402
    HistoryDB, HistoricalCollector, SESSION_EXCEPTIONS_SHA256, VERSION,
    exclusive_collector, ingest, reclassification_targets, report, validate_rows,
)
from datetime import date  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='Audit and update selected catalog rows from verified retained raw responses')
    args = ap.parse_args()
    c = HistoricalCollector()
    c.ledger.verify()
    with exclusive_collector(c.root):
        db = HistoryDB(c.root / 'historical_db' / 'catalog.sqlite')
        selected = {r[0] for r in db.db.execute('SELECT symbol FROM acquisition_scope')}
        if selected != set(c.manifest['scope']['historical_selected_symbols']):
            raise RuntimeError('Catalog and active v9 selected scope differ')
        changes = []
        for row in reclassification_targets(db):
            w = dict(row)
            if w['status'] not in ('FETCHED', 'PARTIAL'):
                raise RuntimeError(f"Target is not stored: {w['id']} {w['status']}")
            raw_path = Path(w['raw_path'])
            old_path = Path(w['normalized_path'])
            body = gzip.decompress(raw_path.read_bytes())
            if sha(body) != w['raw_hash']:
                raise RuntimeError(f"Raw hash mismatch: {w['id']}")
            old = gzip.decompress(old_path.read_bytes())
            if sha(old) != w['normalized_hash']:
                raise RuntimeError(f"Normalized hash mismatch: {w['id']}")
            previous = json.loads(old)
            if previous.get('session_exceptions_sha256') == SESSION_EXCEPTIONS_SHA256:
                continue
            source = json.loads(body)
            if source.get('status') != 'success' or not isinstance(source.get('data', {}).get('candles'), list):
                raise RuntimeError(f"Invalid retained source response: {w['id']}")
            bars = source['data']['candles']
            valid, issues = validate_rows(bars, w['interval'], date.fromisoformat(w['start']),
                                          date.fromisoformat(w['end']), w['symbol'])
            old_rows = {json.dumps(r, separators=(',', ':')) for r in previous['rows']}
            new_rows = {json.dumps(r, separators=(',', ':')) for r in valid}
            if not old_rows <= new_rows or len(valid) < w['valid']:
                raise RuntimeError(f"Reclassification would lose validated bars: {w['id']}")
            changes.append((w, bars, valid, issues))
        recovered = sum(len(valid) - w['valid'] for w, _, valid, _ in changes)
        outcome = {'mode': 'apply' if args.apply else 'dry-run', 'target_windows': len(changes),
                   'additional_valid_rows': recovered, 'session_exceptions_sha256': SESSION_EXCEPTIONS_SHA256,
                   'provider_requests': 0}
        if not args.apply:
            print(json.dumps(outcome, sort_keys=True))
            return
        if not changes:
            print(json.dumps(outcome, sort_keys=True))
            return
        if not 0 < len(changes) <= 362 or recovered != 0:
            raise RuntimeError(f'Expected remaining selected windows with no further row changes; got {outcome}')
        c.event('historical_session_reclassification_started', 'history-db-session-reclassification',
                {**outcome, 'parser_version': VERSION, 'source': 'retained_hashed_raw_only'})
        for w, bars, _, _ in changes:
            key = w['id']
            c.event('historical_session_reclassification_intended', key,
                    {'previous_normalized_sha256': w['normalized_hash'],
                     'raw_sha256': w['raw_hash'], 'session_exceptions_sha256': SESSION_EXCEPTIONS_SHA256})
            ingest(c, db, w, bars,
                   {'raw_sha256': w['raw_hash'], 'raw_path': w['raw_path'], 'elapsed_ms': w['elapsed_ms']},
                   w['source_event'])
            current = db.db.execute('SELECT normalized_hash,valid,status FROM windows WHERE id=?', (key,)).fetchone()
            c.event('historical_session_reclassification_completed', key,
                    {'previous_normalized_sha256': w['normalized_hash'],
                     'new_normalized_sha256': current['normalized_hash'],
                     'previous_valid': w['valid'], 'new_valid': current['valid'],
                     'status': current['status'], 'old_partition_retained': True,
                     'session_exceptions_sha256': SESSION_EXCEPTIONS_SHA256})
        summary = report(c, db)
        c.ledger.verify()
        outcome['states'] = summary['states']
        outcome['total_valid_rows'] = summary['valid_rows']
        outcome['ledger_events'] = len(c.ledger.events())
        print(json.dumps(outcome, sort_keys=True))


if __name__ == '__main__':
    main()
