"""Chronological geometry study for the first liquidity-sweep pattern proposal."""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .ledger import sha256_json


@dataclass(frozen=True)
class StudyRule:
    direction: str
    minimum_sweep_bps: int
    minimum_reclaim_bps: int
    confirmation_bars: int


@dataclass(frozen=True)
class Outcome:
    session_date: date
    symbol: str
    result: str
    realized_r: float


WINDOWS = {
    "development": (date(2026, 6, 16), date(2026, 7, 31)),
    "validation": (date(2026, 8, 4), date(2026, 8, 31)),
    "evaluation": (date(2026, 9, 3), date(2026, 9, 16)),
}
SWEEP_GRID = (5, 10, 20, 30)
RECLAIM_GRID = (0, 5, 10, 20)
CONFIRMATION_GRID = (1, 2, 3, 4)


def _stats(outcomes: list[Outcome]) -> dict[str, Any]:
    count = len(outcomes)
    if not count:
        return {"count": 0, "expectancy_r": None, "target_rate": None}
    target_count = sum(item.result == "target" for item in outcomes)
    stop_count = sum(item.result == "stop" for item in outcomes)
    expiry_count = count - target_count - stop_count
    values = [item.realized_r for item in outcomes]
    mean = sum(values) / count
    variance = sum((value - mean) ** 2 for value in values) / max(1, count - 1)
    return {
        "count": count,
        "targets": target_count,
        "stops": stop_count,
        "expiries": expiry_count,
        "target_rate": target_count / count,
        "expectancy_r": mean,
        "standard_error_r": math.sqrt(variance / count),
    }


class PriorSessionSweepStudy:
    def __init__(self, ledger_path: str | Path) -> None:
        self.ledger_path = Path(ledger_path)

    def run(self) -> dict[str, Any]:
        bars, evidence = self._load_bars()
        evaluations: list[dict[str, Any]] = []
        for direction in ("bullish", "bearish"):
            for sweep_bps in SWEEP_GRID:
                for reclaim_bps in RECLAIM_GRID:
                    for confirmation_bars in CONFIRMATION_GRID:
                        rule = StudyRule(direction, sweep_bps, reclaim_bps, confirmation_bars)
                        outcomes = self._evaluate(bars, rule)
                        windows = {
                            name: _stats([
                                item for item in outcomes if start <= item.session_date <= end
                            ])
                            for name, (start, end) in WINDOWS.items()
                        }
                        evaluations.append({"rule": asdict(rule), "windows": windows})
        eligible = [
            item for item in evaluations
            if item["windows"]["development"]["count"] >= 25
        ]
        selected = max(
            eligible,
            key=lambda item: (
                item["windows"]["development"]["expectancy_r"],
                item["windows"]["development"]["count"],
                item["rule"]["minimum_sweep_bps"],
                item["rule"]["minimum_reclaim_bps"],
                -item["rule"]["confirmation_bars"],
            ),
        )
        selected_rule = StudyRule(**selected["rule"])
        selected_outcomes = self._evaluate(bars, selected_rule)
        overall = _stats(selected_outcomes)
        symbol_counts = Counter(item.symbol for item in selected_outcomes)
        report: dict[str, Any] = {
            "study_id": "prior-session-sweep-reclaim-15m-v1",
            "generated_at": datetime.now().astimezone().isoformat(),
            "data": evidence,
            "hypothesis_count": len(evaluations),
            "selection_rule": "maximum development-window expectancy with >=25 outcomes; later windows excluded from selection",
            "selected_rule": selected["rule"],
            "fixed_geometry": {
                "anchor": "prior_session_high_for_bearish_or_low_for_bullish",
                "minimum_prior_touches": 1,
                "entry": "next_completed_15m_bar_open_in_same_session_after_reclaim",
                "structural_stop": "sweep_extreme",
                "accepted_stop_distance_pct": [0.5, 2.0],
                "target_r": 2.0,
                "holding_sessions": 3,
                "holding_session_counting": "entry_session_inclusive",
                "same_bar_stop_target_policy": "stop_first_without_finer_evidence",
            },
            "windows": selected["windows"],
            "overall": overall,
            "largest_symbol_candidate_share": (
                max(symbol_counts.values()) / len(selected_outcomes) if selected_outcomes else None
            ),
            "comparison": {
                "best_bullish_development": max(
                    (item for item in eligible if item["rule"]["direction"] == "bullish"),
                    key=lambda item: item["windows"]["development"]["expectancy_r"],
                ),
                "best_bearish_development": max(
                    (item for item in eligible if item["rule"]["direction"] == "bearish"),
                    key=lambda item: item["windows"]["development"]["expectancy_r"],
                ),
            },
            "limitations": [
                "Geometry-only underlying labels; no option bid/ask, slippage, fees, or taxes.",
                "The grid compares 128 hypotheses, so the selected development result has selection bias.",
                "The evaluation window was observed during engineering and cannot be reused as future untouched evidence.",
                "The period is short and the bearish evaluation overlaps a weak market regime.",
                "This study supports an inactive research proposal only, not paper-alert activation or funded trading.",
            ],
        }
        report["report_hash"] = sha256_json(report)
        return report

    def _load_bars(self) -> tuple[dict[str, dict[datetime, tuple[float, ...]]], dict[str, Any]]:
        db = sqlite3.connect(self.ledger_path)
        db.row_factory = sqlite3.Row
        observations: dict[str, dict[str, Any]] = {}
        for row in db.execute(
            "SELECT aggregate_id,payload_json FROM ledger_event WHERE event_type='source_observed'"
        ):
            observations[str(row["aggregate_id"])] = json.loads(row["payload_json"])
        captures = [
            json.loads(row["payload_json"])
            for row in db.execute(
                "SELECT payload_json FROM ledger_event WHERE event_type='research_intraday_captured'"
            )
        ]
        result: dict[str, dict[datetime, tuple[float, ...]]] = defaultdict(dict)
        content_hashes: list[str] = []
        for capture in captures:
            observed = observations[str(capture["observation_id"])]
            payload = json.loads(Path(observed["storage_ref"]).read_text(encoding="utf-8"))
            content_hashes.append(str(observed["content_sha256"]))
            for candle in payload.get("data", {}).get("candles", []):
                if not isinstance(candle, list) or len(candle) < 6:
                    raise ValueError("malformed intraday candle in approved raw evidence")
                timestamp = datetime.fromisoformat(str(candle[0]).replace("Z", "+00:00"))
                result[str(capture["symbol"])][timestamp] = tuple(map(float, candle[1:6]))
        evidence = {
            "captured_windows": len(captures),
            "symbols": len(result),
            "interval_minutes": 15,
            "earliest_session": min(ts.date() for rows in result.values() for ts in rows).isoformat(),
            "latest_session": max(ts.date() for rows in result.values() for ts in rows).isoformat(),
            "observation_set_hash": sha256_json(sorted(content_hashes)),
        }
        return result, evidence

    def _evaluate(
        self,
        raw_by_symbol: dict[str, dict[datetime, tuple[float, ...]]],
        rule: StudyRule,
    ) -> list[Outcome]:
        outcomes: list[Outcome] = []
        for symbol, raw in raw_by_symbol.items():
            sessions: dict[date, list[tuple[Any, ...]]] = defaultdict(list)
            for timestamp, values in sorted(raw.items()):
                sessions[timestamp.date()].append((timestamp, *values))
            days = sorted(sessions)
            for index in range(1, len(days)):
                bars = sessions[days[index]]
                previous = sessions[days[index - 1]]
                anchor = (
                    min(bar[3] for bar in previous)
                    if rule.direction == "bullish"
                    else max(bar[2] for bar in previous)
                )
                setup = self._find_setup(bars, anchor, rule)
                if setup is None:
                    continue
                entry_index, entry, stop, risk = setup
                target = entry + 2 * risk if rule.direction == "bullish" else entry - 2 * risk
                future = [
                    bar for future_day in days[index:index + 3]
                    for bar in sessions[future_day]
                    if bar[0] >= bars[entry_index][0]
                ]
                exit_price, result = future[-1][4], "expiry"
                for bar in future:
                    stop_hit = bar[3] <= stop if rule.direction == "bullish" else bar[2] >= stop
                    target_hit = bar[2] >= target if rule.direction == "bullish" else bar[3] <= target
                    if stop_hit:
                        exit_price, result = stop, "stop"
                        break
                    if target_hit:
                        exit_price, result = target, "target"
                        break
                realized_r = (
                    (exit_price - entry) / risk
                    if rule.direction == "bullish" else (entry - exit_price) / risk
                )
                outcomes.append(Outcome(days[index], symbol, result, realized_r))
        return outcomes

    @staticmethod
    def _find_setup(
        bars: list[tuple[Any, ...]], anchor: float, rule: StudyRule
    ) -> tuple[int, float, float, float] | None:
        for index, bar in enumerate(bars[:-1]):
            extreme = bar[3] if rule.direction == "bullish" else bar[2]
            sweep_bps = (
                (anchor - extreme) / anchor * 10_000
                if rule.direction == "bullish" else (extreme - anchor) / anchor * 10_000
            )
            if sweep_bps < rule.minimum_sweep_bps:
                continue
            sweep_extreme = extreme
            for offset in range(index, min(len(bars) - 1, index + rule.confirmation_bars)):
                current = bars[offset]
                sweep_extreme = (
                    min(sweep_extreme, current[3])
                    if rule.direction == "bullish" else max(sweep_extreme, current[2])
                )
                reclaim_bps = (
                    (current[4] - anchor) / anchor * 10_000
                    if rule.direction == "bullish" else (anchor - current[4]) / anchor * 10_000
                )
                if reclaim_bps < rule.minimum_reclaim_bps:
                    continue
                entry = bars[offset + 1][1]
                risk = (
                    entry - sweep_extreme
                    if rule.direction == "bullish" else sweep_extreme - entry
                )
                stop_pct = risk / entry * 100
                if 0.5 <= stop_pct <= 2.0:
                    return offset + 1, entry, sweep_extreme, risk
                break
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", required=True)
    args = parser.parse_args()
    print(json.dumps(PriorSessionSweepStudy(args.ledger).run(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
