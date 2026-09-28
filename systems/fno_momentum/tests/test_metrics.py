from datetime import datetime, timezone

from fno_momentum.ledger import ImmutableLedger
from fno_momentum.metrics import CohortReporter


NOW = datetime(2026, 9, 15, 10, tzinfo=timezone.utc)


def _append(ledger, event_type, aggregate_id, payload, key):
    return ledger.append(
        event_type,
        aggregate_id,
        payload,
        occurred_at=NOW,
        idempotency_key=key,
    )


def test_cohort_metrics_never_pool_pattern_direction_or_timeframe(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    candidate = _append(
        ledger,
        "candidate_created",
        "candidate-1",
        {
            "pattern_family": "liquidity_sweep",
            "pattern_variant": "reversal_reclaim",
            "direction": "bullish",
            "entry_timeframe": "5m",
        },
        "candidate",
    )
    alert = _append(ledger, "core_paper_alert", candidate.aggregate_id, {}, "alert")
    _append(
        ledger,
        "outcome_labeled",
        alert.event_id,
        {
            "alert_event_id": alert.event_id,
            "labels": {"2R": True, "3R": False, "4R": False, "5R": False},
        },
        "outcome",
    )
    _append(
        ledger,
        "settlement",
        "position-1",
        {
            "alert_event_id": alert.event_id,
            "net_option_pnl_per_lot": 125,
            "normalized_underlying_r": 2,
        },
        "settlement",
    )
    report = CohortReporter(ledger).build()
    assert len(report) == 1
    assert report[0].key.direction == "bullish"
    assert report[0].key.entry_timeframe == "5m"
    assert report[0].target_hit_rate["2R"] == 1
    assert report[0].option_net_pnl_per_lot == 125


def test_dhan_source_proposal_hash_is_frozen():
    import json
    from pathlib import Path

    from fno_momentum.source_registry import SourceManifest

    path = Path(__file__).parents[2] / "proposals" / "fno_source_dhan_v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["fields"] = tuple(value["fields"])
    assert SourceManifest(**value).content_hash == (
        "01b3947f2360d3be760b65ba6099c3b83d46c4e228ac81cc695a54cdf69e13ba"
    )


def test_free_upstox_source_proposal_hash_is_frozen():
    import json
    from pathlib import Path

    from fno_momentum.source_registry import SourceManifest

    path = Path(__file__).parents[2] / "proposals" / "fno_source_upstox_free_v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["fields"] = tuple(value["fields"])
    assert SourceManifest(**value).content_hash == (
        "f47a860205a3a9ff922a4338bb1246a94ea56620f34167edf6c9311a5ba86aa1"
    )
