"""SQLite reference implementation of the FnO track's append-only event ledger."""

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


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class LedgerEvent:
    sequence: int
    event_id: str
    event_type: str
    aggregate_id: str
    occurred_at: str
    recorded_at: str
    decision_at: str | None
    data_cutoff: str | None
    payload: dict[str, Any]
    payload_hash: str
    previous_hash: str
    event_hash: str
    idempotency_key: str
    track_id: str = TRACK_ID


class ImmutableLedger:
    """Durable local ledger used by tests and the first vertical slice."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._create()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def _create(self) -> None:
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS ledger_event (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    track_id TEXT NOT NULL CHECK(track_id = 'fno-options-momentum'),
                    event_type TEXT NOT NULL,
                    aggregate_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    decision_at TEXT,
                    data_cutoff TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    idempotency_key TEXT NOT NULL UNIQUE
                );
                CREATE TRIGGER IF NOT EXISTS ledger_event_no_update
                BEFORE UPDATE ON ledger_event BEGIN
                    SELECT RAISE(ABORT, 'ledger events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS ledger_event_no_delete
                BEFORE DELETE ON ledger_event BEGIN
                    SELECT RAISE(ABORT, 'ledger events are immutable');
                END;
                """
            )

    @staticmethod
    def _stamp(value: datetime | None, name: str) -> str | None:
        if value is None:
            return None
        require_aware(value, name)
        return value.isoformat()

    def append(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict[str, Any],
        *,
        occurred_at: datetime,
        idempotency_key: str,
        decision_at: datetime | None = None,
        data_cutoff: datetime | None = None,
    ) -> LedgerEvent:
        require_aware(occurred_at, "occurred_at")
        decision = self._stamp(decision_at, "decision_at")
        cutoff = self._stamp(data_cutoff, "data_cutoff")
        if decision_at is not None and data_cutoff is not None and data_cutoff > decision_at:
            raise ValueError("data_cutoff cannot be after decision_at")
        payload_text = canonical_json(payload)
        payload_hash = hashlib.sha256(payload_text.encode("utf-8")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            duplicate = db.execute(
                "SELECT * FROM ledger_event WHERE idempotency_key=?", (idempotency_key,)
            ).fetchone()
            if duplicate is not None:
                event = self._from_row(duplicate)
                if (
                    event.event_type != event_type
                    or event.aggregate_id != aggregate_id
                    or event.payload_hash != payload_hash
                ):
                    raise ValueError("idempotency key was reused for different event content")
                return event
            previous = db.execute(
                "SELECT event_hash FROM ledger_event ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous[0] if previous else ""
            event_id = str(uuid.uuid4())
            recorded_at = datetime.now(timezone.utc).isoformat()
            body = {
                "event_id": event_id,
                "track_id": TRACK_ID,
                "event_type": event_type,
                "aggregate_id": aggregate_id,
                "occurred_at": occurred_at.isoformat(),
                "recorded_at": recorded_at,
                "decision_at": decision,
                "data_cutoff": cutoff,
                "payload_hash": payload_hash,
                "previous_hash": previous_hash,
                "idempotency_key": idempotency_key,
            }
            event_hash = sha256_json(body)
            cursor = db.execute(
                """INSERT INTO ledger_event (
                    event_id,track_id,event_type,aggregate_id,occurred_at,recorded_at,
                    decision_at,data_cutoff,payload_json,payload_hash,previous_hash,event_hash,
                    idempotency_key
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    TRACK_ID,
                    event_type,
                    aggregate_id,
                    occurred_at.isoformat(),
                    recorded_at,
                    decision,
                    cutoff,
                    payload_text,
                    payload_hash,
                    previous_hash,
                    event_hash,
                    idempotency_key,
                ),
            )
            row = db.execute(
                "SELECT * FROM ledger_event WHERE sequence=?", (cursor.lastrowid,)
            ).fetchone()
            return self._from_row(row)

    def events(self, aggregate_id: str | None = None) -> list[LedgerEvent]:
        query = "SELECT * FROM ledger_event"
        params: tuple[str, ...] = ()
        if aggregate_id is not None:
            query += " WHERE aggregate_id=?"
            params = (aggregate_id,)
        query += " ORDER BY sequence"
        with self._connect() as db:
            return [self._from_row(row) for row in db.execute(query, params)]

    def verify(self) -> None:
        previous = ""
        for event in self.events():
            if event.previous_hash != previous or sha256_json(event.payload) != event.payload_hash:
                raise ValueError(f"ledger integrity failure at sequence {event.sequence}")
            body = {
                "event_id": event.event_id,
                "track_id": event.track_id,
                "event_type": event.event_type,
                "aggregate_id": event.aggregate_id,
                "occurred_at": event.occurred_at,
                "recorded_at": event.recorded_at,
                "decision_at": event.decision_at,
                "data_cutoff": event.data_cutoff,
                "payload_hash": event.payload_hash,
                "previous_hash": event.previous_hash,
                "idempotency_key": event.idempotency_key,
            }
            if sha256_json(body) != event.event_hash:
                raise ValueError(f"ledger integrity failure at sequence {event.sequence}")
            previous = event.event_hash

    @staticmethod
    def _from_row(row: sqlite3.Row) -> LedgerEvent:
        return LedgerEvent(
            sequence=int(row["sequence"]),
            event_id=row["event_id"],
            track_id=row["track_id"],
            event_type=row["event_type"],
            aggregate_id=row["aggregate_id"],
            occurred_at=row["occurred_at"],
            recorded_at=row["recorded_at"],
            decision_at=row["decision_at"],
            data_cutoff=row["data_cutoff"],
            payload=json.loads(row["payload_json"]),
            payload_hash=row["payload_hash"],
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            idempotency_key=row["idempotency_key"],
        )
