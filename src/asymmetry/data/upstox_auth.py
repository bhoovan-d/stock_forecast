"""Local Upstox OAuth helper.

Everything here runs on the user's own machine. The browser login happens in their browser,
the authorisation code is caught by a loopback server on localhost, and the token exchange
is a direct call from their machine to Upstox. No credential is ever transmitted anywhere
except to Upstox itself, and the token is written straight into the gitignored ``.env``.

This exists because the manual alternative — copying a ``code`` out of a redirect URL and
hand-building a POST containing your API secret — is easy to get wrong and tempting to do
insecurely (secrets in shell history, pasted into the wrong window).

Upstox access tokens expire daily around 03:30 IST, so this is a routine operation.
"""

from __future__ import annotations

import http.server
import socket
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx
from loguru import logger

from ..config import ROOT, settings

AUTH_DIALOG = "https://api.upstox.com/v2/login/authorization/dialog"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
ENV_PATH = ROOT / ".env"


class _CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Catches the single OAuth redirect and shows the user a plain result page."""

    code: str | None = None
    error: str | None = None

    def do_GET(self) -> None:  # noqa: N802 — required by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)

        _CallbackHandler.code = (params.get("code") or [None])[0]
        _CallbackHandler.error = (params.get("error_description") or params.get("error") or [None])[0]

        ok = _CallbackHandler.code is not None
        body = (
            "<h2>Authorised.</h2><p>Token is being written to .env. "
            "You can close this tab and return to the terminal.</p>"
            if ok
            else f"<h2>Authorisation failed.</h2><p>{_CallbackHandler.error or 'No code returned.'}</p>"
        )
        page = (
            "<!doctype html><meta charset='utf-8'><title>Upstox</title>"
            "<style>body{font-family:system-ui,sans-serif;max-width:34rem;margin:14vh auto;"
            "padding:0 1.5rem;line-height:1.6;color:#111}h2{margin:0 0 .4rem}"
            "@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}}</style>"
            f"{body}"
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *args) -> None:
        """Silence the default request logging — the URL contains the auth code."""


def _port_from(redirect_uri: str) -> int:
    parsed = urllib.parse.urlparse(redirect_uri)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def update_env(token: str, path: Path = ENV_PATH) -> None:
    """Write UPSTOX_ACCESS_TOKEN into .env, preserving every other line.

    Rewrites in place rather than appending, so repeated refreshes do not accumulate stale
    duplicate keys that would then shadow each other unpredictably.
    """
    line = f"UPSTOX_ACCESS_TOKEN={token}"
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
        replaced = False
        for i, existing in enumerate(lines):
            if existing.strip().startswith("UPSTOX_ACCESS_TOKEN="):
                lines[i] = line
                replaced = True
                break
        if not replaced:
            lines.append(line)
    else:
        lines = [line]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def login(timeout_sec: int = 180) -> str | None:
    """Run the full OAuth flow locally. Returns the access token, or None on failure."""
    if not settings.upstox_api_key or not settings.upstox_api_secret:
        logger.error(
            "Set UPSTOX_API_KEY and UPSTOX_API_SECRET in .env first "
            "(create an app at https://account.upstox.com/developer/apps)."
        )
        return None

    redirect_uri = settings.upstox_redirect_uri
    port = _port_from(redirect_uri)
    if not _port_is_free(port):
        logger.error(
            f"Port {port} is already in use, so the redirect cannot be caught. "
            "Close whatever is using it, or change UPSTOX_REDIRECT_URI (and the matching "
            "redirect URI in your Upstox app) to a free port."
        )
        return None

    _CallbackHandler.code = None
    _CallbackHandler.error = None

    server = http.server.HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.timeout = timeout_sec
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()

    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": settings.upstox_api_key,
            "redirect_uri": redirect_uri,
        }
    )
    url = f"{AUTH_DIALOG}?{params}"

    logger.info("Opening your browser to log in to Upstox…")
    logger.info(f"If it does not open, visit:\n{url}")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001 — a headless box just uses the printed URL
        pass

    thread.join(timeout=timeout_sec)
    server.server_close()

    if _CallbackHandler.code is None:
        logger.error(
            f"No authorisation code received within {timeout_sec}s. "
            f"Error: {_CallbackHandler.error or 'none reported'}"
        )
        return None

    logger.info("Code received, exchanging for an access token…")
    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "code": _CallbackHandler.code,
                "client_id": settings.upstox_api_key,
                "client_secret": settings.upstox_api_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
    except httpx.HTTPError as exc:
        logger.error(f"Token exchange failed: {exc}")
        return None

    if response.status_code != 200:
        # Upstox echoes the reason (bad secret, redirect mismatch, expired code) and the
        # body contains no token, so it is safe and useful to surface.
        logger.error(f"Token exchange rejected ({response.status_code}): {response.text[:300]}")
        return None

    token = response.json().get("access_token")
    if not token:
        logger.error("Upstox returned no access_token.")
        return None

    update_env(token)
    # Never log the token itself.
    logger.info(f"Access token written to {ENV_PATH}")
    return token
