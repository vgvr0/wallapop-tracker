"""Long-polling Telegram transport for the control application service."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .ai.config import AISettings
from .ai.factory import build_listing_analyzer
from .observability import get_metrics
from .storage.database import Database
from .telegram_control import (
    TelegramAmbiguousSearch,
    TelegramCommandError,
    TelegramControlService,
    TelegramIntent,
    parse_command,
)
from .telegram_natural_language import TelegramNaturalLanguageError, TelegramNaturalLanguageParser

logger = logging.getLogger(__name__)


@dataclass
class _Pending:
    intent: TelegramIntent
    kind: str
    candidates: list[tuple[int, str]] | None
    expires_at: float


class TelegramBot:
    def __init__(
        self, database: Database, token: str, *, poll_timeout: int = 30, nl_parser: Any = None
    ) -> None:
        if not token:
            raise ValueError("WALLAPOP_TELEGRAM_BOT_TOKEN is required")
        self.service = TelegramControlService(database)
        self.token = token
        self.poll_timeout = poll_timeout
        self.offset = 0
        self.running = True
        self.base_url = f"https://api.telegram.org/bot{token}"
        self.pending: dict[int, _Pending] = {}
        self.nl_parser = nl_parser
        self.nl_enabled = nl_parser is not None

    async def api(self, client: httpx.AsyncClient, method: str, **payload: Any) -> Any:
        response = await client.post(
            f"{self.base_url}/{method}", json=payload, timeout=self.poll_timeout + 10
        )
        response.raise_for_status()
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(f"Telegram API error in {method}")
        return body.get("result")

    async def run(self) -> None:
        logger.info("telegram_bot_startup poll_timeout=%s", self.poll_timeout)
        async with httpx.AsyncClient() as client:
            while self.running:
                try:
                    updates = await self.api(
                        client, "getUpdates", offset=self.offset, timeout=self.poll_timeout
                    )
                    logger.info("telegram_polling_connected")
                    for update in updates or []:
                        self.offset = int(update["update_id"]) + 1
                        await self.handle_update(client, update)
                except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError):
                    logger.exception("telegram_polling_error")
                    await asyncio.sleep(2)
        logger.info("telegram_bot_shutdown")

    async def handle_update(self, client: httpx.AsyncClient, update: dict[str, Any]) -> None:
        get_metrics().telegram_updates_total.inc()
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        text = message.get("text")
        if not isinstance(chat.get("id"), int) or not isinstance(text, str):
            return
        chat_id = chat["id"]
        self.service.register_chat(
            chat_id,
            username=chat.get("username"),
            first_name=chat.get("first_name"),
            last_name=chat.get("last_name"),
        )
        started = time.perf_counter()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].lower()
        get_metrics().telegram_commands_total.labels(command).inc()
        try:
            if text.lstrip().startswith("/"):
                reply = await self.service.execute_async(chat_id, parse_command(text))
            else:
                reply = await self._handle_text(chat_id, text)
        except TelegramAmbiguousSearch as exc:
            get_metrics().wallapop_telegram_nl_ambiguities_total.inc()
            self.pending[chat_id] = _Pending(
                intent=exc.intent or TelegramIntent("unknown"),
                kind="ambiguity",
                candidates=exc.candidates,
                expires_at=time.time() + 300,
            )
            reply = (
                "Tengo varias búsquedas:\n"
                + "\n".join(f"#{search_id} {name}" for search_id, name in exc.candidates)
                + "\nIndica el número."
            )
        except TelegramCommandError as exc:
            get_metrics().telegram_command_failures_total.labels(command).inc()
            reply = str(exc)
        except Exception:
            get_metrics().telegram_command_failures_total.labels(command).inc()
            logger.exception("telegram_handler_error command=%s", text.split(maxsplit=1)[0])
            reply = "Unable to process that command."
        finally:
            get_metrics().telegram_control_duration_seconds.observe(time.perf_counter() - started)
        await self.api(client, "sendMessage", chat_id=chat_id, text=reply)

    async def _handle_text(self, chat_id: int, text: str) -> str:
        pending = self.pending.get(chat_id)
        if pending is not None:
            if pending.expires_at <= time.time():
                self.pending.pop(chat_id, None)
                return "Ese contexto ha caducado. Repite la petición."
            answer = text.strip().casefold()
            if answer in {"no", "cancelar", "cancel", "n"}:
                self.pending.pop(chat_id, None)
                return "Cancelado."
            if pending.kind == "confirmation" and answer in {"sí", "si", "yes", "y"}:
                self.pending.pop(chat_id, None)
                get_metrics().wallapop_telegram_nl_confirmations_total.inc()
                return await self.service.execute_async(
                    chat_id,
                    TelegramIntent(
                        pending.intent.action,
                        search_id=pending.intent.search_id,
                        search_reference=pending.intent.search_reference,
                        filters=pending.intent.filters,
                        confirmation=True,
                    ),
                )
            if pending.kind == "ambiguity" and answer.isdigit() and pending.candidates:
                selected = int(answer)
                if selected not in {candidate[0] for candidate in pending.candidates}:
                    return "Indica uno de los números mostrados."
                self.pending.pop(chat_id, None)
                return await self.service.execute_async(
                    chat_id,
                    TelegramIntent(
                        pending.intent.action,
                        search_id=selected,
                        query=pending.intent.query,
                        filters=pending.intent.filters,
                        confirmation=pending.intent.confirmation,
                    ),
                )
            return "Responde sí/no o indica uno de los números mostrados."
        if not self.nl_enabled:
            return "El lenguaje natural está deshabilitado. Usa /help y los comandos explícitos."
        try:
            get_metrics().wallapop_telegram_nl_requests_total.inc()
            nl_started = time.perf_counter()
            intent = await self.nl_parser.parse(text)
            if intent.action == "unknown":
                return "No he podido interpretar ese mensaje. Puedes usar /help."
            try:
                reply = await self.service.execute_async(chat_id, intent)
            except TelegramAmbiguousSearch as exc:
                exc.intent = intent
                raise
            if intent.action == "search_delete" and not intent.confirmation:
                self.pending[chat_id] = _Pending(intent, "confirmation", None, time.time() + 300)
            get_metrics().wallapop_telegram_nl_intents_total.labels(intent.action).inc()
            logger.info("nl.intent.executed event=nl.intent.executed action=%s", intent.action)
            get_metrics().wallapop_telegram_nl_duration_seconds.observe(
                time.perf_counter() - nl_started
            )
            return reply
        except TelegramAmbiguousSearch:
            raise
        except (TelegramNaturalLanguageError, TelegramCommandError) as exc:
            get_metrics().wallapop_telegram_nl_parse_failures_total.inc()
            logger.info("nl.intent.invalid event=nl.intent.invalid reason=%s", type(exc).__name__)
            get_metrics().wallapop_telegram_nl_duration_seconds.observe(
                time.perf_counter() - nl_started
            )
            return (
                "No he podido interpretar ese mensaje. Puedes usar /help o los comandos explícitos."
            )


def run_bot(*, poll_timeout: int = 30) -> None:
    token = os.getenv("WALLAPOP_TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    database = Database(os.getenv("WALLAPOP_TRACKER_DB_URL", "sqlite:///data/wallapop_tracker.db"))
    if database.engine.dialect.name == "sqlite":
        database.create_all()
    nl_parser = None
    if os.getenv("WALLAPOP_TELEGRAM_NL_ENABLED", "false").lower() in {"1", "true", "yes", "on"}:
        try:
            analyzer = build_listing_analyzer(AISettings.from_env())
            if analyzer is not None:
                nl_parser = TelegramNaturalLanguageParser(analyzer)
            else:
                logger.warning("telegram_nl_disabled reason=ai_disabled")
        except Exception:
            logger.exception("telegram_nl_disabled reason=invalid_ai_configuration")
    bot = TelegramBot(database, token, poll_timeout=poll_timeout, nl_parser=nl_parser)
    loop = asyncio.new_event_loop()
    for name in ("SIGINT", "SIGTERM"):
        signal_name = getattr(signal, name, None)
        if signal_name is not None:
            loop.add_signal_handler(signal_name, setattr, bot, "running", False)
    try:
        loop.run_until_complete(bot.run())
    finally:
        loop.close()
        database.close()
