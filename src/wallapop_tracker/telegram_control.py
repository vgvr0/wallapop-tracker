"""Deterministic Telegram control plane over the existing search services.

The parser produces intents so a future natural-language parser can use the same
validated application service without gaining direct database access.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from wallapop_tracker.observability import get_metrics
from wallapop_tracker.services.notifications import NotificationService
from wallapop_tracker.services.runner import SearchTrackingRunner
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.repositories import (
    TelegramOwnershipRepository,
    TrackedSearchRepository,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramIntent:
    action: str
    search_id: int | None = None
    query: str | None = None
    filters: dict[str, Any] | None = None
    confirmation: bool = False


class TelegramCommandError(ValueError):
    pass


def parse_command(text: str) -> TelegramIntent:
    parts = shlex.split(text.strip())
    if not parts or not parts[0].startswith("/"):
        raise TelegramCommandError("Unknown command")
    command = parts[0].split("@", 1)[0].lower()
    if command in {"/start", "/help", "/searches"}:
        return TelegramIntent(command[1:])
    if command == "/search_add":
        if not parts[1:]:
            raise TelegramCommandError("Usage: /search_add QUERY [--max-price AMOUNT]")
        query: list[str] = []
        filters: dict[str, Any] = {}
        i = 1
        options = {
            "--min-price": "min_price",
            "--max-price": "max_price",
            "--include": "include",
            "--exclude": "exclude",
            "--title-include": "title_include",
            "--title-exclude": "title_exclude",
            "--description-include": "description_include",
            "--description-exclude": "description_exclude",
            "--brand": "brand",
            "--condition": "condition",
            "--category-id": "category_id",
            "--interval-seconds": "interval_seconds",
        }
        while i < len(parts):
            key = options.get(parts[i])
            if key is None:
                query.append(parts[i])
                i += 1
                continue
            if i + 1 >= len(parts):
                raise TelegramCommandError(f"Missing value for {parts[i]}")
            value: Any = parts[i + 1]
            if key in {"min_price", "max_price"}:
                try:
                    value = Decimal(value)
                except InvalidOperation as exc:
                    raise TelegramCommandError(f"Invalid price: {value}") from exc
            elif key == "interval_seconds":
                try:
                    value = int(value)
                except ValueError as exc:
                    raise TelegramCommandError("interval-seconds must be an integer") from exc
            elif key in {
                "include",
                "exclude",
                "title_include",
                "title_exclude",
                "description_include",
                "description_exclude",
            }:
                value = [item for item in value.split(",") if item]
            filters[key] = value
            i += 2
        return TelegramIntent("search_add", query=" ".join(query), filters=filters)
    if command in {
        "/search_show",
        "/search_enable",
        "/search_disable",
        "/search_delete",
        "/search_run",
    }:
        if len(parts) not in (2, 3) or not parts[1].isdigit():
            raise TelegramCommandError(f"Usage: {command} SEARCH_ID")
        return TelegramIntent(
            command[1:],
            search_id=int(parts[1]),
            confirmation=len(parts) == 3 and parts[2].lower() == "confirm",
        )
    raise TelegramCommandError("Unknown command")


class TelegramControlService:
    """Application service enforcing chat ownership for every search operation."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def register_chat(self, chat_id: int, **metadata: str | None) -> None:
        with self.database.transaction() as session:
            TelegramOwnershipRepository(session).upsert_chat(chat_id, **metadata)

    def execute(self, chat_id: int, intent: TelegramIntent) -> str:
        metrics = get_metrics()
        with self.database.transaction() as session:
            owners = TelegramOwnershipRepository(session)
            owners.touch_chat(chat_id)
            if intent.action == "start":
                return "Welcome. Use /help to manage your Wallapop searches."
            if intent.action == "help":
                return (
                    "Commands: /searches, /search_add QUERY [--max-price AMOUNT], "
                    "/search_show ID, /search_enable ID, /search_disable ID, "
                    "/search_delete ID confirm, /search_run ID"
                )
            if intent.action == "searches":
                rows = owners.list_owned(chat_id)
                return (
                    "No searches owned by this chat."
                    if not rows
                    else "\n".join(
                        f"#{row.id} {row.name or row.query}\n{'enabled' if row.enabled else 'disabled'}\n"
                        f"max_price={row.max_price or '-'} €\ninterval={row.interval_seconds}s"
                        for row in rows
                    )
                )
            if intent.search_id is None:
                if intent.action != "search_add":
                    raise TelegramCommandError("Search not found")
            elif owners.resolve_owned(chat_id, intent.search_id) is None:
                raise TelegramCommandError("Search not found")
            if intent.action == "search_add":
                if not intent.query:
                    raise TelegramCommandError("Query is required")
                values = dict(intent.filters or {})
                min_price = values.pop("min_price", None)
                max_price = values.pop("max_price", None)
                interval = values.pop("interval_seconds", 600)
                row = TrackedSearchRepository(session).create(
                    intent.query,
                    min_price=min_price,
                    max_price=max_price,
                    filters=values,
                    interval_seconds=interval,
                )
                owners.associate(chat_id, row.id)
                return f"Created search #{row.id}: {row.name or row.query}"
            row = owners.resolve_owned(chat_id, intent.search_id)
            assert row is not None
            if intent.action == "search_show":
                return (
                    f"#{row.id} {row.name or row.query}\nowner=current chat\n"
                    f"enabled={row.enabled}\nmin_price={row.min_price or '-'}\n"
                    f"max_price={row.max_price or '-'}\ninterval={row.interval_seconds}s\n"
                    f"filters={row.filters_json or '{}'}\nlast_run={row.last_run_at or '-'}"
                )
            if intent.action in {"search_enable", "search_disable"}:
                (
                    TrackedSearchRepository(session).enable
                    if intent.action == "search_enable"
                    else TrackedSearchRepository(session).disable
                )(row.id)
                return f"Search #{row.id} {'enabled' if row.enabled else 'disabled'}"
            if intent.action == "search_delete":
                if not intent.confirmation:
                    return f"Confirm delete search #{row.id} with:\n/search_delete {row.id} confirm"
                owners.remove_association(chat_id, row.id)
                TrackedSearchRepository(session).remove(row.id)
                return f"Deleted search #{row.id}"
        if intent.action == "search_run":
            return asyncio.run(self.run(chat_id, intent.search_id))
        metrics.telegram_command_failures_total.labels(intent.action).inc()
        raise TelegramCommandError("Unsupported command")

    async def run(self, chat_id: int, search_id: int | None) -> str:
        if search_id is None:
            raise TelegramCommandError("Search not found")
        with self.database.session() as session:
            if TelegramOwnershipRepository(session).resolve_owned(chat_id, search_id) is None:
                raise TelegramCommandError("Search not found")
        result = await SearchTrackingRunner(
            self.database, notification_service=NotificationService(self.database)
        ).run(search_id)
        await NotificationService(self.database).deliver_pending()
        return f"Search #{search_id}: {result.status.value if result.status else 'disabled'}"
