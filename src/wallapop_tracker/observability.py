"""Small, dependency-light operational observability layer."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    """Prometheus metrics grouped in an injectable registry for test isolation."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.tracking_runs_total = Counter(
            "tracking_runs_total",
            "Tracking runs started",
            ["source_type", "status"],
            registry=self.registry,
        )
        self.tracking_runs_failed_total = Counter(
            "tracking_runs_failed_total",
            "Tracking runs that failed",
            ["source_type"],
            registry=self.registry,
        )
        self.tracking_run_duration_seconds = Histogram(
            "tracking_run_duration_seconds",
            "Tracking run duration",
            ["source_type"],
            registry=self.registry,
        )
        self.wallapop_http_requests_total = Counter(
            "wallapop_http_requests_total",
            "Wallapop HTTP requests",
            ["operation", "method", "status_class"],
            registry=self.registry,
        )
        self.wallapop_http_retries_total = Counter(
            "wallapop_http_retries_total",
            "Wallapop HTTP retries",
            ["operation"],
            registry=self.registry,
        )
        self.wallapop_http_429_total = Counter(
            "wallapop_http_429_total",
            "Wallapop HTTP 429 responses",
            ["operation"],
            registry=self.registry,
        )
        self.wallapop_parse_errors_total = Counter(
            "wallapop_parse_errors_total",
            "Wallapop parsing errors",
            ["operation"],
            registry=self.registry,
        )
        self.listings_fetched_total = Counter(
            "listings_fetched_total",
            "Listings fetched by tracking runs",
            ["source_type"],
            registry=self.registry,
        )
        self.tracking_events_created_total = Counter(
            "tracking_events_created_total",
            "Tracking events created",
            ["event_type"],
            registry=self.registry,
        )
        self.event_bus_published_total = Counter(
            "event_bus_published_total",
            "Domain events published",
            ["event_type"],
            registry=self.registry,
        )
        self.event_bus_processed_total = Counter(
            "event_bus_processed_total",
            "Domain events processed",
            ["consumer", "status"],
            registry=self.registry,
        )
        self.event_bus_claimed_total = Counter(
            "event_bus_claimed_total",
            "Domain events claimed",
            ["consumer"],
            registry=self.registry,
        )
        self.event_bus_processing_failures_total = Counter(
            "event_bus_processing_failures_total",
            "Consumer processing failures",
            ["consumer"],
            registry=self.registry,
        )
        self.event_bus_retries_total = Counter(
            "event_bus_retries_total",
            "Consumer retries scheduled",
            ["consumer"],
            registry=self.registry,
        )
        self.event_bus_dlq_total = Counter(
            "event_bus_dlq_total",
            "Events moved to the dead letter queue",
            ["consumer"],
            registry=self.registry,
        )
        self.event_bus_processing_duration_seconds = Histogram(
            "event_bus_processing_duration_seconds",
            "Consumer processing duration",
            ["consumer"],
            registry=self.registry,
        )
        self.event_bus_pending = Gauge(
            "event_bus_pending", "Pending event consumptions", ["consumer"], registry=self.registry
        )
        self.event_bus_leased = Gauge(
            "event_bus_leased", "Leased event consumptions", ["consumer"], registry=self.registry
        )
        self.event_bus_dlq_size = Gauge(
            "event_bus_dlq_size", "Dead letter queue size", ["consumer"], registry=self.registry
        )
        self.notification_deliveries_total = Counter(
            "notification_deliveries_total",
            "Notification delivery attempts",
            ["channel", "status"],
            registry=self.registry,
        )
        self.notification_failures_total = Counter(
            "notification_failures_total",
            "Failed notification deliveries",
            ["channel"],
            registry=self.registry,
        )
        self.scheduler_polls_total = Counter(
            "scheduler_polls_total", "Scheduler polls", registry=self.registry
        )
        self.scheduler_jobs_executed_total = Counter(
            "scheduler_jobs_executed_total",
            "Scheduler jobs executed",
            ["source_type"],
            registry=self.registry,
        )
        self.scheduler_jobs_failed_total = Counter(
            "scheduler_jobs_failed_total",
            "Scheduler jobs failed",
            ["source_type"],
            registry=self.registry,
        )
        self.scheduler_active_jobs = Gauge(
            "scheduler_active_jobs", "Currently active scheduler jobs", registry=self.registry
        )
        self.jobs_claimed_total = Counter(
            "wallapop_jobs_claimed_total",
            "Tracking jobs claimed",
            ["job_type"],
            registry=self.registry,
        )
        self.jobs_completed_total = Counter(
            "wallapop_jobs_completed_total",
            "Tracking jobs completed",
            ["job_type"],
            registry=self.registry,
        )
        self.jobs_failed_total = Counter(
            "wallapop_jobs_failed_total",
            "Tracking jobs failed",
            ["job_type"],
            registry=self.registry,
        )
        self.job_claim_conflicts_total = Counter(
            "wallapop_job_claim_conflicts_total",
            "Job claim conflicts",
            ["job_type"],
            registry=self.registry,
        )
        self.job_lease_expired_total = Counter(
            "wallapop_job_lease_expired_total",
            "Expired job leases",
            ["job_type"],
            registry=self.registry,
        )
        self.notification_claimed_total = Counter(
            "wallapop_notification_claimed_total",
            "Notification deliveries claimed",
            registry=self.registry,
        )
        self.notification_sent_total = Counter(
            "wallapop_notification_sent_total",
            "Notification deliveries sent",
            registry=self.registry,
        )
        self.notification_failed_total = Counter(
            "wallapop_notification_failed_total",
            "Notification deliveries failed",
            registry=self.registry,
        )
        self.notification_retry_total = Counter(
            "wallapop_notification_retry_total", "Notification retries", registry=self.registry
        )
        self.pending_jobs = Gauge(
            "wallapop_pending_jobs", "Due tracking jobs", registry=self.registry
        )
        self.pending_notifications = Gauge(
            "wallapop_pending_notifications", "Pending notifications", registry=self.registry
        )
        self.telegram_updates_total = Counter(
            "wallapop_telegram_updates_total", "Telegram updates received", registry=self.registry
        )
        self.telegram_commands_total = Counter(
            "wallapop_telegram_commands_total",
            "Telegram commands processed",
            ["command"],
            registry=self.registry,
        )
        self.telegram_command_failures_total = Counter(
            "wallapop_telegram_command_failures_total",
            "Telegram command failures",
            ["command"],
            registry=self.registry,
        )
        self.telegram_control_duration_seconds = Histogram(
            "wallapop_telegram_control_duration_seconds",
            "Telegram command duration",
            registry=self.registry,
        )
        self.http_requests_total = Counter(
            "http_requests_total",
            "API HTTP requests",
            ["method", "route", "status_class"],
            registry=self.registry,
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "API HTTP request duration",
            ["method", "route"],
            registry=self.registry,
        )
        self.wallapop_deal_ranking_requests_total = Counter(
            "wallapop_deal_ranking_requests_total", "Deal ranking requests", registry=self.registry
        )
        self.wallapop_deal_ranking_failures_total = Counter(
            "wallapop_deal_ranking_failures_total", "Deal ranking failures", registry=self.registry
        )
        self.wallapop_deal_ranking_duration_seconds = Histogram(
            "wallapop_deal_ranking_duration_seconds",
            "Deal ranking duration",
            registry=self.registry,
        )
        self.wallapop_ai_analysis_requests_total = Counter(
            "wallapop_ai_analysis_requests_total", "AI analysis requests", registry=self.registry
        )
        self.wallapop_ai_analysis_success_total = Counter(
            "wallapop_ai_analysis_success_total", "Successful AI analyses", registry=self.registry
        )
        self.wallapop_ai_analysis_failures_total = Counter(
            "wallapop_ai_analysis_failures_total",
            "Failed AI analyses",
            ["outcome"],
            registry=self.registry,
        )
        self.wallapop_ai_analysis_cache_hits_total = Counter(
            "wallapop_ai_analysis_cache_hits_total",
            "AI analysis cache hits",
            registry=self.registry,
        )
        self.wallapop_ai_input_tokens_total = Counter(
            "wallapop_ai_input_tokens_total", "AI input tokens", registry=self.registry
        )
        self.wallapop_ai_output_tokens_total = Counter(
            "wallapop_ai_output_tokens_total", "AI output tokens", registry=self.registry
        )
        self.wallapop_ai_analysis_duration_seconds = Histogram(
            "wallapop_ai_analysis_duration_seconds",
            "AI analysis duration",
            ["provider", "model"],
            registry=self.registry,
        )


_default_metrics = Metrics()


def get_metrics() -> Metrics:
    return _default_metrics


def operation_for_url(url: str) -> str:
    path = url.lower()
    if "/search" in path:
        return "search"
    if "/items/" in path or path.endswith("/items"):
        return "get_item"
    if "/profile" in path:
        return "get_profile"
    if "/categories" in path:
        return "categories"
    if "/filters" in path:
        return "filters"
    if "/brands" in path:
        return "brands"
    if "/models" in path:
        return "models"
    return "request"


def status_class(status_code: int | None) -> str:
    if status_code is None:
        return "error"
    return f"{status_code // 100}xx"


def redact(value: str) -> str:
    """Keep logs useful without leaking webhook URLs or tokens."""
    if not value:
        return value
    if value.startswith(("http://", "https://")):
        return "[redacted-url]"
    if "token" in value.lower() or "bearer " in value.lower():
        return "[redacted]"
    return value


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        fields: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in (
            "event",
            "run_id",
            "profile_id",
            "search_id",
            "tracked_listing_id",
            "listing_id",
            "status",
            "status_class",
            "operation",
            "duration_ms",
            "attempt",
            "items_fetched",
            "events_created",
            "event_id",
            "event_type",
            "consumer",
            "correlation_id",
            "dead_letter_id",
        ):
            if hasattr(record, key):
                fields[key] = getattr(record, key)
        return json.dumps(fields, ensure_ascii=False, default=str)


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", record.name)
        fields = []
        for key in (
            "run_id",
            "profile_id",
            "search_id",
            "tracked_listing_id",
            "listing_id",
            "status",
            "status_class",
            "operation",
            "duration_ms",
            "attempt",
        ):
            if hasattr(record, key):
                fields.append(f"{key}={redact(str(getattr(record, key)))}")
        suffix = f" {' '.join(fields)}" if fields else ""
        return f"{record.levelname} {event}: {record.getMessage()}{suffix}"


def configure_logging() -> None:
    level_name = os.getenv("WALLAPOP_LOG_LEVEL", "INFO").upper()
    format_name = os.getenv("WALLAPOP_LOG_FORMAT", "human").lower()
    level = getattr(logging, level_name, logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    handler = next((item for item in root.handlers if getattr(item, "_wallapop", False)), None)
    if handler is None:
        handler = logging.StreamHandler(sys.stderr)
        handler._wallapop = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    handler.setFormatter(_JsonFormatter() if format_name == "json" else _HumanFormatter())


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    safe = {
        key: redact(str(value)) if key in {"destination", "url", "token"} else value
        for key, value in fields.items()
    }
    logger.log(level, event, extra={"event": event, **safe})


def duration_seconds(start: float) -> float:
    return time.perf_counter() - start


__all__ = [
    "Metrics",
    "configure_logging",
    "duration_seconds",
    "get_metrics",
    "log_event",
    "operation_for_url",
    "redact",
    "status_class",
]
