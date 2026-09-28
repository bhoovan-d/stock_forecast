"""Date-versioned NSE stock-option universe derived from approved raw evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import RawObservation, require_aware
from .governance import ChangeControl
from .ledger import ImmutableLedger, sha256_json


@dataclass(frozen=True, order=True)
class UniverseMember:
    underlying_key: str
    symbol: str


@dataclass(frozen=True)
class UniverseSnapshot:
    as_of: datetime
    members: tuple[UniverseMember, ...]
    contract_count: int
    source_observation_id: str

    @property
    def universe_hash(self) -> str:
        return sha256_json(
            {
                "as_of": self.as_of.isoformat(),
                "members": [
                    {"underlying_key": member.underlying_key, "symbol": member.symbol}
                    for member in self.members
                ],
                "contract_count": self.contract_count,
                "source_observation_id": self.source_observation_id,
            }
        )


class UniverseBuilder:
    REQUIRED_POLICY = {
        "exchange_segment": "NSE_FO",
        "option_instrument_types": ["CE", "PE"],
        "required_underlying_type": "EQUITY",
        "excluded_underlying_types": ["INDEX", "ETF"],
        "membership_rule": "at_least_one_current_stock_option_contract",
    }

    def __init__(self, ledger: ImmutableLedger, changes: ChangeControl) -> None:
        self.ledger = ledger
        self.changes = changes

    def build_and_record(
        self,
        instrument_rows: list[dict],
        *,
        observation: RawObservation,
        policy_manifest_id: str,
        policy_manifest_hash: str,
    ) -> str:
        require_aware(observation.retrieved_at, "retrieved_at")
        definition = self.changes.active_definition(policy_manifest_id, policy_manifest_hash)
        if definition != self.REQUIRED_POLICY:
            raise ValueError("universe manifest does not match the implemented stock-only rule")
        eligible = [
            row for row in instrument_rows
            if row.get("segment") == "NSE_FO"
            and row.get("instrument_type") in {"CE", "PE"}
            and row.get("underlying_type") == "EQUITY"
        ]
        members = tuple(
            sorted(
                {
                    UniverseMember(
                        underlying_key=str(row["underlying_key"]),
                        symbol=str(row.get("underlying_symbol") or row.get("name")),
                    )
                    for row in eligible
                }
            )
        )
        if not members:
            raise ValueError("approved instrument snapshot contains no stock-option underlyings")
        snapshot = UniverseSnapshot(
            as_of=observation.retrieved_at,
            members=members,
            contract_count=len(eligible),
            source_observation_id=observation.observation_id,
        )
        event = self.ledger.append(
            "universe_snapshot_created",
            snapshot.universe_hash,
            {
                "as_of": snapshot.as_of.isoformat(),
                "universe_hash": snapshot.universe_hash,
                "member_count": len(snapshot.members),
                "contract_count": snapshot.contract_count,
                "members": [
                    {"underlying_key": member.underlying_key, "symbol": member.symbol}
                    for member in snapshot.members
                ],
                "source_observation_id": snapshot.source_observation_id,
                "policy_manifest": [policy_manifest_id, policy_manifest_hash],
            },
            occurred_at=snapshot.as_of,
            decision_at=snapshot.as_of,
            data_cutoff=snapshot.as_of,
            idempotency_key=f"universe-snapshot:{snapshot.universe_hash}",
        )
        return event.event_id
