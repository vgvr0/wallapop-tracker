"""Manual CLI for configuring and running tracked profiles."""

import asyncio
import os
from datetime import timedelta
from urllib.parse import urlparse

import typer

from .client import WallapopClient
from .services.runner import ProfileTrackingRunner
from .services.scheduler import TrackingScheduler
from .services.tracker import ProfileTracker
from .storage.database import Database
from .storage.models import TrackingRunStatus
from .storage.repositories import TrackedProfileRepository

app = typer.Typer(no_args_is_help=True)


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


if __name__ == "__main__":
    app()
