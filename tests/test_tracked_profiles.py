from datetime import UTC, datetime

import pytest

from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.repositories import TrackedProfileRepository


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


def test_create_normalizes_alias_and_lists_enabled(database):
    with database.transaction() as session:
        repository = TrackedProfileRepository(session)
        record = repository.create("https://es.wallapop.com/user/a", "user-a", " Andrey ")
        assert record.alias == "andrey"
        assert repository.list_enabled() == [record]


@pytest.mark.parametrize("field", ["alias", "profile_url", "wallapop_user_id"])
def test_create_rejects_duplicates(database, field):
    with database.transaction() as session:
        repository = TrackedProfileRepository(session)
        repository.create("https://es.wallapop.com/user/a", "user-a", "andrey")
        values = {
            "alias": "andrey",
            "profile_url": "https://es.wallapop.com/user/a",
            "wallapop_user_id": "user-a",
        }
        with pytest.raises(ValueError):
            repository.create(values["profile_url"], values["wallapop_user_id"], values["alias"])


def test_disable_remove_preserves_other_tables(database):
    with database.transaction() as session:
        repository = TrackedProfileRepository(session)
        repository.create("https://es.wallapop.com/user/a", "user-a", "andrey")
        repository.disable("andrey")
        assert repository.list_enabled() == []
        repository.enable("andrey")
        repository.update_last_run(
            "andrey",
            datetime.now(UTC),
            __import__(
                "wallapop_tracker.storage.models", fromlist=["TrackingRunStatus"]
            ).TrackingRunStatus.VALID,
        )
        repository.remove("andrey")
        assert repository.list_all() == []
