"""Persistent notification delivery service and HTTP channel adapters."""

from __future__ import annotations

import json
import logging
import os
import socket
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import Any, cast
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.notifications import (
    DeliveryResult,
    Notification,
    NotificationChannel,
)
from wallapop_tracker.observability import get_metrics
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    ListingSnapshotRecord,
    NotificationDeliveryRecord,
    NotificationDeliveryStatus,
    PossibleRelistingRecord,
    TrackingEventRecord,
)
from wallapop_tracker.storage.repositories import (
    NotificationDeliveryRepository,
    TelegramOwnershipRepository,
)

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096


def truncate_text(value: str | None, max_length: int) -> str | None:
    if not value:
        return None
    value = " ".join(value.split())
    return value if len(value) <= max_length else value[: max(0, max_length - 1)].rstrip() + "…"


def _html(value: object) -> str:
    return escape(str(value), quote=True)


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{value:.2f}".rstrip("0").rstrip(".") + " €"


def _percent(value: Decimal | None) -> str | None:
    return f"{value:.1f}%" if value is not None else None


def render_telegram_message(notification: Notification) -> str:
    title = truncate_text(notification.title, 240) or notification.listing_id
    event = notification.event_type
    heading = {
        AlertType.PRICE_DROP: "📉 Bajada de precio",
        AlertType.REAPPEARED: "🔁 Reapareció",
        AlertType.DEAL_SCORE_THRESHOLD: "🎯 Deal score",
    }.get(event, "🔥" if event in {AlertType.NEW_LISTING, AlertType.NEW_SEARCH_MATCH} else "🔔")
    lines = [f"{heading} <b>{_html(title)}</b>"]
    if (
        event == AlertType.PRICE_DROP
        and notification.old_price is not None
        and notification.new_price is not None
    ):
        drop = (notification.new_price - notification.old_price) / notification.old_price * 100
        lines.extend(
            [
                f"{_html(_money(notification.old_price))} → {_html(_money(notification.new_price))}",
                f"-{_html(_money(notification.old_price - notification.new_price))} ({_html(_percent(-abs(drop)))})",
            ]
        )
    elif notification.new_price is not None:
        lines.append(f"💶 {_html(_money(notification.new_price))}")
    if notification.market_value is not None:
        lines.append(f"📊 Mercado: ~{_html(_money(notification.market_value))}")
        if notification.market_discount_percent is not None:
            lines.append(f"📉 Diferencia: {_html(_percent(notification.market_discount_percent))}")
    if notification.deal_score is not None:
        score = (
            notification.deal_score.quantize(Decimal("1"))
            if notification.deal_score == notification.deal_score.to_integral()
            else notification.deal_score
        )
        lines.append(f"🎯 Deal score: {_html(score)}/100")
    context: list[str] = []
    if notification.location:
        location = _html(notification.location)
        if notification.distance_km is not None:
            location += f" · {_html(notification.distance_km.normalize())} km"
        context.append(f"📍 {location}")
    elif notification.distance_km is not None:
        context.append(f"📍 {_html(notification.distance_km.normalize())} km")
    if notification.shipping_available is not None:
        context.append(f"📦 Envíos: {'Sí' if notification.shipping_available else 'No'}")
    seller: list[str] = []
    if notification.seller_rating is not None:
        seller.append(f"{_html(notification.seller_rating)}★")
    if notification.seller_review_count is not None:
        seller.append(f"{notification.seller_review_count} valoraciones")
    if notification.seller_sales_count is not None:
        seller.append(f"{notification.seller_sales_count} ventas")
    if seller:
        context.append("👤 " + " · ".join(seller))
    if context:
        lines.append("\n".join(context))
    evidence = [truncate_text(item, 180) for item in notification.evidence[:5]]
    evidence = [item for item in evidence if item]
    if evidence:
        lines.append("✅ Coincide porque:\n" + "\n".join(f"• {_html(item)}" for item in evidence))
    if notification.low_period_days is not None:
        lines.append(f"📉 Mínimo de {notification.low_period_days} días")
    description = truncate_text(notification.description, 280)
    if description:
        lines.append(f"\n{_html(description)}")
    if notification.url:
        lines.append(
            f'🔗 <a href="{_html(truncate_text(notification.url, 1800) or notification.url)}">Ver anuncio</a>'
        )
    elif notification.details and event not in {AlertType.NEW_LISTING, AlertType.NEW_SEARCH_MATCH}:
        lines.append(_html(truncate_text(notification.details, 500) or ""))
    return (
        truncate_text("\n\n".join(line for line in lines if line), TELEGRAM_MAX_LENGTH)
        or "🔔 Notificación"
    )


def _price(value: Decimal | None) -> str | None:
    return f"{value:.2f}" if value is not None else None


def _notification_payload(notification: Notification) -> dict[str, Any]:
    return {
        "event_id": notification.event_id,
        "event_type": notification.event_type.value,
        "listing_id": notification.listing_id,
        "title": notification.title,
        "url": notification.url,
        "old_price": _price(notification.old_price),
        "new_price": _price(notification.new_price),
        "created_at": notification.created_at.astimezone(UTC).isoformat(),
        "details": notification.details,
    }


class WebhookNotificationChannel:
    """Generic JSON POST channel with bounded, caller-controlled retries."""

    channel_name = "webhook"

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        headers: Mapping[str, str] | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.headers = dict(headers or {})
        self.http_client = http_client

    def payload(self, notification: Notification) -> dict[str, Any]:
        return _notification_payload(notification)

    async def send(self, notification: Notification, destination: str) -> DeliveryResult:
        parsed = urlparse(destination)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
        ):
            return DeliveryResult(False, False, "Invalid webhook URL")
        try:
            if self.http_client is not None:
                response = await self.http_client.post(
                    destination,
                    json=self.payload(notification),
                    timeout=self.timeout,
                    headers=self.headers,
                    follow_redirects=False,
                )
            else:
                async with httpx.AsyncClient(
                    timeout=self.timeout, follow_redirects=False
                ) as client:
                    response = await client.post(
                        destination, json=self.payload(notification), headers=self.headers
                    )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return DeliveryResult(False, True, type(exc).__name__)
        return _http_result(response)


class DiscordWebhookChannel(WebhookNotificationChannel):
    """Readable Discord webhook formatting using the generic HTTP transport."""

    channel_name = "discord"

    def payload(self, notification: Notification) -> dict[str, Any]:
        title = notification.title or notification.listing_id
        if notification.event_type == AlertType.PRICE_DROP:
            heading = "Price drop"
        elif notification.event_type == AlertType.PRICE_INCREASE:
            heading = "Price increase"
        else:
            heading = notification.event_type.value.replace("_", " ").title()
        prices = ""
        if notification.old_price is not None and notification.new_price is not None:
            prices = f"\n{_price(notification.old_price)} € → {_price(notification.new_price)} €"
        content = f"{heading}\n{title}{prices}"
        if notification.url:
            content += f"\n{notification.url}"
        if notification.details:
            content += f"\n{notification.details}"
        return {"content": content}


class TelegramNotificationChannel:
    """Telegram Bot API adapter; the bot token is kept only in configuration."""

    channel_name = "telegram"

    def __init__(
        self,
        bot_token: str,
        *,
        timeout: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("Telegram bot token is required")
        self.bot_token = bot_token
        self.timeout = timeout
        self.http_client = http_client

    async def send(self, notification: Notification, destination: str) -> DeliveryResult:
        text = render_telegram_message(notification)
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": destination, "text": text, "parse_mode": "HTML"}
        try:
            if self.http_client is not None:
                response = await self.http_client.post(url, json=payload, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return DeliveryResult(False, True, type(exc).__name__)
        result = _http_result(response)
        if not result.delivered:
            return result
        try:
            body = response.json()
        except ValueError:
            body = {}
        if isinstance(body, dict) and body.get("ok") is False:
            return DeliveryResult(False, False, "Telegram API error")
        return DeliveryResult(True)


def _http_result(response: httpx.Response) -> DeliveryResult:
    if 200 <= response.status_code < 300:
        return DeliveryResult(True)
    retryable = response.status_code == 429 or response.status_code >= 500
    return DeliveryResult(False, retryable, f"HTTP {response.status_code}")


@dataclass(frozen=True)
class NotificationSettings:
    webhook_enabled: bool = False
    discord_enabled: bool = False
    telegram_enabled: bool = False
    webhook_url: str | None = None
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    webhook_headers: Mapping[str, str] = field(default_factory=dict)
    timeout: float = 10.0
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> NotificationSettings:
        raw_attempts = os.getenv("WALLAPOP_NOTIFICATION_MAX_ATTEMPTS", "3")
        try:
            max_attempts = max(1, int(raw_attempts))
        except ValueError:
            max_attempts = 3

        def enabled(name: str) -> bool:
            return os.getenv(name, "false").strip().lower() in {"1", "true", "yes", "on"}

        raw_headers = os.getenv("WALLAPOP_WEBHOOK_HEADERS", "")
        try:
            parsed_headers = json.loads(raw_headers) if raw_headers else {}
            if not isinstance(parsed_headers, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in parsed_headers.items()
            ):
                raise ValueError
        except ValueError as exc:
            raise ValueError("WALLAPOP_WEBHOOK_HEADERS must be a JSON object of strings") from exc
        return cls(
            webhook_enabled=enabled("WALLAPOP_NOTIFY_WEBHOOK_ENABLED"),
            discord_enabled=enabled("WALLAPOP_NOTIFY_DISCORD_ENABLED"),
            telegram_enabled=enabled("WALLAPOP_NOTIFY_TELEGRAM_ENABLED"),
            webhook_url=os.getenv("WALLAPOP_WEBHOOK_URL"),
            discord_webhook_url=os.getenv("WALLAPOP_DISCORD_WEBHOOK_URL"),
            telegram_bot_token=os.getenv(
                "WALLAPOP_TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN")
            ),
            telegram_chat_id=os.getenv("WALLAPOP_TELEGRAM_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID")),
            webhook_headers=parsed_headers,
            max_attempts=max_attempts,
        )


class NotificationService:
    """Enqueue and deliver persisted notifications after tracking commits."""

    def __init__(
        self,
        database: Database,
        *,
        channels: Mapping[str, NotificationChannel] | None = None,
        destinations: Mapping[str, Iterable[str]] | None = None,
        settings: NotificationSettings | None = None,
    ) -> None:
        self.database = database
        self.worker_id = os.getenv(
            "WALLAPOP_WORKER_ID", f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )
        self.lease_seconds = max(1, int(os.getenv("WALLAPOP_NOTIFICATION_LEASE_SECONDS", "300")))
        self.settings = settings or NotificationSettings.from_env()
        if channels is None:
            if self.settings.webhook_enabled and not self.settings.webhook_url:
                raise ValueError(
                    "Webhook notifications enabled but WALLAPOP_WEBHOOK_URL is missing"
                )
            if self.settings.discord_enabled and not self.settings.discord_webhook_url:
                raise ValueError(
                    "Discord notifications enabled but WALLAPOP_DISCORD_WEBHOOK_URL is missing"
                )
            if self.settings.telegram_enabled and not (
                self.settings.telegram_bot_token and self.settings.telegram_chat_id
            ):
                raise ValueError("Telegram notifications enabled but token or chat ID is missing")
            configured: dict[str, NotificationChannel] = {}
            configured_destinations: dict[str, tuple[str, ...]] = {}
            if self.settings.webhook_enabled:
                assert self.settings.webhook_url is not None
                configured["webhook"] = WebhookNotificationChannel(
                    timeout=self.settings.timeout, headers=self.settings.webhook_headers
                )
                configured_destinations["webhook"] = (self.settings.webhook_url,)
            if self.settings.discord_enabled:
                assert self.settings.discord_webhook_url is not None
                configured["discord"] = DiscordWebhookChannel(timeout=self.settings.timeout)
                configured_destinations["discord"] = (self.settings.discord_webhook_url,)
            if self.settings.telegram_enabled or self.settings.telegram_bot_token:
                assert self.settings.telegram_bot_token is not None
                configured["telegram"] = TelegramNotificationChannel(
                    self.settings.telegram_bot_token, timeout=self.settings.timeout
                )
                if self.settings.telegram_chat_id:
                    configured_destinations["telegram"] = (self.settings.telegram_chat_id,)
            self.channels = configured
            self.destinations = configured_destinations
        else:
            self.channels = dict(channels)
            self.destinations = {
                channel: tuple(values) for channel, values in (destinations or {}).items()
            }

    def enqueue_event(self, event_id: int) -> list[NotificationDeliveryRecord]:
        with self.database.transaction() as session:
            event = session.get(TrackingEventRecord, event_id)
            if event is None:
                raise ValueError(f"Tracking event not found: {event_id}")
            repository = NotificationDeliveryRepository(session)
            destinations: dict[str, tuple[str, ...]] = dict(self.destinations)
            owner_ids = (
                TelegramOwnershipRepository(session).owner_chat_ids(event.tracked_search_id)
                if event.tracked_search_id is not None
                else []
            )
            if owner_ids and "telegram" in self.channels:
                destinations["telegram"] = tuple(
                    dict.fromkeys(
                        (*destinations.get("telegram", ()), *(str(value) for value in owner_ids))
                    )
                )
            return [
                repository.create_once(
                    event_id=event.id,
                    channel=channel,
                    destination=destination,
                    created_at=event.created_at,
                )[0]
                for channel, channel_destinations in destinations.items()
                if (channel_obj := self.channels.get(channel)) is not None
                for destination in channel_destinations
                if channel_obj is not None
            ]

    def enqueue_events(self, event_ids: Iterable[int]) -> int:
        return sum(len(self.enqueue_event(event_id)) for event_id in event_ids)

    async def deliver_pending(self) -> int:
        delivered = 0
        while True:
            delivery_id = self._next_delivery_id()
            if delivery_id is None:
                return delivered
            if await self._deliver_one(delivery_id):
                delivered += 1

    async def retry_failed(self) -> int:
        delivered = 0
        while True:
            delivery_id = self._next_delivery_id(include_failed=True)
            if delivery_id is None:
                return delivered
            if await self._deliver_one(delivery_id):
                delivered += 1

    def list_deliveries(self) -> list[NotificationDeliveryRecord]:
        with self.database.session() as session:
            return cast(
                list[NotificationDeliveryRecord],
                NotificationDeliveryRepository(session).list_all(),
            )

    def _next_delivery_id(self, *, include_failed: bool = False) -> int | None:
        with self.database.transaction() as session:
            record = NotificationDeliveryRepository(session).claim_next(
                now=datetime.now(UTC),
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
                max_attempts=self.settings.max_attempts,
                include_failed=include_failed,
                ignore_backoff=include_failed,
            )
            return record.id if record is not None else None

    async def _deliver_one(self, delivery_id: int) -> bool:
        metrics = get_metrics()
        with self.database.transaction() as session:
            delivery = session.get(NotificationDeliveryRecord, delivery_id)
            if delivery is None or delivery.status == NotificationDeliveryStatus.DELIVERED:
                return False
            if delivery.claimed_by != self.worker_id:
                return False
            delivery.attempts += 1
            delivery.updated_at = datetime.now(UTC)
            channel = self.channels.get(delivery.channel)
            event = session.get(TrackingEventRecord, delivery.event_id)
            if channel is None or event is None:
                delivery.status = NotificationDeliveryStatus.FAILED
                delivery.last_error = "Channel or event is not configured"
                metrics.notification_deliveries_total.labels(delivery.channel, "failed").inc()
                metrics.notification_failures_total.labels(delivery.channel).inc()
                return False
            notification = self._notification(session, event)
            destination = delivery.destination

        logger.info(
            "notification_delivery_started delivery_id=%s event_id=%s channel=%s attempts=%s",
            delivery_id,
            event.id,
            delivery.channel,
            delivery.attempts,
        )
        metrics.notification_deliveries_total.labels(delivery.channel, "attempted").inc()
        try:
            result = await channel.send(notification, destination)
        except Exception as exc:
            result = DeliveryResult(False, retryable=True, error=type(exc).__name__)
        with self.database.transaction() as session:
            delivery = session.get(NotificationDeliveryRecord, delivery_id)
            if delivery is None:
                return False
            delivery.updated_at = datetime.now(UTC)
            if result.delivered:
                delivery.status = NotificationDeliveryStatus.DELIVERED
                delivery.delivered_at = datetime.now(UTC)
                delivery.last_error = None
                delivery.next_attempt_at = None
                delivery.claimed_by = None
                delivery.claim_expires_at = None
                logger.info(
                    "notification_delivery_succeeded delivery_id=%s event_id=%s "
                    "channel=%s attempts=%s",
                    delivery_id,
                    delivery.event_id,
                    delivery.channel,
                    delivery.attempts,
                )
                metrics.notification_deliveries_total.labels(delivery.channel, "delivered").inc()
                return True
            delivery.status = NotificationDeliveryStatus.FAILED
            delivery.last_error = result.error or "notification channel failed"
            delivery.next_attempt_at = datetime.now(UTC) + self._retry_delay(delivery.attempts)
            delivery.claimed_by = None
            delivery.claim_expires_at = None
            logger.warning(
                "notification_delivery_failed delivery_id=%s event_id=%s channel=%s attempts=%s",
                delivery_id,
                delivery.event_id,
                delivery.channel,
                delivery.attempts,
            )
            metrics.notification_deliveries_total.labels(delivery.channel, "failed").inc()
            metrics.notification_failures_total.labels(delivery.channel).inc()
            return False

    @staticmethod
    def _retry_delay(attempt: int) -> Any:
        return timedelta(seconds=min(1800, 30 * (2 ** max(0, attempt - 1))))

    @staticmethod
    def _notification(session: Any, event: TrackingEventRecord) -> Notification:
        snapshot = session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == event.listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )
        listing_id = (
            event.listing.external_id or event.listing.wallapop_item_id
            if event.listing is not None
            else str(event.listing_id)
        )
        details = None
        if event.metadata_json and event.event_type in {
            AlertType.TARGET_PRICE_REACHED.value,
            AlertType.PERCENTAGE_DROP.value,
            AlertType.NEW_30D_LOW.value,
            AlertType.NEW_90D_LOW.value,
            AlertType.NEW_ALL_TIME_LOW.value,
            AlertType.DEAL_SCORE_THRESHOLD.value,
        }:
            metadata = json.loads(event.metadata_json)
            details = "Advanced alert: " + ", ".join(
                f"{key}={value}" for key, value in metadata.items()
            )
        if event.event_type == AlertType.POSSIBLE_RELISTING.value:
            candidate = session.scalar(
                select(PossibleRelistingRecord).where(PossibleRelistingRecord.event_id == event.id)
            )
            if candidate is not None:
                previous_snapshot = session.scalar(
                    select(ListingSnapshotRecord)
                    .where(ListingSnapshotRecord.listing_id == candidate.previous_listing_id)
                    .order_by(
                        ListingSnapshotRecord.observed_at.desc(),
                        ListingSnapshotRecord.id.desc(),
                    )
                    .limit(1)
                )
                reasons = json.loads(candidate.reasons_json)
                details = "Possible relisting\n"
                previous_title = (
                    previous_snapshot.title
                    if previous_snapshot is not None and previous_snapshot.title
                    else str(candidate.previous_listing_id)
                )
                current_title = (
                    snapshot.title
                    if snapshot is not None and snapshot.title
                    else str(candidate.current_listing_id)
                )
                previous_price = (
                    f"{previous_snapshot.price:.2f} €"
                    if previous_snapshot is not None and previous_snapshot.price is not None
                    else "?"
                )
                current_price = f"{event.new_price:.2f} €" if event.new_price is not None else "?"
                details += f"Previous: {previous_title} — {previous_price}\n"
                details += f"New: {current_title} — {current_price}\n"
                details += f"Confidence: {float(candidate.score):.0%}\nReasons: "
                details += ", ".join(str(reason["name"]) for reason in reasons)
        return Notification(
            event_id=event.id,
            event_type=AlertType(event.event_type),
            listing_id=listing_id or str(event.listing_id),
            title=snapshot.title if snapshot is not None else None,
            url=snapshot.url if snapshot is not None else None,
            old_price=event.old_price,
            new_price=event.new_price,
            created_at=event.created_at,
            details=details,
        )
