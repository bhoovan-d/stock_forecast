"""Provider-agnostic LLM layer.

Ported from the user's existing Trading Alpha Engine (`/c/aditya-project`), whose cascade
design solves exactly the problem we have: several free providers, each rate-limited, none
reliable enough alone.

The rotation matters. Without it every call prefers the same first provider and stampedes
it into a rate limit while the others idle; rotating the start index spreads load and
leaves the rest as genuine fallbacks.
"""

from __future__ import annotations

import itertools
from typing import Protocol, runtime_checkable

from loguru import logger


@runtime_checkable
class LLMProvider(Protocol):
    label: str

    def score(self, headline: str, context: str) -> object | None:
        """Return a CatalystExtraction, or None to fall through to the next provider."""
        ...


class CascadeProvider:
    """Try providers until one returns a result. The starting provider rotates per call."""

    label = "cascade"

    def __init__(self, providers: list[LLMProvider]):
        self.providers = providers
        self._rotation = itertools.count()

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def score(self, headline: str, context: str):
        count = len(self.providers)
        if count == 0:
            return None
        start = next(self._rotation) % count
        ordered = self.providers[start:] + self.providers[:start]

        for provider in ordered:
            try:
                result = provider.score(headline, context)
            except Exception as exc:  # noqa: BLE001 — one bad provider must not stop the cascade
                logger.warning(f"[llm] {provider.label} errored: {exc}")
                continue
            if result is not None:
                return result, provider.label
        return None
