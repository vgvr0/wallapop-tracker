import os

import pytest
from sqlalchemy import inspect, text

from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.repositories import TelegramOwnershipRepository
from wallapop_tracker.telegram_control import TelegramControlService, parse_command

pytestmark = pytest.mark.postgres


@pytest.fixture(scope="module")
def postgres_url():
    url = os.getenv("WALLAPOP_TRACKER_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("WALLAPOP_TRACKER_TEST_POSTGRES_URL is not configured")
    database = Database(url)
    try:
        with database.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        database.close()
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    database.close()
    return url


def test_postgres_telegram_schema_and_multi_chat_update(postgres_url):
    database = Database(postgres_url)
    try:
        inspector = inspect(database.engine)
        assert {"telegram_chats", "telegram_search_owners"} <= set(inspector.get_table_names())
        assert "uq_telegram_chats_chat_id" in {
            constraint["name"] for constraint in inspector.get_unique_constraints("telegram_chats")
        }
        assert "uq_telegram_search_owner" in {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("telegram_search_owners")
        }

        service = TelegramControlService(database)
        service.register_chat(1001, username="alice")
        service.register_chat(2002, username="bob")
        service.execute(1001, parse_command("/search_add iphone --max-price 600"))
        service.execute(2002, parse_command("/search_add camera --max-price 300"))
        service.execute(1001, parse_command("/search_update 1 --max-price 550"))

        with database.session() as session:
            owners = TelegramOwnershipRepository(session)
            assert [row.query for row in owners.list_owned(1001)] == ["iphone"]
            assert [row.query for row in owners.list_owned(2002)] == ["camera"]
            assert owners.resolve_owned(2002, 1) is None
            assert owners.resolve_owned(1001, 1).max_price == 550
            assert len(owners.owner_chat_ids(1)) == 1
            assert owners.owner_chat_ids(1)[0] == 1001
    finally:
        database.close()


def test_postgres_chat_upsert_updates_metadata_without_duplicate_owner(postgres_url):
    database = Database(postgres_url)
    try:
        service = TelegramControlService(database)
        service.register_chat(3003, username="before")
        service.register_chat(3003, username="after")
        with database.session() as session:
            chat = TelegramOwnershipRepository(session).get_chat(3003)
            assert chat is not None
            assert chat.username == "after"
            assert (
                session.execute(
                    text("SELECT count(*) FROM telegram_chats WHERE chat_id=3003")
                ).scalar_one()
                == 1
            )
    finally:
        database.close()
