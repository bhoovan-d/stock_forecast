"""Paper-only order, fill, exit, and settlement lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from .contracts import Direction, MarketBar, QuoteSnapshot, available_only, require_aware
from .governance import ChangeControl
from .ledger import ImmutableLedger, LedgerEvent, sha256_json
from .policy import (
    MAX_CONCURRENT_PAPER_POSITIONS,
    MAX_HOLDING_SESSIONS,
    OPTION_PREMIUM_LOSS_GUARD_PCT,
)
from .source_registry import SourceRegistry


class ExitReason(StrEnum):
    STOP = "stop"
    TARGET = "target"
    EXPIRY = "expiry"
    PREMIUM_GUARD = "premium_guard"


@dataclass(frozen=True)
class ExecutionPolicy:
    fill_rule: str
    both_touch_fallback: str
    contract_roll_policy: str
    maximum_exit_quote_age_seconds: int

    @classmethod
    def from_definition(cls, value: dict) -> "ExecutionPolicy":
        required = {
            "fill_rule",
            "both_touch_fallback",
            "contract_roll_policy",
            "maximum_exit_quote_age_seconds",
        }
        missing = required - set(value)
        if missing:
            raise ValueError("active execution policy is incomplete: " + ", ".join(sorted(missing)))
        policy = cls(
            fill_rule=str(value["fill_rule"]),
            both_touch_fallback=str(value["both_touch_fallback"]),
            contract_roll_policy=str(value["contract_roll_policy"]),
            maximum_exit_quote_age_seconds=int(value["maximum_exit_quote_age_seconds"]),
        )
        if policy.fill_rule != "buy_at_observable_ask_sell_at_next_observable_bid":
            raise ValueError("execution policy violates the binding observable BBO rule")
        if policy.both_touch_fallback != "stop_first":
            raise ValueError("execution policy must record stop first without finer evidence")
        if policy.contract_roll_policy != "no_roll":
            raise ValueError("contract rolling is not supported in the first paper slice")
        if policy.maximum_exit_quote_age_seconds < 0:
            raise ValueError("maximum exit quote age cannot be negative")
        return policy


@dataclass(frozen=True)
class CostPolicy:
    brokerage_per_order_inr: float
    other_fixed_costs_per_lot_inr: float
    sell_turnover_cost_bps: float
    slippage_policy: str

    @classmethod
    def from_definition(cls, value: dict) -> "CostPolicy":
        required = {
            "brokerage_per_order_inr",
            "other_fixed_costs_per_lot_inr",
            "sell_turnover_cost_bps",
            "slippage_policy",
        }
        missing = required - set(value)
        if missing:
            raise ValueError("active cost policy is incomplete: " + ", ".join(sorted(missing)))
        policy = cls(
            brokerage_per_order_inr=float(value["brokerage_per_order_inr"]),
            other_fixed_costs_per_lot_inr=float(value["other_fixed_costs_per_lot_inr"]),
            sell_turnover_cost_bps=float(value["sell_turnover_cost_bps"]),
            slippage_policy=str(value["slippage_policy"]),
        )
        if min(
            policy.brokerage_per_order_inr,
            policy.other_fixed_costs_per_lot_inr,
            policy.sell_turnover_cost_bps,
        ) < 0:
            raise ValueError("paper costs cannot be negative")
        if policy.slippage_policy != "observable_bbo_no_additional_slippage":
            raise ValueError("unsupported slippage policy")
        return policy


@dataclass(frozen=True)
class ExitTrigger:
    position_id: str
    reason: ExitReason
    event_id: str


class PaperExecution:
    """Creates paper events only; no broker or account path exists."""

    def __init__(
        self, ledger: ImmutableLedger, changes: ChangeControl, sources: SourceRegistry
    ) -> None:
        self.ledger = ledger
        self.changes = changes
        self.sources = sources

    def create_order(
        self,
        alert_event_id: str,
        *,
        created_at: datetime,
        expires_at: datetime,
        execution_manifest_id: str,
        execution_manifest_hash: str,
    ) -> str:
        require_aware(created_at, "created_at")
        require_aware(expires_at, "expires_at")
        if expires_at <= created_at:
            raise ValueError("paper order expiry must follow creation")
        ExecutionPolicy.from_definition(
            self.changes.active_definition(execution_manifest_id, execution_manifest_hash)
        )
        alert = self._event_by_id(alert_event_id, "core_paper_alert")
        order_id = sha256_json(
            {"alert_event_id": alert_event_id, "created_at": created_at.isoformat()}
        )[:32]
        self.ledger.append(
            "order_created",
            order_id,
            {
                "alert_event_id": alert_event_id,
                "candidate_id": alert.aggregate_id,
                "option_contract": alert.payload["option_contract"],
                "expires_at": expires_at.isoformat(),
                "execution_manifest_id": execution_manifest_id,
                "execution_manifest_hash": execution_manifest_hash,
            },
            occurred_at=created_at,
            decision_at=created_at,
            data_cutoff=created_at,
            idempotency_key=f"order-created:{order_id}",
        )
        return order_id

    def mark_unfilled(self, order_id: str, *, reason_code: str, at: datetime) -> None:
        self._require_open_order(order_id)
        if not reason_code.strip():
            raise ValueError("unfilled reason code is required")
        self.ledger.append(
            "order_unfilled",
            order_id,
            {"reason_code": reason_code},
            occurred_at=at,
            idempotency_key=f"order-unfilled:{order_id}",
        )

    def fill(
        self,
        order_id: str,
        *,
        quote: QuoteSnapshot,
        lot_size: int,
        filled_at: datetime,
    ) -> str:
        require_aware(filled_at, "filled_at")
        if lot_size <= 0:
            raise ValueError("lot size must be positive")
        order = self._require_open_order(order_id)
        expiry = datetime.fromisoformat(order.payload["expires_at"])
        if filled_at > expiry:
            raise ValueError("paper order has expired")
        available_only([quote], filled_at)
        if quote.observed_at < datetime.fromisoformat(order.occurred_at):
            raise ValueError("entry quote predates the paper order")
        if quote.contract_id != order.payload["option_contract"]:
            raise ValueError("entry quote is for a different option contract")
        self._require_alert_evidence(order, quote.observation_id, filled_at)
        if len(self.open_position_ids()) >= MAX_CONCURRENT_PAPER_POSITIONS:
            raise ValueError("the concurrent paper-position limit has been reached")
        self.ledger.append(
            "paper_fill",
            order_id,
            {
                "order_id": order_id,
                "alert_event_id": order.payload["alert_event_id"],
                "candidate_id": order.payload["candidate_id"],
                "option_contract": quote.contract_id,
                "lot_size": lot_size,
                "entry_fill_price": quote.ask,
                "observable_bid": quote.bid,
                "observable_ask": quote.ask,
                "spread_per_unit": quote.ask - quote.bid,
                "quote_observation_id": quote.observation_id,
                "quote_observed_at": quote.observed_at.isoformat(),
                "quote_available_at": quote.available_at.isoformat(),
            },
            occurred_at=filled_at,
            decision_at=filled_at,
            data_cutoff=quote.available_at,
            idempotency_key=f"paper-fill:{order_id}",
        )
        return order_id

    def evaluate_exit(
        self,
        position_id: str,
        *,
        underlying_bar: MarketBar,
        option_quote: QuoteSnapshot,
        completed_sessions: int,
        decision_at: datetime,
        lower_timeframe_bars: Iterable[MarketBar] = (),
    ) -> ExitTrigger | None:
        require_aware(decision_at, "decision_at")
        if completed_sessions < 0:
            raise ValueError("completed_sessions cannot be negative")
        fill, alert, order = self._open_position(position_id)
        existing_trigger = self._exit_trigger(position_id)
        if existing_trigger is not None:
            return ExitTrigger(
                position_id,
                ExitReason(existing_trigger.payload["reason"]),
                existing_trigger.event_id,
            )
        available_only([underlying_bar, option_quote], decision_at)
        finer = sorted(
            available_only(list(lower_timeframe_bars), decision_at),
            key=lambda bar: bar.interval_start,
        )
        if option_quote.contract_id != fill.payload["option_contract"]:
            raise ValueError("exit observation is for a different option contract")
        self._require_alert_evidence(order, underlying_bar.observation_id, decision_at)
        self._require_alert_evidence(order, option_quote.observation_id, decision_at)
        for bar in finer:
            self._require_alert_evidence(order, bar.observation_id, decision_at)
        direction = Direction(alert.payload["direction"])
        entry = float(alert.payload["underlying_entry"])
        stop = float(alert.payload["underlying_stop"])
        risk = abs(entry - stop)
        target = entry + risk * int(alert.payload["selected_target_r"])
        if direction is Direction.BEARISH:
            target = entry - risk * int(alert.payload["selected_target_r"])

        stop_touched = underlying_bar.low <= stop if direction is Direction.BULLISH else underlying_bar.high >= stop
        target_touched = underlying_bar.high >= target if direction is Direction.BULLISH else underlying_bar.low <= target
        reason: ExitReason | None = None
        reference_price = underlying_bar.close
        resolution = "single_trigger"
        if stop_touched and target_touched:
            reason, reference_price, resolution = self._resolve_both(
                direction, stop, target, finer
            )
        elif stop_touched:
            reason = ExitReason.STOP
            reference_price = self._gap_price(direction, underlying_bar.open, stop, is_stop=True)
        elif target_touched:
            reason = ExitReason.TARGET
            reference_price = self._gap_price(direction, underlying_bar.open, target, is_stop=False)
        elif option_quote.bid <= float(fill.payload["entry_fill_price"]) * (
            1 - OPTION_PREMIUM_LOSS_GUARD_PCT / 100
        ):
            reason = ExitReason.PREMIUM_GUARD
            resolution = "option_bid_guard"
        elif completed_sessions >= MAX_HOLDING_SESSIONS:
            reason = ExitReason.EXPIRY
            resolution = "completed_session_limit"
        if reason is None:
            return None

        event_type = {
            ExitReason.STOP: "paper_stop",
            ExitReason.TARGET: "paper_target",
            ExitReason.EXPIRY: "paper_expiry",
            ExitReason.PREMIUM_GUARD: "paper_premium_guard",
        }[reason]
        event = self.ledger.append(
            event_type,
            position_id,
            {
                "order_id": order.aggregate_id,
                "alert_event_id": order.payload["alert_event_id"],
                "reason": reason.value,
                "underlying_entry": entry,
                "underlying_stop": stop,
                "underlying_target": target,
                "underlying_reference_price": reference_price,
                "completed_sessions": completed_sessions,
                "resolution": resolution,
                "underlying_observation_id": underlying_bar.observation_id,
                "option_observation_id": option_quote.observation_id,
            },
            occurred_at=decision_at,
            decision_at=decision_at,
            data_cutoff=max(underlying_bar.available_at, option_quote.available_at),
            idempotency_key=f"exit-trigger:{position_id}",
        )
        return ExitTrigger(position_id, reason, event.event_id)

    def settle(
        self,
        position_id: str,
        *,
        exit_quote: QuoteSnapshot,
        settled_at: datetime,
        execution_manifest_id: str,
        execution_manifest_hash: str,
        cost_manifest_id: str,
        cost_manifest_hash: str,
    ) -> str:
        require_aware(settled_at, "settled_at")
        fill, alert, _ = self._open_position(position_id)
        execution = ExecutionPolicy.from_definition(
            self.changes.active_definition(execution_manifest_id, execution_manifest_hash)
        )
        costs = CostPolicy.from_definition(
            self.changes.active_definition(cost_manifest_id, cost_manifest_hash)
        )
        trigger = self._exit_trigger(position_id)
        if trigger is None:
            raise ValueError("paper position has no exit trigger")
        available_only([exit_quote], settled_at)
        trigger_at = datetime.fromisoformat(trigger.occurred_at)
        if exit_quote.observed_at <= trigger_at:
            raise ValueError("settlement requires the next observable bid after the exit trigger")
        quote_age = (settled_at - exit_quote.available_at).total_seconds()
        if quote_age > execution.maximum_exit_quote_age_seconds:
            raise ValueError("exit quote is stale under the active execution policy")
        if exit_quote.contract_id != fill.payload["option_contract"]:
            raise ValueError("settlement quote is for a different option contract")
        order = next(
            event for event in self.ledger.events(position_id)
            if event.event_type == "order_created"
        )
        self._require_alert_evidence(order, exit_quote.observation_id, settled_at)
        lot_size = int(fill.payload["lot_size"])
        entry_price = float(fill.payload["entry_fill_price"])
        exit_price = exit_quote.bid
        gross_pnl = (exit_price - entry_price) * lot_size
        sell_cost = exit_price * lot_size * costs.sell_turnover_cost_bps / 10_000
        total_cost = (
            2 * costs.brokerage_per_order_inr
            + costs.other_fixed_costs_per_lot_inr
            + sell_cost
        )
        net_pnl = gross_pnl - total_cost
        direction = Direction(alert.payload["direction"])
        underlying_entry = float(alert.payload["underlying_entry"])
        underlying_stop = float(alert.payload["underlying_stop"])
        reference = float(trigger.payload["underlying_reference_price"])
        signed_move = reference - underlying_entry
        if direction is Direction.BEARISH:
            signed_move *= -1
        normalized_r = signed_move / abs(underlying_entry - underlying_stop)
        payload = {
            "order_id": position_id,
            "alert_event_id": fill.payload["alert_event_id"],
            "candidate_id": fill.payload["candidate_id"],
            "exit_trigger_event_id": trigger.event_id,
            "exit_reason": trigger.payload["reason"],
            "option_contract": exit_quote.contract_id,
            "lot_size": lot_size,
            "entry_ask": entry_price,
            "exit_bid": exit_price,
            "gross_option_pnl_per_lot": gross_pnl,
            "total_cost_per_lot": total_cost,
            "net_option_pnl_per_lot": net_pnl,
            "option_return_pct_before_costs": (exit_price - entry_price) / entry_price * 100,
            "normalized_underlying_r": normalized_r,
            "cost_breakdown": {
                "brokerage": 2 * costs.brokerage_per_order_inr,
                "other_fixed": costs.other_fixed_costs_per_lot_inr,
                "sell_turnover": sell_cost,
                "spread_paid_at_entry_per_unit": float(fill.payload["spread_per_unit"]),
                "additional_slippage": 0.0,
            },
            "execution_manifest": [execution_manifest_id, execution_manifest_hash],
            "cost_manifest": [cost_manifest_id, cost_manifest_hash],
            "exit_quote_observation_id": exit_quote.observation_id,
            "exit_quote_observed_at": exit_quote.observed_at.isoformat(),
            "exit_quote_available_at": exit_quote.available_at.isoformat(),
        }
        event = self.ledger.append(
            "settlement",
            position_id,
            payload,
            occurred_at=settled_at,
            decision_at=settled_at,
            data_cutoff=exit_quote.available_at,
            idempotency_key=f"settlement:{position_id}",
        )
        return event.event_id

    def open_position_ids(self) -> list[str]:
        events = self.ledger.events()
        filled = {event.aggregate_id for event in events if event.event_type == "paper_fill"}
        settled = {event.aggregate_id for event in events if event.event_type == "settlement"}
        return sorted(filled - settled)

    def _require_open_order(self, order_id: str) -> LedgerEvent:
        events = self.ledger.events(order_id)
        order = next((event for event in events if event.event_type == "order_created"), None)
        if order is None:
            raise ValueError("paper order does not exist")
        if any(event.event_type in {"order_unfilled", "paper_fill"} for event in events):
            raise ValueError("paper order already has a terminal order state")
        return order

    def _open_position(self, position_id: str) -> tuple[LedgerEvent, LedgerEvent, LedgerEvent]:
        events = self.ledger.events(position_id)
        order = next((event for event in events if event.event_type == "order_created"), None)
        fill = next((event for event in events if event.event_type == "paper_fill"), None)
        if order is None or fill is None:
            raise ValueError("open paper position does not exist")
        if any(event.event_type == "settlement" for event in events):
            raise ValueError("paper position is already settled")
        alert = self._event_by_id(order.payload["alert_event_id"], "core_paper_alert")
        return fill, alert, order

    def _exit_trigger(self, position_id: str) -> LedgerEvent | None:
        return next(
            (
                event for event in self.ledger.events(position_id)
                if event.event_type in {
                    "paper_stop", "paper_target", "paper_expiry", "paper_premium_guard"
                }
            ),
            None,
        )

    def _event_by_id(self, event_id: str, event_type: str) -> LedgerEvent:
        event = next((event for event in self.ledger.events() if event.event_id == event_id), None)
        if event is None or event.event_type != event_type:
            raise ValueError(f"{event_type} event does not exist")
        return event

    def _require_alert_evidence(
        self, order: LedgerEvent, observation_id: str, decision_at: datetime
    ) -> None:
        alert = self._event_by_id(order.payload["alert_event_id"], "core_paper_alert")
        hashes = set(alert.payload["source_hashes"])
        matching = tuple(
            (event.payload["source_id"], event.payload["source_hash"])
            for event in self.ledger.events(observation_id)
            if event.event_type == "source_observed"
            and event.payload.get("source_hash") in hashes
        )
        if not matching:
            raise ValueError("execution input lacks raw evidence from an alert source hash")
        self.sources.require_observation(
            observation_id, decision_at=decision_at, allowed_versions=matching
        )

    @staticmethod
    def _gap_price(direction: Direction, open_price: float, level: float, *, is_stop: bool) -> float:
        if direction is Direction.BULLISH:
            crossed_at_open = open_price <= level if is_stop else open_price >= level
        else:
            crossed_at_open = open_price >= level if is_stop else open_price <= level
        return open_price if crossed_at_open else level

    @staticmethod
    def _resolve_both(
        direction: Direction,
        stop: float,
        target: float,
        finer: list[MarketBar],
    ) -> tuple[ExitReason, float, str]:
        for bar in finer:
            stop_hit = bar.low <= stop if direction is Direction.BULLISH else bar.high >= stop
            target_hit = bar.high >= target if direction is Direction.BULLISH else bar.low <= target
            if stop_hit and target_hit:
                return ExitReason.STOP, stop, "lower_timeframe_ambiguous_stop_first"
            if stop_hit:
                return ExitReason.STOP, PaperExecution._gap_price(
                    direction, bar.open, stop, is_stop=True
                ), "lower_timeframe_stop_first"
            if target_hit:
                return ExitReason.TARGET, PaperExecution._gap_price(
                    direction, bar.open, target, is_stop=False
                ), "lower_timeframe_target_first"
        return ExitReason.STOP, stop, "no_decisive_lower_timeframe_stop_first"
