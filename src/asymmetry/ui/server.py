"""The panel's HTTP server.

Deliberately the standard library and nothing else. The project has no web dependency and
this does not justify adding one: it serves a handful of JSON routes, one page, and a
stream of log lines to a single local viewer.

It binds to loopback. There is no authentication because there is no remote surface — but
that also means the bind address is not a knob, since anything else would put a
"run this command" endpoint on the network.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import threading
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from ..config import BRIEF_DIR, DATA_DIR, ROOT, settings
from . import commands as registry
from .jobs import JobRunner

STATIC_DIR = Path(__file__).parent / "static"
PUBLIC_DIR = ROOT / "public"
HOST = "127.0.0.1"


def _status_snapshot() -> dict:
    """Cheap facts about the installation — no network, no engine work."""
    from ..storage import latest_stored_date

    try:
        latest = latest_stored_date()
    except Exception:
        latest = None

    db = settings.db_path
    briefs = sorted(BRIEF_DIR.glob("*.md"), reverse=True)
    keys = {
        "Upstox token": bool(settings.upstox_access_token),
        "Cerebras": bool(settings.cerebras_api_key),
        "Groq": bool(settings.groq_api_key),
        "Gemini": bool(settings.gemini_api_key),
        "Anthropic": bool(settings.anthropic_api_key),
    }
    return {
        "root": str(ROOT),
        "latest_stored": str(latest) if latest else None,
        "db_mb": round(db.stat().st_size / 1e6, 1) if db.exists() else 0.0,
        "cache_mb": round(
            sum(p.stat().st_size for p in (DATA_DIR / "cache").rglob("*") if p.is_file()) / 1e6,
            1,
        ),
        "brief_count": len(briefs),
        "latest_brief": briefs[0].stem if briefs else None,
        "keys": keys,
        "universe": settings.universe_index.upper(),
        "min_rr": settings.min_reward_risk,
        "stop_band": [settings.min_stop_pct, settings.v3_max_stop_pct],
    }


def _list_documents() -> list[dict]:
    """Generated briefs (Markdown) and published pages (HTML), newest first."""
    items: list[dict] = []
    for path in BRIEF_DIR.glob("*.md"):
        stat = path.stat()
        items.append({
            "name": path.name,
            "kind": "brief",
            "title": path.stem,
            "modified": stat.st_mtime,
            "size": stat.st_size,
        })
    if PUBLIC_DIR.exists():
        for path in PUBLIC_DIR.glob("*.html"):
            stat = path.stat()
            items.append({
                "name": path.name,
                "kind": "page",
                "title": path.stem,
                "modified": stat.st_mtime,
                "size": stat.st_size,
            })
    items.sort(key=lambda d: (d["modified"]), reverse=True)
    return items


def _safe_child(directory: Path, name: str) -> Path | None:
    """Resolve `name` inside `directory`, refusing anything that escapes it."""
    candidate = (directory / unquote(name)).resolve()
    try:
        candidate.relative_to(directory.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


class Handler(BaseHTTPRequestHandler):
    server_version = "AsymmetryPanel"
    protocol_version = "HTTP/1.1"
    runner: JobRunner

    # ── plumbing ──────────────────────────────────────────────────────────────

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - stdlib signature
        pass  # the panel's own log pane is the interesting output, not access lines

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

    def _json(self, payload, code: int = 200) -> None:
        self._send(code, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _text(self, text: str, ctype: str = "text/plain; charset=utf-8", code: int = 200) -> None:
        self._send(code, text.encode("utf-8"), ctype)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # ── routes ────────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        url = urlparse(self.path)
        path = url.path
        query = parse_qs(url.query)

        if path in ("/", "/index.html"):
            return self._static("index.html")
        if path in ("/app.js", "/app.css"):
            return self._static(path.lstrip("/"))
        if path == "/theme.css":
            from ..report.theme import TOKENS

            return self._text(TOKENS, "text/css; charset=utf-8")

        if path == "/api/commands":
            return self._json({"commands": registry.as_json(), "groups": list(registry.GROUPS)})
        if path == "/api/status":
            return self._json(_status_snapshot())
        if path == "/api/jobs":
            return self._json({"jobs": self.runner.recent(), "active": self.runner.active()})
        if path.startswith("/api/jobs/"):
            rest = path[len("/api/jobs/"):]
            job_id, _, tail = rest.partition("/")
            job = self.runner.get(job_id)
            if job is None:
                return self._json({"error": "no such job"}, HTTPStatus.NOT_FOUND)
            if tail == "stream":
                return self._stream(job_id, int(query.get("from", ["0"])[0]))
            lines, nxt = self.runner.lines_from(job_id, int(query.get("from", ["0"])[0]))
            return self._json({**job.summary(), "lines": lines, "next": nxt})

        if path == "/api/documents":
            return self._json({"documents": _list_documents()})
        if path.startswith("/api/documents/"):
            name = path[len("/api/documents/"):]
            target = _safe_child(BRIEF_DIR, name)
            if target is None:
                return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return self._json({"name": target.name, "text": target.read_text(encoding="utf-8")})
        if path.startswith("/page/"):
            target = _safe_child(PUBLIC_DIR, path[len("/page/"):])
            if target is None:
                return self._text("not found", code=HTTPStatus.NOT_FOUND)
            return self._send(200, target.read_bytes(), "text/html; charset=utf-8")

        return self._text("not found", code=HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        url = urlparse(self.path)
        if url.path == "/api/jobs":
            return self._start(self._body())
        if url.path.startswith("/api/jobs/") and url.path.endswith("/cancel"):
            job_id = url.path[len("/api/jobs/"):-len("/cancel")]
            return self._json({"cancelled": self.runner.cancel(job_id)})
        return self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

    # ── handlers ──────────────────────────────────────────────────────────────

    def _static(self, name: str) -> None:
        target = STATIC_DIR / name
        if not target.is_file():
            return self._text("not found", code=HTTPStatus.NOT_FOUND)
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), f"{ctype}; charset=utf-8")

    def _start(self, body: dict) -> None:
        command = registry.BY_ID.get(str(body.get("command", "")))
        if command is None:
            return self._json({"error": "unknown command"}, HTTPStatus.BAD_REQUEST)
        values = body.get("values") or {}
        if not isinstance(values, dict):
            return self._json({"error": "values must be an object"}, HTTPStatus.BAD_REQUEST)
        try:
            argv = registry.build_argv(command, values)
        except ValueError as exc:
            return self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        try:
            width = int(body.get("width") or 0) or None
        except (TypeError, ValueError):
            width = None
        job = self.runner.submit(command.id, command.title, argv, width=width)
        self._json(job.summary())

    def _stream(self, job_id: str, offset: int) -> None:
        """Server-sent events: one message per batch of new lines."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            for chunk, cursor, status in self.runner.follow(job_id, offset):
                payload = json.dumps({"lines": chunk, "next": cursor, "status": status})
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
            job = self.runner.get(job_id)
            if job is not None:
                done = json.dumps({"done": True, **job.summary()})
                self.wfile.write(f"data: {done}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, OSError):
            pass  # the page navigated away or the tab closed


def serve(port: int = 8765, open_browser: bool = True, width: int = 150) -> None:
    """Run the panel until interrupted."""
    # Windows lets a second socket bind a port that is already listening (SO_REUSEADDR is
    # a hijack there, not a guard), so a duplicate `asymmetry ui` would silently end up
    # with two servers answering one port. Ask first.
    with socket.socket() as probe:
        probe.settimeout(0.4)
        if probe.connect_ex((HOST, port)) == 0:
            raise SystemExit(
                f"A panel is already running on http://{HOST}:{port}/ — open it, "
                "or start this one with --port."
            )

    handler = type("BoundHandler", (Handler,), {"runner": JobRunner(width=width)})
    try:
        httpd = ThreadingHTTPServer((HOST, port), handler)
    except OSError as exc:
        raise SystemExit(
            f"Cannot bind {HOST}:{port} — {exc.strerror or exc}.\n"
            f"Another panel is probably already running; open http://{HOST}:{port}/ "
            "or pass --port."
        ) from None
    httpd.daemon_threads = True
    url = f"http://{HOST}:{port}/"
    started = datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S")
    print(f"[{started}] Asymmetry panel on {url}  (Ctrl-C to stop)")
    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nPanel stopped.")
    finally:
        httpd.server_close()
