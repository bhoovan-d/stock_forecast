from __future__ import annotations

import json
import pandas as pd

import pytest

from asymmetry.v3_events import append, append_correction, load, rollout_status


def test_event_log_is_append_only_hash_chained_and_idempotent(tmp_path):
    path = tmp_path / "events.jsonl"
    first = append("signal-1", "signal_created", "2026-01-02 10:00+05:30", {"entry": 100}, path)
    duplicate = append("signal-1", "signal_created", "2026-01-02 10:00+05:30", {"entry": 100}, path)
    correction = append_correction("signal-1", "bad tick", {"entry": 100.05}, path)
    events = load(path)
    assert duplicate.event_id == first.event_id
    assert [event.event_type for event in events] == ["signal_created", "correction"]
    assert correction.previous_hash == first.event_hash


def test_event_log_detects_rewrites(tmp_path):
    path = tmp_path / "events.jsonl"
    append("signal-1", "signal_created", "2026-01-02 10:00+05:30", {"entry": 100}, path)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["payload"]["entry"] = 99
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity failure"):
        load(path)


def test_forward_approval_requires_thirty_signals_and_six_months(tmp_path):
    path = tmp_path / "events.jsonl"
    start = pd.Timestamp("2025-01-02 10:00+05:30")
    for index in range(30):
        stamp = start + pd.Timedelta(days=index * 7)
        signal_id = f"signal-{index}"
        append(signal_id, "signal_created", stamp, {
            "qualified": True, "expected_cost_r": 0.1,
        }, path)
        won = index < 25
        append(signal_id, "target" if won else "stop", stamp + pd.Timedelta(days=2), {
            "realised_r": 2 if won else -1, "net_r": 1.9 if won else -1.1,
        }, path)
    status = rollout_status(path)
    assert status.phase == "paper-approved"

    short_path = tmp_path / "short.jsonl"
    for index in range(30):
        stamp = start + pd.Timedelta(days=index * 3)
        signal_id = f"short-{index}"
        append(signal_id, "signal_created", stamp, {
            "qualified": True, "expected_cost_r": 0.1,
        }, short_path)
        append(signal_id, "target", stamp + pd.Timedelta(days=1), {
            "realised_r": 2, "net_r": 1.9,
        }, short_path)
    assert rollout_status(short_path).phase == "paper"
