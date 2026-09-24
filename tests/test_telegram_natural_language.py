import pytest

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
