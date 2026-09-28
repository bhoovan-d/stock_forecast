"""Chronological walk-forward research partitions with mandatory embargoes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .contracts import require_aware


@dataclass(frozen=True)
class ResearchExample:
    example_id: str
    candidate_at: datetime
    label_available_at: datetime
    features: dict[str, Any]
    labels: dict[str, bool]

    def __post_init__(self) -> None:
        require_aware(self.candidate_at, "candidate_at")
        require_aware(self.label_available_at, "label_available_at")
        if self.label_available_at < self.candidate_at:
            raise ValueError("a label cannot be available before its candidate")


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        require_aware(self.start, "window start")
        require_aware(self.end, "window end")
        if self.end <= self.start:
            raise ValueError("research window end must follow its start")

    def contains(self, value: datetime) -> bool:
        return self.start <= value < self.end


@dataclass(frozen=True)
class WalkForwardFold:
    fold_id: str
    development: TimeWindow
    validation: TimeWindow
    final_test: TimeWindow
    embargo: timedelta

    def __post_init__(self) -> None:
        if not self.fold_id.strip():
            raise ValueError("fold_id is required")
        if self.embargo <= timedelta(0):
            raise ValueError("walk-forward embargo must be positive")
        if self.development.end + self.embargo > self.validation.start:
            raise ValueError("development and validation windows violate the embargo")
        if self.validation.end + self.embargo > self.final_test.start:
            raise ValueError("validation and final-test windows violate the embargo")

    def partition(
        self, examples: Iterable[ResearchExample]
    ) -> dict[str, tuple[ResearchExample, ...]]:
        items = list(examples)
        if items != sorted(items, key=lambda item: (item.candidate_at, item.example_id)):
            raise ValueError("research examples must be supplied in chronological order")
        return {
            "development": self._eligible(items, self.development),
            "validation": self._eligible(items, self.validation),
            "final_test": self._eligible(items, self.final_test),
        }

    @staticmethod
    def _eligible(
        examples: list[ResearchExample], window: TimeWindow
    ) -> tuple[ResearchExample, ...]:
        return tuple(
            example for example in examples
            if window.contains(example.candidate_at)
            and example.label_available_at <= window.end
        )


@dataclass(frozen=True)
class WalkForwardPlan:
    folds: tuple[WalkForwardFold, ...]

    def validate(self) -> None:
        if not self.folds:
            raise ValueError("at least one walk-forward fold is required")
        ids = [fold.fold_id for fold in self.folds]
        if len(ids) != len(set(ids)):
            raise ValueError("walk-forward fold identifiers must be unique")
        ordered = sorted(self.folds, key=lambda fold: fold.final_test.start)
        if list(self.folds) != ordered:
            raise ValueError("walk-forward folds must be chronologically ordered")
        for prior, current in zip(self.folds, self.folds[1:]):
            if prior.final_test.end > current.final_test.start:
                raise ValueError("walk-forward final-test windows cannot overlap")
