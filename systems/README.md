# Clean-room trading research systems

This directory contains the two new paper-trading research tracks.  They are intentionally
isolated from the legacy `asymmetry` application and from each other.

- `fno_momentum` owns FnO stock momentum candidates, option-paper fills, models, cohorts,
  events, and metrics.
- `nifty500_source_news` owns source/news candidates, models, cohorts, events, and metrics.

Neither package may import `asymmetry`, read the repository's legacy `data/` tree, or import
the other new track.  The clean-room CI check enforces those boundaries.  There is no shared
runtime package yet; shared infrastructure requires a separately recorded approval.

All sources and trading behavior begin inactive.  Creating code for a source is not approval
to collect from it, and creating a rule is not approval to publish an alert from it.

## Completed RELIANCE data-quality pilot

The broad all-contract routine-collection proposal was superseded before activation. The
`fno-upstox-reliance-quality-pilot-v1` completed on 2026-09-21 with 19 of 20 planned
read-only snapshots and one recorded network failure. It covered RELIANCE and all of its
then-listed stock-option contracts, stayed below its 250 MB limit, and created no strategy,
candidate, P&L, alert, order, or trading action. It is not active for another run.

```powershell
$env:PYTHONPATH='systems/fno_momentum/src'
python -m fno_momentum.reliance_quality_pilot `
  --manifest systems/proposals/fno_upstox_reliance_quality_pilot_v1.json `
  --ledger data/fno_momentum/upstox/reliance_quality_pilot_v1/events.sqlite
```

## FnO universe-wide data foundation

Aditya Lakhotia authorized the universe-wide data-foundation step on 2026-09-22. The first
active phase is complete: one public Upstox NSE instrument-master snapshot established the
effective-dated current FnO stock universe and permanent option-contract reference baseline.
It did not request or use the Analytics Token.

Historical daily/15-minute/5-minute collection, ongoing underlying observations, sector
mapping, and corporate-action ingestion remain inactive until the exact missing date ranges,
cadence, storage ceiling, and source/mapping policies are approved. Broad option BBO capture
remains prohibited.

The proposed next stage is recorded in
`systems/proposals/fno_full_underlying_data_collection_v1.json` with status
`proposed_inactive`. The raw-deletion audit procedure is implemented and tested in
`fno_momentum.retention_audit`, but no existing raw file has been deleted and no future
collection or cleanup schedule has been activated.

## Legacy FnO session-capture scaffold

This earlier scaffold is not active. Do not run it unless its exact endpoints, fields,
universe, cadence, retention, and storage boundary are separately approved. If that later
approval is granted, it accepts only the read-only Analytics Token; general OAuth access
tokens are deliberately rejected.

Only after that separate approval may the read-only post-session capture be run directly
from the track package:

```powershell
$env:PYTHONPATH='systems/fno_momentum/src'
python -m fno_momentum.session_capture `
  --ledger systems/runtime/fno_momentum/events.sqlite `
  --raw-store systems/runtime/fno_momentum/raw `
  --session-date 2026-09-16
```

The job refreshes the NSE instrument master, records contract/universe reference changes,
captures completed 15-minute underlying candles, captures provider-timestamped underlying
quotes, and stores option-contract, option-chain, and two-sided BBO evidence for the binding
7–30 DTE envelope. It is resumable by session and symbol. `--symbols RELIANCE,TCS` or
`--maximum-underlyings 5` can be used for a controlled verification run. Every response is
stored content-addressed and linked to the immutable ledger. The command remains data-only:
it neither creates candidates nor publishes alerts or orders.
