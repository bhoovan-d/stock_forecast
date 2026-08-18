"""Reading the figures out of a results filing.

Built because 352 of 617 stored catalysts on the 18 Aug 2026 window were results filings
scored a flat 50 — the largest category in the store recording only *that* a company
reported. The numbers were always one HTTP request away.

Everything here is pure logic. The fetch and the model call are the parts that cannot be
tested offline, and they are exactly the parts whose *failure* has to be safe, so the
fallback is what gets the most attention below.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from asymmetry.engines.catalyst import _event_record, _results_record
from asymmetry.intelligence import results_pdf
from asymmetry.intelligence.filings import route


class _Filing:
    symbol = "IGL"
    headline = "Indraprastha Gas Ltd.: Unaudited Financial Results For Quarter ended June"
    category = "Result"
    subcategory = "Financial Results"
    published = datetime(2026, 7, 24, 18, 30)
    url = "https://www.bseindia.com/xml-data/corpfiling/AttachLive/abc.pdf"
    critical = False
    body = ""


class _Cascade:
    """Stands in for the LLM cascade. `result` is whatever `score` should return."""

    available = True

    def __init__(self, result):
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def score(self, headline, context):
        self.calls.append((headline, context))
        return self._result


class _Extraction:
    def __init__(self, value, delta=-2):
        self._value = value
        self.expectation_delta = delta
        self.materiality = 2
        self.durability = 2
        self.already_priced = False
        self.confidence = 2
        self.rationale = "PAT down ~48% YoY"
        self.catalyst_type = type("T", (), {"value": "earnings_surprise"})()

    def score(self):
        return self._value


# ── Routing ───────────────────────────────────────────────────────────────────


def test_results_route_beats_the_generic_event_route():
    """"Financial Results" is still in EVENT_SUBCATS as the fallback, so ordering inside
    `route` is what sends it down the PDF path rather than to a neutral 50."""
    assert route("Financial Results", "Result") == "results"
    assert route("Dividend", "Corp. Action") == "event"
    assert route("Record Date", "Corp. Action") == "skip"
    assert route("Outcome of Board Meeting", "Board Meeting") == "llm"


# ── Extraction ────────────────────────────────────────────────────────────────


def test_extract_keeps_result_rows_with_their_comparatives():
    """The comparative columns are the entire point — without them there is a number and
    nothing to judge it against."""
    text = "\n".join([
        "UNAUDITED FINANCIAL RESULTS",
        "1 Revenue from operations 5,040.15 4,584.51 4,326.60",
        "7 Profit for the period (5-6) 186.18 277.08 355.94",
        "Notes to the financial results",
    ])
    out = results_pdf.extract_financials(text)
    assert "Revenue from operations 5,040.15 4,584.51 4,326.60" in out
    assert "Profit for the period" in out
    assert "Notes to the financial" not in out


def test_extract_drops_labels_with_no_numbers():
    """A contents line or a note reference matches the keyword but carries no result. The
    first version of this kept them and filled the extract with headings."""
    text = "\n".join([
        "Statement of Profit before tax and other matters",
        "Earnings per share",
        "5 Profit before tax (3-4) 259.92 384.92",
    ])
    out = results_pdf.extract_financials(text)
    assert out == "5 Profit before tax (3-4) 259.92 384.92"


@pytest.mark.parametrize("text", ["", "Board meeting intimation, no figures attached."])
def test_extract_returns_none_when_there_is_nothing_to_read(text):
    """Scanned image PDFs extract to nothing. None is the signal to fall back."""
    assert results_pdf.extract_financials(text) is None


# ── The consensus caveat ──────────────────────────────────────────────────────


def test_context_forbids_calling_it_a_beat():
    """No consensus estimate is available at any price here. A model told only "profit rose
    40%" reliably reports a beat, and a beat against nothing is a fabricated number entering
    a system whose whole discipline is that inputs are measured."""
    context = results_pdf.build_context("1 Revenue from operations 100 90 80")
    assert "NO CONSENSUS ESTIMATE IS AVAILABLE" in context
    assert "beat or a miss" in context
    assert "1 Revenue from operations 100 90 80" in context


# ── The fallback, which is the safety property ────────────────────────────────


def _neutral(record) -> bool:
    return record.score == 50.0 and record.provider == "rule"


def test_unreadable_pdf_falls_back_to_the_neutral_event(monkeypatch):
    monkeypatch.setattr(results_pdf, "read_results", lambda _url: None)
    monkeypatch.setattr("asymmetry.engines.catalyst.read_results", lambda _url: None)
    record = _results_record(_Filing(), _Cascade(None))
    assert _neutral(record)
    assert record.rationale == _event_record(_Filing()).rationale


def test_no_cascade_falls_back_without_fetching_anything(monkeypatch):
    """Ordering matters: with no model to score them, downloading megabytes of PDF would be
    pure waste."""
    called = []
    monkeypatch.setattr(
        "asymmetry.engines.catalyst.read_results",
        lambda url: called.append(url) or "figures",
    )
    assert _neutral(_results_record(_Filing(), None))
    assert called == []


def test_model_failure_falls_back(monkeypatch):
    monkeypatch.setattr("asymmetry.engines.catalyst.read_results", lambda _u: "figures")
    assert _neutral(_results_record(_Filing(), _Cascade(None)))


def test_unremarkable_trajectory_is_recorded_as_the_neutral_event(monkeypatch):
    """The model read the figures and judged nothing moved. The filing still happened, so
    it is recorded — but as the neutral marker, not as a scored catalyst."""
    monkeypatch.setattr("asymmetry.engines.catalyst.read_results", lambda _u: "figures")
    record = _results_record(_Filing(), _Cascade((_Extraction(50.0, delta=0), "gemini")))
    assert _neutral(record)


def test_real_figures_produce_a_directional_catalyst(monkeypatch):
    """The case the whole module exists for: IGL's June 2026 filing scored 50 before this
    and 23.33 after, on a PAT decline of ~48% that was always printed in the PDF."""
    monkeypatch.setattr("asymmetry.engines.catalyst.read_results", lambda _u: "figures")
    record = _results_record(_Filing(), _Cascade((_Extraction(23.33), "gemini")))

    assert record.score == 23.33
    assert record.provider == "gemini"
    assert record.expectation_delta == -2
    # Distinguishable from a subject-line record at a glance, and in the store.
    assert record.source.endswith("(PDF)")
    assert "direction unknown" not in record.rationale


def test_the_model_is_given_the_figures_not_the_subject_line(monkeypatch):
    monkeypatch.setattr("asymmetry.engines.catalyst.read_results", lambda _u: "REV 100 90")
    cascade = _Cascade((_Extraction(23.33), "gemini"))
    _results_record(_Filing(), cascade)

    _headline, context = cascade.calls[0]
    assert "REV 100 90" in context
    assert "NO CONSENSUS ESTIMATE IS AVAILABLE" in context
