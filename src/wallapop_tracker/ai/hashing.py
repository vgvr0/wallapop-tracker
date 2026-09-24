"""Deterministic cache key construction for future persistence/deduplication."""

import hashlib
import json
from typing import Any

from wallapop_tracker.ai.models import ListingAnalysisContext
from wallapop_tracker.ai.prompts import context_payload


def build_analysis_input_hash(
    context: ListingAnalysisContext | dict[str, Any],
    provider: str,
    model: str,
    prompt_version: str,
) -> str:
    normalized_context = (
        context
        if isinstance(context, ListingAnalysisContext)
        else ListingAnalysisContext.model_validate(context)
    )
    payload = context_payload(normalized_context)
    canonical = json.dumps(
        {
            "context": payload,
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
