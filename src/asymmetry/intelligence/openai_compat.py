"""One OpenAI-compatible adapter serving Cerebras, Groq and Gemini.

All three expose OpenAI-shaped chat endpoints, so a single adapter parameterised by base
URL, key and model covers the whole free cascade (the same approach the user's existing
Trading Alpha Engine takes).
"""

from __future__ import annotations

import json
import re

from loguru import logger
from openai import OpenAI

from ..models import CatalystExtraction, CatalystType
from .prompts import JSON_INSTRUCTION, SYSTEM_PROMPT, build_user_prompt

_CATALYST_LOOKUP = {t.value.lower(): t for t in CatalystType}
# How models actually phrase these in the wild.
_CATALYST_LOOKUP.update(
    {
        "earnings": CatalystType.EARNINGS_SURPRISE,
        "results": CatalystType.EARNINGS_SURPRISE,
        "order": CatalystType.ORDER_WIN,
        "contract": CatalystType.ORDER_WIN,
        "order_book": CatalystType.ORDER_WIN,
        "merger": CatalystType.MERGER_ACQUISITION,
        "acquisition": CatalystType.MERGER_ACQUISITION,
        "ma": CatalystType.MERGER_ACQUISITION,
        "government_policy": CatalystType.POLICY,
        "regulation": CatalystType.REGULATORY,
        "approval": CatalystType.REGULATORY,
        "capex": CatalystType.CAPACITY_EXPANSION,
        "expansion": CatalystType.CAPACITY_EXPANSION,
        "promoter": CatalystType.PROMOTER_INSTITUTIONAL,
        "institutional": CatalystType.PROMOTER_INSTITUTIONAL,
        "macro": CatalystType.SECTOR_MACRO,
        "sector": CatalystType.SECTOR_MACRO,
    }
)


def _clamp(value: object, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _extract_json(text: str) -> dict | None:
    """Models wrap JSON in prose or fences despite instructions; recover it."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        braced = re.search(r"\{.*\}", text, re.DOTALL)
        if braced:
            text = braced.group(0)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


class OpenAICompatProvider:
    def __init__(self, label: str, api_key: str, base_url: str, model: str):
        self.label = label
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=45.0, max_retries=1)

    def score(self, headline: str, context: str) -> CatalystExtraction | None:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{JSON_INSTRUCTION}"},
                {"role": "user", "content": build_user_prompt(headline, context)},
            ],
            temperature=0.1,
            max_tokens=300,
        )
        content = (response.choices[0].message.content or "").strip()
        payload = _extract_json(content)
        if payload is None:
            logger.debug(f"[llm] {self.label} returned unparseable output")
            return None

        raw_type = str(payload.get("catalyst_type", "none")).strip().lower().replace(" ", "_")
        catalyst_type = _CATALYST_LOOKUP.get(raw_type, CatalystType.NONE)

        return CatalystExtraction(
            catalyst_type=catalyst_type,
            expectation_delta=_clamp(payload.get("expectation_delta"), -3, 3),
            materiality=_clamp(payload.get("materiality"), 0, 3),
            durability=_clamp(payload.get("durability"), 0, 3),
            already_priced=bool(payload.get("already_priced", False)),
            confidence=_clamp(payload.get("confidence"), 0, 3),
            rationale=str(payload.get("rationale", ""))[:200],
        )


def build_cascade():
    """Assemble the provider chain from whichever keys are configured."""
    from ..config import settings
    from .provider import CascadeProvider

    specs = {
        "cerebras": (settings.cerebras_api_key, settings.cerebras_base_url, settings.cerebras_model),
        "groq": (settings.groq_api_key, settings.groq_base_url, settings.groq_model),
        "gemini": (settings.gemini_api_key, settings.gemini_base_url, settings.gemini_model),
    }

    providers = []
    for name in [n.strip() for n in settings.llm_provider_chain.split(",")]:
        spec = specs.get(name)
        if spec and spec[0]:
            providers.append(OpenAICompatProvider(name, *spec))

    if not providers:
        logger.warning(
            "[llm] no provider keys configured — catalyst scoring will be skipped. "
            "Set CEREBRAS_API_KEY / GROQ_API_KEY / GEMINI_API_KEY in .env."
        )
    return CascadeProvider(providers)
