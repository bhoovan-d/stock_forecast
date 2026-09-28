from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from fno_momentum.intraday_backfill import IntradayHistoryBackfill, monthly_windows
from fno_momentum.ledger import ImmutableLedger


NOW = datetime(2026, 9, 16, 4, tzinfo=timezone.utc)


class FakeClient:
    def historical_candles(self, instrument_key, **kwargs):
        return SimpleNamespace(
            observation=SimpleNamespace(
                observation_id=f"obs-{instrument_key}-{kwargs['from_date']}",
                content_sha256="a" * 64,
                retrieved_at=NOW,
            ),
            payload={"data": {"candles": [["timestamp", 1, 1, 1, 1, 1]]}},
        )


def test_monthly_windows_are_non_overlapping_and_provider_safe():
    windows = monthly_windows(date(2026, 1, 1), date(2026, 3, 5))
    assert windows[0] == (date(2026, 1, 1), date(2026, 1, 28))
    assert all((end - start).days <= 27 for start, end in windows)
    assert all(prior[1] < current[0] for prior, current in zip(windows, windows[1:]))


def test_intraday_backfill_uses_active_universe_and_is_resumable(tmp_path):
    ledger = ImmutableLedger(tmp_path / "events.sqlite")
    ledger.append(
        "universe_snapshot_created",
        "universe-1",
        {
            "universe_hash": "u" * 64,
            "member_count": 1,
            "members": [{"underlying_key": "NSE_EQ|ABC", "symbol": "ABC"}],
        },
        occurred_at=NOW,
        idempotency_key="universe",
    )
    backfill = IntradayHistoryBackfill(ledger, FakeClient())
    first = backfill.run(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 29),
        interval_minutes=15,
        minimum_interval_seconds=0,
    )
    assert first == {"completed": 2, "skipped": 0, "failed": 0}
    second = backfill.run(
        from_date=date(2026, 1, 1),
        to_date=date(2026, 1, 29),
        interval_minutes=15,
        minimum_interval_seconds=0,
    )
    assert second == {"completed": 0, "skipped": 2, "failed": 0}
    with pytest.raises(ValueError, match="restricted"):
        backfill.run(
            from_date=date(2026, 1, 1),
            to_date=date(2026, 1, 2),
            interval_minutes=1,
            minimum_interval_seconds=0,
        )
