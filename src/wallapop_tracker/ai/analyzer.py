"""Stable analyzer boundary used by future asynchronous integrations."""

from typing import Protocol

from wallapop_tracker.ai.models import ListingAnalysisContext, ListingAnalysisResult


class ListingAnalyzer(Protocol):
    model: str

    async def analyze(self, context: ListingAnalysisContext) -> ListingAnalysisResult:
        """Analyze one listing without side effects outside the provider call."""
        ...

    async def complete(self, request: dict[str, object]) -> dict[str, object]:
        """Complete a provider request for another strictly validated parser."""
        ...
