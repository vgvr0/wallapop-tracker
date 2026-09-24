import json
from decimal import Decimal

import pytest
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.services.search_config import (
    SearchConfigError,
    export_document,
    import_document,
    load_document,
    serialize_document,
)
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.repositories import TrackedSearchRepository

runner = CliRunner()


def config_text() -> str:
    return """version: 1
searches:
  - name: iphone-pro
    query: iphone 15 pro
    enabled: true
    interval_seconds: 600
    filters:
      min_price: 300
      max_price: 600
      include: [256gb]
      exclude: [roto]
      title_include: [iphone]
      brand: Apple
      condition: [as_good_as_new]
    location:
      latitude: 40.4168
      longitude: -3.7038
      max_distance_km: 20
    alerts:
      percentage_drop: 10
      notify_30d_low: true
"""


def test_yaml_and_json_import_export_round_trip(database, tmp_path):
    path = tmp_path / "searches.yaml"
    path.write_text(config_text(), encoding="utf-8")
    document = load_document(path)
    with database.transaction() as session:
        created, updated = import_document(session, document)
        assert (created, updated) == (1, 0)
    with database.session() as session:
        exported = export_document(TrackedSearchRepository(session).list_all())
    yaml_output = serialize_document(exported, "yaml")
    json_output = serialize_document(exported, "json")
    assert "version: 1" in yaml_output
    assert json.loads(json_output)["searches"][0]["name"] == "iphone-pro"
    assert "created_at" not in yaml_output
    assert "chat_id" not in yaml_output
    assert "secret" not in yaml_output


def test_cli_dry_run_does_not_write(database, tmp_path, monkeypatch):
    db_url = "sqlite:///" + str(tmp_path / "dry-run.db")
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", db_url)
    database.close()
    config = tmp_path / "searches.yaml"
    config.write_text(config_text(), encoding="utf-8")
    result = runner.invoke(cli.app, ["search", "import-config", str(config), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "VALID: 1" in result.output and "CREATE: 1" in result.output
    with Database(db_url).session() as session:
        assert TrackedSearchRepository(session).list_all() == []


def test_unknown_version_and_duplicate_name_are_rejected(tmp_path):
    unknown = tmp_path / "unknown.yaml"
    unknown.write_text("version: 99\nsearches: []\n", encoding="utf-8")
    with pytest.raises(SearchConfigError, match="unsupported configuration version"):
        load_document(unknown)
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text(
        "version: 1\nsearches:\n  - name: same\n    query: one\n  - name: same\n    query: two\n",
        encoding="utf-8",
    )
    with pytest.raises(SearchConfigError, match="unique"):
        load_document(duplicate)


def test_invalid_coordinates_are_rejected(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text(
        "version: 1\nsearches:\n  - name: bad\n    query: item\n    location: {latitude: 91, longitude: 0, max_distance_km: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(SearchConfigError, match="latitude"):
        load_document(path)


def test_update_existing_is_idempotent(database):
    document = load_document_from_text(config_text())
    with database.transaction() as session:
        import_document(session, document)
    with database.transaction() as session:
        assert import_document(session, document, update_existing=True) == (0, 1)
    with database.session() as session:
        record = TrackedSearchRepository(session).list_all()[0]
        assert record.query == "iphone 15 pro"
        assert record.max_price == Decimal("600")


def test_atomic_import_rolls_back_on_write_error(database, monkeypatch):
    source = "version: 1\nsearches:\n  - name: one\n    query: one\n  - name: two\n    query: two\n"
    document = load_document_from_text(source)
    original = TrackedSearchRepository.create
    calls = 0

    def fail_second(repository, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("synthetic failure")
        return original(repository, *args, **kwargs)

    monkeypatch.setattr(TrackedSearchRepository, "create", fail_second)
    with pytest.raises(ValueError, match="synthetic failure"):
        with database.transaction() as session:
            import_document(session, document)
    with database.session() as session:
        assert TrackedSearchRepository(session).list_all() == []


def load_document_from_text(value: str):
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(
        suffix=".yaml", mode="w", encoding="utf-8", delete=False
    ) as handle:
        handle.write(value)
        name = handle.name
    try:
        return load_document(Path(name))
    finally:
        Path(name).unlink()
