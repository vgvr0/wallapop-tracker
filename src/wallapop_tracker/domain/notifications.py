"""Notification value objects and channel contract."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from .alerts import AlertType


@dataclass(frozen=True)
class Notification:
    """Data needed by a channel to render one tracking event."""

    event_id: int
    event_type: AlertType
    listing_id: str
    title: str | None
    url: str | None
    old_price: Decimal | None
    new_price: Decimal | None
    created_at: datetime
    details: str | None = None


@dataclass(frozen=True)
class DeliveryResult:
    """Outcome returned by a channel without exposing transport details."""

    delivered: bool
    retryable: bool = False
    error: str | None = None


class NotificationChannel(Protocol):
    async def send(self, notification: Notification, destination: str) -> DeliveryResult:
        """Send one notification to one configured destination."""
