"""Independent append-only ledger for the NIFTY 500 Source News track."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import require_aware
from .policy import TRACK_ID


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def hash_manifest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NewsEvent:
    sequence: int
    event_id: str
    event_type: str
    aggregate_id: str
    occurred_at: str
    recorded_at: str
    payload: dict[str, Any]
    payload_hash: str
    previous_hash: str
    event_hash: str
    idempotency_key: str
    track_id: str = TRACK_ID


class NewsLedger:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS news_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    track_id TEXT NOT NULL CHECK(track_id = 'nifty500-source-news'),
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS news_event_no_update
                BEFORE UPDATE ON news_event BEGIN
                    SELECT RAISE(ABORT, 'news events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS news_event_no_delete
                BEFORE DELETE ON news_event BEGIN
                    SELECT RAISE(ABORT, 'news events are immutable');
                END;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def append(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        occurred_at: datetime,
        idempotency_key: str,
    ) -> NewsEvent:
        require_aware(occurred_at, "occurred_at")
        payload_text = _canonical(payload)
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute(
                "SELECT * FROM news_event WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if duplicate is not None:
                event = self._event(duplicate)
                if (
                    event.event_type != event_type
                    or event.aggregate_id != aggregate_id
                    or event.payload_hash != payload_hash
                ):
                    raise ValueError("idempotency key was reused for different event content")
                return event
            previous_row = db.execute(
                "SELECT event_hash FROM news_event ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous_row[0] if previous_row else ""
            event_id = str(uuid.uuid4())
            recorded_at = datetime.now(timezone.utc).isoformat()
            body = {
                "event_id": event_id,
                "track_id": TRACK_ID,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "occurred_at": occurred_at.isoformat(),
                "recorded_at": recorded_at,
                "payload_hash": payload_hash,
                "previous_hash": previous_hash,
                "idempotency_key": idempotency_key,
            }
            event_hash = hash_manifest(body)
            cursor = db.execute(
                """INSERT INTO news_event (
                    event_id,track_id,event_type,aggregate_id,occurred_at,recorded_at,
                    payload_json,payload_hash,previous_hash,event_hash,idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    TRACK_ID,
                    event_type,
                    aggregate_id,
                    occurred_at.isoformat(),
                    recorded_at,
                    payload_text,
                    payload_hash,
                    previous_hash,
                    event_hash,
                    idempotency_key,
                ),
            )
            row = db.execute(
                "SELECT * FROM news_event WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            return self._event(row)

    def events(self) -> list[NewsEvent]:
        with self._connect() as db:
            return [self._event(row) for row in db.execute("SELECT * FROM news_event ORDER BY sequence")]

    def verify(self) -> None:
        previous = ""
        for event in self.events():
            if event.previous_hash != previous or hash_manifest(event.payload) != event.payload_hash:
                raise ValueError(f"news ledger integrity failure at sequence {event.sequence}")
            body = {
                "event_id": event.event_id,
                "track_id": event.track_id,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "occurred_at": event.occurred_at,
                "recorded_at": event.recorded_at,
                "payload_hash": event.payload_hash,
                "previous_hash": event.previous_hash,
                "idempotency_key": event.idempotency_key,
            }
            if hash_manifest(body) != event.event_hash:
                raise ValueError(f"news ledger integrity failure at sequence {event.sequence}")
            previous = event.event_hash

    @staticmethod
    def _event(row: sqlite3.Row) -> NewsEvent:
        return NewsEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            track_id=row["track_id"],
            event_type=row["event_type"],
            aggregate_id=row["aggregate_id"],
            occurred_at=row["occurred_at"],
            recorded_at=row["recorded_at"],
            payload=json.loads(row["payload_json"]),
            payload_hash=row["payload_hash"],
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            idempotency_key=row["idempotency_key"],
        )
