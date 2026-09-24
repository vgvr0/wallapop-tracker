"""Small async HTTP adapter for OpenAI-compatible chat-completions endpoints."""

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from wallapop_tracker.ai.models import (
    ListingAIAnalysis,
    ListingAnalysisContext,
    ListingAnalysisMetadata,
    ListingAnalysisResult,
)
from wallapop_tracker.ai.prompts import PROMPT_VERSION, build_messages
from wallapop_tracker.ai.providers.base import (
    ListingAnalysisProviderError,
    ListingAnalysisValidationError,
)

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


class OpenAICompatibleListingAnalyzer:
    """Analyze listings through an OpenAI-style `/chat/completions` endpoint."""

    provider_name = "openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 30.0,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self._http_client = http_client
        self._sleep = sleep

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(base_url={self.base_url!r}, model={self.model!r}, "
            f"timeout={self.timeout!r}, max_retries={self.max_retries!r})"
        )

    async def analyze(self, context: ListingAnalysisContext) -> ListingAnalysisResult:
        started = time.perf_counter()
        request = {
            "model": self.model,
            "messages": build_messages(context),
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        response = await self.complete(request)
        analysis, usage = self._parse_response(response)
        latency_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "listing analysis completed listing_id=%s provider=%s model=%s prompt_version=%s "
            "latency_ms=%.2f input_tokens=%s output_tokens=%s total_tokens=%s",
            context.listing_id,
            self.provider_name,
            self.model,
            PROMPT_VERSION,
            latency_ms,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
        )
        return ListingAnalysisResult(
            analysis=analysis,
            metadata=ListingAnalysisMetadata(
                provider=self.provider_name,
                model=self.model,
                prompt_version=PROMPT_VERSION,
                input_tokens=_token(usage, "prompt_tokens"),
                output_tokens=_token(usage, "completion_tokens"),
                total_tokens=_token(usage, "total_tokens"),
                latency_ms=latency_ms,
            ),
        )

    async def complete(self, request: dict[str, Any]) -> dict[str, Any]:
        """Complete one structured chat request using the configured provider.

        This is intentionally a provider-only boundary. Callers still own the
        system prompt and schema validation, so the provider cannot execute
        application actions.
        """
        owns_client = self._http_client is None
        client = self._http_client or httpx.AsyncClient(timeout=self.timeout)
        try:
            for attempt in range(self.max_retries + 1):
                try:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        json=request,
                    )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
                    if attempt < self.max_retries:
                        await self._backoff(attempt)
                        continue
                    raise ListingAnalysisProviderError("LLM transport request failed") from exc
                if response.status_code == 200:
                    try:
                        data = response.json()
                    except (ValueError, json.JSONDecodeError) as exc:
                        raise ListingAnalysisProviderError("LLM returned invalid JSON") from exc
                    if not isinstance(data, dict):
                        raise ListingAnalysisProviderError(
                            "LLM returned an invalid response object"
                        )
                    return data
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < self.max_retries:
                        await self._backoff(attempt)
                        continue
                    raise ListingAnalysisProviderError(
                        f"LLM request failed with transient HTTP status {response.status_code}"
                    )
                raise ListingAnalysisProviderError(
                    f"LLM request failed with non-retryable HTTP status {response.status_code}"
                )
        finally:
            if owns_client:
                await client.aclose()
        raise ListingAnalysisProviderError("LLM request failed")

    _request = complete

    async def _backoff(self, attempt: int) -> None:
        await self._sleep(min(2.0, 0.25 * (2**attempt)))

    @staticmethod
    def _parse_response(response: dict[str, Any]) -> tuple[ListingAIAnalysis, dict[str, Any]]:
        try:
            choices = response.get("choices")
            content = (
                choices[0]["message"]["content"] if isinstance(choices, list) and choices else None
            )
            if isinstance(content, str):
                content = content.strip()
                if content.startswith("```"):
                    content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                content = json.loads(content)
            if not isinstance(content, dict):
                raise ValueError("missing JSON content")
            return ListingAIAnalysis.model_validate(content), response.get("usage") or {}
        except ValidationError as exc:
            raise ListingAnalysisValidationError(
                "LLM response does not match the analysis schema"
            ) from exc
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ListingAnalysisProviderError(
                "LLM returned an empty or invalid JSON response"
            ) from exc


def _token(usage: dict[str, Any], key: str) -> int | None:
    value = usage.get(key)
    return value if isinstance(value, int) and value >= 0 else None
