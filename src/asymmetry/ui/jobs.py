"""Run CLI commands as child processes and stream their output to the panel.

Runs are **serialised through one worker**. That is not a simplification — Yahoo throttles
hard enough that the data layer paces itself, and two scans at once would fight over both
that pacing and the SQLite writer. A second run therefore queues rather than starting.

Each run is a child process rather than a call into the engines on a server thread: a scan
that dies takes nothing with it, and a run that overshoots can actually be cancelled.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterator

from ..config import ROOT

MAX_LINES = 6000          # per job; older lines are dropped, and the drop is reported
POLL_SEC = 0.25


@dataclass
class Job:
    id: str
    command_id: str
    title: str
    argv: list[str]
    status: str = "queued"        # queued | running | done | failed | cancelled
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    exit_code: int | None = None
    error: str = ""
    width: int = 150
    lines: list[str] = field(default_factory=list)
    dropped: int = 0
    _proc: subprocess.Popen | None = None
    _cancelled: bool = False

    @property
    def cli(self) -> str:
        return "asymmetry " + " ".join(self.argv)

    @property
    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        return (self.finished or time.time()) - self.started

    def summary(self) -> dict:
        return {
            "id": self.id,
            "command": self.command_id,
            "title": self.title,
            "cli": self.cli,
            "status": self.status,
            "created": self.created,
            "elapsed": round(self.elapsed, 1),
            "exit_code": self.exit_code,
            "error": self.error,
            "line_count": self.dropped + len(self.lines),
        }


class JobRunner:
    """A single-worker queue of child processes, with per-job output subscribers."""

    def __init__(self, width: int = 150) -> None:
        self.width = width
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._worker = threading.Thread(target=self._run_forever, daemon=True)
        self._worker.start()

    # ── public API ────────────────────────────────────────────────────────────

    def submit(
        self, command_id: str, title: str, argv: list[str], width: int | None = None
    ) -> Job:
        # The page measures how many characters its log pane actually fits and sends it,
        # so Rich lays the tables out to the window instead of to a guessed 150 columns.
        job = Job(
            id=uuid.uuid4().hex[:12],
            command_id=command_id,
            title=title,
            argv=argv,
            width=min(max(int(width or self.width), 90), 260),
        )
        with self._changed:
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._changed.notify_all()
        self._queue.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def recent(self, limit: int = 40) -> list[dict]:
        with self._lock:
            ids = self._order[-limit:][::-1]
            return [self._jobs[i].summary() for i in ids]

    def active(self) -> dict | None:
        with self._lock:
            for job_id in reversed(self._order):
                job = self._jobs[job_id]
                if job.status in ("running", "queued"):
                    return job.summary()
        return None

    def cancel(self, job_id: str) -> bool:
        with self._changed:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("queued", "running"):
                return False
            job._cancelled = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished = time.time()
                self._changed.notify_all()
                return True
            proc = job._proc
        if proc is not None:
            # terminate() is a hard kill on Windows; the child owns no external state
            # beyond the SQLite writer, which is transactional.
            try:
                proc.terminate()
            except OSError:
                return False
        return True

    def lines_from(self, job_id: str, offset: int) -> tuple[list[str], int]:
        """Lines from an absolute index, plus the next index to ask for."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return [], offset
            start = max(offset - job.dropped, 0)
            return list(job.lines[start:]), job.dropped + len(job.lines)

    def follow(self, job_id: str, offset: int = 0) -> Iterator[tuple[list[str], int, str]]:
        """Yield (new lines, next offset, status) until the job ends and is drained."""
        cursor = offset
        while True:
            with self._changed:
                job = self._jobs.get(job_id)
                if job is None:
                    return
                available = job.dropped + len(job.lines)
                if available <= cursor and job.status in ("queued", "running"):
                    self._changed.wait(timeout=POLL_SEC)
                    job = self._jobs[job_id]
                    available = job.dropped + len(job.lines)
                start = max(cursor - job.dropped, 0)
                chunk = list(job.lines[start:])
                status = job.status
            cursor = available
            yield chunk, cursor, status
            if status not in ("queued", "running") and not chunk:
                return

    # ── worker ────────────────────────────────────────────────────────────────

    def _run_forever(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.get(job_id)
            if job is None or job.status == "cancelled":
                continue
            try:
                self._execute(job)
            except Exception as exc:  # a broken launch must not kill the worker
                with self._changed:
                    job.status = "failed"
                    job.error = f"{type(exc).__name__}: {exc}"
                    job.finished = time.time()
                    self._changed.notify_all()

    def _execute(self, job: Job) -> None:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"       # so loguru's stderr and Rich's stdout interleave
        env["PYTHONIOENCODING"] = "utf-8"
        env["ASYMMETRY_UI_WIDTH"] = str(job.width)
        env.pop("NO_COLOR", None)

        with self._changed:
            job.status = "running"
            job.started = time.time()
            self._changed.notify_all()

        proc = subprocess.Popen(
            [sys.executable, "-m", "asymmetry.ui.runner", *job.argv],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        with self._changed:
            job._proc = proc

        assert proc.stdout is not None
        for raw in proc.stdout:
            # Rich emits a trailing newline per print; a bare \r would otherwise glue
            # separate updates into one very long line.
            for piece in raw.rstrip("\r\n").split("\r"):
                self._append(job, piece)
        code = proc.wait()

        with self._changed:
            job.exit_code = code
            job.finished = time.time()
            if job._cancelled:
                # A terminated child exits non-zero; that is the cancel, not a failure.
                job.status = "cancelled"
                job.error = "cancelled"
            elif job.status == "running":
                job.status = "done" if code == 0 else "failed"
                if code != 0:
                    job.error = f"exited with code {code}"
            job._proc = None
            self._changed.notify_all()

    def _append(self, job: Job, line: str) -> None:
        with self._changed:
            job.lines.append(line)
            if len(job.lines) > MAX_LINES:
                overflow = len(job.lines) - MAX_LINES
                del job.lines[:overflow]
                job.dropped += overflow
            self._changed.notify_all()
