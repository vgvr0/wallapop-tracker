"""Explicit application service for cached, persistent listing assessment."""

import logging
import time
from dataclasses import dataclass

from wallapop_tracker.ai.analyzer import ListingAnalyzer
from wallapop_tracker.ai.hashing import build_analysis_input_hash
from wallapop_tracker.ai.models import ListingAnalysisResult
from wallapop_tracker.ai.prompts import PROMPT_VERSION
from wallapop_tracker.ai.storage import ListingAIRepository
from wallapop_tracker.observability import Metrics
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import ListingAIAssessmentRecord

logger = logging.getLogger(__name__)


class AIDisabledError(RuntimeError):
    """AI analysis was requested while disabled or not configured."""


@dataclass(frozen=True)
class ListingAnalysisExecution:
    assessment: ListingAIAssessmentRecord
    cache_hit: bool


class ListingAIAnalysisService:
    def __init__(
        self, database: Database, analyzer: ListingAnalyzer | None, metrics: Metrics
    ) -> None:
        self.database = database
        self.analyzer = analyzer
        self.metrics = metrics

    async def assess(self, listing_id: int, *, force: bool = False) -> ListingAnalysisExecution:
        started = time.perf_counter()
        self.metrics.wallapop_ai_analysis_requests_total.inc()
        if self.analyzer is None:
            self.metrics.wallapop_ai_analysis_failures_total.labels("disabled").inc()
            raise AIDisabledError("AI analysis is disabled")
        provider = getattr(self.analyzer, "provider_name", "unknown")
        model = getattr(self.analyzer, "model", "unknown")
        with self.database.session() as session:
            repository = ListingAIRepository(session)
            context = repository.build_context(listing_id)
            input_hash = build_analysis_input_hash(context, provider, model, PROMPT_VERSION)
            if not force:
                cached = repository.get_cached_assessment(
                    listing_id, provider, model, PROMPT_VERSION, input_hash
                )
                if cached is not None:
                    self.metrics.wallapop_ai_analysis_cache_hits_total.inc()
                    logger.info(
                        "ai.analysis.cache_hit listing_id=%s input_hash_prefix=%s",
                        listing_id,
                        input_hash[:12],
                    )
                    return ListingAnalysisExecution(cached, True)
        try:
            result = await self.analyzer.analyze(context)
            self._record_usage(result)
        except Exception:
            self.metrics.wallapop_ai_analysis_failures_total.labels("provider").inc()
            raise
        with self.database.transaction() as session:
            record = ListingAIRepository(session).save_assessment(
                listing_id=listing_id,
                input_hash=input_hash,
                analysis=result.analysis,
                metadata=result.metadata,
            )
        elapsed = time.perf_counter() - started
        self.metrics.wallapop_ai_analysis_success_total.inc()
        self.metrics.wallapop_ai_analysis_duration_seconds.labels(provider, model).observe(elapsed)
        logger.info(
            "ai.analysis.completed listing_id=%s input_hash_prefix=%s", listing_id, input_hash[:12]
        )
        return ListingAnalysisExecution(record, False)

    def _record_usage(self, result: ListingAnalysisResult) -> None:
        if result.metadata.input_tokens is not None:
            self.metrics.wallapop_ai_input_tokens_total.inc(result.metadata.input_tokens)
        if result.metadata.output_tokens is not None:
            self.metrics.wallapop_ai_output_tokens_total.inc(result.metadata.output_tokens)
