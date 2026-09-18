# Wallapop Tracker

Wallapop Tracker is a read-only tracker for authorized monitoring of public Wallapop profiles and searches. It stores historical snapshots, applies reusable filters, derives changes, and deduplicates global listing alerts across overlapping searches. It does not write to Wallapop.

## Key features

- Asynchronous HTTP client for public profile, statistics, review-summary, listing, and search data.
- Search tracking with configurable query, price range, filters, enable/disable state, and per-search interval.
- Pure filters for price, case-insensitive include ANY/ALL, exclusions, and regular expressions.
- Profile URL resolution through the public profile page or a locally recognizable canonical ID.
- Cursor-based listing pagination with duplicate and repeated-cursor protection.
- Historical SQLite/SQLAlchemy storage for profiles, listings, tracking runs, presence, and snapshots.
- Valid, partial, and failed tracking-run outcomes with component-level capture flags.
- Deterministic diffing for new, removed, and reappeared listings; prices; titles; reservation; shipping; brand; rating; review count; and sold count.
- Read-only reporting queries for current inventory, inventory history, price history, presence history, profile metrics, active duration, and weekly summaries.
- Internal alert services for new saved-search matches and listing price drops.
- Persistent global event idempotency: one new-listing or price-change alert per listing transition, even across overlapping searches and process restarts.
- Persistent notification deliveries with idempotent webhook, Discord, and Telegram channels.
- Direct listing monitoring with shared listing identity, snapshots, events, and notifications.
- Read-only metadata discovery for observed categories, filters, brands, and models.
- Conservative rate limiting, retries, `Retry-After` handling, and optional RAW response capture for contract investigation.
- Typer CLI for tracked-profile administration, manual runs, batch runs, and scheduling.
- Alembic migrations for the historical schema and alert-related tables.

## Architecture

```mermaid
flowchart LR
    CLI[wallapop-track CLI] --> Scheduler[TrackingScheduler]
    CLI --> Runner[ProfileTrackingRunner]
    Scheduler --> Runner
    Runner --> Tracker[ProfileTracker]
    Tracker --> Client[WallapopClient]
    Client --> API[Wallapop public/frontend API]
    Client --> Parsers[Profile, stats, reviews, item parsers]
    Parsers --> Tracker
    Tracker --> DB[(SQLAlchemy database)]
    DB --> Notifications[NotificationService and deliveries]
    Notifications --> Channels[Webhook / Discord / Telegram]
    CLI --> Listing[TrackedListing commands]
    Listing --> ListingTracker[TrackedListingTracker]
    ListingTracker --> DB
    DB --> Diff[DiffService]
    DB --> Reporting[Reporting queries and metrics]
    DB --> Alerts[Search and price alert services]
    Migrations[Alembic migrations] -. schema evolution .-> DB
```

The client performs extraction asynchronously. Profile and search trackers persist valid captures atomically. The shared scheduler selects due profiles and searches, while the event ledger makes global alerts idempotent.

```text
               ┌── Profile Tracker
Wallapop ──────┤
               └── Search Tracker
                        │
                     Filters
                        │
                     Storage
                        │
                       Diff
                        │
                  Deduplication
                        │
                      Alerts
```

## Project structure

```text
.
├── src/wallapop_tracker/
│   ├── client.py             # Async read-only HTTP client
│   ├── cli.py                # Typer application
│   ├── parsers/              # API response normalization
│   ├── services/             # Tracking, running, scheduling, diffing, alerts
│   ├── reporting/            # Historical queries and derived metrics
│   ├── storage/              # SQLAlchemy models, database, repositories
│   └── domain/               # Change and alert value objects
├── alembic/                  # Versioned schema migrations
├── tests/                    # Unit, contract, integration-style, and live tests
├── scripts/                  # Manual E2E and fixture validation utilities
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Installation

```bash
git clone <repository-url>
cd wallapop-tracker
python -m venv .venv
pip install -e ".[dev]"
```

The package requires Python 3.12 or newer. The default database URL is `sqlite:///data/wallapop_tracker.db`; the `data/` directory is created when the CLI initializes the database.

## CLI usage

The installed entry point is `wallapop-track`.

```bash
wallapop-track add https://es.wallapop.com/user/<profile> --alias seller
wallapop-track list
wallapop-track run seller
wallapop-track run-all
wallapop-track enable seller
wallapop-track disable seller
wallapop-track remove seller --yes
wallapop-track schedule --once
wallapop-track schedule --interval-hours 168
wallapop-track schedule --once --max-concurrency 4
wallapop-track search add --name "iphone barato" --query "iphone 15 pro" --max-price 650 --include 256gb --exclude roto
wallapop-track search add --name "iphone con avisos iniciales" --query "iphone 15 pro" --notify-on-first-run
wallapop-track search import "https://es.wallapop.com/app/search?keywords=iphone+15&min_sale_price=300&max_sale_price=650" --name "iPhone 15 barato" --notify-on-first-run
wallapop-track metadata categories
wallapop-track metadata filters --query iphone --category-id 24200
wallapop-track metadata brands --category-id 24200
wallapop-track metadata models --category-id 24200 --query iphone
wallapop-track search list
wallapop-track search show 1
wallapop-track search run 1
wallapop-track search run-all
wallapop-track search disable 1
wallapop-track search delete 1 --yes
wallapop-track notifications list
wallapop-track notifications retry
wallapop-track listing add https://es.wallapop.com/item/<slug>-<id> --alias camera
wallapop-track listing list
wallapop-track listing show camera
wallapop-track listing run camera
wallapop-track listing enable camera
wallapop-track listing disable camera
wallapop-track listing remove camera --yes
```

`add` accepts an optional `--notes` value and resolves/checks the profile before creating the tracked-profile record. Search creation is local and does not contact Wallapop; `search import` parses only semantic values present in a compatible Wallapop search URL and warns about unsupported parameters. New searches suppress `NEW_LISTING` on their first valid run; pass `--notify-on-first-run` to keep initial notifications enabled. `--include` and `--exclude` can be repeated, and `--include-all` changes inclusion from ANY to ALL. `remove` and `search delete` ask for confirmation unless `--yes` is supplied.

The scheduler also accepts `--poll-seconds` (default: `60`). Without `--once`, it keeps polling until interrupted. `schedule --once` evaluates due profiles and tracked searches once, then exits. Profiles use the scheduler interval; searches use their persisted `interval_seconds`.

Metadata commands are read-only and use only discovery endpoints validated by
RAW fixtures. Suggestions/autocomplete remain pending because the observed
endpoint did not return a reproducible public contract.

The scheduler runs due profiles, searches, and tracked listings with bounded
global concurrency (default `4`), while the shared Wallapop client limiter
continues to control HTTP request rate. SQLite file databases use WAL and a
busy timeout; multiple scheduler processes are not coordinated.

## Scheduling

Scheduling operates on tracked profiles with `enabled = true`. A profile is due when it has never run or when its `last_run_at` is at least the configured interval in the past. The runner writes the attempt time and resulting status back to the tracked-profile record.

Profiles are executed sequentially. A failure is recorded for the affected profile and does not prevent later due profiles from being attempted. Scheduler and tracking timestamps are handled in UTC; naive input datetimes are normalized to UTC. The interval is configured with `--interval-hours` and defaults to 168 hours (one week). `--once` performs a single due-profile evaluation, which is useful for one-shot jobs and external schedulers.

## Historical model

- **Profiles** represent the normalized Wallapop identity and stable profile attributes.
- **Tracked profiles** are user-managed configurations with a profile URL, unique alias, enabled flag, notes, and scheduling metadata.
- **Tracking runs** record each capture attempt, timestamps, status, fetched-item counts, component success flags, and errors. Terminal statuses are `valid`, `partial`, and `failed`.
- **Listings** represent normalized Wallapop items associated with a profile.
- **Profile snapshots** store change-based profile metrics such as rating, review count, published count, purchases, sales, sold count, reports, and rating distribution.
- **Listing snapshots** store change-based listing fields such as title, price, status, reservation, shipping, brand, condition, and source timestamps.
- **Presence rows** associate listings with valid runs. Listing snapshots use `ACTIVE` or `REMOVED` to preserve lifecycle state.
- **Tracked searches** store query, price bounds, structured filters, enablement, interval metadata and the configurable first-run notification policy. A silent first valid run establishes inventory without `NEW_LISTING` alerts.
- **Search matches** associate each global listing with every search that detected it, including first/last seen timestamps and detection count.
- **Tracking events** store globally idempotent `NEW_LISTING`, `PRICE_DROP`, and `PRICE_INCREASE` alerts.
- **Tracked listings** monitor one global listing by alias and interval, including price, title, reservation, shipping, status, removal, and reappearance changes.

Snapshots are written only for valid runs. Partial or failed captures are retained as run outcomes but cannot establish new profile/listing presence or overwrite the last valid historical state.

The repository contains Alembic revisions `0001` through `0011`, with `0007_search_tracking` adding the persistent event ledger, `0009_notification_deliveries` adding the delivery queue, `0010_tracked_listings` adding direct listing monitoring, and `0011_search_initial_baseline` adding the per-search baseline policy. The CLI initializes new databases through SQLAlchemy metadata; Alembic remains the migration path for existing deployments.

## Change detection

`DiffService` compares valid runs for the same profile and derives:

- new listings;
- removed listings;
- listings that reappeared after an absent run;
- price, title, reservation, shipping-availability, and brand changes;
- profile rating, review-count, and sold-count changes.

Presence is derived from the listing-to-run association rather than from a missing snapshot. Reporting queries expose related inventory, price, presence, and metric histories without modifying the database.

## Reliability

The asynchronous client applies a minimum request interval and serializes rate-limit waits. Transient network and timeout errors are retried. HTTP `429` and `500`, `502`, `503`, and `504` responses use exponential backoff and honor a numeric or HTTP-date `Retry-After` value, capped by the configured maximum. Other HTTP errors are surfaced without retrying.

The tracker distinguishes complete captures from partial and failed captures. A valid capture is persisted in a database transaction; partial and failed captures retain diagnostic run metadata while preserving the last valid historical state. Optional RAW response capture writes response bodies before normalization and treats filesystem failures as warnings.

The parser contract tests use checked-in RAW fixtures under `tests/fixtures/raw/`, so API-shape regressions can be detected without network access. The fixture scripts and manual E2E validation are separate from the default test suite.

## Testing

Install development dependencies and run:

```bash
pytest
ruff check .
mypy src
```

The default pytest configuration excludes tests marked `live`. Live tests are explicitly authorized integration checks and can be selected with `pytest -m live` when `WALLAPOP_TEST_PROFILE_URL` is configured. The manual E2E script requires both `WALLAPOP_E2E_PROFILE_URL_1` and `WALLAPOP_E2E_PROFILE_URL_2` and stores its separate history in `data/e2e_validation.db`.

## CI

GitHub Actions runs on pushes and pull requests using Python 3.12. The workflow installs the package with development dependencies, then runs Ruff, mypy, and pytest. Since the default pytest marker excludes live tests, CI does not require Wallapop network access or live profile URLs.

## Docker

The image installs the package into a Python 3.12 slim image and runs as a non-root `app` user. Docker Compose mounts the local `data/` directory at `/app/data` and passes `WALLAPOP_TRACKER_DB_URL`, defaulting to the SQLite database under that mount.

```bash
docker build -t wallapop-tracker .
docker compose run --rm tracker wallapop-track --help
docker compose run --rm tracker wallapop-track list
```

## Configuration

The application reads the following environment variables:

- `WALLAPOP_TRACKER_DB_URL`: SQLAlchemy database URL used by the CLI. It defaults to `sqlite:///data/wallapop_tracker.db`.
- `WALLAPOP_TEST_PROFILE_URL`: optional profile URL used by the opt-in live pytest.
- `WALLAPOP_E2E_PROFILE_URL_1` and `WALLAPOP_E2E_PROFILE_URL_2`: the two optional profile URLs required by `scripts/run_e2e_validation.py`.
- `WALLAPOP_WEBHOOK_URL`: optional generic webhook destination.
- `WALLAPOP_DISCORD_WEBHOOK_URL`: optional Discord webhook destination.
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`: optional Telegram Bot API configuration.
- `WALLAPOP_NOTIFICATION_MAX_ATTEMPTS`: delivery attempt limit, default `3`.

The client itself also accepts runtime options such as base URLs, timeout, retry limits, rate-limit interval, user agent, and an optional RAW data directory through its Python constructor; these are not environment variables.

## Limitations

- The integration relies on internal or undocumented Wallapop endpoints and frontend response shapes, which may change without notice.
- The project is read-only. It does not automate purchases, messages, listing edits, or any other action on Wallapop.
- It is intended for authorized, moderate-volume monitoring, not for mass scraping.
- Search results are intentionally limited to a configurable recent-page window (`max_pages`, default five), rather than being an exhaustive historical search.
- Notification delivery is intentionally sequential and has no distributed queue or concurrent worker pool.
- Direct listing detail depends on the observed public endpoint `/api/v3/items/{id}`; its undocumented contract may change and remains covered by offline fixtures.

## Development status

This is a functional technical project with profile tracking, search tracking, normalized parsers, historical snapshots, deterministic diffing, reusable filters, global deduplication, reporting, alerts, a shared scheduler, CLI, SQLite, Alembic, Docker packaging, automated tests, CI, and manual E2E validation. Its external API integration remains subject to the limitations above, especially changes to undocumented Wallapop response contracts.
