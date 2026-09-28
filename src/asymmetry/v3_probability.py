"""Leakage-resistant probabilities and funding gates for V3."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from .config import DATA_DIR, settings

if TYPE_CHECKING:
    from .engines.v3_scan import V3Candidate, V3Scan
    from .v3_backtest import Trade

MODEL_PATH = DATA_DIR / "v3_probability.json"
MODEL_VERSION = 4
INDIA = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = time(9, 15)
SESSION_CLOSE = time(15, 30)
TARGETS = (2,)
SETUPS = ("reclaim",)
FEATURE_NAMES = (
    "bias", "stop_pct", "carry_score", "setup_quality", "adr_pct", "atr_pct",
    "entry_time", "regime_aggressive", "regime_defensive",
)


def market_is_open(now: datetime | None = None) -> bool:
    moment = now or datetime.now(INDIA)
    moment = moment.replace(tzinfo=INDIA) if moment.tzinfo is None else moment.astimezone(INDIA)
    return moment.weekday() < 5 and SESSION_OPEN <= moment.time().replace(tzinfo=None) < SESSION_CLOSE


def trigger_is_fresh(trigger, now: datetime | None = None) -> bool:
    if trigger is None:
        return False
    moment = now or datetime.now(INDIA)
    moment = moment.replace(tzinfo=INDIA) if moment.tzinfo is None else moment.astimezone(INDIA)
    stamp = pd.Timestamp(trigger)
    stamp = stamp.tz_localize(INDIA) if stamp.tzinfo is None else stamp.tz_convert(INDIA)
    age = (pd.Timestamp(moment) - stamp).total_seconds() / 60
    return stamp.date() == moment.date() and 0 <= age <= settings.v3_probability_max_bar_age_minutes


def wilson_interval(wins: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if n <= 0:
        return float("nan"), float("nan")
    p = wins / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def block_interval(trades: list[Trade], target_r: int, seed: int = 17) -> tuple[float, float]:
    """Bootstrap whole dates so one market day is not treated as independent trades."""
    if not trades:
        return float("nan"), float("nan")
    by_day: dict[date, list[float]] = {}
    for trade in trades:
        by_day.setdefault(_decision_date(trade), []).append(float(trade.hit_target(target_r)))
    days = list(by_day)
    rng = np.random.default_rng(seed + target_r)
    values = []
    for _ in range(1000):
        picked = rng.choice(days, size=len(days), replace=True)
        outcomes = [outcome for day in picked for outcome in by_day[day]]
        values.append(float(np.mean(outcomes)))
    return float(np.quantile(values, 0.025)), float(np.quantile(values, 0.975))


def cost_adjusted_break_even(target_r: float, cost_r: float) -> float:
    return (1.0 + cost_r) / (target_r + 1.0)


def expected_net_r(probability: float, target_r: float, cost_r: float) -> float:
    return probability * target_r - (1.0 - probability) - cost_r


def _point_floor(target_r: int) -> float:
    return {2: settings.v3_min_probability_2r, 3: settings.v3_min_probability_3r,
            4: settings.v3_min_probability_4r}[target_r]


def _conservative_floor(target_r: int) -> float:
    return {2: settings.v3_min_conservative_2r, 3: settings.v3_min_conservative_3r,
            4: settings.v3_min_conservative_4r}[target_r]


def _decision_date(trade: Trade) -> date:
    return pd.Timestamp(trade.signal_at or trade.entered_at).date()


def _eligible(trade: Trade) -> bool:
    return (trade.entry_available and trade.direction == "long" and trade.setup in SETUPS
            and trade.outcome in {"target", "stop", "timeout"}
            and trade.resolved_at is not None
            and (trade.setup == "reclaim" or trade.carry_passed))


def _trade_features(trade: Trade) -> np.ndarray:
    stamp = pd.Timestamp(trade.entered_at)
    minutes = max(0, min(375, (stamp.hour * 60 + stamp.minute) - 555)) / 375
    return np.array([
        1.0, trade.stop_pct / 1.5, trade.carry_score / 100,
        trade.setup_quality / 100, min(trade.adr_pct, 10) / 5,
        min(trade.atr_pct, 10) / 5, minutes,
        float(trade.regime == "aggressive"), float(trade.regime == "defensive"),
    ], dtype=float)


def _candidate_features(candidate: V3Candidate, regime: str) -> np.ndarray:
    plan = candidate.plan
    stamp = pd.Timestamp(plan.trigger_bar) if plan and plan.trigger_bar is not None else pd.Timestamp.now()
    minutes = max(0, min(375, (stamp.hour * 60 + stamp.minute) - 555)) / 375
    return np.array([
        1.0, (plan.stop_pct if plan else 0.0) / 1.5, candidate.carry.score / 100,
        candidate.setup.quality / 100, min(candidate.adr_pct, 10) / 5,
        min(candidate.atr_pct, 10) / 5, minutes,
        float(regime == "aggressive"), float(regime == "defensive"),
    ], dtype=float)


def _stop_bucket(stop_pct: float) -> str:
    if stop_pct < 0.80:
        return "tight"
    if stop_pct < 1.10:
        return "medium"
    return "wide"


def _time_bucket(stamp) -> str:
    clock = pd.Timestamp(stamp).time()
    if clock < time(10, 30):
        return "open"
    if clock < time(12, 30):
        return "mid"
    return "late"


def _trade_segment(trade: Trade) -> str:
    return f"{trade.regime}|{_stop_bucket(trade.stop_pct)}|{_time_bucket(trade.entered_at)}"


def _candidate_segment(candidate: V3Candidate, regime: str) -> str:
    plan = candidate.plan
    stamp = plan.trigger_bar if plan and plan.trigger_bar is not None else pd.Timestamp.now()
    return f"{regime}|{_stop_bucket(plan.stop_pct if plan else 0.0)}|{_time_bucket(stamp)}"


def _sigmoid(values):
    return 1.0 / (1.0 + np.exp(-np.clip(values, -30, 30)))


def _fit_logistic(trades: list[Trade], target_r: int) -> list[float]:
    if not trades:
        return []
    x = np.vstack([_trade_features(trade) for trade in trades])
    y = np.array([trade.hit_target(target_r) for trade in trades], dtype=float)
    if y.min() == y.max():
        return []

    def objective(beta):
        z = x @ beta
        loss = np.mean(np.logaddexp(0, z) - y * z) + 0.5 * np.sum(beta[1:] ** 2) / len(y)
        gradient = x.T @ (_sigmoid(z) - y) / len(y)
        gradient[1:] += beta[1:] / len(y)
        return float(loss), gradient

    result = minimize(objective, np.zeros(x.shape[1]), jac=True, method="L-BFGS-B")
    return result.x.tolist() if result.success else []


def _predict_many(method: str, probability: float, coefficients: list[float], trades: list[Trade]):
    if method == "logistic" and coefficients:
        return _sigmoid(np.vstack([_trade_features(trade) for trade in trades]) @ np.array(coefficients))
    return np.full(len(trades), probability, dtype=float)


@dataclass
class Evidence:
    n: int = 0
    wins: int = 0
    probability: float = float("nan")
    lower: float = float("nan")
    upper: float = float("nan")
    symbols: int = 0
    sessions: int = 0
    mean_net_r: float = float("nan")
    conservative_net_r: float = float("nan")
    brier: float = float("nan")
    calibration_error: float = float("nan")
    approved: bool = False
    max_drawdown_r: float = float("nan")
    max_losing_streak: int = 0
    max_symbol_share: float = float("nan")
    max_sector_share: float = float("nan")
    max_month_share: float = float("nan")
    regimes_positive: bool = False
    stability_approved: bool = False


@dataclass
class StrategyModel:
    setup: str = ""
    target_r: int = 0
    method: str = "empirical"
    feature_names: list[str] = field(default_factory=lambda: list(FEATURE_NAMES))
    coefficients: list[float] = field(default_factory=list)
    base_probability: float = float("nan")
    training_n: int = 0
    validation: Evidence = field(default_factory=Evidence)
    final: Evidence = field(default_factory=Evidence)
    raw_validation: Evidence = field(default_factory=Evidence)
    raw_final: Evidence = field(default_factory=Evidence)
    validation_segments: dict[str, Evidence] = field(default_factory=dict)
    final_segments: dict[str, Evidence] = field(default_factory=dict)
    raw_final_segments: dict[str, Evidence] = field(default_factory=dict)
    walk_forward_segments: dict[str, bool] = field(default_factory=dict)
    approved_segments: list[str] = field(default_factory=list)
    walk_forward_profitable: bool = False
    approved: bool = False


@dataclass
class ProbabilityEstimate:
    target_r: int
    probability: float
    lower: float
    upper: float
    n: int
    wins: int
    cohort: str
    cost_r: float
    break_even: float
    expected_net_r: float
    conservative_net_r: float
    validation_approved: bool
    validation_n: int
    validation_rate: float
    validation_gap: float
    accepted: bool
    reason: str
    method: str = "none"


@dataclass
class ProbabilityModel:
    version: int = MODEL_VERSION
    created_at: str = ""
    source: str = ""
    development_start: str = ""
    development_end: str = ""
    validation_start: str = ""
    validation_end: str = ""
    final_start: str = ""
    final_end: str = ""
    embargo_sessions: int = 5
    strategies: dict[str, StrategyModel] = field(default_factory=dict)
    rule_version: str = ""
    code_revision: str = ""
    code_dirty: bool = False
    universe_snapshot: str = ""
    universe_hash: str = ""
    data_hash: str = ""

    @property
    def approved(self) -> bool:
        return any(strategy.approved for strategy in self.strategies.values())

    @property
    def training_start(self) -> str:
        return self.development_start

    @property
    def training_end(self) -> str:
        return self.development_end

    def predict(self, *, setup: str, admitted: bool, stop_pct: float, target_r: int,
                features: np.ndarray | None = None, segment: str = "") -> ProbabilityEstimate:
        strategy = self.strategies.get(f"{setup}:{target_r}")
        if strategy is None or strategy.training_n == 0 or (
            setup == "base-breakout" and not admitted
        ):
            return ProbabilityEstimate(
                target_r, float("nan"), float("nan"), float("nan"), 0, 0,
                f"setup={setup}|admitted={int(admitted)}", 0.0, float("nan"),
                float("nan"), float("nan"), False, 0, float("nan"), float("nan"),
                False, "no independently tested model for this setup and carry condition",
            )
        probability = strategy.base_probability
        if strategy.method == "logistic" and strategy.coefficients and features is not None:
            probability = float(_sigmoid(features @ np.array(strategy.coefficients)))
        evidence = strategy.final
        cost_r = ((settings.cost_roundtrip_pct + settings.slippage_pct) / stop_pct
                  if stop_pct > 0 else float("inf"))
        expected = expected_net_r(probability, target_r, cost_r)
        conservative = expected_net_r(evidence.lower, target_r, cost_r)
        reasons = []
        if not strategy.approved:
            reasons.append("the fixed reclaim strategy failed independent approval")
        if probability < _point_floor(target_r):
            reasons.append(f"{probability:.0%} measured chance is below {_point_floor(target_r):.0%}")
        if evidence.lower < _conservative_floor(target_r):
            conservative_text = (
                f"{evidence.lower:.0%}" if np.isfinite(evidence.lower) else "unavailable"
            )
            reasons.append(
                f"the conservative result {conservative_text} is below "
                f"{_conservative_floor(target_r):.0%}"
            )
        if conservative < settings.v3_min_conservative_net_r:
            reasons.append(f"conservative expected profit {conservative:+.2f}R is below +{settings.v3_min_conservative_net_r:.2f}R")
        return ProbabilityEstimate(
            target_r=target_r, probability=probability, lower=evidence.lower,
            upper=evidence.upper, n=evidence.n, wins=evidence.wins,
            cohort=f"setup={setup}|{segment}|admitted={int(admitted)}", cost_r=cost_r,
            break_even=cost_adjusted_break_even(target_r, cost_r), expected_net_r=expected,
            conservative_net_r=conservative, validation_approved=strategy.approved,
            validation_n=evidence.n, validation_rate=evidence.probability,
            validation_gap=evidence.calibration_error, accepted=not reasons,
            reason="; ".join(reasons) if reasons else "all historical funding gates passed",
            method=strategy.method,
        )


def _split(trades: list[Trade], embargo: int):
    dates = sorted({_decision_date(trade) for trade in trades})
    if len(dates) < 20:
        raise ValueError("at least 20 decision sessions are required")
    dev_end_i = max(1, int(len(dates) * 0.60)) - 1
    validation_start_i = dev_end_i + 1 + embargo
    validation_end_i = max(validation_start_i, int(len(dates) * 0.80) - 1)
    final_start_i = validation_end_i + 1 + embargo
    if final_start_i >= len(dates):
        raise ValueError("history is too short for development, validation, final test and embargoes")
    validation_start, final_start = dates[validation_start_i], dates[final_start_i]
    development = [t for t in trades if _decision_date(t) <= dates[dev_end_i]
                   and t.resolved_at is not None and pd.Timestamp(t.resolved_at).date() < validation_start]
    validation = [t for t in trades if validation_start <= _decision_date(t) <= dates[validation_end_i]
                  and t.resolved_at is not None and pd.Timestamp(t.resolved_at).date() < final_start]
    final = [t for t in trades if _decision_date(t) >= final_start]
    return dates, development, validation, final


def _evidence(trades: list[Trade], probabilities, target_r: int) -> Evidence:
    if not trades:
        return Evidence()
    probabilities = np.asarray(probabilities, dtype=float)
    mask = probabilities >= _point_floor(target_r)
    selected = [trade for trade, include in zip(trades, mask) if include]
    selected_p = probabilities[mask]
    if not selected:
        return Evidence()
    wins = sum(trade.hit_target(target_r) for trade in selected)
    probability = wins / len(selected)
    lower, upper = wilson_interval(wins, len(selected))
    mean_cost = float(np.mean([trade.cost_r or 0.0 for trade in selected]))
    mean_net = float(np.mean([trade.payoff_r(target_r) - (trade.cost_r or 0.0) for trade in selected]))
    conservative_net = expected_net_r(lower, target_r, mean_cost)
    outcomes = np.array([trade.hit_target(target_r) for trade in selected], dtype=float)
    net_values = [trade.payoff_r(target_r) - (trade.cost_r or 0.0) for trade in selected]
    equity, peak, max_drawdown, losing, max_losing = 0.0, 0.0, 0.0, 0, 0
    for value in net_values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        losing = losing + 1 if value <= 0 else 0
        max_losing = max(max_losing, losing)

    def largest_share(values: list[str]) -> float:
        counts = pd.Series(values).value_counts()
        return float(counts.iloc[0] / len(values)) if len(counts) else float("nan")

    regimes_positive = True
    for regime in ("aggressive", "selective", "defensive"):
        group = [value for trade, value in zip(selected, net_values) if trade.regime == regime]
        if len(group) < settings.reliability_min_regime_trades or float(np.mean(group)) <= 0:
            regimes_positive = False
    max_symbol_share = largest_share([trade.symbol for trade in selected])
    sectors = [trade.sector or "unknown" for trade in selected]
    max_sector_share = largest_share(sectors)
    max_month_share = largest_share([str(_decision_date(trade))[:7] for trade in selected])
    stability = (
        regimes_positive
        and max_drawdown <= settings.reliability_max_drawdown_r
        and max_losing <= settings.reliability_max_losing_streak
        and max_symbol_share <= settings.reliability_max_symbol_share
        and max_sector_share <= settings.reliability_max_sector_share
        and max_month_share <= settings.reliability_max_month_share
    )
    evidence = Evidence(
        n=len(selected), wins=wins, probability=probability, lower=lower, upper=upper,
        symbols=len({trade.symbol for trade in selected}),
        sessions=len({_decision_date(trade) for trade in selected}),
        mean_net_r=mean_net, conservative_net_r=conservative_net,
        brier=float(np.mean((selected_p - outcomes) ** 2)),
        calibration_error=abs(float(selected_p.mean()) - probability),
        max_drawdown_r=max_drawdown, max_losing_streak=max_losing,
        max_symbol_share=max_symbol_share, max_sector_share=max_sector_share,
        max_month_share=max_month_share, regimes_positive=regimes_positive,
        stability_approved=stability,
    )
    evidence.approved = (
        evidence.n >= settings.v3_probability_min_validation
        and evidence.symbols >= settings.v3_probability_min_symbols
        and evidence.sessions >= settings.v3_probability_min_sessions
        and evidence.probability >= _point_floor(target_r)
        and evidence.lower >= _conservative_floor(target_r)
        and evidence.conservative_net_r >= settings.v3_min_conservative_net_r
        and evidence.mean_net_r > 0
        and evidence.stability_approved
    )
    return evidence


def _segment_evidence(
    trades: list[Trade], probabilities, target_r: int,
) -> dict[str, Evidence]:
    grouped: dict[str, tuple[list[Trade], list[float]]] = {}
    for trade, probability in zip(trades, probabilities):
        segment = _trade_segment(trade)
        group_trades, group_probabilities = grouped.setdefault(segment, ([], []))
        group_trades.append(trade)
        group_probabilities.append(float(probability))
    return {
        segment: _evidence(group_trades, group_probabilities, target_r)
        for segment, (group_trades, group_probabilities) in grouped.items()
    }


def _raw_evidence(trades: list[Trade], target_r: int) -> Evidence:
    """Describe every executable trade without turning that description into approval."""
    if not trades:
        return Evidence()
    outcomes = np.array([trade.hit_target(target_r) for trade in trades], dtype=float)
    wins = int(outcomes.sum())
    probability = wins / len(trades)
    lower, upper = block_interval(trades, target_r)
    mean_cost = float(np.mean([trade.cost_r or 0.0 for trade in trades]))
    return Evidence(
        n=len(trades), wins=wins, probability=probability, lower=lower, upper=upper,
        symbols=len({trade.symbol for trade in trades}),
        sessions=len({_decision_date(trade) for trade in trades}),
        mean_net_r=float(np.mean([
            trade.payoff_r(target_r) - (trade.cost_r or 0.0) for trade in trades
        ])),
        conservative_net_r=expected_net_r(lower, target_r, mean_cost),
    )


def _raw_segment_evidence(trades: list[Trade], target_r: int) -> dict[str, Evidence]:
    grouped: dict[str, list[Trade]] = {}
    for trade in trades:
        grouped.setdefault(_trade_segment(trade), []).append(trade)
    return {
        segment: _raw_evidence(group, target_r) for segment, group in grouped.items()
    }


def _walk_forward_profitable(
    trades: list[Trade], target_r: int, method: str, embargo_sessions: int,
) -> bool:
    dates = sorted({_decision_date(trade) for trade in trades})
    results = []
    for fraction in (0.45, 0.60, 0.75):
        cut = int(len(dates) * fraction)
        test_start = cut + embargo_sessions
        if cut <= 0 or test_start >= len(dates):
            continue
        train_dates = set(dates[:cut])
        test_dates = set(dates[test_start:test_start + max(2, len(dates) // 8)])
        first_test = dates[test_start]
        training = [
            trade for trade in trades
            if _decision_date(trade) in train_dates
            and pd.Timestamp(trade.resolved_at).date() < first_test
        ]
        testing = [trade for trade in trades if _decision_date(trade) in test_dates]
        if not training or not testing:
            continue
        base = float(np.mean([trade.hit_target(target_r) for trade in training]))
        coefficients = _fit_logistic(training, target_r) if method == "logistic" else []
        predictions = _predict_many(method, base, coefficients, testing)
        selected = [trade for trade, probability in zip(testing, predictions)
                    if probability >= _point_floor(target_r)]
        if selected:
            results.append(float(np.mean([
                trade.payoff_r(target_r) - (trade.cost_r or 0.0) for trade in selected
            ])) > 0)
    return len(results) == 3 and all(results)


def fit(trades: list[Trade], *, source: str = "", embargo_sessions: int = 5,
        provenance: dict[str, object] | None = None) -> ProbabilityModel:
    eligible = sorted((trade for trade in trades if _eligible(trade)), key=_decision_date)
    dates, development, validation, final = _split(eligible, embargo_sessions)
    if not development:
        raise ValueError("no development trades remain after purging overlapping outcomes")
    provenance = provenance or {}
    model = ProbabilityModel(
        created_at=datetime.now().astimezone().isoformat(), source=source,
        development_start=str(dates[0]),
        development_end=str(max(_decision_date(trade) for trade in development)),
        validation_start=str(min(_decision_date(trade) for trade in validation)) if validation else "",
        validation_end=str(max(_decision_date(trade) for trade in validation)) if validation else "",
        final_start=str(min(_decision_date(trade) for trade in final)) if final else "",
        final_end=str(max(_decision_date(trade) for trade in final)) if final else "",
        embargo_sessions=embargo_sessions,
        rule_version=settings.reliability_rule_version,
        code_revision=str(provenance.get("code_revision", "")),
        code_dirty=bool(provenance.get("code_dirty", False)),
        universe_snapshot=str(provenance.get("universe_snapshot", "")),
        universe_hash=str(provenance.get("universe_hash", "")),
        data_hash=str(provenance.get("data_hash", "")),
    )
    for setup in SETUPS:
        dev = [trade for trade in development if trade.setup == setup]
        val = [trade for trade in validation if trade.setup == setup]
        fin = [trade for trade in final if trade.setup == setup]
        for target_r in TARGETS:
            base = float(np.mean([trade.hit_target(target_r) for trade in dev])) if dev else float("nan")
            coefficients = _fit_logistic(dev, target_r)
            empirical = _predict_many("empirical", base, [], val)
            logistic = _predict_many("logistic", base, coefficients, val)
            outcomes = np.array([trade.hit_target(target_r) for trade in val], dtype=float)
            empirical_brier = float(np.mean((empirical - outcomes) ** 2)) if val else float("inf")
            logistic_brier = float(np.mean((logistic - outcomes) ** 2)) if val and coefficients else float("inf")
            method = "logistic" if logistic_brier + 0.005 < empirical_brier else "empirical"
            production = dev + val
            production_base = float(np.mean([trade.hit_target(target_r) for trade in production])) if production else float("nan")
            production_coefficients = _fit_logistic(production, target_r) if method == "logistic" else []
            val_predictions = _predict_many(method, base, coefficients, val)
            final_predictions = _predict_many(
                method, production_base, production_coefficients, fin
            )
            val_evidence = _evidence(val, val_predictions, target_r)
            final_evidence = _evidence(
                fin, final_predictions, target_r
            )
            validation_segments = _segment_evidence(val, val_predictions, target_r)
            final_segments = _segment_evidence(fin, final_predictions, target_r)
            segment_names = sorted(set(validation_segments) | set(final_segments))
            walk_segments = {
                segment: _walk_forward_profitable(
                    [trade for trade in development + validation
                     if _trade_segment(trade) == segment],
                    target_r, method, embargo_sessions,
                )
                for segment in segment_names
            }
            approved_segments = [
                segment for segment in segment_names
                if validation_segments.get(segment, Evidence()).approved
                and final_segments.get(segment, Evidence()).approved
                and walk_segments.get(segment, False)
            ]
            walk = _walk_forward_profitable(
                development + validation, target_r, method, embargo_sessions
            )
            strategy = StrategyModel(
                setup=setup, target_r=target_r, method=method,
                coefficients=production_coefficients, base_probability=production_base,
                training_n=len(production), validation=val_evidence, final=final_evidence,
                raw_validation=_raw_evidence(val, target_r),
                raw_final=_raw_evidence(fin, target_r),
                validation_segments=validation_segments, final_segments=final_segments,
                raw_final_segments=_raw_segment_evidence(fin, target_r),
                walk_forward_segments=walk_segments,
                approved_segments=approved_segments, walk_forward_profitable=walk,
            )
            strategy.approved = bool(
                val_evidence.approved and final_evidence.approved and walk
            )
            model.strategies[f"{setup}:{target_r}"] = strategy
    return model


def save(model: ProbabilityModel, path: str | Path = MODEL_PATH) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(asdict(model), indent=2) + "\n", encoding="utf-8")


def load(path: str | Path = MODEL_PATH) -> ProbabilityModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != MODEL_VERSION:
        raise ValueError("unsupported V3 probability model version; recalibration is required")
    payload["strategies"] = {
        key: StrategyModel(**{
            **value, "validation": Evidence(**value.get("validation", {})),
            "final": Evidence(**value.get("final", {})),
            "raw_validation": Evidence(**value.get("raw_validation", {})),
            "raw_final": Evidence(**value.get("raw_final", {})),
            "validation_segments": {
                segment: Evidence(**evidence)
                for segment, evidence in value.get("validation_segments", {}).items()
            },
            "final_segments": {
                segment: Evidence(**evidence)
                for segment, evidence in value.get("final_segments", {}).items()
            },
            "raw_final_segments": {
                segment: Evidence(**evidence)
                for segment, evidence in value.get("raw_final_segments", {}).items()
            },
        }) for key, value in payload.get("strategies", {}).items()
    }
    return ProbabilityModel(**payload)


def annotate_candidate(candidate: V3Candidate, model: ProbabilityModel, regime: str = "selective") -> None:
    if candidate.plan is None:
        return
    admitted = candidate.setup.kind.value == "reclaim" or candidate.carry.passes
    features = _candidate_features(candidate, regime)
    for target_r in TARGETS:
        setattr(candidate, f"probability_{target_r}r", model.predict(
            setup=candidate.setup.kind.value, admitted=admitted,
            stop_pct=candidate.plan.stop_pct, target_r=target_r, features=features,
            segment=_candidate_segment(candidate, regime),
        ))


def apply_to_scan(scan: V3Scan, model: ProbabilityModel, *, strict: bool = False,
                  require_live: bool = False, require_capital: bool = False) -> V3Scan:
    scan.probability_model_status = "funding-qualified" if model.approved else "paper evidence only"
    scan.probability_model_source = model.source
    candidates = list(scan.trades) + [candidate for candidate in scan.near_miss if candidate.plan]
    for candidate in candidates:
        annotate_candidate(candidate, model, scan.regime)
    if not strict:
        return scan
    kept, demoted = [], []
    for candidate in scan.trades:
        estimates = [candidate.probability_2r]
        accepted = [estimate for estimate in estimates if estimate and estimate.accepted]
        reason = ""
        if candidate.direction != "long":
            reason = "the first production strategy is long-only"
        elif require_live and model.final_end and (
            date.today() - date.fromisoformat(model.final_end)
        ).days > settings.v3_probability_max_age_days:
            reason = f"probability evidence is older than {settings.v3_probability_max_age_days} days"
        elif require_live and not market_is_open():
            reason = "the NSE cash session is closed"
        elif require_live and not candidate.intraday_tier.startswith("LIVE"):
            reason = f"entry feed is {candidate.intraday_tier or 'not verified live'}"
        elif require_live and (candidate.plan is None or not candidate.plan.is_live):
            reason = "the trigger is stale"
        elif require_live and not trigger_is_fresh(candidate.plan.trigger_bar):
            reason = "the newest completed trigger bar is stale or from another session"
        elif require_capital and settings.account_equity_inr is None:
            reason = "account equity is not configured, so safe size cannot be verified"
        elif not accepted:
            reason = " | ".join(estimate.reason for estimate in estimates if estimate)
        if reason:
            candidate.rejected_by, candidate.reject_detail = "probability gate", reason
            demoted.append(candidate)
            continue
        chosen = max(accepted, key=lambda estimate: estimate.conservative_net_r)
        candidate.recommended_reward_risk = chosen.target_r
        risk = abs(candidate.plan.entry - candidate.plan.stop)
        candidate.recommended_target = candidate.plan.entry + chosen.target_r * risk
        kept.append(candidate)
    kept.sort(key=lambda candidate: -candidate.probability_2r.conservative_net_r)
    scan.trades = kept[:1]
    for candidate in kept[1:]:
        candidate.rejected_by = "daily cap"
        candidate.reject_detail = "only the strongest probability-qualified candidate is shown"
    scan.near_miss = kept[1:] + demoted + scan.near_miss
    return scan


def report_markdown(model: ProbabilityModel) -> str:
    lines = [
        "# Corrected V3 probability report", "",
        f"Evidence: {model.development_start} to {model.final_end or 'unavailable'}; "
        "five-session gaps separate development, validation and final test.", "",
        "## Plain-language result", "",
        "The corrected replay did not find a strategy that is safe to fund. The table below "
        "shows how often each target was reached in the final period. The required win "
        "rates were not reached, the conservative estimates were not reached, and the final "
        "sample was much too short. The system therefore chooses no trade and remains in "
        "paper mode.", "",
        "The raw columns explain what happened. Approval still uses only predictions made "
        "by the validation-selected model in the untouched final period.", "",
        "| Setup | Target | Model | Training | Raw validation | Raw final result | Conservative | Conservative net | Model-selected final | Decision |",
        "| --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    closest = None
    for strategy in model.strategies.values():
        final = strategy.raw_final
        decision = "PASS" if strategy.approved else "PAPER ONLY"
        final_rate = f"{final.wins}/{final.n} ({final.probability:.1%})" if final.n else "0/0"
        lower = f"{final.lower:.1%}" if np.isfinite(final.lower) else "—"
        net = f"{final.conservative_net_r:+.2f}R" if np.isfinite(final.conservative_net_r) else "—"
        lines.append(
            f"| {strategy.setup} | {strategy.target_r}R | {strategy.method} | {strategy.training_n} | "
            f"{strategy.raw_validation.n} | {final_rate} | {lower} | {net} | "
            f"{strategy.final.n} | {decision} |"
        )
        if final.n:
            distance = max(0.0, _point_floor(strategy.target_r) - final.probability)
            distance += max(0.0, _conservative_floor(strategy.target_r) - final.lower)
            if closest is None or distance < closest[0]:
                closest = (distance, strategy, final)
    lines += [
        "", "## Research diagnostics by decision group", "",
        "Regime, stop size and entry time are displayed for stability review. They do not "
        "change or veto the frozen production rule.", "",
        "| Setup | Target | Regime / stop / time | Final trades | Wins | Conservative | Status |",
        "| --- | ---: | --- | ---: | ---: | ---: | --- |",
    ]
    for strategy in model.strategies.values():
        for segment, evidence in sorted(strategy.raw_final_segments.items()):
            lower = f"{evidence.lower:.1%}" if np.isfinite(evidence.lower) else "—"
            decision = "DESCRIPTIVE"
            display_segment = segment.replace("|", " / ")
            lines.append(
                f"| {strategy.setup} | {strategy.target_r}R | {display_segment} | {evidence.n} | "
                f"{evidence.wins}/{evidence.n} | {lower} | {decision} |"
            )
    lines += ["", (
        "The fixed rule cleared every historical gate. It must still pass the forward paper phase."
        if model.approved else "The fixed rule did not clear every gate, so qualified paper alerts remain disabled."
    )]
    final_sessions = max(
        (strategy.raw_final.sessions for strategy in model.strategies.values()), default=0
    )
    if final_sessions < settings.v3_probability_min_sessions:
        lines += ["", (
            f"The final window contains only {final_sessions} trading sessions with usable "
            f"signals; the rule requires {settings.v3_probability_min_sessions}. More "
            "chronological history is required even if a displayed win rate looks attractive."
        )]
    if closest:
        strategy, evidence = closest[1], closest[2]
        lines += ["", (
            f"Closest result by the stated probability floors: {strategy.setup} at "
            f"{strategy.target_r}R ({evidence.wins}/{evidence.n}, {evidence.probability:.1%}; "
            f"conservative {evidence.lower:.1%}). It still has only {evidence.n} trades "
            f"across {evidence.sessions} sessions, versus the required "
            f"{settings.v3_probability_min_validation} trades and "
            f"{settings.v3_probability_min_sessions} sessions. Continue collecting it "
            "without lowering the thresholds."
        )]
    return "\n".join(lines) + "\n"
