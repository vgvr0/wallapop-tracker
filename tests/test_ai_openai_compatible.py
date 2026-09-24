import json
from decimal import Decimal

import httpx
import pytest

from wallapop_tracker.ai.models import ListingAnalysisContext
from wallapop_tracker.ai.providers.base import (
    ListingAnalysisProviderError,
    ListingAnalysisValidationError,
)
from wallapop_tracker.ai.providers.openai_compatible import OpenAICompatibleListingAnalyzer


def response_payload() -> dict[str, object]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "condition_assessment": "good",
                            "condition_confidence": 0.8,
                            "defects": [
                                {
                                    "type": "cosmetic_damage",
                                    "severity": "minor",
                                    "confidence": 0.9,
                                    "evidence": "small scratch",
                                }
                            ],
                            "risk_flags": [],
                            "positive_signals": ["works"],
                            "missing_information": [],
                            "semantic_score": 80,
                            "risk_score": 10,
                            "deal_quality": "good",
                            "deal_confidence": 0.8,
                            "summary": "Good listing with a minor cosmetic issue.",
                        }
                    )
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def context() -> ListingAnalysisContext:
    return ListingAnalysisContext(listing_id="1", title="Phone", price=Decimal("650"))


@pytest.mark.asyncio
async def test_successful_response_exposes_analysis_and_usage() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer super-secret-test-key"
        return httpx.Response(200, json=response_payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleListingAnalyzer(
            base_url="https://example.test",
            api_key="super-secret-test-key",
            model="test-model",
            http_client=client,
        ).analyze(context())
    assert result.analysis.defects[0].type == "cosmetic_damage"
    assert result.metadata.total_tokens == 30


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [429, 500])
async def test_transient_status_retries(status: int) -> None:
    calls = 0
    delays: list[float] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (
            httpx.Response(status, json={})
            if calls == 1
            else httpx.Response(200, json=response_payload())
        )

    async def no_sleep(delay: float) -> None:
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await OpenAICompatibleListingAnalyzer(
            base_url="https://example.test",
            api_key="secret",
            model="m",
            http_client=client,
            sleep=no_sleep,
        ).analyze(context())
    assert result.analysis.semantic_score == 80
    assert calls == 2
    assert delays == [0.25]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401])
async def test_non_retryable_status_does_not_retry(status: int) -> None:
    calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        analyzer = OpenAICompatibleListingAnalyzer(
            base_url="https://example.test", api_key="secret", model="m", http_client=client
        )
        with pytest.raises(ListingAnalysisProviderError):
            await analyzer.analyze(context())
    assert calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,exception",
    [
        (b"not-json", ListingAnalysisProviderError),
        (b"{}", ListingAnalysisProviderError),
    ],
)
async def test_invalid_or_empty_provider_response(body: bytes, exception: type[Exception]) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(exception):
            await OpenAICompatibleListingAnalyzer(
                base_url="https://example.test", api_key="secret", model="m", http_client=client
            ).analyze(context())


@pytest.mark.asyncio
async def test_invalid_schema_is_not_retried() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        payload = response_payload()
        content = json.loads(payload["choices"][0]["message"]["content"])
        content["semantic_score"] = 999
        payload["choices"][0]["message"]["content"] = json.dumps(content)
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ListingAnalysisValidationError):
            await OpenAICompatibleListingAnalyzer(
                base_url="https://example.test", api_key="secret", model="m", http_client=client
            ).analyze(context())


def test_secret_is_not_in_repr() -> None:
    analyzer = OpenAICompatibleListingAnalyzer(
        base_url="https://example.test", api_key="super-secret-test-key", model="m"
    )
    assert "super-secret-test-key" not in repr(analyzer)
