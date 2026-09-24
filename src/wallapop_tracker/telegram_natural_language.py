"""Safe natural-language-to-intent parsing for the Telegram control plane."""

from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from wallapop_tracker.ai.providers.base import ListingAnalysisError
from wallapop_tracker.telegram_control import TelegramIntent

logger = logging.getLogger(__name__)


class ChatCompleter(Protocol):
    async def complete(self, request: dict[str, Any]) -> dict[str, Any]: ...


class TelegramIntentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    search_id: int | None = Field(default=None, ge=1)
    search_reference: str | None = Field(default=None, min_length=1, max_length=200)
    query: str | None = Field(default=None, min_length=1, max_length=300)
    filters: dict[str, Any] = Field(default_factory=dict)
    confirmation: bool = False
    confidence: float | None = Field(default=None, ge=0, le=1)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value: object) -> object:
        return str(value).strip().lower() if value is not None else value


class TelegramNaturalLanguageError(Exception):
    """An LLM response was unavailable or did not satisfy the intent contract."""


class TelegramNaturalLanguageParser:
    """Translate text into a whitelisted TelegramIntent, never execute it."""

    ACTIONS = {
        "list_searches": "searches",
        "list": "searches",
        "show_search": "search_show",
        "create_search": "search_add",
        "update_search": "update_search",
        "enable_search": "search_enable",
        "disable_search": "search_disable",
        "delete_search": "search_delete",
        "run_search": "search_run",
        "help": "help",
        "unknown": "unknown",
    }
    FILTERS = {
        "min_price",
        "max_price",
        "include",
        "exclude",
        "title_include",
        "title_exclude",
        "description_include",
        "description_exclude",
        "brand",
        "condition",
        "category_id",
        "interval_seconds",
    }

    def __init__(self, completer: ChatCompleter) -> None:
        self.completer = completer

    async def parse(self, text: str) -> TelegramIntent:
        request = {
            "model": getattr(self.completer, "model", ""),
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": text[:2000]},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        try:
            response = await self.completer.complete(request)
            content = response.get("choices", [{}])[0].get("message", {}).get("content")
            if not isinstance(content, str):
                raise ValueError("missing JSON content")
            payload = TelegramIntentPayload.model_validate(json.loads(content))
        except (
            ListingAnalysisError,
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            IndexError,
        ) as exc:
            raise TelegramNaturalLanguageError("invalid natural language intent") from exc
        action = self.ACTIONS.get(payload.action)
        if action is None:
            raise TelegramNaturalLanguageError("unsupported natural language action")
        if action == "unknown":
            return TelegramIntent("unknown", raw_text=text)
        filters = {key: value for key, value in payload.filters.items() if key in self.FILTERS}
        if len(filters) != len(payload.filters):
            raise TelegramNaturalLanguageError("unsupported search filter")
        try:
            self._validate_filters(filters)
        except (TypeError, ValueError) as exc:
            raise TelegramNaturalLanguageError("invalid search filter") from exc
        if action in {
            "search_show",
            "search_enable",
            "search_disable",
            "search_delete",
            "search_run",
            "update_search",
        } and not (payload.search_id or payload.search_reference):
            raise TelegramNaturalLanguageError("search reference is required")
        if action == "search_add" and not payload.query:
            raise TelegramNaturalLanguageError("query is required")
        if action == "update_search" and not filters:
            raise TelegramNaturalLanguageError("update contains no fields")
        return TelegramIntent(
            action,
            search_id=payload.search_id,
            search_reference=payload.search_reference,
            query=payload.query,
            filters=filters,
            confirmation=payload.confirmation,
            confidence=payload.confidence,
            raw_text=text,
        )

    @staticmethod
    def _validate_filters(filters: dict[str, Any]) -> None:
        minimum, maximum = filters.get("min_price"), filters.get("max_price")
        for name in ("min_price", "max_price"):
            if name in filters:
                value = Decimal(str(filters[name]))
                if value < 0:
                    raise TelegramNaturalLanguageError("prices must be non-negative")
                filters[name] = value
        if (
            minimum is not None
            and maximum is not None
            and Decimal(str(minimum)) > Decimal(str(maximum))
        ):
            raise TelegramNaturalLanguageError("min_price must not exceed max_price")
        if "interval_seconds" in filters and int(filters["interval_seconds"]) <= 0:
            raise TelegramNaturalLanguageError("interval_seconds must be positive")

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a strict Telegram intent parser. Return JSON only. Never return SQL, tools, "
            "URLs, repository methods, or instructions. Allowed actions: list_searches, create_search, "
            "show_search, update_search, enable_search, disable_search, delete_search, run_search, "
            "help, unknown. Use search_reference for names, never invent search_id. Use filters only "
            "with min_price, max_price, include, exclude, title_include, title_exclude, "
            "description_include, description_exclude, brand, condition, category_id, interval_seconds. "
            "If required data is missing or ambiguous, use unknown."
        )
