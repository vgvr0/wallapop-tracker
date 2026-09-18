"""Persistent notification delivery service and HTTP channel adapters."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

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
from wallapop_tracker.storage.repositories import NotificationDeliveryRepository

logger = logging.getLogger(__name__)


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
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.http_client = http_client

    def payload(self, notification: Notification) -> dict[str, Any]:
        return _notification_payload(notification)

    async def send(self, notification: Notification, destination: str) -> DeliveryResult:
        try:
            if self.http_client is not None:
                response = await self.http_client.post(
                    destination, json=self.payload(notification), timeout=self.timeout
                )
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(destination, json=self.payload(notification))
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
        title = notification.title or notification.listing_id
        text = f"{notification.event_type.value.replace('_', ' ').title()}\n{title}"
        if notification.old_price is not None and notification.new_price is not None:
            text += f"\n{_price(notification.old_price)} € → {_price(notification.new_price)} €"
        if notification.url:
            text += f"\n{notification.url}"
        if notification.details:
            text += f"\n{notification.details}"
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": destination, "text": text}
        try:
            if self.http_client is not None:
                response = await self.http_client.post(url, json=payload, timeout=self.timeout)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(url, json=payload)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return DeliveryResult(False, True, type(exc).__name__)
        return _http_result(response)


def _http_result(response: httpx.Response) -> DeliveryResult:
    if 200 <= response.status_code < 300:
        return DeliveryResult(True)
    retryable = response.status_code == 429 or response.status_code >= 500
    return DeliveryResult(False, retryable, f"HTTP {response.status_code}")


@dataclass(frozen=True)
class NotificationSettings:
    webhook_url: str | None = None
    discord_webhook_url: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    max_attempts: int = 3

    @classmethod
    def from_env(cls) -> NotificationSettings:
        raw_attempts = os.getenv("WALLAPOP_NOTIFICATION_MAX_ATTEMPTS", "3")
        try:
            max_attempts = max(1, int(raw_attempts))
        except ValueError:
            max_attempts = 3
        return cls(
            webhook_url=os.getenv("WALLAPOP_WEBHOOK_URL"),
            discord_webhook_url=os.getenv("WALLAPOP_DISCORD_WEBHOOK_URL"),
            telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
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
        self.settings = settings or NotificationSettings.from_env()
        if channels is None:
            configured: dict[str, NotificationChannel] = {}
            configured_destinations: dict[str, tuple[str, ...]] = {}
            if self.settings.webhook_url:
                configured["webhook"] = WebhookNotificationChannel()
                configured_destinations["webhook"] = (self.settings.webhook_url,)
            if self.settings.discord_webhook_url:
                configured["discord"] = DiscordWebhookChannel()
                configured_destinations["discord"] = (self.settings.discord_webhook_url,)
            if self.settings.telegram_bot_token and self.settings.telegram_chat_id:
                configured["telegram"] = TelegramNotificationChannel(
                    self.settings.telegram_bot_token
                )
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
            return [
                repository.create_once(
                    event_id=event.id,
                    channel=channel,
                    destination=destination,
                    created_at=event.created_at,
                )[0]
                for channel, destinations in self.destinations.items()
                if (channel_obj := self.channels.get(channel)) is not None
                for destination in destinations
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
        with self.database.session() as session:
            record = NotificationDeliveryRepository(session).next_pending(
                self.settings.max_attempts, include_failed=include_failed
            )
            return record.id if record is not None else None

    async def _deliver_one(self, delivery_id: int) -> bool:
        metrics = get_metrics()
        with self.database.transaction() as session:
            delivery = session.get(NotificationDeliveryRecord, delivery_id)
            if delivery is None or delivery.status == NotificationDeliveryStatus.DELIVERED:
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
    def _notification(session: Any, event: TrackingEventRecord) -> Notification:
        snapshot = session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == event.listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )
        listing_id = (
            event.listing.wallapop_item_id if event.listing is not None else str(event.listing_id)
        )
        details = None
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
            listing_id=listing_id,
            title=snapshot.title if snapshot is not None else None,
            url=snapshot.url if snapshot is not None else None,
            old_price=event.old_price,
            new_price=event.new_price,
            created_at=event.created_at,
            details=details,
        )
