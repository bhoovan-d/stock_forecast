"""Point-in-time candle contract and immutable 15-minute research store.

Market-data frames in the older engines use the candle start as their index.  This
module makes the other two times explicit: when the interval ended and when a decision
was first allowed to use it.  The research store refuses conflicting revisions so a
backtest cannot silently change underneath an approved model.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from ..config import DATA_DIR

INDIA = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
BAR_COLUMNS = ("open", "high", "low", "close", "volume")


def _india_timestamp(value) -> pd.Timestamp:
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(INDIA) if stamp.tzinfo is None else stamp.tz_convert(INDIA)


def _duration(interval: str) -> pd.Timedelta:
    aliases = {"15m": "15min", "60m": "60min", "1h": "60min", "120m": "120min"}
    if interval == "1d":
        return pd.Timedelta(0)
    try:
        return pd.Timedelta(aliases.get(interval, interval))
    except ValueError as exc:
        raise ValueError(f"unsupported candle interval: {interval}") from exc


def with_availability(frame: pd.DataFrame | None, interval: str) -> pd.DataFrame:
    """Return OHLCV candles with explicit start/end/availability timestamps."""
    if frame is None or frame.empty:
        return pd.DataFrame(columns=[*BAR_COLUMNS, "interval_start", "interval_end", "available_at"])
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"candle frame is missing columns: {', '.join(sorted(missing))}")
    out = frame.loc[:, BAR_COLUMNS].copy().sort_index()
    starts = pd.DatetimeIndex([_india_timestamp(value) for value in out.index])
    if interval == "1d":
        ends = pd.DatetimeIndex([
            pd.Timestamp(datetime.combine(stamp.date(), SESSION_CLOSE), tz=INDIA)
            for stamp in starts
        ])
    else:
        ends = starts + _duration(interval)
    out.index = starts
    out["interval_start"] = starts
    out["interval_end"] = ends
    out["available_at"] = ends
    return out


def completed_bars(frame: pd.DataFrame | None, interval: str, decision_at) -> pd.DataFrame:
    """Expose only candles whose availability time is at or before the decision."""
    contracted = with_availability(frame, interval)
    if contracted.empty:
        return contracted
    cutoff = _india_timestamp(decision_at)
    return contracted[contracted["available_at"] <= cutoff].copy()


@dataclass(frozen=True)
class IntegrityReport:
    valid: bool
    sessions: int
    bars: int
    first_session: str = ""
    last_session: str = ""
    errors: tuple[str, ...] = ()


def validate_15m(frame: pd.DataFrame | None, *, require_complete_sessions: bool = True) -> IntegrityReport:
    """Validate NSE cash-session geometry and OHLCV invariants."""
    errors: list[str] = []
    try:
        bars = with_availability(frame, "15m")
    except ValueError as exc:
        return IntegrityReport(False, 0, 0, errors=(str(exc),))
    if bars.empty:
        return IntegrityReport(False, 0, 0, errors=("no 15-minute bars",))
    if bars.index.has_duplicates:
        errors.append("duplicate interval_start values")
    numeric = bars.loc[:, BAR_COLUMNS]
    if numeric.isna().any().any():
        errors.append("missing OHLCV values")
    if ((numeric[["open", "high", "low", "close"]] <= 0).any().any()
            or (numeric["volume"] < 0).any()):
        errors.append("non-positive price or negative volume")
    if ((numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)).any()
            or (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1)).any()):
        errors.append("invalid OHLC relationship")
    grouped = bars.groupby(bars.index.date)
    expected = pd.date_range("09:15", "15:15", freq="15min").time
    for session, group in grouped:
        clocks = pd.Index(group.index.time)
        if any(clock < SESSION_OPEN or clock >= SESSION_CLOSE for clock in clocks):
            errors.append(f"{session}: bar outside the cash session")
        if require_complete_sessions and not clocks.equals(pd.Index(expected)):
            errors.append(f"{session}: incomplete or irregular 15-minute grid")
    sessions = sorted(grouped.groups)
    return IntegrityReport(
        not errors, len(sessions), len(bars), str(sessions[0]), str(sessions[-1]), tuple(errors)
    )


class IntradayStore:
    """SQLite-backed immutable store for normalized 15-minute candles."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else DATA_DIR / "intraday_15m.sqlite"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _create(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS intraday_bars (
                    symbol TEXT NOT NULL,
                    interval_start TEXT NOT NULL,
                    interval_end TEXT NOT NULL,
                    available_at TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    source TEXT NOT NULL, fetched_at TEXT NOT NULL,
                    exchange_timezone TEXT NOT NULL,
                    adjustment_status TEXT NOT NULL,
                    batch_id TEXT NOT NULL,
                    PRIMARY KEY (symbol, interval_start)
                );
                CREATE TABLE IF NOT EXISTS intraday_batches (
                    batch_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL, source TEXT NOT NULL,
                    fetched_at TEXT NOT NULL, first_session TEXT NOT NULL,
                    last_session TEXT NOT NULL, bars INTEGER NOT NULL,
                    integrity_json TEXT NOT NULL, data_hash TEXT NOT NULL
                );
            """)

    def append(self, symbol: str, frame: pd.DataFrame, *, source: str,
               fetched_at: datetime | None = None, adjustment_status: str = "unadjusted") -> str:
        report = validate_15m(frame)
        if not report.valid:
            raise ValueError("invalid 15-minute data: " + "; ".join(report.errors))
        fetched = _india_timestamp(fetched_at or datetime.now(INDIA)).isoformat()
        bars = with_availability(frame, "15m")
        canonical = bars.loc[:, [*BAR_COLUMNS, "interval_end", "available_at"]].to_csv(
            float_format="%.10g"
        )
        data_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        batch_id = hashlib.sha256(
            f"{symbol.upper()}|{source}|{fetched}|{data_hash}".encode("utf-8")
        ).hexdigest()[:24]
        rows = []
        for stamp, row in bars.iterrows():
            rows.append((
                symbol.upper(), stamp.isoformat(), row["interval_end"].isoformat(),
                row["available_at"].isoformat(), float(row.open), float(row.high),
                float(row.low), float(row.close), float(row.volume), source, fetched,
                str(INDIA), adjustment_status, batch_id,
            ))
        with closing(self._connect()) as db, db:
            for row in rows:
                existing = db.execute(
                    "SELECT open,high,low,close,volume FROM intraday_bars "
                    "WHERE symbol=? AND interval_start=?", row[:2]
                ).fetchone()
                if existing is not None:
                    if tuple(float(existing[key]) for key in BAR_COLUMNS) != tuple(row[4:9]):
                        raise ValueError(f"immutable candle conflict for {row[0]} at {row[1]}")
                    continue
                db.execute(
                    "INSERT INTO intraday_bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", row
                )
            db.execute(
                "INSERT OR IGNORE INTO intraday_batches VALUES (?,?,?,?,?,?,?,?,?)",
                (batch_id, symbol.upper(), source, fetched, report.first_session,
                 report.last_session, report.bars, json.dumps(asdict(report)), data_hash),
            )
        return batch_id

    def load(self, symbol: str, start: date | None = None, end: date | None = None) -> pd.DataFrame:
        clauses, values = ["symbol=?"], [symbol.upper()]
        if start is not None:
            clauses.append("interval_start>=?")
            values.append(pd.Timestamp(start, tz=INDIA).isoformat())
        if end is not None:
            clauses.append("interval_start<?")
            values.append(pd.Timestamp(end + timedelta(days=1), tz=INDIA).isoformat())
        query = (
            "SELECT interval_start,open,high,low,close,volume FROM intraday_bars WHERE "
            + " AND ".join(clauses) + " ORDER BY interval_start"
        )
        with closing(self._connect()) as db, db:
            rows = db.execute(query, values).fetchall()
        if not rows:
            return pd.DataFrame(columns=BAR_COLUMNS)
        frame = pd.DataFrame(rows, columns=rows[0].keys())
        frame["interval_start"] = pd.to_datetime(frame["interval_start"], utc=True).dt.tz_convert(INDIA)
        loaded = frame.set_index("interval_start").loc[:, BAR_COLUMNS].astype(float)
        loaded.index.name = None
        return loaded

    def coverage(self, symbols: list[str]) -> tuple[date | None, date | None, int]:
        if not symbols:
            return None, None, 0
        placeholders = ",".join("?" for _ in symbols)
        with closing(self._connect()) as db, db:
            row = db.execute(
                f"SELECT MIN(interval_start) first, MAX(interval_start) last, "
                f"COUNT(DISTINCT substr(interval_start,1,10)) sessions "
                f"FROM intraday_bars WHERE symbol IN ({placeholders})",
                [symbol.upper() for symbol in symbols],
            ).fetchone()
        return (
            pd.Timestamp(row["first"]).date() if row and row["first"] else None,
            pd.Timestamp(row["last"]).date() if row and row["last"] else None,
            int(row["sessions"] or 0) if row else 0,
        )

    def coverage_failures(self, symbols: list[str], start: date, end: date) -> list[str]:
        failures: list[str] = []
        with closing(self._connect()) as db, db:
            for symbol in symbols:
                row = db.execute(
                    "SELECT MIN(interval_start) first, MAX(interval_start) last "
                    "FROM intraday_bars WHERE symbol=?", (symbol.upper(),)
                ).fetchone()
                first = pd.Timestamp(row["first"]).date() if row and row["first"] else None
                last = pd.Timestamp(row["last"]).date() if row and row["last"] else None
                if first is None or first > start or last is None or last < end:
                    failures.append(
                        f"{symbol.upper()}: {first or 'missing'} to {last or 'missing'}"
                    )
        return failures
