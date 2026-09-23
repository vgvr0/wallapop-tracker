"""Runtime explanations for the filters of a persisted tracked search.

The domain stays pure: :class:`~wallapop_tracker.domain.filters.FilterEngine`
knows nothing about storage. This small service is the orchestration needed to
answer "why did this stored listing match (or fail) this stored search" from
the CLI and the API, and it is also the only place that knows which listing
fields a snapshot cannot reconstruct: those filters are reported as UNKNOWN
instead of a false failure. Nothing is persisted: an explanation is a runtime
diagnostic and no schema change is involved.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallapop_tracker.domain.filters import (
    FilterEvaluation,
    FilterTrace,
    filters_from_config,
)
from wallapop_tracker.domain.marketplace import Marketplace
from wallapop_tracker.models import Listing, ListingSaleStatus
from wallapop_tracker.storage.models import (
    ListingRecord,
    ListingSnapshotRecord,
    TrackedSearchRecord,
)
from wallapop_tracker.storage.repositories import TrackedSearchRepository

# Traces whose inputs ``listing_snapshots`` does not persist. Rebuilding the
# domain listing leaves those fields empty, and an empty field must never be
# read as "the listing does not satisfy the filter": the condition is simply
# not evaluable from stored data.
_UNRECONSTRUCTABLE_INPUTS: dict[str, str] = {
    "model": "model is not persisted in listing snapshots",
    "distance": "distance cannot be evaluated because coordinates are not persisted",
}


@dataclass(frozen=True)
class SearchListingExplanation:
    """Explanation of one stored listing against one stored search."""

    search_id: int
    listing_id: int
    evaluation: FilterEvaluation
    warnings: tuple[str, ...] = ()

    @property
    def matched(self) -> bool | None:
        """``True`` PASS, ``False`` FAIL, ``None`` when data is missing."""

        return self.evaluation.matched

    @property
    def complete(self) -> bool:
        return self.evaluation.complete


def search_filter_config(search: TrackedSearchRecord) -> dict[str, Any]:
    """Effective filter configuration of a persisted search.

    Mirrors :class:`~wallapop_tracker.services.search_tracker.SearchTracker`:
    the price columns win over the stored JSON, so an explanation evaluates the
    exact configuration that the tracking run used.
    """

    config: dict[str, Any] = {}
    if search.filters_json:
        value = json.loads(search.filters_json)
        if not isinstance(value, dict):
            raise ValueError("stored search filters are not a JSON object")
        config.update(value)
    config["min_price"] = str(search.min_price) if search.min_price is not None else None
    config["max_price"] = str(search.max_price) if search.max_price is not None else None
    return config


def unavailable_input_traces(traces: Iterable[FilterTrace]) -> tuple[FilterTrace, ...]:
    """Mark as UNKNOWN the traces whose inputs storage cannot reconstruct.

    Listing snapshots keep price, category, condition, brand and the text
    fields, which is everything the remaining filters read. ``model`` and the
    coordinates of a distance filter have no column, so their traces become
    ``passed=None`` instead of a verdict about the original listing.
    """

    marked: list[FilterTrace] = []
    for trace in traces:
        reason = _UNRECONSTRUCTABLE_INPUTS.get(trace.filter_name)
        if reason is None:
            marked.append(trace)
            continue
        marked.append(
            FilterTrace(
                filter_name=trace.filter_name,
                passed=None,
                expected_value=trace.expected_value,
                reason=reason,
            )
        )
    return tuple(marked)


def evaluation_warnings(evaluation: FilterEvaluation) -> tuple[str, ...]:
    """Short summary of the conditions that could not be evaluated."""

    return tuple(
        f"{trace.filter_name} could not be evaluated from persisted data"
        for trace in evaluation.traces
        if trace.passed is None
    )


def latest_snapshot(session: Session, listing_id: int) -> ListingSnapshotRecord | None:
    """Most recent stored snapshot of a listing, the data filters ran against."""

    return session.scalar(
        select(ListingSnapshotRecord)
        .where(ListingSnapshotRecord.listing_id == listing_id)
        .order_by(ListingSnapshotRecord.observed_at.desc(), ListingSnapshotRecord.id.desc())
        .limit(1)
    )


def listing_from_snapshot(record: ListingRecord, snapshot: ListingSnapshotRecord | None) -> Listing:
    """Rebuild the domain listing a stored match refers to.

    Only the fields a filter can read are reconstructed; media and attribute
    payloads are skipped because no filter looks at them.
    """

    return Listing(
        item_id=record.external_id or record.wallapop_item_id or str(record.id),
        marketplace=Marketplace(record.marketplace),
        user_id=record.seller_user_id or "",
        title=snapshot.title if snapshot is not None else None,
        description=snapshot.description if snapshot is not None else None,
        price=snapshot.price if snapshot is not None else None,
        currency=snapshot.currency if snapshot is not None else None,
        category_id=snapshot.category_id if snapshot is not None else None,
        category_name=snapshot.category_name if snapshot is not None else None,
        status=snapshot.status if snapshot is not None else None,
        reserved=snapshot.reserved if snapshot is not None else None,
        sale_status=snapshot.sale_status if snapshot is not None else ListingSaleStatus.UNKNOWN,
        shipping_available=snapshot.shipping_available if snapshot is not None else None,
        seller_allows_shipping=snapshot.seller_allows_shipping if snapshot is not None else None,
        condition=snapshot.condition if snapshot is not None else None,
        condition_code=snapshot.condition_code if snapshot is not None else None,
        condition_label=snapshot.condition_label if snapshot is not None else None,
        brand=snapshot.brand if snapshot is not None else None,
        has_warranty=snapshot.has_warranty if snapshot is not None else None,
        is_refurbished=snapshot.is_refurbished if snapshot is not None else None,
        url=snapshot.url if snapshot is not None else None,
        image_url=snapshot.image_url if snapshot is not None else None,
        created_at=snapshot.created_at_source if snapshot is not None else None,
        modified_at=snapshot.modified_at_source if snapshot is not None else None,
    )


def explain_search_listing(
    session: Session, search_id: int, listing_id: int
) -> SearchListingExplanation:
    """Evaluate a stored listing against a stored search and explain every filter.

    The listing does not need to be a stored match of the search: explaining a
    rejection is the point of the feature. Filters whose inputs snapshots do
    not persist are reported as UNKNOWN, so ``matched`` may be ``None`` and
    ``complete`` may be ``False``. Raises :class:`ValueError` for an unknown
    search or listing.
    """

    search = TrackedSearchRepository(session).get(search_id)
    if search is None:
        raise ValueError(f"Unknown search: {search_id}")
    record = session.get(ListingRecord, listing_id)
    if record is None:
        raise ValueError(f"Unknown listing: {listing_id}")
    config = search_filter_config(search)
    rebuilt = filters_from_config(config).evaluate(
        listing_from_snapshot(record, latest_snapshot(session, listing_id))
    )
    evaluation = FilterEvaluation.from_traces(unavailable_input_traces(rebuilt.traces))
    return SearchListingExplanation(
        search_id=search_id,
        listing_id=listing_id,
        evaluation=evaluation,
        warnings=evaluation_warnings(evaluation),
    )
