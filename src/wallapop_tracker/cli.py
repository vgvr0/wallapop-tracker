"""Manual CLI for configuring and running tracked profiles."""

import asyncio
import os
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse

import typer

from .client import WallapopClient
from .domain.listing_urls import parse_listing_reference
from .domain.metadata import AvailableFilter, Brand, Category, ProductModel
from .exceptions import WallapopError
from .models import Listing
from .parsers.search_url import SearchURLParseError, parse_search_url
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
from .storage.models import TrackingRunStatus
from .storage.repositories import (
    ListingRepository,
    TrackedListingRepository,
    TrackedProfileRepository,
    TrackedSearchRepository,
)

app = typer.Typer(no_args_is_help=True)
search_app = typer.Typer(no_args_is_help=True)
notifications_app = typer.Typer(no_args_is_help=True)
listing_app = typer.Typer(no_args_is_help=True)
metadata_app = typer.Typer(no_args_is_help=True)
app.add_typer(search_app, name="search")
app.add_typer(notifications_app, name="notifications")
app.add_typer(listing_app, name="listing")
app.add_typer(metadata_app, name="metadata")


def _db() -> Database:
    database = Database(os.getenv("WALLAPOP_TRACKER_DB_URL", "sqlite:///data/wallapop_tracker.db"))
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
            typer.echo(f"{alias}\t{tracking.run_id}\t{result.status.value}\t{tracking.items_fetched}")
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
    regex: str | None = typer.Option(None, "--regex"),
    regex_target: str = typer.Option("both", "--regex-target"),
    notify_on_first_run: bool = typer.Option(False, "--notify-on-first-run"),
) -> None:
    """Create a persistent read-only Wallapop search tracker."""
    filters = {
        "include": include,
        "exclude": exclude,
        "include_mode": "all" if include_all else "any",
        "regex": regex,
        "regex_target": regex_target,
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
    typer.echo(
        f"initial notifications: {'enabled' if record.notify_on_first_run else 'disabled'}"
    )
    typer.echo("enabled: true")


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
    typer.echo(
        f"initial notifications: {'enabled' if record.notify_on_first_run else 'disabled'}"
    )
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
                record, _ = ListingRepository(session).get_or_create_global_listing(
                    listing, None
                )
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
            values = [
                record.alias for record in TrackedListingRepository(session).list_enabled()
            ]
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
