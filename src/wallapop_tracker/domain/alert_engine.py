"""Rule evaluation and channel-independent alert orchestration."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from wallapop_tracker.services.notifications import NotificationService
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import (
    AlertRuleRecord,
    NotificationDeliveryRecord,
    TrackingEventRecord,
)


class AlertEngine:
    def __init__(
        self, database: Database, notification_service: NotificationService | None = None
    ) -> None:
        self.database = database
        self.notifications = notification_service or NotificationService(database)

    def process_event(self, event_id: int) -> int:
        """Create at most one durable delivery per event/rule/channel/destination."""
        now = datetime.now(UTC)
        created = 0
        with self.database.transaction() as session:
            event = session.get(TrackingEventRecord, event_id)
            if event is None:
                raise ValueError(f"Tracking event not found: {event_id}")
            rules = list(
                session.scalars(
                    select(AlertRuleRecord).where(
                        AlertRuleRecord.enabled, AlertRuleRecord.event_type == event.event_type
                    )
                )
            )
            for rule in rules:
                if not self._matches(rule, event):
                    continue
                if rule.cooldown_seconds and session.scalar(
                    select(NotificationDeliveryRecord).where(
                        NotificationDeliveryRecord.channel == rule.channel,
                        NotificationDeliveryRecord.status != "skipped",
                        NotificationDeliveryRecord.created_at
                        >= now - timedelta(seconds=rule.cooldown_seconds),
                    )
                ):
                    continue
                existing = session.scalar(
                    select(NotificationDeliveryRecord).where(
                        NotificationDeliveryRecord.event_id == event.id,
                        NotificationDeliveryRecord.channel == rule.channel,
                        NotificationDeliveryRecord.destination == rule.destination,
                    )
                )
                if existing is None:
                    session.add(
                        NotificationDeliveryRecord(
                            event_id=event.id,
                            channel=rule.channel,
                            destination=rule.destination,
                            created_at=event.created_at,
                            updated_at=event.created_at,
                        )
                    )
                    created += 1
        return created

    @staticmethod
    def _matches(rule: AlertRuleRecord, event: TrackingEventRecord) -> bool:
        try:
            filters: dict[str, Any] = json.loads(rule.filters_json or "{}")
        except json.JSONDecodeError:
            return False
        for key in ("search_id", "profile_id", "listing_id"):
            expected = filters.get(key)
            actual = (
                getattr(event, "tracked_search_id", None)
                if key == "search_id"
                else getattr(event, "listing_id", None)
                if key == "listing_id"
                else getattr(event.tracking_run, "profile_id", None)
            )
            if expected is not None and str(expected) != str(actual):
                return False
        if filters.get("maximum_price") is not None and (
            event.new_price is None or event.new_price > Decimal(str(filters["maximum_price"]))
        ):
            return False
        if filters.get("minimum_price_drop_percent") is not None:
            if event.old_price is None or event.new_price is None or event.old_price <= 0:
                return False
            drop = (event.old_price - event.new_price) * 100 / event.old_price
            if drop < Decimal(str(filters["minimum_price_drop_percent"])):
                return False
        return True
