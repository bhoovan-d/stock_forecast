"""Immutable, hash-chained forward evidence for the reliability-first strategy."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from .config import DATA_DIR, settings
from .v3_backtest import Trade, resolve_forward, simulate_entry

INDIA = ZoneInfo("Asia/Kolkata")
EVENT_PATH = DATA_DIR / "v3_events.jsonl"
TERMINAL_EVENTS = {"order_cancelled", "stop", "target", "timeout"}


@dataclass(frozen=True)
class SignalEvent:
    event_id: str
    signal_id: str
    event_type: str
    occurred_at: str
    recorded_at: str
    payload: dict
    previous_hash: str
    event_hash: str


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _hash(body: dict) -> str:
    return hashlib.sha256(_canonical(body).encode("utf-8")).hexdigest()


def load(path: str | Path | None = None, *, verify: bool = True) -> list[SignalEvent]:
    source = Path(path) if path is not None else EVENT_PATH
    if not source.exists():
        return []
    events: list[SignalEvent] = []
    previous = ""
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        event = SignalEvent(**value)
        body = {key: value for key, value in asdict(event).items() if key != "event_hash"}
        if verify and (event.previous_hash != previous or _hash(body) != event.event_hash):
            raise ValueError(f"event log integrity failure at line {line_number}")
        events.append(event)
        previous = event.event_hash
    return events


def append(signal_id: str, event_type: str, occurred_at, payload: dict,
           path: str | Path | None = None) -> SignalEvent:
    destination = Path(path) if path is not None else EVENT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    existing = load(destination)
    payload = _json_safe(payload)
    occurred = pd.Timestamp(occurred_at).isoformat()
    identity = _hash({
        "signal_id": signal_id, "event_type": event_type,
        "occurred_at": occurred, "payload": payload,
    })
    duplicate = next((event for event in existing if event.event_id == identity), None)
    if duplicate is not None:
        return duplicate
    body = {
        "event_id": identity,
        "signal_id": signal_id,
        "event_type": event_type,
        "occurred_at": occurred,
        "recorded_at": datetime.now(INDIA).isoformat(),
        "payload": payload,
        "previous_hash": existing[-1].event_hash if existing else "",
    }
    event = SignalEvent(**body, event_hash=_hash(body))
    with destination.open("a", encoding="utf-8", newline="") as handle:
        handle.write(_canonical(asdict(event)) + "\n")
    return event


def _signal_id(symbol: str, trigger_bar, rule_version: str) -> str:
    return _hash({
        "symbol": symbol.upper(), "trigger_bar": pd.Timestamp(trigger_bar).isoformat(),
        "rule_version": rule_version,
    })[:24]


def record_scan(scan, model, path: str | Path | None = None) -> int:
    """Append one frozen signal-created event for every plan-bearing candidate."""
    published = {id(candidate) for candidate in scan.trades}
    candidates = list(scan.trades) + [item for item in scan.near_miss if item.plan is not None]
    existing_signals = {
        event.signal_id for event in load(path) if event.event_type == "signal_created"
    }
    added = 0
    for candidate in candidates:
        plan = candidate.plan
        if plan is None or plan.trigger_bar is None:
            continue
        signal_id = _signal_id(candidate.symbol, plan.trigger_bar, settings.reliability_rule_version)
        if signal_id in existing_signals:
            continue
        estimate = candidate.probability_2r
        qualified = (
            id(candidate) in published and not candidate.rejected_by
            and estimate is not None and estimate.accepted
        )
        risk = abs(plan.entry - plan.stop)
        payload = {
            "symbol": candidate.symbol, "direction": candidate.direction,
            "sector": candidate.sector, "setup": candidate.setup.kind.value,
            "rule_version": settings.reliability_rule_version,
            "trigger_bar": pd.Timestamp(plan.trigger_bar).isoformat(),
            "decision_at": pd.Timestamp(
                plan.decision_at or pd.Timestamp(plan.trigger_bar) + pd.Timedelta(minutes=15)
            ).isoformat(),
            "entry_rule": plan.entry_rule, "entry": plan.entry,
            "entry_min": plan.entry_min, "entry_max": plan.entry_max,
            "stop": plan.stop, "target": plan.entry + settings.reliability_target_r * risk,
            "target_r": settings.reliability_target_r, "quantity": plan.quantity,
            "risk_inr": round(plan.quantity * risk, 2),
            "expected_cost_r": (
                (settings.cost_roundtrip_pct + settings.slippage_pct) / plan.stop_pct
            ),
            "qualified": qualified, "published": id(candidate) in published,
            "refused_by": candidate.rejected_by,
            "refusal_reason": candidate.reject_detail,
            "quality_score": candidate.score,
            "research_features": candidate.modules,
            "probability": getattr(estimate, "probability", None),
            "conservative_probability": getattr(estimate, "lower", None),
            "conservative_net_r": getattr(estimate, "conservative_net_r", None),
            "model_version": model.version, "model_created_at": model.created_at,
            "code_revision": model.code_revision, "code_dirty": model.code_dirty,
            "universe_hash": model.universe_hash, "data_hash": model.data_hash,
            "data_tier": candidate.intraday_tier,
        }
        append(signal_id, "signal_created", payload["decision_at"], payload, path)
        existing_signals.add(signal_id)
        added += 1
    return added


def _states(events: list[SignalEvent]) -> dict[str, list[SignalEvent]]:
    states: dict[str, list[SignalEvent]] = {}
    for event in events:
        states.setdefault(event.signal_id, []).append(event)
    return states


def open_records(path: str | Path | None = None) -> list[SimpleNamespace]:
    records = []
    for signal_id, events in _states(load(path)).items():
        created = next((event for event in events if event.event_type == "signal_created"), None)
        if created is None or not created.payload.get("qualified"):
            continue
        if any(event.event_type in TERMINAL_EVENTS for event in events):
            continue
        payload = created.payload
        records.append(SimpleNamespace(
            signal_id=signal_id, symbol=payload["symbol"], sector=payload.get("sector", ""),
            risk_inr=float(payload.get("risk_inr", 0)), outcome="open",
            prediction_qualified=True,
            key=(payload["symbol"], payload["trigger_bar"]),
        ))
    return records


def settle(path: str | Path | None = None) -> int:
    """Append fill/cancellation/outcome events for qualified paper signals."""
    from .data import yahoo

    changed = 0
    for signal_id, events in _states(load(path)).items():
        created = next((event for event in events if event.event_type == "signal_created"), None)
        if created is None or not created.payload.get("qualified"):
            continue
        if any(event.event_type in TERMINAL_EVENTS for event in events):
            continue
        payload = created.payload
        bars = yahoo.fetch_chart(
            yahoo.to_yahoo_symbol(payload["symbol"]), range_="60d", interval="15m"
        )
        if bars is None or bars.empty:
            if not any(event.event_type == "data_unavailable" for event in events):
                append(signal_id, "data_unavailable", datetime.now(INDIA), {
                    "reason": "15-minute settlement bars unavailable", "source": "Yahoo"
                }, path)
                changed += 1
            continue
        wanted = pd.Timestamp(payload["trigger_bar"])
        positions = [i for i, stamp in enumerate(bars.index)
                     if pd.Timestamp(stamp).tz_localize(None) == wanted.tz_localize(None)]
        if not positions:
            continue
        signal_position = positions[0]
        fill_event = next((event for event in events if event.event_type == "order_filled"), None)
        if fill_event is None:
            plan = SimpleNamespace(
                direction=payload["direction"], entry=payload["entry"],
                entry_rule=payload["entry_rule"], entry_min=payload["entry_min"],
                entry_max=payload["entry_max"],
            )
            fill_position, fill, reason = simulate_entry(bars, signal_position, plan)
            if fill_position is None or fill is None:
                if len(bars) - signal_position > 4:
                    append(signal_id, "order_cancelled", bars.index[min(
                        signal_position + 4, len(bars) - 1
                    )], {"reason": reason}, path)
                    changed += 1
                continue
            append(signal_id, "order_filled", bars.index[fill_position], {
                "price": fill, "reason": reason, "source": "Yahoo",
                "slippage_pct": abs(fill / payload["entry"] - 1) * 100,
            }, path)
            changed += 1
        else:
            fill = float(fill_event.payload["price"])
            fill_positions = [i for i, stamp in enumerate(bars.index)
                              if pd.Timestamp(stamp).isoformat() == fill_event.occurred_at]
            fill_position = fill_positions[0] if fill_positions else signal_position + 1
        risk = abs(fill - float(payload["stop"]))
        trade = Trade(
            payload["symbol"], payload["direction"], payload["setup"],
            pd.Timestamp(bars.index[fill_position]), fill, float(payload["stop"]),
            fill + settings.reliability_target_r * risk, risk / fill * 100,
            signal_at=pd.Timestamp(payload["decision_at"]),
        )
        resolve_forward(
            bars, fill_position, trade, settings.max_holding_sessions * 25,
            include_entry_bar=True, require_full_horizon=True,
        )
        if trade.outcome == "incomplete":
            continue
        append(signal_id, trade.outcome, trade.resolved_at, {
            "realised_r": trade.realised_r, "net_r": trade.net_r,
            "bars_held": trade.bars_held, "mae_r": trade.mae_r, "mfe_r": trade.mfe_r,
        }, path)
        changed += 1
    return changed


@dataclass(frozen=True)
class RolloutStatus:
    phase: str
    progress: str
    completed: int
    wins: int
    probability: float
    lower: float
    reason: str


def rollout_status(path: str | Path | None = None) -> RolloutStatus:
    from .v3_probability import expected_net_r, wilson_interval

    completed = []
    for events in _states(load(path)).values():
        created = next((event for event in events if event.event_type == "signal_created"), None)
        terminal = next((event for event in events if event.event_type in {"target", "stop", "timeout"}), None)
        if created and created.payload.get("qualified") and terminal:
            completed.append((created, terminal))
    wins = sum(terminal.event_type == "target" for _, terminal in completed)
    probability = wins / len(completed) if completed else float("nan")
    lower, _ = wilson_interval(wins, len(completed))
    months = 0.0
    if completed:
        start = min(pd.Timestamp(created.occurred_at) for created, _ in completed)
        end = max(pd.Timestamp(created.occurred_at) for created, _ in completed)
        months = (end - start).days / 30.4375
    failures = []
    if len(completed) < settings.v3_forward_min_signals:
        failures.append(f"needs {settings.v3_forward_min_signals - len(completed)} more signals")
    if months < settings.reliability_min_months_forward:
        failures.append(f"needs {settings.reliability_min_months_forward - months:.1f} more months")
    if completed and probability < settings.v3_min_probability_2r:
        failures.append(f"measured wins {probability:.1%} below {settings.v3_min_probability_2r:.0%}")
    if completed and lower < settings.v3_min_conservative_2r:
        failures.append(f"Wilson lower bound {lower:.1%} below {settings.v3_min_conservative_2r:.0%}")
    costs = [float(created.payload.get("expected_cost_r", 0.0)) for created, _ in completed]
    mean_cost = float(pd.Series(costs).mean()) if costs else 0.0
    conservative_net = expected_net_r(lower, settings.reliability_target_r, mean_cost)
    if completed and conservative_net < settings.v3_min_conservative_net_r:
        failures.append(
            f"conservative expected profit {conservative_net:+.2f}R below "
            f"+{settings.v3_min_conservative_net_r:.2f}R"
        )
    equity = peak = max_drawdown = 0.0
    losing = max_losing = 0
    for _, terminal in sorted(completed, key=lambda pair: pair[1].occurred_at):
        value = float(terminal.payload.get("net_r", terminal.payload.get("realised_r", 0.0)))
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        losing = losing + 1 if value <= 0 else 0
        max_losing = max(max_losing, losing)
    if max_drawdown > settings.reliability_max_drawdown_r:
        failures.append(f"paper drawdown {max_drawdown:.2f}R exceeds {settings.reliability_max_drawdown_r:.2f}R")
    if max_losing > settings.reliability_max_losing_streak:
        failures.append(
            f"paper losing streak {max_losing} exceeds {settings.reliability_max_losing_streak}"
        )
    phase = "paper-approved" if not failures else "paper"
    return RolloutStatus(
        phase, f"{len(completed)} of {settings.v3_forward_min_signals} completed; {months:.1f} months",
        len(completed), wins, probability, lower,
        "; ".join(failures) if failures else "historical and forward paper gates passed",
    )


def append_correction(signal_id: str, reason: str, replacement: dict,
                      path: str | Path | None = None) -> SignalEvent:
    return append(signal_id, "correction", datetime.now(INDIA), {
        "reason": reason, "replacement": replacement,
    }, path)


def record_manual_fill(symbol: str, price: float, path: str | Path | None = None):
    candidates = []
    for signal_id, events in _states(load(path)).items():
        created = next((event for event in events if event.event_type == "signal_created"), None)
        if created is None or not created.payload.get("qualified"):
            continue
        if created.payload.get("symbol", "").upper() != symbol.upper():
            continue
        if any(event.event_type in TERMINAL_EVENTS or event.event_type == "order_filled"
               for event in events):
            continue
        candidates.append((created, signal_id))
    if not candidates:
        raise ValueError(f"no open immutable reliability signal for {symbol.upper()}")
    created, signal_id = max(candidates, key=lambda item: item[0].occurred_at)
    expected = float(created.payload["entry"])
    slippage = abs(price / expected - 1) * 100 if expected else 0.0
    append(signal_id, "order_filled", datetime.now(INDIA), {
        "price": price, "reason": "manual paper fill", "source": "manual",
        "slippage_pct": slippage,
    }, path)
    return SimpleNamespace(
        symbol=symbol.upper(), actual_fill=price, actual_slippage_pct=slippage,
        signal_id=signal_id,
    )
