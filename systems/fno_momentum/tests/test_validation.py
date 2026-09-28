from datetime import datetime, timedelta, timezone

import pytest

from fno_momentum.validation import (
    ResearchExample,
    TimeWindow,
    WalkForwardFold,
    WalkForwardPlan,
)


UTC = timezone.utc


def _dt(day):
    return datetime(2026, 1, day, tzinfo=UTC)


def test_walk_forward_enforces_embargo_and_point_in_time_labels():
    fold = WalkForwardFold(
        "fold-1",
        development=TimeWindow(_dt(1), _dt(5)),
        validation=TimeWindow(_dt(6), _dt(10)),
        final_test=TimeWindow(_dt(11), _dt(15)),
        embargo=timedelta(days=1),
    )
    examples = [
        ResearchExample("a", _dt(2), _dt(3), {}, {"2R": True}),
        ResearchExample("late-label", _dt(3), _dt(7), {}, {"2R": False}),
        ResearchExample("b", _dt(7), _dt(8), {}, {"2R": True}),
        ResearchExample("c", _dt(12), _dt(13), {}, {"2R": False}),
    ]
    partition = fold.partition(examples)
    assert [item.example_id for item in partition["development"]] == ["a"]
    assert [item.example_id for item in partition["validation"]] == ["b"]
    assert [item.example_id for item in partition["final_test"]] == ["c"]
    WalkForwardPlan((fold,)).validate()


def test_random_or_overlapping_partitions_are_rejected():
    with pytest.raises(ValueError, match="embargo"):
        WalkForwardFold(
            "bad",
            TimeWindow(_dt(1), _dt(5)),
            TimeWindow(_dt(5), _dt(9)),
            TimeWindow(_dt(10), _dt(15)),
            timedelta(days=1),
        )
    fold = WalkForwardFold(
        "fold",
        TimeWindow(_dt(1), _dt(4)),
        TimeWindow(_dt(5), _dt(8)),
        TimeWindow(_dt(9), _dt(12)),
        timedelta(days=1),
    )
    shuffled = [
        ResearchExample("later", _dt(6), _dt(7), {}, {}),
        ResearchExample("earlier", _dt(2), _dt(3), {}, {}),
    ]
    with pytest.raises(ValueError, match="chronological"):
        fold.partition(shuffled)
