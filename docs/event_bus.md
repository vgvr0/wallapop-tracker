# PostgreSQL event bus and DLQ

Tracking remains the source of truth for listings, snapshots, searches, profiles and persisted
analytics. `domain_events` is an append-only transactional outbox used for integration, audit and
controlled asynchronous work; this is not event sourcing.

```text
Tracker
  ↓ one database transaction
  ├── domain state
  └── domain_events (idempotent outbox)
        ↓ independent consumer leases
  ├── notifications
  ├── analytics (read-only/replayable)
  └── future consumers
        ↓ bounded exponential backoff
      event_consumptions → dead_letters
```

## Guarantees

Events are JSON-serializable and ordered by `(created_at, id)`. Every event has a caller-supplied
idempotency key with a unique constraint. Existing `TrackingEventRepository.create_once` publishes
the corresponding domain event before its caller commits, so a successful domain commit cannot lose
the outbox row. A duplicate snapshot or retry returns the original event.

Each consumer has independent state. PostgreSQL consumers claim with `FOR UPDATE SKIP LOCKED`; the
SQLite fallback uses the same lease predicates without claiming distributed execution as strongly.
Leases expire after the configured duration and can be reclaimed after a worker crash. Delivery is
at-least-once, with consumer-side idempotency required for external effects. Notifications reuse
the existing `(event, channel, destination)` uniqueness constraint.

Failures use exponential backoff and move to the persistent DLQ after `max_attempts`. `dlq retry`
resets only the consumer state and marks the original DLQ record as requeued; it never deletes the
failure audit. Replay is explicit and read-only analytics replay is allowed without a flag. Consumers
with side effects require `--allow-side-effects`.

Correlation IDs connect a causal chain and `causation_id` points to the immediate parent when known.
Logs and Prometheus metrics use bounded labels only; IDs are fields, never labels. Secrets and
notification destinations are redacted by the existing logging layer.

## Operations

```bash
wallapop-track events list --consumer notifications
wallapop-track events show 42
wallapop-track worker events --consumer notifications
wallapop-track events consume --consumer notifications --once
wallapop-track dlq list
wallapop-track dlq show 7
wallapop-track dlq retry 7
wallapop-track events replay --consumer analytics --from-id 1000 --to-id 2000
```

The API exposes read-only `/api/v1/events`, `/api/v1/events/{id}`, `/api/v1/dlq` and
`/api/v1/dlq/{id}`. Legacy tracking-event inspection remains available under
`/api/v1/tracking-events`.
