import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from wallapop_tracker.storage.database import Database


def _clear_postgres_test_state(url: str) -> None:
    database_name = make_url(url).database
    if database_name is None or "test" not in database_name.lower():
        raise RuntimeError(
            "Refusing PostgreSQL test cleanup: database name must contain 'test' "
            f"(got {database_name!r})"
        )

    database = Database(url)
    try:
        with database.engine.begin() as connection:
            tables = inspect(connection).get_table_names(schema="public")
            tables = [table for table in tables if table != "alembic_version"]
            if tables:
                quoted_tables = ", ".join(f'public."{table}"' for table in tables)
                connection.execute(text(f"TRUNCATE TABLE {quoted_tables} RESTART IDENTITY CASCADE"))
    finally:
        database.close()


@pytest.fixture(autouse=True)
def isolate_postgres_test_state(request: pytest.FixtureRequest):
    if not request.node.get_closest_marker("postgres"):
        yield
        return

    if "postgres_url" in request.fixturenames:
        url = request.getfixturevalue("postgres_url")
    elif "postgres_database" in request.fixturenames:
        database = request.getfixturevalue("postgres_database")
        url = database.engine.url.render_as_string(hide_password=False)
    else:
        raise RuntimeError(
            "PostgreSQL tests must expose a postgres_url or postgres_database fixture"
        )
    _clear_postgres_test_state(url)
    yield


@pytest.fixture
def database():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    yield database
    database.close()


@pytest.fixture
def fixture_data() -> Any:
    def load(name: str) -> Any:
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))

    return load
