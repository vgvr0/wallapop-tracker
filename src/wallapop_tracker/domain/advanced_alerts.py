"""Pure-ish, SQL-backed advanced price alert detection."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.storage.models import ListingSnapshotRecord

EVENTS = (
    "TARGET_PRICE_REACHED",
    "PERCENTAGE_DROP",
    "NEW_30D_LOW",
    "NEW_90D_LOW",
    "NEW_ALL_TIME_LOW",
)


def detect_price_alerts(
    session: Session,
    *,
    listing_id: int,
    run_id: int,
    tracked_search_id: int | None,
    previous: ListingSnapshotRecord | None,
    current: ListingSnapshotRecord,
    config: Any,
) -> list[dict[str, Any]]:
    old, price = (previous.price if previous else None), current.price
    if price is None:
        return []
    rows: list[dict[str, Any]] = []

    def add(kind: str, data: dict[str, Any]) -> None:
        key = json.dumps(
            {
                "event": kind,
                "listing": listing_id,
                "previous": previous.id if previous else None,
                "current": current.id,
                "threshold": data.get("threshold"),
            },
            sort_keys=True,
            default=str,
        )
        rows.append({"event_type": kind, "key": key, "metadata": data})

    if old is not None and config.target_price is not None and old > config.target_price >= price:
        add(
            "TARGET_PRICE_REACHED",
            {"previous_price": old, "current_price": price, "threshold": config.target_price},
        )
    if old is not None and old > 0 and price < old and config.percentage_drop_threshold is not None:
        drop = (old - price) / old * 100
        if drop >= config.percentage_drop_threshold:
            add(
                "PERCENTAGE_DROP",
                {
                    "previous_price": old,
                    "current_price": price,
                    "drop_percentage": drop,
                    "threshold": config.percentage_drop_threshold,
                },
            )
    for days, enabled, kind in (
        (30, config.notify_on_30d_low, "NEW_30D_LOW"),
        (90, config.notify_on_90d_low, "NEW_90D_LOW"),
    ):
        if enabled:
            cutoff = current.observed_at - timedelta(days=days)
            minimum = session.scalar(
                select(__import__("sqlalchemy").func.min(ListingSnapshotRecord.price)).where(
                    ListingSnapshotRecord.listing_id == listing_id,
                    ListingSnapshotRecord.observed_at < current.observed_at,
                    ListingSnapshotRecord.observed_at >= cutoff,
                    ListingSnapshotRecord.price.is_not(None),
                )
            )
            if minimum is not None and price < minimum:
                add(kind, {"previous_min": minimum, "current_price": price, "window_days": days})
    if config.notify_on_all_time_low:
        minimum = session.scalar(
            select(__import__("sqlalchemy").func.min(ListingSnapshotRecord.price)).where(
                ListingSnapshotRecord.listing_id == listing_id,
                ListingSnapshotRecord.observed_at < current.observed_at,
                ListingSnapshotRecord.price.is_not(None),
            )
        )
        if minimum is not None and price < minimum:
            add("NEW_ALL_TIME_LOW", {"previous_min": minimum, "current_price": price})
    return rows
