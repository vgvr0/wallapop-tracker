"""Persistence and context construction for the AI integration layer."""

import json
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wallapop_tracker.ai.models import (
    ListingAIAnalysis,
    ListingAnalysisContext,
    ListingAnalysisMetadata,
)
from wallapop_tracker.storage.models import (
    ListingAIAssessmentRecord,
    ListingRecord,
    ListingSnapshotRecord,
    ProfileSnapshotRecord,
)


class ListingNotFoundError(LookupError):
    """The requested stored listing does not exist."""


class ListingAIRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_cached_assessment(
        self, listing_id: int, provider: str, model: str, prompt_version: str, input_hash: str
    ) -> ListingAIAssessmentRecord | None:
        return self.session.scalar(
            select(ListingAIAssessmentRecord).where(
                ListingAIAssessmentRecord.listing_id == listing_id,
                ListingAIAssessmentRecord.provider == provider,
                ListingAIAssessmentRecord.model == model,
                ListingAIAssessmentRecord.prompt_version == prompt_version,
                ListingAIAssessmentRecord.input_hash == input_hash,
            )
        )

    def get_latest_assessment(self, listing_id: int) -> ListingAIAssessmentRecord | None:
        return self.session.scalar(
            select(ListingAIAssessmentRecord)
            .where(ListingAIAssessmentRecord.listing_id == listing_id)
            .order_by(
                desc(ListingAIAssessmentRecord.created_at), desc(ListingAIAssessmentRecord.id)
            )
        )

    def list_assessments(self, listing_id: int, limit: int = 50) -> list[ListingAIAssessmentRecord]:
        return list(
            self.session.scalars(
                select(ListingAIAssessmentRecord)
                .where(ListingAIAssessmentRecord.listing_id == listing_id)
                .order_by(
                    desc(ListingAIAssessmentRecord.created_at), desc(ListingAIAssessmentRecord.id)
                )
                .limit(limit)
            )
        )

    def save_assessment(
        self,
        *,
        listing_id: int,
        input_hash: str,
        analysis: ListingAIAnalysis,
        metadata: ListingAnalysisMetadata,
    ) -> ListingAIAssessmentRecord:
        record = ListingAIAssessmentRecord(
            listing_id=listing_id,
            provider=metadata.provider,
            model=metadata.model,
            prompt_version=metadata.prompt_version,
            input_hash=input_hash,
            semantic_score=Decimal(str(analysis.semantic_score)),
            risk_score=Decimal(str(analysis.risk_score)),
            condition_assessment=analysis.condition_assessment.value,
            condition_confidence=Decimal(str(analysis.condition_confidence)),
            deal_quality=analysis.deal_quality.value,
            deal_confidence=Decimal(str(analysis.deal_confidence)),
            analysis_json=json.dumps(
                analysis.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            ),
            input_tokens=metadata.input_tokens,
            output_tokens=metadata.output_tokens,
            total_tokens=metadata.total_tokens,
            latency_ms=Decimal(str(metadata.latency_ms)),
            created_at=datetime.now(UTC),
        )
        try:
            with self.session.begin_nested():
                self.session.add(record)
                self.session.flush()
            return record
        except IntegrityError:
            existing = self.get_cached_assessment(
                listing_id, metadata.provider, metadata.model, metadata.prompt_version, input_hash
            )
            if existing is None:
                raise
            return existing

    def build_context(self, listing_id: int) -> ListingAnalysisContext:
        listing = self.session.get(ListingRecord, listing_id)
        if listing is None:
            raise ListingNotFoundError(f"listing {listing_id} not found")
        snapshot = self.session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing_id)
            .order_by(desc(ListingSnapshotRecord.observed_at), desc(ListingSnapshotRecord.id))
        )
        profile_snapshot = None
        if listing.profile_id is not None:
            profile_snapshot = self.session.scalar(
                select(ProfileSnapshotRecord)
                .where(ProfileSnapshotRecord.profile_id == listing.profile_id)
                .order_by(desc(ProfileSnapshotRecord.observed_at), desc(ProfileSnapshotRecord.id))
            )
        return ListingAnalysisContext(
            listing_id=str(listing.id),
            title=snapshot.title if snapshot and snapshot.title else "",
            description=snapshot.description if snapshot and snapshot.description else "",
            price=snapshot.price if snapshot else None,
            currency=snapshot.currency if snapshot else None,
            condition=(snapshot.condition_label or snapshot.condition_code) if snapshot else None,
            brand=snapshot.brand if snapshot else None,
            model=None,
            category=(snapshot.category_name or snapshot.category_id) if snapshot else None,
            seller_rating=_float(profile_snapshot.rating) if profile_snapshot else None,
            seller_reviews=profile_snapshot.review_count if profile_snapshot else None,
            seller_sales=profile_snapshot.sales_count if profile_snapshot else None,
        )


def assessment_payload(
    record: ListingAIAssessmentRecord, cache_hit: bool = False
) -> dict[str, object]:
    return {
        "id": record.id,
        "listing_id": record.listing_id,
        "provider": record.provider,
        "model": record.model,
        "prompt_version": record.prompt_version,
        "input_hash": record.input_hash,
        "semantic_score": float(record.semantic_score),
        "risk_score": float(record.risk_score),
        "condition_assessment": record.condition_assessment,
        "condition_confidence": float(record.condition_confidence),
        "deal_quality": record.deal_quality,
        "deal_confidence": float(record.deal_confidence),
        "analysis": json.loads(record.analysis_json),
        "input_tokens": record.input_tokens,
        "output_tokens": record.output_tokens,
        "total_tokens": record.total_tokens,
        "latency_ms": float(record.latency_ms) if record.latency_ms is not None else None,
        "created_at": record.created_at.isoformat(),
        "cache_hit": cache_hit,
    }


def _float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None
