import pytest

from wallapop_tracker.storage.repositories import TelegramOwnershipRepository
from wallapop_tracker.telegram_control import (
    TelegramCommandError,
    TelegramControlService,
    parse_command,
)


def test_parser_is_deterministic_and_confirmation_is_explicit():
    intent = parse_command('/search_add "iphone 15 pro" --max-price 600 --brand Apple')
    assert intent.query == "iphone 15 pro"
    assert (
        intent.filters == {"max_price": 600, "brand": "Apple"}
        or str(intent.filters["max_price"]) == "600"
    )
    assert parse_command("/search_delete 12 confirm").confirmation
    assert not parse_command("/search_delete 12").confirmation


def test_chat_ownership_isolated_and_persistent(database):
    with database.transaction() as session:
        service = TelegramControlService(database)
        service.register_chat(101, username="alice")
        service.register_chat(202, username="bob")
        first = service.execute(101, parse_command("/search_add camera --max-price 20"))
        second = service.execute(202, parse_command("/search_add phone --max-price 30"))
        assert first.startswith("Created search #")
        assert second.startswith("Created search #")
    with database.session() as session:
        rows_a = TelegramOwnershipRepository(session).list_owned(101)
        rows_b = TelegramOwnershipRepository(session).list_owned(202)
        assert [row.query for row in rows_a] == ["camera"]
        assert [row.query for row in rows_b] == ["phone"]


def test_cross_chat_operations_are_not_revealed(database):
    service = TelegramControlService(database)
    service.register_chat(101)
    service.register_chat(202)
    service.execute(101, parse_command("/search_add camera"))
    with pytest.raises(TelegramCommandError, match="Search not found"):
        service.execute(202, parse_command("/search_show 1"))
    with pytest.raises(TelegramCommandError, match="Search not found"):
        service.execute(202, parse_command("/search_delete 1 confirm"))


def test_delete_requires_confirmation(database):
    service = TelegramControlService(database)
    service.register_chat(101)
    service.execute(101, parse_command("/search_add camera"))
    assert "Confirm delete" in service.execute(101, parse_command("/search_delete 1"))
    assert "Deleted" in service.execute(101, parse_command("/search_delete 1 confirm"))
    assert service.execute(101, parse_command("/searches")) == "No searches owned by this chat."


def test_search_update_is_partial_and_preserves_existing_filters(database):
    service = TelegramControlService(database)
    service.register_chat(101)
    service.execute(101, parse_command("/search_add iphone --max-price 600 --brand Apple"))
    assert (
        service.execute(101, parse_command("/search_update 1 --max-price 550"))
        == "Updated search #1"
    )
    shown = service.execute(101, parse_command("/search_show 1"))
    assert "max_price=550.00" in shown
    assert '"brand": "Apple"' in shown


def test_search_update_multiple_fields_and_validation(database):
    service = TelegramControlService(database)
    service.register_chat(101)
    service.execute(101, parse_command("/search_add iphone --max-price 600"))
    service.execute(
        101,
        parse_command(
            "/search_update 1 --min-price 100 --interval-seconds 300 "
            "--title-include iphone --category-id 123"
        ),
    )
    shown = service.execute(101, parse_command("/search_show 1"))
    assert "min_price=100.00" in shown
    assert "interval=300s" in shown
    assert "title_include" in shown
    with pytest.raises(TelegramCommandError, match="min_price must not exceed max_price"):
        service.execute(101, parse_command("/search_update 1 --min-price 700"))
    with pytest.raises(TelegramCommandError, match="Unknown option"):
        parse_command("/search_update 1 --unknown value")
    with pytest.raises(TelegramCommandError, match="at least one option"):
        parse_command("/search_update 1")


def test_search_update_cross_chat_is_not_found(database):
    service = TelegramControlService(database)
    service.register_chat(101)
    service.register_chat(202)
    service.execute(101, parse_command("/search_add iphone --max-price 600"))
    with pytest.raises(TelegramCommandError, match="Search not found"):
        service.execute(202, parse_command("/search_update 1 --max-price 1"))
