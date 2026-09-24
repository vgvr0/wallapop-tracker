"""Stable analyzer boundary used by future asynchronous integrations."""

from typing import Protocol

from wallapop_tracker.ai.models import ListingAnalysisContext, ListingAnalysisResult


class ListingAnalyzer(Protocol):
    async def analyze(self, context: ListingAnalysisContext) -> ListingAnalysisResult:
        """Analyze one listing without side effects outside the provider call."""
        ...
