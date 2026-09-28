"""Point-in-time, reproducible trading-universe snapshots.

Snapshots are built only from stored bhavcopy rows available on the requested date.  They
deliberately contain no index membership: every symbol that actually traded can qualify,
which avoids selecting historical tests with today's surviving constituents.
"""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from .config import settings
from .data.universe import liquidity_table


class FrozenUniverse(list[str]):
    """A list which carries the provenance needed when it is saved."""

    def __init__(self, symbols=(), *, as_of: date | None = None, lookback: int = 20):
        super().__init__(symbols)
        self.as_of = as_of
        self.lookback = lookback


def freeze(as_of: date, *, lookback: int = 20) -> list[str]:
    """Return symbols passing the existing liquidity gate at ``as_of``."""
    from .storage import load_history

    # Calendar days, with ample room for weekends and exchange holidays.
    history = load_history(days=max(60, lookback * 3), end=as_of)
    if history.empty:
        return FrozenUniverse(as_of=as_of, lookback=lookback)
    history = history.copy()
    history["date"] = history["date"].map(
        lambda value: value.date() if hasattr(value, "date") else value
    )
    history = history[history["date"] <= as_of]
    if history.empty:
        return FrozenUniverse(as_of=as_of, lookback=lookback)
    table = liquidity_table(history, lookback=lookback)
    symbols = sorted(table.loc[table["liquid"], "symbol"].astype(str).tolist())
    return FrozenUniverse(symbols, as_of=as_of, lookback=lookback)


def save(symbols: list[str], path: str | Path) -> None:
    """Save symbols and the exact liquidity settings which produced them."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "as_of": str(getattr(symbols, "as_of", None) or date.today()),
        "lookback": int(getattr(symbols, "lookback", settings.liquidity_lookback_days)),
        "settings": {
            "min_median_turnover_inr": settings.min_median_turnover_inr,
            "min_median_volume": settings.min_median_volume,
        },
        "symbols": list(dict.fromkeys(str(s).upper() for s in symbols)),
    }
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def load(path: str | Path) -> FrozenUniverse:
    """Load the symbol list from a snapshot, validating its basic shape."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise ValueError("universe snapshot must be an object containing a symbols list")
    try:
        as_of = date.fromisoformat(payload["as_of"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("universe snapshot must contain a valid as_of date") from exc
    return FrozenUniverse(
        [str(symbol).upper() for symbol in payload["symbols"]],
        as_of=as_of,
        lookback=int(payload.get("lookback", settings.liquidity_lookback_days)),
    )


def sample(symbols: list[str], n: int, seed: int) -> FrozenUniverse:
    """Take a reproducible uniform sample without quality ranking."""
    population = list(symbols)
    chosen = population if n <= 0 or n >= len(population) else random.Random(seed).sample(population, n)
    return FrozenUniverse(
        chosen, as_of=getattr(symbols, "as_of", None),
        lookback=getattr(symbols, "lookback", settings.liquidity_lookback_days),
    )
