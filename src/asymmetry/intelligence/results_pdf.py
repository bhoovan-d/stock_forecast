"""Read the numbers out of a results filing.

This exists because of a measurement. Of 617 catalyst records collected over the 18 Aug
2026 replay window, **352 were results filings scored exactly 50.0** — a deliberate
neutral, because `_event_record` could only see a subject line reading "Financial Results -
Quarter ended June 2026". The single largest catalyst category in the store said only *that
a company reported*, never *what it reported*. "Has a catalyst" therefore mostly meant
"filed results recently", which is an attendance marker rather than an answer to §12's "why
now" — and that is very likely why the catalyst filter measured as worthless on the setups
that have edge.

The numbers were always reachable. Every BSE filing carries a direct PDF link and those
PDFs fetch fine from here (verified: 200, ~900KB, `application/pdf`). Nothing read them.

**What this can and cannot establish.** It extracts the reported figures and the
prior-period columns printed beside them, so it can judge *trajectory* — revenue and profit
against the previous quarter and the year-ago quarter. It cannot judge *surprise*, because
no consensus estimate is available here at any price. A model told only "profit rose 40%"
will happily call that a beat; it is not, if the street wanted 60%. The prompt context says
so explicitly, and `confidence` is capped accordingly — inventing a consensus is exactly
the failure `_event_record`'s neutral score was protecting against, and replacing one
invention with another would be no improvement.

Scanned image PDFs extract to nothing and fall back to the neutral event record, the same
as a fetch failure. A filing whose numbers could not be read must never be scored as though
they had been.
"""

from __future__ import annotations

import hashlib
import re

from loguru import logger

from ..config import CACHE_DIR
from ..data.cache import PacedClient

# Own client: BSE's attachment host is a different pressure point from its JSON API, and a
# slow PDF must not stall the announcements fetch that produced the link.
_client = PacedClient(
    min_interval_sec=1.0,
    max_retries=2,
    headers={"Referer": "https://www.bseindia.com/corporates/ann.html"},
)

_TEXT_CACHE = CACHE_DIR / "filing-text"

# Results PDFs are mostly boilerplate — auditor language, segment notes, signatures. These
# are the lines that carry the actual result, matched case-insensitively against the
# extracted text. Indian filings follow the SEBI LODR format closely enough that the
# wording is stable across companies.
_KEY_LINES = (
    "revenue from operations",
    "total income",
    "total expenses",
    "profit before tax",
    "profit after tax",
    "profit for the period",
    "profit/(loss)",
    "total comprehensive income",
    "earnings per share",
    "earnings per equity share",
    "ebitda",
    "finance cost",
)

# A results PDF runs to tens of pages of notes; the result itself is in the first few. Read
# past that and the extract fills with segment breakdowns and auditor boilerplate, which
# costs tokens and buries the figures the model is meant to compare.
_MAX_PAGES = 12
# Enough for the statement of results with its comparative columns, not so much that the
# model has to hunt. Measured against a real filing, the matched lines come to ~1-2KB.
_MAX_CHARS = 6000


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]


def fetch_pdf_text(url: str) -> str | None:
    """Download a filing PDF and return its text, cached on disk by URL.

    Cached because the backfill re-walks the same days and these are ~1MB each; re-fetching
    them would dominate the run and hammer a host that is already rate-sensitive.
    """
    if not url.lower().endswith(".pdf"):
        return None

    _TEXT_CACHE.mkdir(parents=True, exist_ok=True)
    cached = _TEXT_CACHE / f"{_cache_key(url)}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8", errors="replace") or None

    response = _client.get(url)
    if response is None or response.status_code != 200:
        return None
    if not response.content.startswith(b"%PDF-"):
        return None

    try:
        from io import BytesIO

        from pypdf import PdfReader

        reader = PdfReader(BytesIO(response.content))
        pages = [(page.extract_text() or "") for page in reader.pages[:_MAX_PAGES]]
        text = "\n".join(pages)
    except Exception as exc:  # noqa: BLE001 — a malformed PDF must not break the scan
        logger.debug(f"[results-pdf] could not parse {url}: {exc}")
        return None

    # Write even when empty, so a scanned image PDF is not re-downloaded on every run just
    # to yield nothing again. The empty string reads back as None above.
    cached.write_text(text, encoding="utf-8")
    return text or None


def extract_financials(text: str) -> str | None:
    """The lines a reader would actually look at, with their comparative columns intact.

    Whole pages are not sent. A results statement is a table, and the value is in the row
    labels and the numbers beside them; the surrounding notes add tokens and no signal.
    """
    if not text:
        return None

    kept: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        lowered = line.lower()
        if any(key in lowered for key in _KEY_LINES):
            # A label with no digits beside it is a heading or a note reference, not a row
            # of results. Keeping those was how the first extract filled with contents pages.
            if re.search(r"\d", line):
                kept.append(line)
        if sum(len(k) for k in kept) > _MAX_CHARS:
            break

    if not kept:
        return None
    return "\n".join(kept[:60])


def read_results(url: str) -> str | None:
    """Fetch a results filing and return its figures, or None if they could not be read."""
    text = fetch_pdf_text(url)
    if text is None:
        return None
    return extract_financials(text)


def build_context(figures: str) -> str:
    """Frame the figures for the scoring prompt, including what is *not* available.

    The consensus caveat is not decoration. Given only "profit up 40%" a model reliably
    reports a beat, and a beat against nothing is a fabricated number entering a scoring
    system that treats every input as measured.
    """
    return (
        "Reported figures extracted from the company's own results filing (PDF). The "
        "columns are, in the order the filing prints them, the current period followed by "
        "its comparatives — the preceding quarter and the year-ago quarter.\n\n"
        f"{figures}\n\n"
        "NO CONSENSUS ESTIMATE IS AVAILABLE. Judge the change in trajectory against the "
        "comparative columns only — growth, margin direction, any swing between profit and "
        "loss. Do NOT describe this as a beat or a miss, and do not assume what the street "
        "expected. If the trajectory is unremarkable, score expectation_delta 0."
    )
