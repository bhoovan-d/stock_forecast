"""Decision journal — what the system said, what you did, what happened.

Without this the factor weights can never be tuned by anything but taste. The backtest
tells you whether the *model* has edge; the journal tells you whether **you** do, and where
the two diverge. The gap between them is usually the most valuable signal available:
skipped winners, oversized losers, entries taken early.

Every emitted plan is recorded automatically when a brief is generated, so the record
exists whether or not you act. You then log your own action, and outcomes are marked
against real bhavcopy prices rather than memory.
"""

from __future__ import annotations

import datetime as dt
from datetime import date, timedelta

import pandas as pd
from loguru import logger
from sqlmodel import Field, Session, SQLModel, select

from .models import ScanResult


class JournalEntry(SQLModel, table=True):
    """One system call, plus whatever the user did about it."""

    __tablename__ = "journal"

    id: int | None = Field(default=None, primary_key=True)
    as_of: dt.date = Field(index=True)
    symbol: str = Field(index=True)

    # What the system said.
    rank: int = 0
    total_score: float = 0.0
    regime: str = ""
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    planned_r: float = 0.0
    quantity: int = 0
    catalyst_note: str = ""

    # What you did. `action` stays "none" unless you log otherwise.
    action: str = "none"  # none | taken | skipped
    actual_entry: float | None = None
    actual_exit: float | None = None
    actual_quantity: int | None = None
    note: str = ""

    # What happened — filled in by `settle`, from stored prices.
    outcome: str = "open"  # open | target | stop | expired | no_trigger
    realised_r: float | None = None
    settled_on: dt.date | None = None


def _engine():
    from .storage import _engine as engine

    return engine


def record_brief(result: ScanResult) -> int:
    """Persist every plan from a brief. Idempotent per (as_of, symbol)."""
    from .storage import init_db

    init_db()
    written = 0
    with Session(_engine()) as session:
        for rank, cand in enumerate(result.candidates, 1):
            if cand.plan is None:
                continue
            exists = session.exec(
                select(JournalEntry).where(
                    JournalEntry.as_of == result.as_of, JournalEntry.symbol == cand.symbol
                )
            ).first()
            if exists:
                continue
            session.add(
                JournalEntry(
                    as_of=result.as_of,
                    symbol=cand.symbol,
                    rank=rank,
                    total_score=cand.total_score,
                    regime=result.regime.verdict.value,
                    entry=cand.plan.entry,
                    stop=cand.plan.stop,
                    target=cand.plan.target,
                    planned_r=cand.plan.reward_risk,
                    quantity=cand.plan.quantity,
                    catalyst_note=cand.catalyst_note,
                )
            )
            written += 1
        session.commit()
    if written:
        logger.info(f"[journal] recorded {written} calls for {result.as_of}")
    return written


def log_action(
    symbol: str,
    action: str,
    *,
    on: date | None = None,
    actual_entry: float | None = None,
    actual_quantity: int | None = None,
    note: str = "",
) -> bool:
    """Record what you actually did about a call."""
    target_date = on or date.today()
    with Session(_engine()) as session:
        entry = session.exec(
            select(JournalEntry)
            .where(JournalEntry.symbol == symbol, JournalEntry.as_of <= target_date)
            .order_by(JournalEntry.as_of.desc())
        ).first()
        if entry is None:
            return False
        entry.action = action
        if actual_entry is not None:
            entry.actual_entry = actual_entry
        if actual_quantity is not None:
            entry.actual_quantity = actual_quantity
        if note:
            entry.note = note
        session.add(entry)
        session.commit()
    return True


def settle(horizon: int = 10) -> int:
    """Mark open entries against stored prices.

    Uses the same conservative rule as the backtest: a bar touching both stop and target
    counts as a loss, because the intraday sequence is unknown.
    """
    from .storage import load_history

    history = load_history(days=400)
    if history.empty:
        return 0

    highs = history.pivot_table(index="date", columns="symbol", values="high").sort_index()
    lows = history.pivot_table(index="date", columns="symbol", values="low").sort_index()

    settled = 0
    with Session(_engine()) as session:
        rows = session.exec(select(JournalEntry).where(JournalEntry.outcome == "open")).all()
        for entry in rows:
            if entry.symbol not in highs.columns:
                continue
            forward = highs.index[highs.index > entry.as_of][:horizon]
            if len(forward) == 0:
                continue

            risk = entry.entry - entry.stop
            if risk <= 0:
                continue

            triggered = False
            outcome, realised = None, None
            for day in forward:
                high, low = highs.at[day, entry.symbol], lows.at[day, entry.symbol]
                if pd.isna(high) or pd.isna(low):
                    continue
                if not triggered:
                    if high >= entry.entry:
                        triggered = True
                    else:
                        continue
                if low <= entry.stop:
                    outcome, realised = "stop", -1.0
                    break
                if high >= entry.target:
                    outcome, realised = "target", (entry.target - entry.entry) / risk
                    break

            if outcome is None:
                # Only close out once the full horizon has elapsed.
                if len(forward) < horizon:
                    continue
                outcome = "no_trigger" if not triggered else "expired"
                realised = 0.0 if not triggered else None

            entry.outcome = outcome
            entry.realised_r = realised
            entry.settled_on = forward[-1]
            session.add(entry)
            settled += 1
        session.commit()

    if settled:
        logger.info(f"[journal] settled {settled} entries")
    return settled


def load_journal(days: int = 90) -> pd.DataFrame:
    cutoff = date.today() - timedelta(days=days)
    with Session(_engine()) as session:
        rows = session.exec(select(JournalEntry).where(JournalEntry.as_of >= cutoff)).all()
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([r.model_dump(exclude={"id"}) for r in rows])


def performance(days: int = 90) -> dict:
    """System calls versus your actual decisions.

    ``taken_vs_system`` is the number that matters: if the system's calls beat the ones you
    acted on, your filtering is subtracting value, and vice versa.
    """
    frame = load_journal(days)
    if frame.empty:
        return {}

    resolved = frame[frame["realised_r"].notna()]
    taken = resolved[resolved["action"] == "taken"]
    skipped = resolved[resolved["action"] == "skipped"]

    def summarise(subset: pd.DataFrame) -> dict:
        if subset.empty:
            return {"n": 0}
        wins = int((subset["realised_r"] > 0).sum())
        return {
            "n": len(subset),
            "win_rate": wins / len(subset) * 100,
            "mean_r": float(subset["realised_r"].mean()),
            "total_r": float(subset["realised_r"].sum()),
        }

    return {
        "all_calls": summarise(resolved),
        "taken": summarise(taken),
        "skipped": summarise(skipped),
        "logged": int((frame["action"] != "none").sum()),
        "total_calls": len(frame),
    }
