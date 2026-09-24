"""Manual CLI for configuring and running tracked profiles."""

import asyncio
import json
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

import typer
from sqlalchemy import select

from .client import WallapopClient
from .domain.filters import FilterTrace
from .domain.listing_urls import parse_listing_reference
from .domain.metadata import AvailableFilter, Brand, Category, ProductModel
from .exceptions import WallapopError
from .models import Listing
from .observability import configure_logging
from .parsers.search_url import SearchURLParseError, parse_search_url
from .reporting import (
    get_activity_time_series,
    get_brand_market_stats,
    get_market_summary,
    get_price_time_series,
    get_seller_market_stats,
)
from .services.deal_scoring import DealScoringService
from .services.event_bus import EventBus, deserialize_event
from .services.filter_explanation import SearchListingExplanation, explain_search_listing
from .services.notifications import NotificationService
from .services.runner import (
    ListingTrackingRunner,
    ProfileTrackingRunner,
    SearchTrackingRunner,
)
from .services.scheduler import TrackingScheduler
from .services.search_tracker import SearchTracker
from .services.tracker import ProfileTracker
from .storage.database import Database
from .storage.models import (
    DeadLetterRecord,
    DomainEventRecord,
    ListingRecord,
    TrackingRunStatus,
)
from .storage.repositories import (
    ListingRepository,
    PossibleRelistingRepository,
    TrackedListingRepository,
    TrackedProfileRepository,
    TrackedSearchRepository,
)

app = typer.Typer(no_args_is_help=True)
search_app = typer.Typer(no_args_is_help=True)
notifications_app = typer.Typer(no_args_is_help=True)
listing_app = typer.Typer(no_args_is_help=True)
listing_alerts_app = typer.Typer(no_args_is_help=True)
search_alerts_app = typer.Typer(no_args_is_help=True)
metadata_app = typer.Typer(no_args_is_help=True)
relistings_app = typer.Typer(no_args_is_help=True)
analytics_app = typer.Typer(no_args_is_help=True)
score_app = typer.Typer(no_args_is_help=True)
app.add_typer(search_app, name="search")
app.add_typer(notifications_app, name="notifications")
app.add_typer(listing_app, name="listing")
listing_app.add_typer(listing_alerts_app, name="alerts")
search_app.add_typer(search_alerts_app, name="alerts")
app.add_typer(metadata_app, name="metadata")
app.add_typer(relistings_app, name="relistings")
app.add_typer(analytics_app, name="analytics")
app.add_typer(score_app, name="score")
worker_app = typer.Typer(no_args_is_help=True)
app.add_typer(worker_app, name="worker")
events_app = typer.Typer(no_args_is_help=True)
dlq_app = typer.Typer(no_args_is_help=True)
app.add_typer(events_app, name="events")
app.add_typer(dlq_app, name="dlq")


def _db() -> Database:
    configure_logging()
    database = Database(os.getenv("WALLAPOP_TRACKER_DB_URL", "sqlite:///data/wallapop_tracker.db"))
    if database.engine.dialect.name == "sqlite":
        database.create_all()
    return database


def _url(value: str) -> str:
    parsed = urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.netloc not in {"es.wallapop.com", "www.wallapop.com"}
        or not parsed.path.rstrip("/").split("/")[-1]
    ):
        raise typer.BadParameter("profile_url must be an HTTPS Wallapop profile URL")
    return value.strip()


def _alias(value: str) -> str:
    value = value.strip().lower()
    if not value:
        raise typer.BadParameter("alias is required")
    return value


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return str(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _decimal_option(value: str | None, name: str) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value)
    except Exception as exc:
        raise typer.BadParameter(f"{name} must be numeric") from exc


_TEXT_FILTER_KEYS = (
    "title_include",
    "description_include",
    "title_exclude",
    "description_exclude",
    "title_first_word_include",
    "title_first_word_exclude",
)


def _text_filter_summary(filters: Mapping[str, object]) -> list[str]:
    """Human-readable summary of the advanced text filters that are in use."""

    lines: list[str] = []
    for key in _TEXT_FILTER_KEYS:
        value = filters.get(key)
        terms = [str(item) for item in value] if isinstance(value, list) else []
        if not terms:
            continue
        mode = filters.get(f"{key}_mode")
        suffix = f" ({mode})" if isinstance(mode, str) else ""
        lines.append(f"{key}: {', '.join(terms)}{suffix}")
    return lines


@analytics_app.command("market")
def analytics_market(
    search_id: int,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Show a read-only market summary for one tracked search."""
    database = _db()
    try:
        with database.session() as session:
            summary = get_market_summary(session, search_id)
            if as_json:
                typer.echo(json.dumps(summary.__dict__, default=_json_default, sort_keys=True))
                return
            typer.echo(f"Market summary — search {search_id}")
            typer.echo(f"Active listings: {summary.active_listings}")
            typer.echo(f"Unique observed: {summary.unique_listings}")
            typer.echo(f"Median price: {_money(summary.median_price)}")
            typer.echo(f"Average price: {_money(summary.average_price)}")
            typer.echo(f"P25: {_money(summary.p25_price)}")
            typer.echo(f"P75: {_money(summary.p75_price)}")
            typer.echo(f"New listings (period): {summary.new_listings}")
            typer.echo(f"Removed listings: {summary.removed_listings}")
            typer.echo(f"Price drops: {summary.price_drops}")
            typer.echo(f"Price increases: {summary.price_increases}")
            typer.echo(f"Median observed active duration: {summary.median_active_duration or '-'}")
    finally:
        database.close()


@analytics_app.command("prices")
def analytics_prices(search_id: int, weekly: bool = typer.Option(False, "--weekly")) -> None:
    database = _db()
    try:
        with database.session() as session:
            for point in get_price_time_series(
                session, search_id, granularity="weekly" if weekly else "daily"
            ):
                typer.echo(
                    f"{point.date.isoformat()}\t{_money(point.median_price)}\t"
                    f"{_money(point.average_price)}\t{point.active_listings}"
                )
    finally:
        database.close()


@analytics_app.command("activity")
def analytics_activity(search_id: int, weekly: bool = typer.Option(False, "--weekly")) -> None:
    database = _db()
    try:
        with database.session() as session:
            for point in get_activity_time_series(
                session, search_id, granularity="weekly" if weekly else "daily"
            ):
                typer.echo(
                    f"{point.date.isoformat()}\t{point.new_listings}\t"
                    f"{point.removed_listings}\t{point.price_drops}"
                )
    finally:
        database.close()


@analytics_app.command("sellers")
def analytics_sellers(search_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            for seller in get_seller_market_stats(session, search_id):
                typer.echo(
                    f"{seller.seller_external_id or '-'}\t{seller.listing_count}\t"
                    f"{seller.active_count}\t{_money(seller.median_price)}\t"
                    f"{seller.price_drop_count}"
                )
    finally:
        database.close()


@analytics_app.command("brands")
def analytics_brands(search_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            for brand in get_brand_market_stats(session, search_id):
                typer.echo(
                    f"{brand.brand}\t{brand.listing_count}\t{brand.active_count}\t"
                    f"{_money(brand.median_price)}"
                )
    finally:
        database.close()


@score_app.command("listing")
def score_listing(
    listing_id: int,
    search_id: int = typer.Option(..., "--search-id", min=1),
) -> None:
    """Show a deterministic opportunity score in one search context."""
    database = _db()
    try:
        with database.session() as session:
            result = DealScoringService(session).score_listing(listing_id, search_id)
            typer.echo(
                f"Deal score: {result.score}/100"
                if result.score is not None
                else "Deal score: unavailable"
            )
            typer.echo(f"Confidence: {result.confidence:.0%}")
            typer.echo(f"Status: {result.status.value}")
            if result.reasons:
                typer.echo("Reasons:")
                for reason in result.reasons:
                    typer.echo(f"+ {reason.contribution:>5.1f}  {reason.description}")
    finally:
        database.close()


@score_app.command("search")
def score_search(
    search_id: int,
    limit: int = typer.Option(20, "--limit", min=1, max=100),
) -> None:
    """Rank active listings by their derived score for one search."""
    database = _db()
    try:
        with database.session() as session:
            typer.echo("Score\tConfidence\tPrice\tListing")
            service = DealScoringService(session)
            for result in service.score_search(search_id, limit=limit):
                listing = session.get(ListingRecord, result.listing_id)
                snapshot = service._latest_snapshot(result.listing_id)
                price = _money(snapshot.price if snapshot is not None else None)
                label = listing.wallapop_item_id if listing is not None else str(result.listing_id)
                typer.echo(
                    f"{result.score if result.score is not None else '-'}\t"
                    f"{result.confidence:.0%}\t{price}\t{label}"
                )
    finally:
        database.close()


def _money(value: Decimal | None) -> str:
    return f"{value:.2f} €" if value is not None else "-"


@relistings_app.command("list")
def relistings_list(
    min_score: float | None = typer.Option(None, "--min-score", min=0, max=1),
    listing_id: int | None = typer.Option(None, "--listing-id", min=1),
) -> None:
    """List possible relistings without changing their candidate status."""
    database = _db()
    try:
        with database.session() as session:
            rows = PossibleRelistingRepository(session).list_all(
                min_score=Decimal(str(min_score)) if min_score is not None else None,
                listing_id=listing_id,
            )
            for row in rows:
                typer.echo(
                    f"{row.id}\t{row.status.value}\t{float(row.score):.4f}\t"
                    f"{row.previous_listing_id}->{row.current_listing_id}"
                )
    finally:
        database.close()


@relistings_app.command("show")
def relistings_show(relisting_id: int) -> None:
    """Show one possible relisting and its explainable reasons."""
    database = _db()
    try:
        with database.session() as session:
            row = PossibleRelistingRepository(session).get(relisting_id)
            if row is None:
                raise typer.BadParameter(f"Unknown relisting candidate: {relisting_id}")
            typer.echo(f"id: {row.id}")
            typer.echo(f"status: {row.status.value}")
            typer.echo(f"score: {float(row.score):.4f}")
            typer.echo(f"previous_listing_id: {row.previous_listing_id}")
            typer.echo(f"current_listing_id: {row.current_listing_id}")
            typer.echo(f"reasons: {row.reasons_json}")
    finally:
        database.close()


@app.command()
def add(
    profile_url: str, alias: str = typer.Option(...), notes: str | None = typer.Option(None)
) -> None:
    """Add a profile without downloading listings."""
    profile_url, alias = _url(profile_url), _alias(alias)

    async def resolve() -> str:
        async with WallapopClient() as client:
            user_id = await client.resolve_user_id(profile_url)
            await client.get_profile(user_id)
            return user_id

    try:
        user_id = asyncio.run(resolve())
        database = _db()
        try:
            with database.transaction() as session:
                record = TrackedProfileRepository(session).create(
                    profile_url, user_id, alias, notes
                )
        finally:
            database.close()
        typer.echo("Added profile")
        typer.echo(f"alias: {record.alias}")
        typer.echo(f"user_id: {record.wallapop_user_id}")
        typer.echo(f"enabled: {record.enabled}")
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("list")
def list_profiles() -> None:
    database = _db()
    try:
        with database.session() as session:
            for record in TrackedProfileRepository(session).list_all():
                state = "enabled" if record.enabled else "disabled"
                typer.echo(
                    f"{record.alias}\t{state}\t{record.wallapop_user_id}\t"
                    f"{record.last_run_at or '-'}\t{record.last_run_status or '-'}"
                )
    finally:
        database.close()


def _toggle(alias: str, enabled: bool) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            repository = TrackedProfileRepository(session)
            record = (repository.enable if enabled else repository.disable)(alias)
    finally:
        database.close()
    typer.echo(f"{record.alias}: {'enabled' if record.enabled else 'disabled'}")


@app.command()
def enable(alias: str) -> None:
    _toggle(_alias(alias), True)


@app.command()
def disable(alias: str) -> None:
    _toggle(_alias(alias), False)


@app.command()
def remove(alias: str, yes: bool = typer.Option(False, "--yes")) -> None:
    if not yes and not typer.confirm(f"Remove tracking configuration for {alias}?"):
        raise typer.Abort()
    database = _db()
    try:
        with database.transaction() as session:
            TrackedProfileRepository(session).remove(_alias(alias))
    finally:
        database.close()
    typer.echo(f"Removed: {alias}")


async def _run(alias: str) -> TrackingRunStatus:
    database = _db()
    try:
        result = await ProfileTrackingRunner(
            database, client_factory=WallapopClient, tracker_factory=ProfileTracker
        ).run(alias)
        if result.tracking_result is not None:
            tracking = result.tracking_result
            typer.echo(
                f"{alias}\t{tracking.run_id}\t{result.status.value}\t{tracking.items_fetched}"
            )
        else:
            typer.echo(f"{alias}\tfailed\t{result.error or 'unknown error'}")
        return result.status
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()


@app.command()
def run(alias: str) -> None:
    asyncio.run(_run(_alias(alias)))


@app.command("run-all")
def run_all() -> None:
    database = _db()
    try:
        with database.session() as session:
            aliases = [r.alias for r in TrackedProfileRepository(session).list_enabled()]
    finally:
        database.close()
    totals = {
        status.value: 0
        for status in (TrackingRunStatus.VALID, TrackingRunStatus.PARTIAL, TrackingRunStatus.FAILED)
    }
    for alias in aliases:
        try:
            status = asyncio.run(_run(alias))
            totals[status.value] += 1
        except Exception as exc:
            typer.echo(f"{alias}\tfailed\t{exc}")
            totals["failed"] += 1
    typer.echo("Summary")
    typer.echo(" ".join(f"{key}: {value}" for key, value in totals.items()))


@app.command()
def schedule(
    interval_hours: int = typer.Option(168, min=1),
    poll_seconds: float = typer.Option(60.0, min=0),
    once: bool = typer.Option(False, "--once"),
    max_concurrency: int = typer.Option(4, "--max-concurrency", min=1),
) -> None:
    """Run enabled profiles on a recurring schedule."""
    database = _db()
    scheduler = TrackingScheduler(
        database,
        timedelta(hours=interval_hours),
        poll_seconds=poll_seconds,
        runner=ProfileTrackingRunner(
            database, client_factory=WallapopClient, tracker_factory=ProfileTracker
        ),
        max_concurrency=max_concurrency,
    )
    try:
        if once:
            result = asyncio.run(scheduler.run_once())
            typer.echo(
                f"evaluated: {result.evaluated} executed: {result.executed} "
                f"succeeded: {result.succeeded} failed: {result.failed}"
            )
        else:
            asyncio.run(scheduler.run_forever())
    except KeyboardInterrupt:
        typer.echo("Scheduler stopped")
    finally:
        database.close()


@worker_app.command("tracking")
def tracking_worker(
    once: bool = typer.Option(False, "--once"),
    poll_seconds: float = typer.Option(60.0, "--poll-seconds", min=0),
    max_concurrency: int = typer.Option(4, "--max-concurrency", min=1),
) -> None:
    """Run PostgreSQL-backed tracking jobs claimed by this worker."""
    database = _db()
    scheduler = TrackingScheduler(
        database, timedelta(hours=168), poll_seconds=poll_seconds, max_concurrency=max_concurrency
    )
    try:
        if once:
            asyncio.run(scheduler.run_once())
        else:
            asyncio.run(scheduler.run_forever())
    finally:
        database.close()


@events_app.command("list")
def events_list(
    event_type: str | None = typer.Option(None, "--event-type"),
    consumer: str | None = typer.Option(None, "--consumer"),
    status: str | None = typer.Option(None, "--status"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
    since: datetime | None = typer.Option(None, "--since"),  # noqa: B008
) -> None:
    """List durable domain events in stable append order."""
    database = _db()
    try:
        with database.session() as session:
            for row in EventBus(database).list_events(
                session,
                event_type=event_type,
                consumer=consumer,
                status=status,
                limit=limit,
                since=since,
            ):
                typer.echo(
                    f"{row.id}\t{row.event_type}\t{row.aggregate_type}:{row.aggregate_id}\t{row.created_at.isoformat()}"
                )
    finally:
        database.close()


@events_app.command("show")
def events_show(event_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            row = session.get(DomainEventRecord, event_id)
            if row is None:
                raise typer.BadParameter(f"Unknown event: {event_id}")
            typer.echo(json.dumps(deserialize_event(row), default=_json_default, sort_keys=True))
    finally:
        database.close()


@events_app.command("consume")
def events_consume(
    consumer: str = typer.Option(..., "--consumer"),
    once: bool = typer.Option(False, "--once"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds", min=0),
    max_attempts: int = typer.Option(5, "--max-attempts", min=1),
    lease_seconds: int = typer.Option(300, "--lease-seconds", min=1),
) -> None:
    """Consume durable events; notifications remain idempotent via delivery constraints."""
    database = _db()
    bus = EventBus(database)

    def handle(event: DomainEventRecord) -> None:
        payload = json.loads(event.payload_json)
        if consumer == "notifications":
            tracking_event_id = payload.get("tracking_event_id")
            if tracking_event_id is not None:
                NotificationService(database).enqueue_event(int(tracking_event_id))
        # analytics and future consumers intentionally remain read-only hooks.

    async def loop() -> None:
        while True:
            worked = bus.consume_once(
                consumer, handle, lease_seconds=lease_seconds, max_attempts=max_attempts
            )
            if once or not worked:
                return
            await asyncio.sleep(poll_seconds)

    try:
        asyncio.run(loop())
    finally:
        database.close()


@events_app.command("replay")
def events_replay(
    consumer: str = typer.Option(..., "--consumer"),
    from_id: int = typer.Option(..., "--from-id", min=1),
    to_id: int = typer.Option(..., "--to-id", min=1),
    allow_side_effects: bool = typer.Option(False, "--allow-side-effects"),
) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            count = EventBus(database).replay(
                session,
                consumer=consumer,
                from_id=from_id,
                to_id=to_id,
                allow_side_effects=allow_side_effects,
            )
            typer.echo(f"replayed: {count}")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()


@dlq_app.command("list")
def dlq_list(
    consumer: str | None = typer.Option(None, "--consumer"),
    limit: int = typer.Option(100, "--limit", min=1, max=1000),
) -> None:
    database = _db()
    try:
        with database.session() as session:
            statement = select(DeadLetterRecord).order_by(DeadLetterRecord.id).limit(limit)
            if consumer:
                statement = statement.where(DeadLetterRecord.consumer_name == consumer)
            for row in session.scalars(statement):
                typer.echo(
                    f"{row.id}\t{row.consumer_name}\tevent={row.event_id}\tattempts={row.attempts}\t{row.last_error}"
                )
    finally:
        database.close()


@dlq_app.command("show")
def dlq_show(dead_letter_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            row = session.get(DeadLetterRecord, dead_letter_id)
            if row is None:
                raise typer.BadParameter(f"Unknown dead letter: {dead_letter_id}")
            event = session.get(DomainEventRecord, row.event_id)
            typer.echo(
                json.dumps(
                    {
                        "id": row.id,
                        "consumer": row.consumer_name,
                        "event": deserialize_event(event) if event else None,
                        "attempts": row.attempts,
                        "last_error": row.last_error,
                        "failed_at": row.failed_at.isoformat(),
                        "requeued_at": row.requeued_at.isoformat() if row.requeued_at else None,
                    },
                    default=_json_default,
                    sort_keys=True,
                )
            )
    finally:
        database.close()


@dlq_app.command("retry")
def dlq_retry(dead_letter_id: int) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            EventBus(database).requeue(session, dead_letter_id)
            typer.echo(f"requeued: {dead_letter_id}")
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()


@worker_app.command("events")
def events_worker(
    consumer: str = typer.Option(..., "--consumer"),
    once: bool = typer.Option(False, "--once"),
    poll_seconds: float = typer.Option(1.0, "--poll-seconds", min=0),
) -> None:
    """Run a persistent event consumer worker with graceful Ctrl-C shutdown."""
    events_consume(consumer=consumer, once=once, poll_seconds=poll_seconds)


@worker_app.command("notifications")
def notification_worker(
    once: bool = typer.Option(False, "--once"),
    poll_seconds: float = typer.Option(10.0, "--poll-seconds", min=0),
) -> None:
    """Deliver persisted notifications with crash-recoverable leases."""
    database = _db()
    service = NotificationService(database)

    async def loop() -> None:
        while True:
            await service.deliver_pending()
            if once:
                return
            await asyncio.sleep(poll_seconds)

    try:
        asyncio.run(loop())
    finally:
        database.close()


@metadata_app.command("categories")
def metadata_categories(context: str | None = typer.Option(None, "--context")) -> None:
    """List the observed public Wallapop category catalog."""

    async def fetch() -> list[Category]:
        async with WallapopClient() as client:
            return await client.categories(context=context)

    try:
        categories = asyncio.run(fetch())
    except WallapopError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("ID\tName\tParent")
    for category in categories:
        _echo_category(category)


def _echo_category(category: Category, parent: str | None = None) -> None:
    typer.echo(f"{category.id}\t{category.name}\t{category.parent_id or parent or '-'}")
    for child in category.children:
        _echo_category(child, category.id)


@metadata_app.command("filters")
def metadata_filters(
    query: str | None = typer.Option(None, "--query"),
    category_id: str | None = typer.Option(None, "--category-id"),
) -> None:
    """List filters exposed for a search context."""

    async def fetch() -> list[AvailableFilter]:
        async with WallapopClient() as client:
            return await client.available_filters(query=query, category_id=category_id)

    try:
        filters = asyncio.run(fetch())
    except WallapopError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("ID\tType\tTitle\tParameters")
    for item in filters:
        typer.echo(
            f"{item.id}\t{item.filter_type}\t{item.title}\t{','.join(item.parameter_keys) or '-'}"
        )


@metadata_app.command("brands")
def metadata_brands(
    query: str | None = typer.Option(None, "--query"),
    category_id: str | None = typer.Option(None, "--category-id"),
) -> None:
    """List brand options exposed for a search context."""

    async def fetch() -> list[Brand]:
        async with WallapopClient() as client:
            return await client.brands(query=query, category_id=category_id)

    try:
        brands = asyncio.run(fetch())
    except WallapopError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("ID\tName")
    for brand in brands:
        typer.echo(f"{brand.id or '-'}\t{brand.name}")


@metadata_app.command("models")
def metadata_models(
    query: str | None = typer.Option(None, "--query"),
    category_id: str | None = typer.Option(None, "--category-id"),
) -> None:
    """List model options exposed for a search context."""

    async def fetch() -> list[ProductModel]:
        async with WallapopClient() as client:
            return await client.models(query=query, category_id=category_id)

    try:
        models = asyncio.run(fetch())
    except WallapopError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("ID\tName")
    for model in models:
        typer.echo(f"{model.id or '-'}\t{model.name}")


@search_app.command("add")
def search_add(
    query: str = typer.Option(..., "--query"),
    name: str | None = typer.Option(None, "--name"),
    min_price: str | None = typer.Option(None, "--min-price"),
    max_price: str | None = typer.Option(None, "--max-price"),
    interval_seconds: int = typer.Option(600, "--interval-seconds", min=1),
    include: list[str] = typer.Option([], "--include"),  # noqa: B008
    exclude: list[str] = typer.Option([], "--exclude"),  # noqa: B008
    include_all: bool = typer.Option(False, "--include-all"),
    title_include: list[str] = typer.Option([], "--title-include"),  # noqa: B008
    title_include_mode: str = typer.Option("any", "--title-include-mode"),
    description_include: list[str] = typer.Option([], "--description-include"),  # noqa: B008
    description_include_mode: str = typer.Option("any", "--description-include-mode"),
    title_exclude: list[str] = typer.Option([], "--title-exclude"),  # noqa: B008
    description_exclude: list[str] = typer.Option([], "--description-exclude"),  # noqa: B008
    title_first_word_include: list[str] = typer.Option(  # noqa: B008
        [], "--title-first-word-include"
    ),
    title_first_word_exclude: list[str] = typer.Option(  # noqa: B008
        [], "--title-first-word-exclude"
    ),
    regex: str | None = typer.Option(None, "--regex"),
    regex_target: str = typer.Option("both", "--regex-target"),
    category_id: str | None = typer.Option(None, "--category-id"),
    brand: list[str] = typer.Option([], "--brand"),  # noqa: B008
    model: list[str] = typer.Option([], "--model"),  # noqa: B008
    condition: list[str] = typer.Option([], "--condition"),  # noqa: B008
    latitude: float | None = typer.Option(None, "--latitude"),
    longitude: float | None = typer.Option(None, "--longitude"),
    max_distance_km: float | None = typer.Option(None, "--max-distance-km"),
    notify_on_first_run: bool = typer.Option(False, "--notify-on-first-run"),
) -> None:
    """Create a persistent read-only Wallapop search tracker."""
    filters = {
        "include": include,
        "exclude": exclude,
        "include_mode": "all" if include_all else "any",
        "title_include": title_include,
        "title_include_mode": title_include_mode,
        "description_include": description_include,
        "description_include_mode": description_include_mode,
        "title_exclude": title_exclude,
        "description_exclude": description_exclude,
        "title_first_word_include": title_first_word_include,
        "title_first_word_exclude": title_first_word_exclude,
        "regex": regex,
        "regex_target": regex_target,
        "category_id": category_id,
        "brands": brand,
        "models": model,
        "conditions": condition,
        "latitude": latitude,
        "longitude": longitude,
        "max_distance_km": max_distance_km,
    }
    database = _db()
    try:
        with database.transaction() as session:
            record = TrackedSearchRepository(session).create(
                query,
                name=name,
                min_price=Decimal(min_price) if min_price is not None else None,
                max_price=Decimal(max_price) if max_price is not None else None,
                filters=filters,
                interval_seconds=interval_seconds,
                notify_on_first_run=notify_on_first_run,
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"id: {record.id}")
    typer.echo(f"query: {record.query}")
    for line in _text_filter_summary(filters):
        typer.echo(line)
    typer.echo(f"initial notifications: {'enabled' if record.notify_on_first_run else 'disabled'}")
    typer.echo("enabled: true")


@search_app.command("update")
def search_update(
    search_id: int,
    name: str | None = typer.Option(None, "--name"),
    title_include: list[str] | None = typer.Option(None, "--title-include"),  # noqa: B008
    title_include_mode: str | None = typer.Option(None, "--title-include-mode"),
    description_include: list[str] | None = typer.Option(  # noqa: B008
        None, "--description-include"
    ),
    description_include_mode: str | None = typer.Option(None, "--description-include-mode"),
    title_exclude: list[str] | None = typer.Option(None, "--title-exclude"),  # noqa: B008
    description_exclude: list[str] | None = typer.Option(  # noqa: B008
        None, "--description-exclude"
    ),
    title_first_word_include: list[str] | None = typer.Option(  # noqa: B008
        None, "--title-first-word-include"
    ),
    title_first_word_exclude: list[str] | None = typer.Option(  # noqa: B008
        None, "--title-first-word-exclude"
    ),
    clear_text_filters: bool = typer.Option(False, "--clear-text-filters"),
) -> None:
    """Update the advanced text filters of an existing tracked search."""
    overrides: dict[str, object] = {
        "title_include": title_include,
        "title_include_mode": title_include_mode,
        "description_include": description_include,
        "description_include_mode": description_include_mode,
        "title_exclude": title_exclude,
        "description_exclude": description_exclude,
        "title_first_word_include": title_first_word_include,
        "title_first_word_exclude": title_first_word_exclude,
    }
    provided = {key: value for key, value in overrides.items() if value is not None}
    if not provided and not clear_text_filters and name is None:
        raise typer.BadParameter("provide at least one option to update")
    database = _db()
    try:
        with database.transaction() as session:
            repository = TrackedSearchRepository(session)
            record = repository.get(search_id)
            if record is None:
                raise typer.BadParameter(f"Unknown search: {search_id}")
            filters: dict[str, object] = (
                json.loads(record.filters_json) if record.filters_json else {}
            )
            if not isinstance(filters, dict):
                raise typer.BadParameter("stored search filters are not a JSON object")
            if clear_text_filters:
                for key in (
                    *_TEXT_FILTER_KEYS,
                    "title_include_mode",
                    "description_include_mode",
                ):
                    filters.pop(key, None)
            filters |= provided
            record = repository.update(search_id, name=name, filters=filters)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"id: {record.id}")
    typer.echo(f"name: {record.name or '-'}")
    typer.echo(f"query: {record.query}")
    for line in _text_filter_summary(filters):
        typer.echo(line)
    typer.echo(f"enabled: {record.enabled}")


@search_app.command("list")
def search_list() -> None:
    database = _db()
    try:
        with database.session() as session:
            for record in TrackedSearchRepository(session).list_all():
                state = "enabled" if record.enabled else "disabled"
                label = record.name or record.query
                typer.echo(
                    f"{record.id}\t{state}\t{label}\t{record.query}\t"
                    f"{record.last_run_at or '-'}\t{record.last_run_status or '-'}\t"
                    f"initial-notifications={'on' if record.notify_on_first_run else 'off'}"
                )
    finally:
        database.close()


@search_app.command("import")
def search_import(
    url: str,
    name: str | None = typer.Option(None, "--name"),
    interval_seconds: int = typer.Option(600, "--interval-seconds", min=1),
    disabled: bool = typer.Option(False, "--disabled"),
    notify_on_first_run: bool = typer.Option(False, "--notify-on-first-run"),
) -> None:
    """Create a tracked search from an observed Wallapop search URL."""
    try:
        imported = parse_search_url(url)
        if imported.query is None:
            raise SearchURLParseError(
                "search URL has no query; category-only imports are not supported by TrackedSearch"
            )
        database = _db()
        try:
            with database.transaction() as session:
                record = TrackedSearchRepository(session).create(
                    imported.query,
                    name=name,
                    min_price=imported.min_price,
                    max_price=imported.max_price,
                    filters=imported.search_filters(),
                    interval_seconds=interval_seconds,
                    notify_on_first_run=notify_on_first_run,
                )
                if disabled:
                    record.enabled = False
        finally:
            database.close()
    except (SearchURLParseError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo("Search created")
    typer.echo(f"id: {record.id}")
    typer.echo(f"name: {record.name or record.query}")
    typer.echo(f"query: {record.query}")
    typer.echo(f"initial notifications: {'enabled' if record.notify_on_first_run else 'disabled'}")
    if record.min_price is not None or record.max_price is not None:
        typer.echo(f"price: {record.min_price or '-'}–{record.max_price or '-'} €")
    if imported.category_id is not None:
        typer.echo(f"category_id: {imported.category_id}")
    if imported.latitude is not None or imported.longitude is not None:
        typer.echo(f"location: {imported.latitude or '-'}, {imported.longitude or '-'}")
    if imported.distance is not None:
        typer.echo(f"distance: {imported.distance:g} km")
    if imported.shipping_required is not None:
        typer.echo(f"shipping: {'required' if imported.shipping_required else 'not required'}")
    if imported.condition is not None:
        typer.echo(f"condition: {imported.condition}")
    if imported.brand is not None:
        typer.echo(f"brand: {imported.brand}")
    typer.echo(f"enabled: {record.enabled}")
    if imported.unknown_params:
        typer.echo("Ignored unsupported parameters:")
        for parameter in sorted(imported.unknown_params):
            typer.echo(f"- {parameter}")


def _quoted(values: Iterable[str]) -> str:
    return ", ".join(f'"{value}"' for value in values)


def _terms(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable):
        return tuple(str(item) for item in value)
    return ()


def _number(value: object) -> str:
    if isinstance(value, Decimal):
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _trace_detail(trace: FilterTrace) -> str:
    """Readable detail for one trace; presentation only, no filter semantics."""

    name = trace.filter_name
    actual = trace.actual_value
    if trace.passed is None:
        return trace.reason or "not evaluated from persisted data"
    if actual is None and trace.reason:
        return trace.reason
    if name in {"min_price", "max_price"}:
        operator = "<=" if name == "max_price" else ">="
        if not trace.passed:
            operator = "<" if operator == ">=" else ">"
        return f"{_number(actual)} {operator} {_number(trace.expected_value)}"
    if name in {"include", "title_include", "description_include"}:
        if trace.passed:
            return f"matched {_quoted(trace.matched_values)}"
        missing = tuple(
            term for term in _terms(trace.expected_value) if term not in trace.matched_values
        )
        detail = f"missing {_quoted(missing)}"
        if trace.matched_values:
            detail += f"; matched {_quoted(trace.matched_values)}"
        return detail
    if name in {"exclude", "title_exclude", "description_exclude"}:
        if trace.passed:
            return "no excluded terms found"
        return f"found {_quoted(trace.matched_values)}"
    if name == "title_first_word_include":
        if trace.passed:
            return f'"{actual}"'
        return f'"{actual}" not one of {_quoted(_terms(trace.expected_value))}'
    if name == "title_first_word_exclude":
        return f'"{actual}" is excluded' if not trace.passed else f'"{actual}" not excluded'
    if name == "regex":
        if trace.passed:
            return f"matched {_quoted(trace.matched_values)} in {actual}"
        return f"no match for {trace.expected_value!r} in {actual}"
    if name == "distance":
        return f"{_number(actual)} km <= {_number(trace.expected_value)} km"
    if name == "category_id":
        return (
            str(actual) if trace.passed else f"{actual or 'missing'} is not {trace.expected_value}"
        )
    if name in {"condition", "brand", "model"}:
        if trace.passed:
            return str(actual)
        return f"{actual or 'missing'} not one of {_quoted(_terms(trace.expected_value))}"
    return str(actual) if trace.passed and actual is not None else (trace.reason or "failed")


def _explanation_payload(explanation: SearchListingExplanation) -> dict[str, object]:
    return {
        "search_id": explanation.search_id,
        "listing_id": explanation.listing_id,
        "matched": explanation.matched,
        "complete": explanation.complete,
        "traces": [
            {
                "filter_name": trace.filter_name,
                "passed": trace.passed,
                "actual_value": trace.actual_value,
                "expected_value": trace.expected_value,
                "matched_values": list(trace.matched_values),
                "reason": trace.reason,
            }
            for trace in explanation.evaluation.traces
        ],
        "warnings": list(explanation.warnings),
    }


@search_app.command("show")
def search_show(search_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            record = TrackedSearchRepository(session).get(search_id)
            if record is None:
                raise typer.BadParameter(f"Unknown search: {search_id}")
            typer.echo(f"id: {record.id}")
            typer.echo(f"name: {record.name or '-'}")
            typer.echo(f"query: {record.query}")
            typer.echo(f"min_price: {record.min_price or '-'}")
            typer.echo(f"max_price: {record.max_price or '-'}")
            typer.echo(f"enabled: {record.enabled}")
            typer.echo(f"interval_seconds: {record.interval_seconds}")
            typer.echo(
                f"initial notifications: {'enabled' if record.notify_on_first_run else 'disabled'}"
            )
            typer.echo(f"filters: {record.filters_json or '{}'}")
    finally:
        database.close()


@search_app.command("explain")
def search_explain(
    search_id: int,
    listing_id: int,
    as_json: bool = typer.Option(False, "--json"),
) -> None:
    """Explain why a stored listing matched or failed a tracked search."""
    database = _db()
    try:
        with database.session() as session:
            try:
                explanation = explain_search_listing(session, search_id, listing_id)
            except ValueError as exc:
                raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    if as_json:
        payload = _explanation_payload(explanation)
        typer.echo(json.dumps(payload, default=_json_default, sort_keys=True))
        return
    if explanation.matched is None:
        typer.echo("INCOMPLETE")
    elif explanation.matched:
        typer.echo("MATCHED")
    else:
        typer.echo("NOT MATCHED" if explanation.complete else "NOT MATCHED (INCOMPLETE)")
    typer.echo("")
    for trace in explanation.evaluation.traces:
        mark = "?" if trace.passed is None else ("✓" if trace.passed else "✗")
        typer.echo(f"{mark} {trace.filter_name}: {_trace_detail(trace)}")
    for warning in explanation.warnings:
        typer.echo(f"warning: {warning}")


def _toggle_search(search_id: int, enabled: bool) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            repository = TrackedSearchRepository(session)
            record = repository.enable(search_id) if enabled else repository.disable(search_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"{record.id}: {'enabled' if record.enabled else 'disabled'}")


@search_app.command("enable")
def search_enable(search_id: int) -> None:
    _toggle_search(search_id, True)


@search_app.command("disable")
def search_disable(search_id: int) -> None:
    _toggle_search(search_id, False)


@search_app.command("delete")
def search_delete(search_id: int, yes: bool = typer.Option(False, "--yes")) -> None:
    if not yes and not typer.confirm(f"Remove tracking configuration for search {search_id}?"):
        raise typer.Abort()
    database = _db()
    try:
        with database.transaction() as session:
            TrackedSearchRepository(session).remove(search_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"Removed: {search_id}")


async def _run_search(search_id: int) -> None:
    database = _db()
    try:
        notification_service = NotificationService(database)
        result = await SearchTrackingRunner(
            database,
            client_factory=WallapopClient,
            tracker_factory=SearchTracker,
            notification_service=notification_service,
        ).run(search_id)
        await notification_service.deliver_pending()
        if result.status is None:
            typer.echo(f"{search_id}\tdisabled")
        elif result.status == TrackingRunStatus.FAILED:
            typer.echo(f"{search_id}\tfailed\t{result.error or 'unknown error'}")
        else:
            typer.echo(
                f"{search_id}\t{result.run_id}\t{result.status.value}\t"
                f"matched={result.matched_listings} new={result.new_listings} "
                f"price_changes={result.price_changes} duplicates={result.duplicates_suppressed}"
            )
    finally:
        database.close()


def _safe_destination(destination: str) -> str:
    if destination.startswith(("http://", "https://")):
        from urllib.parse import urlsplit

        parsed = urlsplit(destination)
        return f"{parsed.scheme}://{parsed.netloc}/…"
    return destination


@notifications_app.command("list")
def notifications_list() -> None:
    database = _db()
    try:
        for delivery in NotificationService(database).list_deliveries():
            typer.echo(
                f"{delivery.id}\t{delivery.status.value}\t{delivery.channel}\t"
                f"{_safe_destination(delivery.destination)}\t"
                f"attempts={delivery.attempts}\t{delivery.last_error or '-'}"
            )
    finally:
        database.close()


@notifications_app.command("retry")
def notifications_retry() -> None:
    database = _db()
    try:
        retried = asyncio.run(NotificationService(database).retry_failed())
    finally:
        database.close()
    typer.echo(f"delivered: {retried}")


@app.command("notify")
def notify(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show pending deliveries without sending them."
    ),
) -> None:
    """Dispatch persisted notification deliveries through configured channels."""
    database = _db()
    try:
        service = NotificationService(database)
        before = service.list_deliveries()
        if dry_run:
            pending = [row for row in before if row.status.value in {"pending", "failed"}]
            typer.echo(f"Pending deliveries: {len(pending)} (dry-run; nothing sent)")
            for channel in sorted({row.channel for row in pending}):
                typer.echo(
                    f"{channel}: {sum(row.channel == channel for row in pending)} would send"
                )
            return
        asyncio.run(service.deliver_pending())
        after = service.list_deliveries()
        channels = sorted({row.channel for row in before + after})
        typer.echo(f"Deliveries processed: {len(after)}")
        for channel in channels:
            old = [row for row in before if row.channel == channel]
            current = [row for row in after if row.channel == channel]
            delivered = sum(
                row.status.value == "delivered"
                and next((x for x in old if x.id == row.id), None) is None
                for row in current
            )
            skipped = sum(row.status.value == "delivered" for row in old)
            failed = sum(row.status.value == "failed" for row in current)
            typer.echo(f"{channel}: {delivered} delivered, {failed} failed, {skipped} skipped")
        if not channels:
            typer.echo("No enabled notification channels or pending deliveries")
    finally:
        database.close()


@listing_app.command("add")
def listing_add(
    reference: str,
    alias: str | None = typer.Option(None, "--alias"),
    interval_seconds: int = typer.Option(600, "--interval-seconds", min=1),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    """Add a Wallapop listing by item ID or public item URL."""
    try:
        item_id = parse_listing_reference(reference)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    async def fetch() -> Listing:
        async with WallapopClient() as client:
            return await client.get_item(item_id)

    try:
        listing = asyncio.run(fetch())
        database = _db()
        try:
            with database.transaction() as session:
                record, _ = ListingRepository(session).get_or_create_global_listing(listing, None)
                tracked = TrackedListingRepository(session).create(
                    record.id,
                    alias or item_id,
                    interval_seconds=interval_seconds,
                    notes=notes,
                )
        finally:
            database.close()
    except Exception as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"id: {tracked.id}")
    typer.echo(f"alias: {tracked.alias}")
    typer.echo(f"item_id: {item_id}")
    typer.echo("enabled: true")


@listing_app.command("list")
def listing_list() -> None:
    database = _db()
    try:
        with database.session() as session:
            for tracked in TrackedListingRepository(session).list_all():
                state = "enabled" if tracked.enabled else "disabled"
                typer.echo(
                    f"{tracked.id}\t{tracked.alias}\t{state}\t"
                    f"{tracked.listing.wallapop_item_id}\t{tracked.last_run_at or '-'}\t"
                    f"{tracked.last_run_status or '-'}"
                )
    finally:
        database.close()


@listing_app.command("show")
def listing_show(value: str) -> None:
    database = _db()
    try:
        with database.session() as session:
            tracked = TrackedListingRepository(session).get_by_alias_or_id(value)
            if tracked is None:
                raise typer.BadParameter(f"Unknown tracked listing: {value}")
            typer.echo(f"id: {tracked.id}")
            typer.echo(f"alias: {tracked.alias}")
            typer.echo(f"item_id: {tracked.listing.wallapop_item_id}")
            typer.echo(f"enabled: {tracked.enabled}")
            typer.echo(f"interval_seconds: {tracked.interval_seconds}")
            typer.echo(f"last_run_at: {tracked.last_run_at or '-'}")
            typer.echo(f"last_run_status: {tracked.last_run_status or '-'}")
            typer.echo(f"notes: {tracked.notes or '-'}")
    finally:
        database.close()


def _toggle_listing(value: str, enabled: bool) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            tracked = TrackedListingRepository(session).set_enabled(value, enabled)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"{tracked.alias}: {'enabled' if tracked.enabled else 'disabled'}")


@listing_app.command("enable")
def listing_enable(value: str) -> None:
    _toggle_listing(value, True)


@listing_app.command("disable")
def listing_disable(value: str) -> None:
    _toggle_listing(value, False)


@listing_app.command("remove")
def listing_remove(value: str, yes: bool = typer.Option(False, "--yes")) -> None:
    if not yes and not typer.confirm(f"Remove tracked listing {value}?"):
        raise typer.Abort()
    database = _db()
    try:
        with database.transaction() as session:
            TrackedListingRepository(session).remove(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    finally:
        database.close()
    typer.echo(f"Removed: {value}")


def _alert_values(row: object) -> None:
    for name in (
        "target_price",
        "percentage_drop_threshold",
        "deal_score_threshold",
        "notify_on_30d_low",
        "notify_on_90d_low",
        "notify_on_all_time_low",
    ):
        typer.echo(f"{name}: {getattr(row, name)}")


@listing_alerts_app.command("show")
def listing_alerts_show(value: str) -> None:
    database = _db()
    try:
        with database.session() as session:
            row = TrackedListingRepository(session).get_by_alias_or_id(value)
            if row is None:
                raise typer.BadParameter(f"Unknown tracked listing: {value}")
            _alert_values(row)
    finally:
        database.close()


@listing_alerts_app.command("set")
def listing_alerts_set(
    value: str,
    target_price: str | None = typer.Option(None),
    percentage_drop: str | None = typer.Option(None, "--percentage-drop"),
    deal_score_threshold: str | None = typer.Option(None),
    notify_30d_low: bool = typer.Option(False, "--notify-30d-low/--no-notify-30d-low"),
    notify_90d_low: bool = typer.Option(False, "--notify-90d-low/--no-notify-90d-low"),
    notify_all_time_low: bool = typer.Option(
        False, "--notify-all-time-low/--no-notify-all-time-low"
    ),
    clear_target_price: bool = typer.Option(False),
    clear_percentage_drop: bool = typer.Option(False),
    clear_deal_score_threshold: bool = typer.Option(False),
) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            row = TrackedListingRepository(session).get_by_alias_or_id(value)
            if row is None:
                raise typer.BadParameter(f"Unknown tracked listing: {value}")
            target_value = _decimal_option(target_price, "target-price")
            percentage_value = _decimal_option(percentage_drop, "percentage-drop")
            score_value = _decimal_option(deal_score_threshold, "deal-score-threshold")
            if target_value is not None and target_value < 0:
                raise typer.BadParameter("target-price must be non-negative")
            if score_value is not None and not 0 <= score_value <= 100:
                raise typer.BadParameter("deal-score-threshold must be between 0 and 100")
            if clear_target_price:
                row.target_price = None
            elif target_value is not None:
                row.target_price = target_value
            if clear_deal_score_threshold:
                row.deal_score_threshold = None
            elif score_value is not None:
                row.deal_score_threshold = score_value
            if clear_percentage_drop:
                row.percentage_drop_threshold = None
            elif percentage_value is not None:
                row.percentage_drop_threshold = percentage_value
            row.notify_on_30d_low, row.notify_on_90d_low, row.notify_on_all_time_low = (
                notify_30d_low,
                notify_90d_low,
                notify_all_time_low,
            )
            if percentage_value is not None and not 0 < percentage_value <= 100:
                raise typer.BadParameter("percentage-drop must be > 0 and <= 100")
            _alert_values(row)
    finally:
        database.close()


@search_alerts_app.command("show")
def search_alerts_show(search_id: int) -> None:
    database = _db()
    try:
        with database.session() as session:
            row = TrackedSearchRepository(session).get(search_id)
            if row is None:
                raise typer.BadParameter(f"Unknown search: {search_id}")
            _alert_values(row)
    finally:
        database.close()


@search_alerts_app.command("set")
def search_alerts_set(
    search_id: int,
    percentage_drop: str | None = typer.Option(None, "--percentage-drop"),
    deal_score_threshold: str | None = typer.Option(None),
    notify_30d_low: bool = typer.Option(False, "--notify-30d-low/--no-notify-30d-low"),
    notify_90d_low: bool = typer.Option(False, "--notify-90d-low/--no-notify-90d-low"),
    notify_all_time_low: bool = typer.Option(
        False, "--notify-all-time-low/--no-notify-all-time-low"
    ),
    clear_percentage_drop: bool = typer.Option(False),
    clear_deal_score_threshold: bool = typer.Option(False),
) -> None:
    database = _db()
    try:
        with database.transaction() as session:
            row = TrackedSearchRepository(session).get(search_id)
            if row is None:
                raise typer.BadParameter(f"Unknown search: {search_id}")
            percentage_value = _decimal_option(percentage_drop, "percentage-drop")
            score_value = _decimal_option(deal_score_threshold, "deal-score-threshold")
            if score_value is not None and not 0 <= score_value <= 100:
                raise typer.BadParameter("deal-score-threshold must be between 0 and 100")
            if clear_percentage_drop:
                row.percentage_drop_threshold = None
            elif percentage_value is not None:
                row.percentage_drop_threshold = percentage_value
            if clear_deal_score_threshold:
                row.deal_score_threshold = None
            elif score_value is not None:
                row.deal_score_threshold = score_value
            row.notify_on_30d_low = notify_30d_low
            row.notify_on_90d_low = notify_90d_low
            row.notify_on_all_time_low = notify_all_time_low
            _alert_values(row)
    finally:
        database.close()


async def _run_listing(value: str) -> None:
    database = _db()
    try:
        with database.session() as session:
            tracked = TrackedListingRepository(session).get_by_alias_or_id(value)
            if tracked is None:
                raise typer.BadParameter(f"Unknown tracked listing: {value}")
            tracked_id = tracked.id
        notification_service = NotificationService(database)
        result = await ListingTrackingRunner(
            database, notification_service=notification_service
        ).run(tracked_id)
        await notification_service.deliver_pending()
        if result.status is None:
            typer.echo(f"{value}\tdisabled")
        else:
            typer.echo(
                f"{value}\t{result.run_id or '-'}\t{result.status.value}\t"
                f"events={len(result.alerts)}\t{result.error or '-'}"
            )
    finally:
        database.close()


@listing_app.command("run")
def listing_run(value: str) -> None:
    asyncio.run(_run_listing(value))


@listing_app.command("run-all")
def listing_run_all() -> None:
    database = _db()
    try:
        with database.session() as session:
            values = [record.alias for record in TrackedListingRepository(session).list_enabled()]
    finally:
        database.close()
    for value in values:
        asyncio.run(_run_listing(value))


@search_app.command("run")
def search_run(search_id: int) -> None:
    asyncio.run(_run_search(search_id))


@search_app.command("run-all")
def search_run_all() -> None:
    database = _db()
    try:
        with database.session() as session:
            ids = [record.id for record in TrackedSearchRepository(session).list_enabled()]
    finally:
        database.close()
    for search_id in ids:
        asyncio.run(_run_search(search_id))


if __name__ == "__main__":
    app()
