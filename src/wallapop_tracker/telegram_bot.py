"""Long-polling Telegram transport for the control application service."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

import httpx

from .observability import get_metrics
from .storage.database import Database
from .telegram_control import TelegramCommandError, TelegramControlService, parse_command

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self, database: Database, token: str, *, poll_timeout: int = 30) -> None:
        if not token:
            raise ValueError("WALLAPOP_TELEGRAM_BOT_TOKEN is required")
        self.service = TelegramControlService(database)
        self.token = token
        self.poll_timeout = poll_timeout
        self.offset = 0
        self.running = True
        self.base_url = f"https://api.telegram.org/bot{token}"

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
            reply = self.service.execute(chat_id, parse_command(text))
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


def run_bot(*, poll_timeout: int = 30) -> None:
    token = os.getenv("WALLAPOP_TELEGRAM_BOT_TOKEN", os.getenv("TELEGRAM_BOT_TOKEN", ""))
    database = Database(os.getenv("WALLAPOP_TRACKER_DB_URL", "sqlite:///data/wallapop_tracker.db"))
    if database.engine.dialect.name == "sqlite":
        database.create_all()
    bot = TelegramBot(database, token, poll_timeout=poll_timeout)
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
