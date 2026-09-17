import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def fixture_data() -> Any:
    def load(name: str) -> Any:
        return json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))

    return load
