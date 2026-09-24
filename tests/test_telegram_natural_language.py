import pytest

from wallapop_tracker.telegram_bot import TelegramBot, _Pending
from wallapop_tracker.telegram_control import TelegramIntent
from wallapop_tracker.telegram_natural_language import (
    TelegramNaturalLanguageError,
    TelegramNaturalLanguageParser,
)


class FakeCompleter:
    model = "test-model"

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    async def complete(self, request: dict[str, object]) -> dict[str, object]:
        self.calls += 1
        return {"choices": [{"message": {"content": self.content}}]}


@pytest.mark.asyncio
async def test_parser_maps_create_and_validates_filters():
    completer = FakeCompleter(
        '{"action":"create_search","query":"iPhone 15 Pro","filters":{"max_price":600}}'
    )
    intent = await TelegramNaturalLanguageParser(completer).parse("avísame de iphone")
    assert intent.action == "search_add"
    assert intent.query == "iPhone 15 Pro"
    assert str(intent.filters["max_price"]) == "600"
    assert completer.calls == 1


@pytest.mark.asyncio
async def test_parser_supports_reference_and_rejects_unknown_fields():
    completer = FakeCompleter('{"action":"disable_search","search_reference":"MacBook"}')
    intent = await TelegramNaturalLanguageParser(completer).parse("desactiva el MacBook")
    assert intent.search_reference == "MacBook"

    completer = FakeCompleter(
        '{"action":"create_search","query":"x","filters":{"sql":"drop table"}}'
    )
    with pytest.raises(TelegramNaturalLanguageError):
        await TelegramNaturalLanguageParser(completer).parse("x")


@pytest.mark.asyncio
async def test_parser_rejects_malformed_provider_output():
    completer = FakeCompleter("not-json")
    with pytest.raises(TelegramNaturalLanguageError):
        await TelegramNaturalLanguageParser(completer).parse("anything")


@pytest.mark.asyncio
async def test_explicit_help_and_pending_confirmation_bypass_llm(database):
    class FailingParser:
        async def parse(self, text: str) -> object:
            raise AssertionError("LLM must not be called")

    bot = TelegramBot(database, "token", nl_parser=FailingParser())
    bot.service.register_chat(101)
    replies: list[str] = []

    async def fake_api(client: object, method: str, **payload: object) -> object:
        replies.append(str(payload["text"]))
        return None

    bot.api = fake_api  # type: ignore[method-assign]
    await bot.handle_update(
        object(), {"update_id": 1, "message": {"chat": {"id": 101}, "text": "/help"}}
    )
    assert replies[-1].startswith("Commands:")

    bot.pending[101] = _Pending(
        TelegramIntent("search_delete", search_id=1), "confirmation", None, 9999999999
    )
    assert await bot._handle_text(101, "no") == "Cancelado."
