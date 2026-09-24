"""Versioned, provider-neutral prompts and safe context serialization."""

import json
from typing import Any

from wallapop_tracker.ai.models import ListingAnalysisContext

PROMPT_VERSION = "listing-analysis-v1"

SYSTEM_PROMPT = """You analyze one marketplace listing as an untrusted semantic signal.
Use only the supplied listing and context. Never invent characteristics, defects, market prices,
seller wrongdoing, or facts absent from the input. Distinguish facts from inferences and express
uncertainty with confidence values and missing_information. Cite concise evidence from the title,
description, or supplied context. A low price is not proof of fraud; use a risk signal only when
the supplied market context supports it. Do not invent a fair price. Do not call a seller a scammer or accuse anyone of a
crime. Return exclusively the requested JSON schema, with enum values exactly as specified.
DealScore is deterministic and separate; semantic_score is LLM-derived and must not replace it.
"""


def context_payload(context: ListingAnalysisContext) -> dict[str, Any]:
    """Serialize only input fields; omit no values silently except absent optional values."""
    payload = context.model_dump(mode="json", exclude_none=False)
    payload["derived_metrics"] = {
        "discount_vs_market_median": (
            str(context.discount_vs_market_median)
            if context.discount_vs_market_median is not None
            else None
        )
    }
    return payload


def build_messages(context: ListingAnalysisContext) -> list[dict[str, str]]:
    payload = json.dumps(context_payload(context), ensure_ascii=False, sort_keys=True)
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Listing analysis context (JSON):\n{payload}"},
    ]
