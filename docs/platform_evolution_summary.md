# Platform evolution summary

## 1. Starting point

The project began as a synchronous-looking Wallapop profile tracker with
profile-oriented persistence, direct client calls, in-memory alert behavior
and listings identified by Wallapop-specific names. It evolved incrementally;
historical data and read-only external behavior remained priorities.

## 2. Final architecture

```text
Wallapop client/adapters
  ├── ProfileTracker
  ├── SearchTracker
  └── TrackedListingTracker
          ↓
SQLAlchemy + Alembic persistence
  snapshots / presence / TrackingRun / TrackingEvent
          ├── NotificationDelivery
          ├── market analytics
          ├── deterministic scoring
          └── relisting detection

TrackingScheduler ── bounded jobs ──┘
FastAPI ── local/private read and management transport
CLI ────── local operator transport
```

Search and listing behavior use small provider contracts. Only Wallapop
adapters are implemented.

## 3. Completed phases

Phases 0–16 are complete: audit, event modeling, search identity separation,
provider abstraction, persistent notification delivery, tracked listings,
analytics, scoring, relisting detection, API, concurrency, observability,
multi-marketplace preparation and final cleanup.

## 4. Key decisions

- A `TrackingRun` has exactly one source.
- Search runs do not create synthetic profiles.
- `TrackingEvent` persistence is separate from notification delivery.
- Delivery is idempotent and retry-bounded per channel/destination.
- Marketplace-ready listing identity is `(marketplace, external_id)`.
- `TrackingRun` derives marketplace from its source rather than duplicating it.
- Wallapop URLs, metadata and HTTP details remain inside the Wallapop adapter.

## 5. Data model and reliability

Valid runs establish snapshots and presence; partial and failed runs do not
overwrite valid history. Events use database uniqueness for idempotency.
Notification HTTP calls happen after tracking persistence and cannot invalidate
the run. Scheduler jobs have bounded concurrency and isolated sessions.

Alembic revisions `0001` through `0013` preserve historical IDs, snapshots,
events, matches and relisting candidates. Revision `0013` adds marketplace
identity and keeps `wallapop_item_id` only as a documented compatibility
bridge.

## 6. Analytics and scoring

Reporting is read-only and search-scoped. It derives inventory, price,
presence, activity, seller, brand and category metrics from valid history.
Deal scoring is deterministic, explainable and contextual to a listing/search
pair. Relisting detection is heuristic and never merges listing identities.

## 7. API and observability

FastAPI exposes local/private `/api/v1` resources plus `/health`, `/ready` and
Prometheus-compatible `/metrics`. It has no authentication and does not call
Wallapop for read queries. Structured logs redact secrets and metrics avoid
destination/token labels.

## 8. Multi-marketplace preparation

`Marketplace.WALLAPOP` is the only implemented value. `SearchProvider` and
`ListingProvider` remain small contracts, ready for future adapters such as
`VintedSearchProvider`, `VintedListingProvider` and `VintedProfileProvider`.
No Vinted, eBay, Milanuncios or cross-market matching is implemented.

## 9. Conscious technical debt

- Legacy saved-search, price-watch and `alert_delivered` columns remain for
  compatibility and are deprecated.
- `wallapop_item_id` remains as a physical migration bridge.
- Profile identity is still `wallapop_user_id`.
- External Wallapop endpoints are undocumented and may change.
- There is no auth, frontend, distributed queue or distributed scheduler lock.

## 10. Possible next steps

Possible future work is deliberately outside the completed roadmap: a second
marketplace adapter, private frontend, authentication, score calibration,
image hashing and distributed scheduler locking.
