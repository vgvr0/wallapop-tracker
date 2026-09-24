"""Factory for configured analyzer implementations."""

from wallapop_tracker.ai.analyzer import ListingAnalyzer
from wallapop_tracker.ai.config import AIConfigurationError, AISettings
from wallapop_tracker.ai.providers.openai_compatible import OpenAICompatibleListingAnalyzer


def build_listing_analyzer(settings: AISettings) -> ListingAnalyzer | None:
    if not settings.enabled:
        return None
    if settings.provider != "openai-compatible":
        raise AIConfigurationError(f"unsupported AI provider: {settings.provider}")
    if not settings.base_url or not settings.api_key or not settings.model:
        raise AIConfigurationError("AI provider configuration is incomplete")
    return OpenAICompatibleListingAnalyzer(
        base_url=settings.base_url,
        api_key=settings.api_key,
        model=settings.model,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
