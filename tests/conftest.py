import json
from pathlib import Path
from typing import Any

import pytest

from wallapop_tracker.storage.database import Database


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
