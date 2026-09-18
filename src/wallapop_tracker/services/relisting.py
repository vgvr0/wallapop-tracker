"""Deterministic, explainable detection of possible listing republications."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.alerts import AlertType
from wallapop_tracker.domain.relisting import RelistingCandidate, RelistingReason
from wallapop_tracker.models import Listing
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    PossibleRelistingRecord,
    PresenceState,
)
from wallapop_tracker.storage.repositories import (
    PossibleRelistingRepository,
    TrackingEventRepository,
)


@dataclass(frozen=True)
class RelistingPolicy:
    threshold: float = 0.75
    window: timedelta = timedelta(days=30)


@dataclass(frozen=True)
class RelistingDetection:
    candidate: RelistingCandidate
    record: PossibleRelistingRecord
    event_id: int


class RelistingDetectionService:
    """Find candidate pairs without asserting that they are the same item."""

    def __init__(self, session: Session, policy: RelistingPolicy | None = None) -> None:
        self.session = session
        self.policy = policy or RelistingPolicy()

    def detect_new_listing(
        self,
        current: ListingRecord,
        listing: Listing,
        *,
        tracking_run_id: int,
        detected_at: datetime,
    ) -> RelistingDetection | None:
        seller = current.seller_user_id
        if not seller:
            return None
        cutoff = detected_at - self.policy.window
        candidates = self.session.scalars(
            select(ListingRecord)
            .where(
                ListingRecord.id != current.id,
                ListingRecord.seller_user_id == seller,
                ListingRecord.last_seen_at >= cutoff,
            )
            .order_by(ListingRecord.last_seen_at.desc(), ListingRecord.id.desc())
        ).all()
        best: tuple[float, ListingRecord, tuple[RelistingReason, ...]] | None = None
        for previous in candidates:
            snapshot = self._latest_snapshot(previous.id)
            if snapshot is None or snapshot.presence_state != PresenceState.REMOVED:
                continue
            scored = self._score(previous, snapshot, listing)
            if scored is None:
                continue
            score, reasons = scored
            if score >= self.policy.threshold and (
                best is None or score > best[0]
            ):
                best = (score, previous, reasons)
        if best is None:
            return None

        score, previous, reasons = best
        candidate = RelistingCandidate(
            previous_listing_id=previous.id,
            current_listing_id=current.id,
            score=score,
            reasons=reasons,
        )
        reasons_json = json.dumps(
            [
                {
                    "name": reason.name,
                    "value": reason.value,
                    "contribution": reason.contribution,
                }
                for reason in reasons
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        record, _ = PossibleRelistingRepository(self.session).create_once(
            previous_listing_id=previous.id,
            current_listing_id=current.id,
            score=Decimal(str(round(score, 4))),
            reasons_json=reasons_json,
            detected_at=detected_at,
        )
        event_key = f"possible-relisting:{previous.id}:{current.id}"
        event, _ = TrackingEventRepository(self.session).create_once(
            event_type=AlertType.POSSIBLE_RELISTING.value,
            idempotency_key=event_key,
            listing_id=current.id,
            tracking_run_id=tracking_run_id,
            tracked_search_id=None,
            old_price=self._latest_price(previous.id),
            new_price=listing.price,
            created_at=detected_at,
        )
        record.event_id = event.id
        self.session.flush()
        return RelistingDetection(candidate, record, event.id)

    def _latest_snapshot(self, listing_id: int) -> ListingSnapshotRecord | None:
        return self.session.scalar(
            select(ListingSnapshotRecord)
            .where(ListingSnapshotRecord.listing_id == listing_id)
            .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
            .limit(1)
        )

    def _latest_price(self, listing_id: int) -> Decimal | None:
        snapshot = self._latest_snapshot(listing_id)
        return snapshot.price if snapshot is not None else None

    def _score(
        self,
        previous: ListingRecord,
        snapshot: ListingSnapshotRecord,
        current: Listing,
    ) -> tuple[float, tuple[RelistingReason, ...]] | None:
        signals: list[tuple[float, RelistingReason]] = []
        signals.append((0.35, RelistingReason("same_seller", True, 0.35)))
        title = self._title_similarity(snapshot.title, current.title)
        if title is not None:
            if title < 0.65:
                return None
            signals.append((0.35, RelistingReason("title_similarity", title, 0.35 * title)))
        price = self._price_similarity(snapshot.price, current.price)
        if price is not None:
            delta, similarity = price
            if similarity <= 0:
                return None
            signals.append(
                (0.15, RelistingReason("price_delta", delta, 0.15 * similarity))
            )
        category = self._same_text(snapshot.category_id, current.category_id)
        if category is not None:
            signals.append((0.10, RelistingReason("same_category", category, 0.10)))
        brand = self._same_text(snapshot.brand, current.brand)
        if brand is not None:
            signals.append((0.05, RelistingReason("same_brand", brand, 0.05 if brand else 0.0)))
        total_weight = sum(weight for weight, _ in signals)
        score = sum(reason.contribution for _, reason in signals) / total_weight
        return score, tuple(reason for _, reason in signals)

    @staticmethod
    def _title_similarity(first: str | None, second: str | None) -> float | None:
        first_tokens = RelistingDetectionService._tokens(first)
        second_tokens = RelistingDetectionService._tokens(second)
        if not first_tokens or not second_tokens:
            return None
        first_text, second_text = " ".join(first_tokens), " ".join(second_tokens)
        sequence = SequenceMatcher(None, first_text, second_text).ratio()
        union = set(first_tokens) | set(second_tokens)
        jaccard = len(set(first_tokens) & set(second_tokens)) / len(union)
        return round(0.6 * sequence + 0.4 * jaccard, 4)

    @staticmethod
    def _tokens(value: str | None) -> list[str]:
        if not value:
            return []
        normalized = unicodedata.normalize("NFKD", value.lower())
        normalized = "".join(char for char in normalized if not unicodedata.combining(char))
        return re.findall(r"[a-z0-9]+", normalized)

    @staticmethod
    def _price_similarity(
        previous: Decimal | None, current: Decimal | None
    ) -> tuple[float, float] | None:
        if previous is None or current is None or previous <= 0:
            return None
        delta = float(abs(current - previous) / previous)
        return round(delta, 4), max(0.0, 1.0 - delta / 0.5)

    @staticmethod
    def _same_text(first: str | None, second: str | None) -> bool | None:
        if not first or not second:
            return None
        return RelistingDetectionService._tokens(first) == RelistingDetectionService._tokens(
            second
        )
