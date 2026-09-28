"""Audited 2015-12-31 exact daily duplicate collapse from retained Upstox raw data."""

from __future__ import annotations

import argparse
from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'systems' / 'fno_momentum' / 'src'))

from fno_momentum.historical_db import (  # noqa: E402
    HistoryDB, HistoricalCollector, VERSION, exclusive_collector, ingest, report, validate_rows,
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    c = HistoricalCollector()
    c.ledger.verify()
    with exclusive_collector(c.root):
        db = HistoryDB(c.root / 'historical_db' / 'catalog.sqlite')
        selected = {r[0] for r in db.db.execute('SELECT symbol FROM acquisition_scope')}
        if selected != set(c.manifest['scope']['historical_selected_symbols']):
            raise RuntimeError('Selected catalog scope does not match active v9 manifest')
        rows = db.db.execute('''SELECT w.* FROM windows w JOIN acquisition_scope a
            ON a.instrument=w.instrument WHERE w.interval='daily'
            AND w.start<='2015-12-31' AND w.end>='2015-12-31' ''').fetchall()
        changes = []
        for row in rows:
            w = dict(row)
            if w['status'] not in ('FETCHED', 'PARTIAL'):
                raise RuntimeError('Daily window lacks stored raw: ' + w['id'])
            raw = gzip.decompress(Path(w['raw_path']).read_bytes())
            old = gzip.decompress(Path(w['normalized_path']).read_bytes())
            if sha(raw) != w['raw_hash'] or sha(old) != w['normalized_hash']:
                raise RuntimeError('Stored hash mismatch: ' + w['id'])
            bars = json.loads(raw).get('data', {}).get('candles')
            if not isinstance(bars, list):
                raise RuntimeError('Invalid retained source schema: ' + w['id'])
            new, issues = validate_rows(bars, 'daily', date.fromisoformat(w['start']),
                                        date.fromisoformat(w['end']), w['symbol'])
            old_rows = json.loads(old)['rows']
            if not {json.dumps(r) for r in old_rows} <= {json.dumps(r) for r in new}:
                raise RuntimeError('Previously accepted daily bar would be lost: ' + w['id'])
            if len(new) == w['valid']:
                continue
            if len(new) != w['valid'] + 1 or sum(x['reason']=='identical_daily_duplicate_collapsed'
                                                 for x in issues) != 1:
                raise RuntimeError('Unexpected daily duplicate change: ' + w['id'])
            changes.append((w, bars))
        outcome = {'mode': 'apply' if args.apply else 'dry-run',
                   'windows_to_correct': len(changes), 'recovered_daily_rows': len(changes),
                   'provider_requests': 0, 'parser_version': VERSION}
        if not args.apply or not changes:
            print(json.dumps(outcome, sort_keys=True))
            return
        if len(changes) != 17:
            raise RuntimeError('Expected 17 identical selected duplicate pairs; got ' + str(len(changes)))
        c.event('historical_identical_daily_duplicates_started', 'history-db-2015-12-31',
                {**outcome, 'source': 'verified_retained_raw_only'})
        for w, bars in changes:
            c.event('historical_identical_daily_duplicate_intended', w['id'],
                    {'raw_sha256': w['raw_hash'], 'previous_normalized_sha256': w['normalized_hash'],
                     'rule': 'identical OHLCV and trailing fields; choose earliest provider timestamp'})
            ingest(c, db, w, bars,
                   {'raw_sha256': w['raw_hash'], 'raw_path': w['raw_path'], 'elapsed_ms': w['elapsed_ms']},
                   w['source_event'])
            now = db.db.execute('SELECT normalized_hash,valid FROM windows WHERE id=?', (w['id'],)).fetchone()
            c.event('historical_identical_daily_duplicate_completed', w['id'],
                    {'previous_normalized_sha256': w['normalized_hash'],
                     'new_normalized_sha256': now['normalized_hash'],
                     'previous_valid': w['valid'], 'new_valid': now['valid'],
                     'previous_partition_retained': True})
        summary = report(c, db)
        c.ledger.verify()
        outcome['total_valid_rows'] = summary['valid_rows']
        outcome['states'] = summary['states']
        print(json.dumps(outcome, sort_keys=True))


if __name__ == '__main__':
    main()
