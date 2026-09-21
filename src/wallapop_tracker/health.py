"""Deterministic health classification and aggregate reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import quantiles
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .storage.models import TrackingRunRecord

RECENT_FAILURE_THRESHOLD = 3
SUSPICIOUS_HISTORY_SAMPLE = 3


def classify_run(
    *,
    completed: bool,
    parse_errors: int | None = None,
    http_errors: int | None = None,
    http_429: int | None = None,
    http_5xx: int | None = None,
    suspicious_result: bool = False,
) -> str:
    if not completed:
        return "FAILED"
    if (
        any((parse_errors or 0, http_errors or 0, http_429 or 0, http_5xx or 0))
        or suspicious_result
    ):
        return "DEGRADED"
    return "SUCCESS"


def suspicious_zero(session: Session, source_column: Any, source_id: int, *, current: int) -> bool:
    if current != 0:
        return False
    rows = session.scalars(
        select(TrackingRunRecord)
        .where(source_column == source_id)
        .order_by(TrackingRunRecord.started_at.desc())
        .limit(10)
    ).all()
    return (
        sum(1 for row in rows if (row.items_scanned or row.items_fetched or 0) > 0)
        >= SUSPICIOUS_HISTORY_SAMPLE
    )


def health_summary(
    session: Session, *, search_id: int | None = None, profile_id: int | None = None
) -> dict[str, Any]:
    column = (
        TrackingRunRecord.tracked_search_id
        if search_id is not None
        else TrackingRunRecord.profile_id
    )
    source_id = search_id if search_id is not None else profile_id
    statement = select(TrackingRunRecord).order_by(TrackingRunRecord.finished_at.desc())
    if source_id is not None:
        statement = statement.where(column == source_id)
    runs = session.scalars(statement).all()
    now = datetime.now(UTC)
    recent = [
        r
        for r in runs
        if r.finished_at and (now - r.finished_at.replace(tzinfo=UTC)) <= timedelta(hours=24)
    ]
    durations = [r.duration_ms for r in recent if r.duration_ms is not None]
    consecutive = 0
    for row in runs:
        if row.health_status == "FAILED":
            consecutive += 1
        else:
            break
    if consecutive >= RECENT_FAILURE_THRESHOLD:
        status = "FAILING"
    elif (
        not runs
        or runs[0].health_status != "SUCCESS"
        or (runs and runs[0].health_status == "DEGRADED")
    ):
        status = "DEGRADED"
    else:
        status = "HEALTHY"
    success = sum(r.health_status == "SUCCESS" for r in recent)
    return {
        "status": status,
        "last_success_at": next(
            (r.finished_at for r in runs if r.health_status == "SUCCESS"), None
        ),
        "last_failure_at": next((r.finished_at for r in runs if r.health_status == "FAILED"), None),
        "consecutive_failures": consecutive,
        "runs_24h": len(recent),
        "failed_runs_24h": sum(r.health_status == "FAILED" for r in recent),
        "degraded_runs_24h": sum(r.health_status == "DEGRADED" for r in recent),
        "success_rate_24h": success / len(recent) if recent else None,
        "average_duration_ms_24h": sum(durations) / len(durations) if durations else None,
        "p95_duration_ms_24h": (
            quantiles(durations, n=20, method="inclusive")[18]
            if len(durations) >= 2
            else (durations[0] if durations else None)
        ),
    }


__all__ = ["classify_run", "health_summary", "suspicious_zero"]
