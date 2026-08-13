"""On-disk response cache and a paced HTTP client.

Yahoo's chart API throttles aggressively: during source testing a rapid burst of ~11
symbols failed on *every* symbol with a JSON decode error (an HTML error page), while the
identical symbols succeeded when spaced ~2s apart. So pacing, retry-with-backoff and
caching are load-bearing here, not conveniences.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import httpx
from loguru import logger

from ..config import CACHE_DIR, settings

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DiskCache:
    """Content-addressed cache. Keys are hashed so URLs/params can be any length."""

    def __init__(self, root: Path = CACHE_DIR):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str, suffix: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:24]
        return self.root / f"{digest}{suffix}"

    def get_json(self, key: str, ttl_sec: int) -> Any | None:
        path = self._path(key, ".json")
        if not path.exists():
            return None
        if ttl_sec >= 0 and (time.time() - path.stat().st_mtime) > ttl_sec:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def set_json(self, key: str, value: Any) -> None:
        self._path(key, ".json").write_text(json.dumps(value), encoding="utf-8")

    def get_bytes(self, key: str, ttl_sec: int = -1) -> bytes | None:
        """Immutable artefacts (a settled bhavcopy never changes) use ttl_sec=-1."""
        path = self._path(key, ".bin")
        if not path.exists():
            return None
        if ttl_sec >= 0 and (time.time() - path.stat().st_mtime) > ttl_sec:
            return None
        return path.read_bytes()

    def set_bytes(self, key: str, value: bytes) -> None:
        self._path(key, ".bin").write_bytes(value)


class PacedClient:
    """HTTP client enforcing a minimum interval between calls, with backoff on failure.

    One instance per upstream host, so a slow/throttled host cannot stall the others.
    """

    def __init__(
        self,
        min_interval_sec: float,
        max_retries: int = 3,
        headers: dict[str, str] | None = None,
    ):
        self.min_interval = min_interval_sec
        self.max_retries = max_retries
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._client = httpx.Client(
            headers={"User-Agent": BROWSER_UA, **(headers or {})},
            timeout=settings.http_timeout_sec,
            follow_redirects=True,
        )

    def _wait_turn(self) -> None:
        with self._lock:
            elapsed = time.monotonic() - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()

    def get(self, url: str, **kwargs: Any) -> httpx.Response | None:
        """Return the response, or None once retries are exhausted.

        Returning None rather than raising lets a caller degrade to another data tier;
        a single unavailable symbol must never abort a whole scan.
        """
        for attempt in range(self.max_retries):
            self._wait_turn()
            try:
                resp = self._client.get(url, **kwargs)
                if resp.status_code == 200:
                    return resp
                # 429/5xx are worth retrying; a 404 is a genuine answer.
                if resp.status_code in (404, 403):
                    logger.debug(f"[http] {url} -> {resp.status_code}, not retrying")
                    return None
                logger.debug(f"[http] {url} -> {resp.status_code}, retry {attempt + 1}")
            except (httpx.HTTPError, OSError) as exc:
                logger.debug(f"[http] {url} {type(exc).__name__}, retry {attempt + 1}")
            time.sleep(1.5 * (2**attempt))  # exponential backoff
        logger.warning(f"[http] giving up on {url} after {self.max_retries} attempts")
        return None

    def close(self) -> None:
        self._client.close()


cache = DiskCache()
