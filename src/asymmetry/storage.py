"""SQLite persistence and history backfill.

Raw archive files are already cached on disk, but re-parsing 400 zipped bhavcopies for
every factor calculation is far too slow. This layer parses once into SQLite so the
engines can pull a wide price matrix in a single query.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd
from loguru import logger
from sqlalchemy import text
from sqlmodel import Session, SQLModel, create_engine, select

from .config import settings
from .data import nse_archive
from .models import CatalystRecord, DailyBar

_engine = create_engine(f"sqlite:///{settings.db_path}", echo=False)


def init_db() -> None:
    SQLModel.metadata.create_all(_engine)
    with Session(_engine) as session:
        # The scan reads by date and by symbol constantly; without these it crawls.
        session.exec(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_bar_date_symbol "
                "ON daily_bar (date, symbol)"
            )
        )
        session.exec(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_catalyst_symbol_headline "
                "ON catalyst (symbol, headline)"
            )
        )
        session.commit()


def stored_dates() -> set[date]:
    with Session(_engine) as session:
        rows = session.exec(text("SELECT DISTINCT date FROM daily_bar")).all()
    return {date.fromisoformat(r[0]) if isinstance(r[0], str) else r[0] for r in rows}


def store_day(day: date) -> int:
    """Parse one trading day's bhavcopy + delivery into the DB. Returns rows written."""
    cm = nse_archive.fetch_cm_bhavcopy(day)
    if cm is None:
        return 0

    delivery = nse_archive.fetch_delivery(day)
    if delivery is not None:
        cm = cm.merge(
            delivery[["symbol", "delivery_pct"]], on="symbol", how="left"
        )
    else:
        cm["delivery_pct"] = None

    frame = cm.where(pd.notna(cm), None)
    with Session(_engine) as session:
        session.exec(text("DELETE FROM daily_bar WHERE date = :d"), params={"d": day.isoformat()})
        session.add_all(
            [
                DailyBar(
                    date=row.date,
                    symbol=row.symbol,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    prev_close=row.prev_close,
                    volume=row.volume,
                    turnover=row.turnover,
                    trades=row.trades,
                    delivery_pct=row.delivery_pct,
                )
                for row in frame.itertuples()
            ]
        )
        session.commit()
    return len(frame)


def backfill_history(start: date, end: date) -> int:
    """Fetch and store every trading day in range that is not already stored."""
    init_db()
    have = stored_dates()
    total = 0
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5 and cursor not in have:
            written = store_day(cursor)
            if written:
                total += written
                logger.info(f"[backfill] {cursor} — {written:,} bars")
        cursor += timedelta(days=1)
    return total


def load_history(days: int = 400, end: date | None = None) -> pd.DataFrame:
    """Long-format bars for the trailing window."""
    end = end or date.today()
    start = end - timedelta(days=days)
    with Session(_engine) as session:
        rows = session.exec(
            select(DailyBar).where(DailyBar.date >= start, DailyBar.date <= end)
        ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([r.model_dump(exclude={"id"}) for r in rows])


def latest_stored_date() -> date | None:
    with Session(_engine) as session:
        row = session.exec(text("SELECT MAX(date) FROM daily_bar")).first()
    if not row or row[0] is None:
        return None
    return date.fromisoformat(row[0]) if isinstance(row[0], str) else row[0]


def save_catalysts(records: list[CatalystRecord]) -> int:
    """Upsert scored catalysts, keyed by (symbol, headline) so re-runs do not duplicate."""
    if not records:
        return 0
    init_db()
    written = 0
    # expire_on_commit=False so the caller can keep reading these objects after the commit;
    # otherwise every attribute access raises DetachedInstanceError.
    with Session(_engine, expire_on_commit=False) as session:
        for rec in records:
            existing = session.exec(
                select(CatalystRecord).where(
                    CatalystRecord.symbol == rec.symbol,
                    CatalystRecord.headline == rec.headline,
                )
            ).first()
            if existing:
                continue
            session.add(rec)
            written += 1
        session.commit()
    return written


def load_catalysts(since_days: int = 7, as_of: date | None = None) -> pd.DataFrame:
    """Catalysts published in the window ending at ``as_of`` (default today).

    The upper bound is what makes historical runs honest: without it a backtest scores
    March using news published in August.
    """
    end = as_of or date.today()
    start = end - timedelta(days=since_days)
    upper = datetime.combine(end, datetime.max.time())
    with Session(_engine) as session:
        rows = session.exec(
            select(CatalystRecord).where(
                CatalystRecord.published >= start, CatalystRecord.published <= upper
            )
        ).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([r.model_dump(exclude={"id"}) for r in rows])
