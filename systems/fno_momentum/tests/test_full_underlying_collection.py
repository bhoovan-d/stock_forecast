import json

import pytest

from fno_momentum.full_underlying_collection import Collector, MANIFEST, StorageLimitError


def test_total_storage_cap_blocks_a_payload_before_writing(tmp_path, monkeypatch):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["storage"]["root"] = str(tmp_path / "store")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    collector = Collector(path)
    cap = collector.cap
    monkeypatch.setattr("fno_momentum.full_underlying_collection.used_bytes", lambda _root: cap - collector.reserve)
    target = collector.root / "raw" / "example.gz"
    with pytest.raises(StorageLimitError):
        collector.write_capped(target, b"x")
    assert not target.exists()


def test_historical_allocation_preserves_room_for_session_capture(tmp_path, monkeypatch):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["storage"]["root"] = str(tmp_path / "store")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    collector = Collector(path)
    monkeypatch.setattr("fno_momentum.full_underlying_collection.used_bytes",
                        lambda _root: collector.historical_soft_cap)
    with pytest.raises(StorageLimitError):
        collector.write_capped(collector.root / "raw" / "old-history.gz", b"x", historical=True)


def test_bulk_history_is_disabled_in_active_on_demand_phase(tmp_path):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["storage"]["root"] = str(tmp_path / "store")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(PermissionError):
        Collector(path).backfill_all()
