from datetime import datetime, timezone
from pathlib import Path

import pytest

from fno_momentum.universe_foundation import (
    inspect_instrument_master,
    load_collection_manifest,
    load_source_manifest,
    store_reference_snapshot,
)


def test_inspection_is_stock_only_and_reports_quality_without_repair():
    rows = [
        {
            "segment": "NSE_FO", "instrument_type": "CE", "underlying_type": "EQUITY",
            "instrument_key": "NSE_FO|1", "underlying_key": "NSE_EQ|ABC",
            "underlying_symbol": "ABC", "strike_price": 100, "expiry": "2026-09-24",
            "lot_size": 25, "tick_size": 0.05,
        },
        {
            "segment": "NSE_FO", "instrument_type": "PE", "underlying_type": "EQUITY",
            "instrument_key": "NSE_FO|1", "underlying_key": "NSE_EQ|ABC",
            "underlying_symbol": "ABC", "strike_price": -1, "expiry": "bad",
            "lot_size": 0, "tick_size": 0,
        },
        {
            "segment": "NSE_FO", "instrument_type": "CE", "underlying_type": "INDEX",
            "instrument_key": "NSE_FO|2", "underlying_key": "NSE_INDEX|NIFTY",
            "strike_price": 1, "expiry": "2026-09-24", "lot_size": 1, "tick_size": 0.05,
        },
        "malformed",
    ]
    quality, snapshot = inspect_instrument_master(rows)
    assert quality["fno_stock_underlying_count"] == 1
    assert quality["stock_option_contract_count"] == 2
    assert quality["duplicate_contract_identifiers"] == 1
    assert quality["invalid_expiry"] == 1
    assert quality["invalid_strike"] == 1
    assert quality["invalid_lot_size"] == 1
    assert quality["invalid_tick_size"] == 1
    assert quality["malformed_master_rows"] == 1
    assert len(snapshot["contracts"]) == 2


def test_expiry_epoch_milliseconds_are_normalized_to_ist_contract_date():
    rows = [{
        "segment": "NSE_FO", "instrument_type": "CE", "underlying_type": "EQUITY",
        "instrument_key": "NSE_FO|1", "underlying_key": "NSE_EQ|ABC",
        "underlying_symbol": "ABC", "strike_price": 100,
        "expiry": 1793125799000, "lot_size": 25, "tick_size": 0.05,
    }]
    quality, snapshot = inspect_instrument_master(rows)
    assert quality["invalid_expiry"] == 0
    assert snapshot["contracts"][0]["expiry"] == "2026-10-27"


def test_reference_snapshot_is_content_addressed_and_reproducible(tmp_path):
    snapshot = {"members": [{"symbol": "ABC"}], "contracts": [{"id": "1"}]}
    first = store_reference_snapshot(tmp_path, snapshot)
    second = store_reference_snapshot(tmp_path, snapshot)
    assert first == second
    assert Path(first[2]).is_file()


def test_production_manifests_keep_metadata_phase_fail_closed():
    proposals = Path(__file__).parents[2] / "proposals"
    collection = load_collection_manifest(
        proposals / "fno_upstox_universe_foundation_v1.json"
    )
    source = load_source_manifest(
        proposals / "fno_source_upstox_universe_foundation_v1.json"
    )
    assert collection["active_phase"]["credential_required"] is False
    assert source.source_id == "upstox-read-only-market-data-v3"


def test_collection_manifest_rejects_enabled_prohibited_behavior(tmp_path):
    path = Path(__file__).parents[2] / "proposals" / "fno_upstox_universe_foundation_v1.json"
    value = path.read_text(encoding="utf-8").replace(
        '"orders_or_execution": false', '"orders_or_execution": true'
    )
    changed = tmp_path / "changed.json"
    changed.write_text(value, encoding="utf-8")
    with pytest.raises(PermissionError, match="prohibited behavior"):
        load_collection_manifest(changed)
