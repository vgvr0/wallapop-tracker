"""FastAPI transport layer for the existing tracker services."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from wallapop_tracker.ai.config import AIConfigurationError, AISettings
from wallapop_tracker.ai.factory import build_listing_analyzer
from wallapop_tracker.ai.service import AIDisabledError, ListingAIAnalysisService
from wallapop_tracker.ai.storage import (
    ListingAIRepository,
    ListingNotFoundError,
    assessment_payload,
)
from wallapop_tracker.domain.deal_scoring import DealScore
from wallapop_tracker.domain.filters import FilterTrace
from wallapop_tracker.domain.marketplace import Marketplace, require_supported_marketplace
from wallapop_tracker.health import health_summary
from wallapop_tracker.observability import Metrics, configure_logging, get_metrics
from wallapop_tracker.parsers.search_url import SearchURLParseError, parse_search_url
from wallapop_tracker.reporting.market import (
    get_activity_time_series,
    get_brand_market_stats,
    get_listing_market_estimate,
    get_market_summary,
    get_price_time_series,
    get_seller_market_stats,
)
from wallapop_tracker.reporting.queries import get_price_history, get_profile_metrics_history
from wallapop_tracker.services.deal_scoring import DealScoringService
from wallapop_tracker.services.event_bus import deserialize_event
from wallapop_tracker.services.filter_explanation import explain_search_listing
from wallapop_tracker.services.market_value import MarketValueService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    AlertRuleRecord,
    DeadLetterRecord,
    DomainEventRecord,
    EventConsumptionRecord,
    ListingAIAssessmentRecord,
    ListingRecord,
    ListingSnapshotRecord,
    NotificationDeliveryRecord,
    PossibleRelistingRecord,
    ProfileRecord,
    ProfileSnapshotRecord,
    SearchListingMatchRecord,
    TrackedListingRecord,
    TrackedSearchRecord,
    TrackingEventRecord,
    TrackingRunRecord,
)
from wallapop_tracker.storage.repositories import (
    NotificationDeliveryRepository,
    PossibleRelistingRepository,
    TrackedListingRepository,
    TrackedSearchRepository,
)

logger = logging.getLogger(__name__)


class APIModel(BaseModel):
    @field_serializer("*", when_used="json")
    def serialize_decimal(self, value: Any) -> Any:
        return f"{value:.2f}" if isinstance(value, Decimal) else value


class AlertRuleCreate(APIModel):
    event_type: str = Field(min_length=1, max_length=64)
    channel: str = Field(min_length=1, max_length=32)
    destination: str = Field(min_length=1, max_length=2048)
    filters: dict[str, Any] = {}
    cooldown_seconds: int = Field(default=0, ge=0)
    enabled: bool = True


class AlertRulePatch(APIModel):
    event_type: str | None = Field(default=None, min_length=1, max_length=64)
    channel: str | None = Field(default=None, min_length=1, max_length=32)
    destination: str | None = Field(default=None, min_length=1, max_length=2048)
    filters: dict[str, Any] | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class SearchCreate(APIModel):
    marketplace: str = Marketplace.WALLAPOP.value
    name: str | None = None
    query: str = Field(min_length=1, max_length=255)
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    filters: dict[str, Any] | None = None
    enabled: bool = True
    notify_on_first_run: bool = False
    interval_seconds: int = Field(default=600, gt=0)
    target_price: Decimal | None = Field(default=None, ge=0)
    percentage_drop_threshold: Decimal | None = Field(default=None, gt=0, le=100)
    deal_score_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    notify_on_30d_low: bool = False
    notify_on_90d_low: bool = False
    notify_on_all_time_low: bool = False


class SearchPatch(APIModel):
    marketplace: str | None = None
    name: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=255)
    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    filters: dict[str, Any] | None = None
    enabled: bool | None = None
    notify_on_first_run: bool | None = None
    interval_seconds: int | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, ge=0)
    percentage_drop_threshold: Decimal | None = Field(default=None, gt=0, le=100)
    deal_score_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    notify_on_30d_low: bool | None = None
    notify_on_90d_low: bool | None = None
    notify_on_all_time_low: bool | None = None


class SearchImport(APIModel):
    url: str
    name: str | None = None
    interval_seconds: int = Field(default=3600, gt=0)


class TrackedListingCreate(APIModel):
    listing_id: int = Field(gt=0)
    alias: str = Field(min_length=1, max_length=100)
    interval_seconds: int = Field(default=600, gt=0)
    notes: str | None = None
    target_price: Decimal | None = Field(default=None, ge=0)
    percentage_drop_threshold: Decimal | None = Field(default=None, gt=0, le=100)
    deal_score_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    notify_on_30d_low: bool = False
    notify_on_90d_low: bool = False
    notify_on_all_time_low: bool = False


class TrackedListingPatch(APIModel):
    enabled: bool | None = None
    interval_seconds: int | None = Field(default=None, gt=0)
    notes: str | None = None
    target_price: Decimal | None = Field(default=None, ge=0)
    percentage_drop_threshold: Decimal | None = Field(default=None, gt=0, le=100)
    deal_score_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    notify_on_30d_low: bool | None = None
    notify_on_90d_low: bool | None = None
    notify_on_all_time_low: bool | None = None


class SearchResponse(APIModel):
    id: int
    marketplace: str
    name: str | None
    query: str
    min_price: Decimal | None
    max_price: Decimal | None
    filters: dict[str, Any] | None
    enabled: bool
    notify_on_first_run: bool
    interval_seconds: int
    last_run_at: datetime | None
    last_run_status: str | None
    last_run_id: int | None
    target_price: Decimal | None = None
    percentage_drop_threshold: Decimal | None = None
    deal_score_threshold: Decimal | None = None
    notify_on_30d_low: bool = False
    notify_on_90d_low: bool = False
    notify_on_all_time_low: bool = False


class ProfileResponse(APIModel):
    id: int
    wallapop_user_id: str
    name: str | None
    url: str | None
    registered_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    reports_received: int | None = None


class ListingResponse(APIModel):
    id: int
    marketplace: str
    external_id: str
    profile_id: int | None
    seller_user_id: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    title: str | None = None
    price: Decimal | None = None
    url: str | None = None
    presence_state: str | None = None


class EventResponse(APIModel):
    id: int
    event_type: str
    listing_id: int
    tracking_run_id: int
    tracked_search_id: int | None
    old_price: Decimal | None
    new_price: Decimal | None
    created_at: datetime
    metadata: dict[str, Any] | None = None


class DomainEventResponse(APIModel):
    id: int
    event_type: str
    aggregate_type: str
    aggregate_id: str
    marketplace: str | None
    payload: dict[str, Any]
    metadata: dict[str, Any]
    occurred_at: datetime
    created_at: datetime
    correlation_id: str | None
    causation_id: str | None


class RunResponse(APIModel):
    id: int
    source: str
    source_id: int
    status: str
    started_at: datetime
    finished_at: datetime | None
    items_fetched: int | None
    pages_fetched: int | None
    matched_listings: int | None
    new_listings: int | None
    price_changes: int | None
    error_type: str | None
    error_message: str | None


class ReasonResponse(APIModel):
    name: str
    contribution: float
    value: Any
    description: str


class ScoreResponse(APIModel):
    listing_id: int
    search_id: int
    score: int | None
    confidence: float
    status: str
    reasons: list[ReasonResponse]
    calculated_at: datetime


class FilterTraceResponse(BaseModel):
    """Kept outside :class:`APIModel` on purpose.

    ``APIModel`` registers a wildcard field serializer for the money values, and
    that would erase the JSON schema of the tri-state ``passed`` field. Only the
    value fields are serialized here, so ``passed`` keeps its
    ``boolean | null`` schema.
    """

    filter_name: str
    passed: bool | None = None
    actual_value: Any = None
    expected_value: Any = None
    matched_values: list[str] = []
    reason: str | None = None

    @field_serializer("actual_value", "expected_value", when_used="json")
    def serialize_decimal(self, value: Any) -> Any:
        return f"{value:.2f}" if isinstance(value, Decimal) else value


class FilterExplanationResponse(BaseModel):
    matched: bool | None
    complete: bool
    traces: list[FilterTraceResponse]
    warnings: list[str] = []


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return (_utc(value) or value).isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, dict):
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _filters(record: TrackedSearchRecord) -> dict[str, Any] | None:
    return json.loads(record.filters_json) if record.filters_json else None


def _search(record: TrackedSearchRecord) -> SearchResponse:
    return SearchResponse(
        id=record.id,
        marketplace=record.marketplace,
        name=record.name,
        query=record.query,
        min_price=record.min_price,
        max_price=record.max_price,
        filters=_filters(record),
        enabled=record.enabled,
        notify_on_first_run=record.notify_on_first_run,
        interval_seconds=record.interval_seconds,
        last_run_at=_utc(record.last_run_at),
        last_run_status=record.last_run_status,
        last_run_id=record.last_run_id,
        target_price=record.target_price,
        percentage_drop_threshold=record.percentage_drop_threshold,
        deal_score_threshold=record.deal_score_threshold,
        notify_on_30d_low=record.notify_on_30d_low,
        notify_on_90d_low=record.notify_on_90d_low,
        notify_on_all_time_low=record.notify_on_all_time_low,
    )


def _profile(record: ProfileRecord, reports_received: int | None = None) -> ProfileResponse:
    return ProfileResponse(
        id=record.id,
        wallapop_user_id=record.wallapop_user_id,
        name=record.name,
        url=record.url,
        registered_at=_utc(record.registered_at),
        first_seen_at=_utc(record.first_seen_at),
        last_seen_at=_utc(record.last_seen_at),
        reports_received=reports_received,
    )


def _latest_profile_snapshot(session: Session, profile_id: int) -> ProfileSnapshotRecord | None:
    return session.scalar(
        select(ProfileSnapshotRecord)
        .where(ProfileSnapshotRecord.profile_id == profile_id)
        .order_by(ProfileSnapshotRecord.observed_at.desc(), ProfileSnapshotRecord.id.desc())
        .limit(1)
    )


def _latest_snapshot(session: Session, listing_id: int) -> ListingSnapshotRecord | None:
    return session.scalar(
        select(ListingSnapshotRecord)
        .where(ListingSnapshotRecord.listing_id == listing_id)
        .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
        .limit(1)
    )


def _listing(session: Session, record: ListingRecord) -> ListingResponse:
    snapshot = _latest_snapshot(session, record.id)
    return ListingResponse(
        id=record.id,
        marketplace=record.marketplace,
        external_id=record.external_id or record.wallapop_item_id or "",
        profile_id=record.profile_id,
        seller_user_id=record.seller_user_id,
        first_seen_at=_utc(record.first_seen_at),
        last_seen_at=_utc(record.last_seen_at),
        title=snapshot.title if snapshot else None,
        price=snapshot.price if snapshot else None,
        url=snapshot.url if snapshot else None,
        presence_state=snapshot.presence_state.value if snapshot else None,
    )


def _filter_trace(trace: FilterTrace) -> FilterTraceResponse:
    return FilterTraceResponse(
        filter_name=trace.filter_name,
        passed=trace.passed,
        actual_value=trace.actual_value,
        expected_value=trace.expected_value,
        matched_values=list(trace.matched_values),
        reason=trace.reason,
    )


def _event(record: TrackingEventRecord) -> EventResponse:
    return EventResponse(
        id=record.id,
        event_type=record.event_type,
        listing_id=record.listing_id,
        tracking_run_id=record.tracking_run_id,
        tracked_search_id=record.tracked_search_id,
        old_price=record.old_price,
        new_price=record.new_price,
        created_at=_utc(record.created_at),
        metadata=json.loads(record.metadata_json) if record.metadata_json else None,
    )


def _dead_letter(record: DeadLetterRecord, session: Session) -> dict[str, Any]:
    event = session.get(DomainEventRecord, record.event_id)
    return {
        "id": record.id,
        "event_id": record.event_id,
        "consumer": record.consumer_name,
        "attempts": record.attempts,
        "last_error": record.last_error,
        "correlation_id": record.correlation_id,
        "failed_at": _utc(record.failed_at),
        "requeued_at": _utc(record.requeued_at),
        "event": deserialize_event(event) if event is not None else None,
    }


def _run(record: TrackingRunRecord) -> RunResponse:
    source, source_id = (
        ("profile", record.profile_id)
        if record.profile_id is not None
        else ("search", record.tracked_search_id)
        if record.tracked_search_id is not None
        else ("listing", record.tracked_listing_id)
    )
    return RunResponse(
        id=record.id,
        source=source,
        source_id=source_id,
        status=record.status.value,
        started_at=_utc(record.started_at),
        finished_at=_utc(record.finished_at),
        items_fetched=record.items_fetched,
        pages_fetched=record.pages_fetched,
        matched_listings=record.matched_listings,
        new_listings=record.new_listings,
        price_changes=record.price_changes,
        error_type=record.error_type,
        error_message=record.error_message,
    )


def _score(result: DealScore) -> ScoreResponse:
    return ScoreResponse(
        listing_id=result.listing_id,
        search_id=result.search_id,
        score=result.score,
        confidence=result.confidence,
        status=result.status.value,
        reasons=[ReasonResponse(**reason.__dict__) for reason in result.reasons],
        calculated_at=_utc(result.calculated_at),
    )


def _not_found(entity: str, entity_id: int) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{entity} {entity_id} not found")


def _ai_assessment(record: ListingAIAssessmentRecord, cache_hit: bool) -> dict[str, Any]:
    payload = assessment_payload(record, cache_hit)
    payload["created_at"] = _utc(record.created_at)
    return payload


def _page(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)) -> tuple[int, int]:
    return limit, offset


def _schema_is_ready(database: Database) -> bool:
    try:
        project_root = Path(__file__).resolve().parents[3]
        config_path = project_root / "alembic.ini"
        if not config_path.exists():
            # In the container the package is installed under site-packages,
            # while the migration files are copied to the application root.
            project_root = Path.cwd()
            config_path = project_root / "alembic.ini"
        config = Config(str(config_path if config_path.exists() else Path("alembic.ini")))
        config.set_main_option("script_location", str(project_root / "alembic"))
        script = ScriptDirectory.from_config(config)
        expected = script.get_current_head()
        with database.engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
        # 0013 remains accepted for compatibility with databases created by
        # older deployments; Alembic still upgrades them to the new head.
        return current in {expected, "0013_marketplace_identity"}
    except Exception:
        logger.exception("Readiness schema check failed")
        return False


def create_app(
    database: Database | None = None,
    *,
    metrics: Metrics | None = None,
    ai_service: ListingAIAnalysisService | None = None,
) -> FastAPI:
    configure_logging()
    owned_database = database is None
    database = database or Database(
        os.getenv("WALLAPOP_TRACKER_DB_URL", "sqlite:///data/wallapop_tracker.db")
    )
    app_metrics = metrics or get_metrics()
    metrics_enabled = os.getenv("WALLAPOP_METRICS_ENABLED", "true").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    try:
        # Local SQLite remains convenient for development/tests. Production
        # databases are provisioned exclusively through ``alembic upgrade head``.
        if database.engine.dialect.name == "sqlite":
            database.create_all()
    except Exception:
        # /health must remain available while /ready reports the outage.
        pass
    api = FastAPI(title="Wallapop Tracker API", version="1.0", description="Local/private API")
    api.state.metrics = app_metrics
    if ai_service is None:
        try:
            ai_service = ListingAIAnalysisService(
                database, build_listing_analyzer(AISettings.from_env()), app_metrics
            )
        except AIConfigurationError:
            ai_service = ListingAIAnalysisService(database, None, app_metrics)
    api.state.ai_service = ai_service

    def get_session() -> Iterator[Session]:
        with database.session() as session:
            yield session

    def close_owned() -> None:
        if owned_database:
            database.close()

    @api.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/api/v1/listings/{listing_id}/market-value", tags=["listings"])
    def listing_market_value(
        listing_id: int,
        window_days: int = Query(30, ge=1, le=3650),
        session: Session = Depends(get_session),
    ) -> dict[str, object]:
        if session.get(ListingRecord, listing_id) is None:
            raise _not_found("Listing", listing_id)
        try:
            return (
                MarketValueService(session)
                .estimate(listing_id, window=timedelta(days=window_days))
                .to_dict()
            )
        except ValueError as exc:
            raise _not_found("Listing", listing_id) from exc

    @api.get("/ready", tags=["health"])
    def ready() -> dict[str, str]:
        try:
            with database.engine.connect() as connection:
                connection.exec_driver_sql("SELECT 1")
                connection.exec_driver_sql("SELECT 1 FROM tracking_runs LIMIT 1")
        except Exception as exc:
            raise HTTPException(status_code=503, detail="database unavailable") from exc
        if not _schema_is_ready(database):
            raise HTTPException(status_code=503, detail="schema is not at Alembic head")
        return {"status": "ready", "database": "ok", "schema": "ok"}

    @api.get("/metrics", tags=["health"])
    def metrics_endpoint() -> Response:
        if not metrics_enabled:
            raise HTTPException(status_code=404, detail="metrics disabled")
        return Response(generate_latest(app_metrics.registry), media_type=CONTENT_TYPE_LATEST)

    @api.middleware("http")
    async def observe_requests(request: Request, call_next: Any) -> Response:
        import time

        if not metrics_enabled:
            return cast(Response, await call_next(request))
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return cast(Response, response)
        finally:
            route = request.scope.get("route")
            route_template = getattr(route, "path", request.url.path)
            app_metrics.http_requests_total.labels(
                request.method, route_template, f"{status_code // 100}xx"
            ).inc()
            app_metrics.http_request_duration_seconds.labels(
                request.method, route_template
            ).observe(time.perf_counter() - started)

    @api.get("/api/v1/profiles", response_model=list[ProfileResponse], tags=["profiles"])
    def profiles(
        session: Session = Depends(get_session), page: tuple[int, int] = Depends(_page)
    ) -> list[ProfileResponse]:
        limit, offset = page
        rows = session.scalars(
            select(ProfileRecord).order_by(ProfileRecord.id).offset(offset).limit(limit)
        ).all()
        result: list[ProfileResponse] = []
        for row in rows:
            snapshot = _latest_profile_snapshot(session, row.id)
            result.append(_profile(row, snapshot.reports_received if snapshot else None))
        return result

    @api.get("/api/v1/profiles/{profile_id}", response_model=ProfileResponse, tags=["profiles"])
    def profile(profile_id: int, session: Session = Depends(get_session)) -> ProfileResponse:
        row = session.get(ProfileRecord, profile_id)
        if row is None:
            raise _not_found("Profile", profile_id)
        snapshot = _latest_profile_snapshot(session, row.id)
        return _profile(row, snapshot.reports_received if snapshot else None)

    @api.get("/api/v1/profiles/{profile_id}/history", tags=["profiles"])
    def profile_history(
        profile_id: int, session: Session = Depends(get_session)
    ) -> list[dict[str, Any]]:
        if session.get(ProfileRecord, profile_id) is None:
            raise _not_found("Profile", profile_id)
        return [
            _json_value(point.__dict__)
            for point in get_profile_metrics_history(session, profile_id)
        ]

    @api.get("/api/v1/searches", response_model=list[SearchResponse], tags=["searches"])
    def searches(
        session: Session = Depends(get_session), page: tuple[int, int] = Depends(_page)
    ) -> list[SearchResponse]:
        limit, offset = page
        rows = session.scalars(
            select(TrackedSearchRecord).order_by(TrackedSearchRecord.id).offset(offset).limit(limit)
        ).all()
        return [_search(row) for row in rows]

    @api.get("/api/v1/searches/{search_id}", response_model=SearchResponse, tags=["searches"])
    def search(search_id: int, session: Session = Depends(get_session)) -> SearchResponse:
        row = session.get(TrackedSearchRecord, search_id)
        if row is None:
            raise _not_found("Search", search_id)
        return _search(row)

    @api.get(
        "/api/v1/searches/{search_id}/listings/{listing_id}/explain",
        response_model=FilterExplanationResponse,
        tags=["searches"],
    )
    def explain_listing(
        search_id: int, listing_id: int, session: Session = Depends(get_session)
    ) -> FilterExplanationResponse:
        """Explain why a stored listing matched or failed a tracked search.

        Read-only and runtime only: nothing is persisted and the stored search
        is evaluated against the latest stored snapshot of the listing, so the
        listing does not need to be a stored match of the search. Fields that
        snapshots do not keep make their filter ``passed: null`` with
        ``complete: false`` and ``matched: null``, and are summarized in
        ``warnings``.
        """

        try:
            explanation = explain_search_listing(session, search_id, listing_id)
        except ValueError as exc:
            if str(exc).startswith("Unknown search"):
                raise _not_found("Search", search_id) from exc
            raise _not_found("Listing", listing_id) from exc
        return FilterExplanationResponse(
            matched=explanation.matched,
            complete=explanation.complete,
            traces=[_filter_trace(trace) for trace in explanation.evaluation.traces],
            warnings=list(explanation.warnings),
        )

    @api.post("/api/v1/searches", response_model=SearchResponse, status_code=201, tags=["searches"])
    def create_search(
        payload: SearchCreate, session: Session = Depends(get_session)
    ) -> SearchResponse:
        try:
            row = TrackedSearchRepository(session).create(
                payload.query,
                name=payload.name,
                min_price=payload.min_price,
                max_price=payload.max_price,
                filters=payload.filters,
                interval_seconds=payload.interval_seconds,
                notify_on_first_run=payload.notify_on_first_run,
                marketplace=payload.marketplace,
                target_price=payload.target_price,
                percentage_drop_threshold=payload.percentage_drop_threshold,
                deal_score_threshold=payload.deal_score_threshold,
                notify_on_30d_low=payload.notify_on_30d_low,
                notify_on_90d_low=payload.notify_on_90d_low,
                notify_on_all_time_low=payload.notify_on_all_time_low,
            )
            row.enabled = payload.enabled
            session.commit()
            return _search(row)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.patch("/api/v1/searches/{search_id}", response_model=SearchResponse, tags=["searches"])
    def patch_search(
        search_id: int, payload: SearchPatch, session: Session = Depends(get_session)
    ) -> SearchResponse:
        try:
            if payload.marketplace is not None:
                require_supported_marketplace(payload.marketplace)
            row = TrackedSearchRepository(session).update(
                search_id, **payload.model_dump(exclude_unset=True)
            )
            for field in (
                "target_price",
                "percentage_drop_threshold",
                "deal_score_threshold",
                "notify_on_30d_low",
                "notify_on_90d_low",
                "notify_on_all_time_low",
            ):
                if field in payload.model_fields_set:
                    setattr(row, field, getattr(payload, field))
            session.commit()
            return _search(row)
        except ValueError as exc:
            if str(exc).startswith("Unknown search"):
                raise _not_found("Search", search_id) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.post(
        "/api/v1/searches/import", response_model=SearchResponse, status_code=201, tags=["searches"]
    )
    def import_search(
        payload: SearchImport, session: Session = Depends(get_session)
    ) -> SearchResponse:
        try:
            parsed = parse_search_url(payload.url)
            if parsed.query is None:
                raise SearchURLParseError("search URL does not contain a query")
            row = TrackedSearchRepository(session).create(
                parsed.query,
                name=payload.name,
                min_price=parsed.min_price,
                max_price=parsed.max_price,
                filters=parsed.search_filters(),
                interval_seconds=payload.interval_seconds,
            )
            session.commit()
            return _search(row)
        except SearchURLParseError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/api/v1/listings", response_model=list[ListingResponse], tags=["listings"])
    def listings(
        search_id: int | None = None,
        profile_id: int | None = None,
        active: bool | None = None,
        min_price: Decimal | None = Query(None, ge=0),
        max_price: Decimal | None = Query(None, ge=0),
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[ListingResponse]:
        limit, offset = page
        statement = select(ListingRecord).order_by(ListingRecord.id)
        if profile_id is not None:
            statement = statement.where(ListingRecord.profile_id == profile_id)
        if search_id is not None:
            ids = select(SearchListingMatchRecord.listing_id).where(
                SearchListingMatchRecord.tracked_search_id == search_id
            )
            statement = statement.where(ListingRecord.id.in_(ids))
        rows = session.scalars(statement.offset(offset).limit(limit)).all()
        result = [_listing(session, row) for row in rows]
        if min_price is not None:
            result = [row for row in result if row.price is not None and row.price >= min_price]
        if max_price is not None:
            result = [row for row in result if row.price is not None and row.price <= max_price]
        if active is not None:
            result = [row for row in result if (row.presence_state == "active") == active]
        return result

    @api.get("/api/v1/listings/{listing_id}", response_model=ListingResponse, tags=["listings"])
    def listing(listing_id: int, session: Session = Depends(get_session)) -> ListingResponse:
        row = session.get(ListingRecord, listing_id)
        if row is None:
            raise _not_found("Listing", listing_id)
        return _listing(session, row)

    @api.get("/api/v1/listings/{listing_id}/history", tags=["listings"])
    def listing_history(
        listing_id: int, session: Session = Depends(get_session)
    ) -> list[dict[str, Any]]:
        if session.get(ListingRecord, listing_id) is None:
            raise _not_found("Listing", listing_id)
        return [_json_value(point.__dict__) for point in get_price_history(session, listing_id)]

    @api.post("/api/v1/listings/{listing_id}/ai-assessment", tags=["ai"])
    async def create_ai_assessment(
        listing_id: int,
        force: bool = Query(False),
    ) -> dict[str, Any]:
        try:
            execution = await api.state.ai_service.assess(listing_id, force=force)
        except ListingNotFoundError as exc:
            raise _not_found("Listing", listing_id) from exc
        except AIDisabledError as exc:
            raise HTTPException(status_code=409, detail="AI analysis is disabled") from exc
        except AIConfigurationError as exc:
            raise HTTPException(status_code=409, detail="AI analysis is not configured") from exc
        except Exception as exc:
            from wallapop_tracker.ai.providers.base import ListingAnalysisError

            if isinstance(exc, ListingAnalysisError):
                raise HTTPException(status_code=502, detail="AI provider request failed") from exc
            raise
        return _ai_assessment(execution.assessment, execution.cache_hit)

    @api.get("/api/v1/listings/{listing_id}/ai-assessment", tags=["ai"])
    def get_ai_assessment(
        listing_id: int, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        if session.get(ListingRecord, listing_id) is None:
            raise _not_found("Listing", listing_id)
        record = ListingAIRepository(session).get_latest_assessment(listing_id)
        if record is None:
            raise HTTPException(
                status_code=404, detail=f"AI assessment for listing {listing_id} not found"
            )
        return _ai_assessment(record, False)

    @api.get("/api/v1/tracked-listings", tags=["tracked-listings"])
    def tracked_listings(
        session: Session = Depends(get_session), page: tuple[int, int] = Depends(_page)
    ) -> list[dict[str, Any]]:
        limit, offset = page
        rows = session.scalars(
            select(TrackedListingRecord)
            .order_by(TrackedListingRecord.id)
            .offset(offset)
            .limit(limit)
        ).all()
        return [_tracked_listing(row) for row in rows]

    @api.post("/api/v1/tracked-listings", status_code=201, tags=["tracked-listings"])
    def create_tracked_listing(
        payload: TrackedListingCreate, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        try:
            row = TrackedListingRepository(session).create(
                payload.listing_id,
                payload.alias,
                interval_seconds=payload.interval_seconds,
                notes=payload.notes,
                target_price=payload.target_price,
                percentage_drop_threshold=payload.percentage_drop_threshold,
                deal_score_threshold=payload.deal_score_threshold,
                notify_on_30d_low=payload.notify_on_30d_low,
                notify_on_90d_low=payload.notify_on_90d_low,
                notify_on_all_time_low=payload.notify_on_all_time_low,
            )
            session.commit()
            return _tracked_listing(row)
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="Tracked listing alias or listing already exists"
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.patch("/api/v1/tracked-listings/{tracked_listing_id}", tags=["tracked-listings"])
    def patch_tracked_listing(
        tracked_listing_id: int,
        payload: TrackedListingPatch,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        try:
            row = TrackedListingRepository(session).update(
                tracked_listing_id, **payload.model_dump(exclude_unset=True)
            )
            for field in (
                "target_price",
                "percentage_drop_threshold",
                "deal_score_threshold",
                "notify_on_30d_low",
                "notify_on_90d_low",
                "notify_on_all_time_low",
            ):
                if field in payload.model_fields_set:
                    setattr(row, field, getattr(payload, field))
            session.commit()
            return _tracked_listing(row)
        except ValueError as exc:
            if str(exc).startswith("Unknown tracked listing"):
                raise _not_found("Tracked listing", tracked_listing_id) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @api.get("/api/v1/events", response_model=list[EventResponse], tags=["events"])
    def events(
        event_type: str | None = None,
        listing_id: int | None = None,
        search_id: int | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[EventResponse]:
        limit, offset = page
        statement = select(TrackingEventRecord).order_by(
            TrackingEventRecord.created_at, TrackingEventRecord.id
        )
        if event_type is not None:
            statement = statement.where(TrackingEventRecord.event_type == event_type)
        if listing_id is not None:
            statement = statement.where(TrackingEventRecord.listing_id == listing_id)
        if search_id is not None:
            statement = statement.where(TrackingEventRecord.tracked_search_id == search_id)
        if start_at is not None:
            statement = statement.where(TrackingEventRecord.created_at >= start_at)
        if end_at is not None:
            statement = statement.where(TrackingEventRecord.created_at <= end_at)
        return [_event(row) for row in session.scalars(statement.offset(offset).limit(limit)).all()]

    @api.get("/api/v1/events/{event_id}", response_model=EventResponse, tags=["events"])
    def event(event_id: int, session: Session = Depends(get_session)) -> EventResponse:
        row = session.get(TrackingEventRecord, event_id)
        if row is None:
            raise _not_found("Event", event_id)
        return _event(row)

    @api.get("/api/v1/tracking-events", response_model=list[EventResponse], tags=["events"])
    def tracking_events_alias(
        event_type: str | None = None,
        listing_id: int | None = None,
        search_id: int | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[EventResponse]:
        return events(event_type, listing_id, search_id, start_at, end_at, session, page)

    @api.get("/api/v1/domain-events", response_model=list[DomainEventResponse], tags=["events"])
    def domain_events(
        event_type: str | None = None,
        consumer: str | None = None,
        status: str | None = None,
        since: datetime | None = None,
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[DomainEventResponse]:
        limit, offset = page
        statement = select(DomainEventRecord).order_by(
            DomainEventRecord.created_at, DomainEventRecord.id
        )
        if event_type is not None:
            statement = statement.where(DomainEventRecord.event_type == event_type)
        if since is not None:
            statement = statement.where(DomainEventRecord.created_at >= since)
        if consumer is not None or status is not None:
            statement = statement.join(
                EventConsumptionRecord, EventConsumptionRecord.event_id == DomainEventRecord.id
            )
            if consumer is not None:
                statement = statement.where(EventConsumptionRecord.consumer_name == consumer)
            if status is not None:
                statement = statement.where(EventConsumptionRecord.status == status)
        return [
            DomainEventResponse(**deserialize_event(row))
            for row in session.scalars(statement.offset(offset).limit(limit)).all()
        ]

    @api.get(
        "/api/v1/domain-events/{event_id}", response_model=DomainEventResponse, tags=["events"]
    )
    def domain_event(event_id: int, session: Session = Depends(get_session)) -> DomainEventResponse:
        row = session.get(DomainEventRecord, event_id)
        if row is None:
            raise _not_found("Event", event_id)
        return DomainEventResponse(**deserialize_event(row))

    @api.get("/api/v1/dlq", tags=["events"])
    def dead_letters(
        consumer: str | None = None,
        limit: int = Query(100, ge=1, le=1000),
        session: Session = Depends(get_session),
    ) -> list[dict[str, Any]]:
        statement = select(DeadLetterRecord).order_by(DeadLetterRecord.id).limit(limit)
        if consumer is not None:
            statement = statement.where(DeadLetterRecord.consumer_name == consumer)
        return [_dead_letter(row, session) for row in session.scalars(statement).all()]

    @api.get("/api/v1/dlq/{dead_letter_id}", tags=["events"])
    def dead_letter(dead_letter_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        row = session.get(DeadLetterRecord, dead_letter_id)
        if row is None:
            raise _not_found("Dead letter", dead_letter_id)
        return _dead_letter(row, session)

    @api.get("/api/v1/runs", response_model=list[RunResponse], tags=["runs"])
    def runs(
        session: Session = Depends(get_session), page: tuple[int, int] = Depends(_page)
    ) -> list[RunResponse]:
        limit, offset = page
        rows = session.scalars(
            select(TrackingRunRecord)
            .order_by(TrackingRunRecord.started_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return [_run(row) for row in rows]

    @api.get("/api/v1/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    def run(run_id: int, session: Session = Depends(get_session)) -> RunResponse:
        row = session.get(TrackingRunRecord, run_id)
        if row is None:
            raise _not_found("Run", run_id)
        return _run(row)

    @api.get("/api/v1/health", tags=["health"])
    def health_summary_endpoint(session: Session = Depends(get_session)) -> dict[str, Any]:
        return health_summary(session)

    @api.get("/api/v1/health/runs", tags=["health"])
    def health_runs(
        session: Session = Depends(get_session), limit: int = Query(50, ge=1, le=500)
    ) -> list[dict[str, Any]]:
        rows = session.scalars(
            select(TrackingRunRecord).order_by(TrackingRunRecord.started_at.desc()).limit(limit)
        ).all()
        return [
            {
                "run_id": r.id,
                "status": r.health_status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "duration_ms": r.duration_ms,
                "items_scanned": r.items_scanned,
                "http_errors": r.http_errors,
                "http_403": r.http_403,
                "http_429": r.http_429,
                "http_5xx": r.http_5xx,
                "parse_errors": r.parse_errors,
            }
            for r in rows
        ]

    @api.get("/api/v1/health/searches/{search_id}", tags=["health"])
    def search_health(search_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        return health_summary(session, search_id=search_id)

    @api.get("/api/v1/health/profiles/{profile_id}", tags=["health"])
    def profile_health(profile_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        return health_summary(session, profile_id=profile_id)

    @api.get("/api/v1/analytics/market/{search_id}", tags=["analytics"])
    def market(search_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        return cast(dict[str, Any], _json_value(get_market_summary(session, search_id).__dict__))

    @api.get("/api/v1/analytics/listing/{listing_id}/market", tags=["analytics"])
    def listing_market(listing_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        estimate = get_listing_market_estimate(session, listing_id)
        return cast(dict[str, Any], _json_value(estimate.__dict__))

    @api.get("/api/v1/analytics/market/{search_id}/prices", tags=["analytics"])
    def prices(
        search_id: int, weekly: bool = False, session: Session = Depends(get_session)
    ) -> list[dict[str, Any]]:
        return [
            _json_value(point.__dict__)
            for point in get_price_time_series(
                session, search_id, granularity="weekly" if weekly else "daily"
            )
        ]

    @api.get("/api/v1/analytics/market/{search_id}/activity", tags=["analytics"])
    def activity(
        search_id: int, weekly: bool = False, session: Session = Depends(get_session)
    ) -> list[dict[str, Any]]:
        return [
            _json_value(point.__dict__)
            for point in get_activity_time_series(
                session, search_id, granularity="weekly" if weekly else "daily"
            )
        ]

    @api.get("/api/v1/analytics/market/{search_id}/sellers", tags=["analytics"])
    def sellers(search_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return [
            _json_value(point.__dict__) for point in get_seller_market_stats(session, search_id)
        ]

    @api.get("/api/v1/analytics/market/{search_id}/brands", tags=["analytics"])
    def brands(search_id: int, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return [_json_value(point.__dict__) for point in get_brand_market_stats(session, search_id)]

    @api.get("/api/v1/scores/listing/{listing_id}", response_model=ScoreResponse, tags=["scoring"])
    def listing_score(
        listing_id: int, search_id: int = Query(..., gt=0), session: Session = Depends(get_session)
    ) -> ScoreResponse:
        return _score(DealScoringService(session).score_listing(listing_id, search_id))

    @api.get(
        "/api/v1/scores/search/{search_id}", response_model=list[ScoreResponse], tags=["scoring"]
    )
    def search_scores(
        search_id: int,
        limit: int = Query(20, ge=1, le=100),
        session: Session = Depends(get_session),
    ) -> list[ScoreResponse]:
        return [
            _score(result)
            for result in DealScoringService(session).score_search(search_id, limit=limit)
        ]

    @api.get("/api/v1/relistings", tags=["relistings"])
    def relistings(
        min_score: Decimal | None = Query(None, ge=0, le=1),
        listing_id: int | None = None,
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[dict[str, Any]]:
        limit, offset = page
        rows = PossibleRelistingRepository(session).list_all(
            min_score=min_score, listing_id=listing_id
        )
        return [_relisting(row) for row in rows[offset : offset + limit]]

    @api.get("/api/v1/relistings/{relisting_id}", tags=["relistings"])
    def relisting(relisting_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        row = PossibleRelistingRepository(session).get(relisting_id)
        if row is None:
            raise _not_found("Relisting", relisting_id)
        return _relisting(row)

    @api.get("/api/v1/alerts/rules", tags=["alerts"])
    def alert_rules(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
        return [
            _alert_rule(row)
            for row in session.scalars(select(AlertRuleRecord).order_by(AlertRuleRecord.id))
        ]

    @api.post("/api/v1/alerts/rules", status_code=201, tags=["alerts"])
    def create_alert_rule(
        body: AlertRuleCreate, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        if body.channel not in {"webhook", "telegram"}:
            raise HTTPException(status_code=422, detail="channel must be webhook or telegram")
        now = datetime.now(UTC)
        row = AlertRuleRecord(
            event_type=body.event_type,
            channel=body.channel,
            destination=body.destination,
            filters_json=json.dumps(body.filters),
            cooldown_seconds=body.cooldown_seconds,
            enabled=body.enabled,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        session.commit()
        return _alert_rule(row)

    @api.get("/api/v1/alerts/rules/{rule_id}", tags=["alerts"])
    def get_alert_rule(rule_id: int, session: Session = Depends(get_session)) -> dict[str, Any]:
        row = session.get(AlertRuleRecord, rule_id)
        if row is None:
            raise _not_found("Alert rule", rule_id)
        return _alert_rule(row)

    @api.patch("/api/v1/alerts/rules/{rule_id}", tags=["alerts"])
    def patch_alert_rule(
        rule_id: int, body: AlertRulePatch, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        row = session.get(AlertRuleRecord, rule_id)
        if row is None:
            raise _not_found("Alert rule", rule_id)
        if body.channel is not None and body.channel not in {"webhook", "telegram"}:
            raise HTTPException(status_code=422, detail="channel must be webhook or telegram")
        values = body.model_dump(exclude_unset=True)
        if "filters" in values:
            row.filters_json = json.dumps(values.pop("filters"))
        for key, value in values.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(UTC)
        session.flush()
        session.commit()
        return _alert_rule(row)

    @api.delete("/api/v1/alerts/rules/{rule_id}", status_code=204, tags=["alerts"])
    def delete_alert_rule(rule_id: int, session: Session = Depends(get_session)) -> None:
        row = session.get(AlertRuleRecord, rule_id)
        if row is None:
            raise _not_found("Alert rule", rule_id)
        session.delete(row)
        session.commit()

    @api.get("/api/v1/alerts/deliveries", tags=["alerts"])
    @api.get("/api/v1/notifications", tags=["notifications"])
    def notifications(
        status: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        session: Session = Depends(get_session),
        page: tuple[int, int] = Depends(_page),
    ) -> list[dict[str, Any]]:
        limit, offset = page
        rows = NotificationDeliveryRepository(session).list_all()
        rows = [
            row
            for row in rows
            if (status is None or row.status.value == status)
            and (channel is None or row.channel == channel)
            and (event_type is None or row.event.event_type == event_type)
        ]
        return [_notification(row) for row in rows[offset : offset + limit]]

    @api.get("/api/v1/notifications/{notification_id}", tags=["notifications"])
    def notification(
        notification_id: int, session: Session = Depends(get_session)
    ) -> dict[str, Any]:
        row = session.get(NotificationDeliveryRecord, notification_id)
        if row is None:
            raise _not_found("Notification", notification_id)
        return _notification(row)

    api.router.on_shutdown.append(close_owned)
    return api


def _tracked_listing(row: TrackedListingRecord) -> dict[str, Any]:
    return {
        "id": row.id,
        "listing_id": row.listing_id,
        "alias": row.alias,
        "enabled": row.enabled,
        "interval_seconds": row.interval_seconds,
        "last_run_at": _utc(row.last_run_at),
        "last_run_status": row.last_run_status,
        "notes": row.notes,
        "target_price": row.target_price,
        "percentage_drop_threshold": row.percentage_drop_threshold,
        "deal_score_threshold": row.deal_score_threshold,
        "notify_on_30d_low": row.notify_on_30d_low,
        "notify_on_90d_low": row.notify_on_90d_low,
        "notify_on_all_time_low": row.notify_on_all_time_low,
    }


def _alert_rule(row: AlertRuleRecord) -> dict[str, Any]:
    try:
        filters = json.loads(row.filters_json)
    except json.JSONDecodeError:
        filters = {}
    return {
        "id": row.id,
        "event_type": row.event_type,
        "channel": row.channel,
        "destination": "[redacted]" if row.channel in {"webhook", "telegram"} else row.destination,
        "filters": filters,
        "cooldown_seconds": row.cooldown_seconds,
        "enabled": row.enabled,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
    }


def _relisting(row: PossibleRelistingRecord) -> dict[str, Any]:
    try:
        reasons = json.loads(row.reasons_json)
    except json.JSONDecodeError:
        reasons = []
    return {
        "id": row.id,
        "previous_listing_id": row.previous_listing_id,
        "current_listing_id": row.current_listing_id,
        "score": row.score,
        "status": row.status.value,
        "reasons": reasons,
        "detected_at": _utc(row.detected_at),
        "event_id": row.event_id,
    }


def _notification(row: NotificationDeliveryRecord) -> dict[str, Any]:
    sensitive = row.channel in {"webhook", "discord", "telegram"}
    return {
        "id": row.id,
        "event_id": row.event_id,
        "channel": row.channel,
        "destination": "[redacted]" if sensitive else row.destination,
        "status": row.status.value,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "created_at": _utc(row.created_at),
        "updated_at": _utc(row.updated_at),
        "delivered_at": _utc(row.delivered_at),
    }


app = create_app()
