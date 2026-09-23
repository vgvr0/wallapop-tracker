"""Integration coverage for the advanced title/description search filters."""

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from wallapop_tracker import cli
from wallapop_tracker.api import create_app
from wallapop_tracker.domain.filters import filters_from_config
from wallapop_tracker.models import Listing
from wallapop_tracker.providers.search import SearchRequest
from wallapop_tracker.services.search_tracker import SearchTracker
from wallapop_tracker.storage.database import Database
from wallapop_tracker.storage.models import TrackedSearchRecord
from wallapop_tracker.storage.repositories import TrackedSearchRepository


def listing(
    title: str | None = "iPhone 15 Pro 256GB",
    description: str | None = "Con factura y caja original",
    price: str | None = "600",
) -> Listing:
    return Listing(
        item_id="item-1",
        user_id="seller-1",
        title=title,
        description=description,
        price=Decimal(price) if price is not None else None,
        currency="EUR",
        url="https://es.wallapop.com/item/item-1",
    )


class StubSearchProvider:
    def __init__(self, listings: list[Listing]) -> None:
        self.listings = listings

    async def search(self, request: SearchRequest) -> list[Listing]:
        return list(self.listings)


def test_filters_round_trip_through_tracked_search_persistence(database):
    filters = {
        "title_include": ["iphone", "15 pro"],
        "title_include_mode": "all",
        "description_include": ["factura"],
        "description_include_mode": "any",
        "title_exclude": ["funda"],
        "description_exclude": ["para piezas"],
        "title_first_word_include": ["iphone"],
        "title_first_word_exclude": ["lote"],
    }
    with database.transaction() as session:
        created = TrackedSearchRepository(session).create("iphone", name="iphone", filters=filters)
        search_id = created.id

    with database.session() as session:
        record = session.get(TrackedSearchRecord, search_id)
        assert record is not None
        assert record.filters_json is not None
        assert json.loads(record.filters_json) == filters
        engine = filters_from_config(json.loads(record.filters_json))

    assert engine.matches(listing())
    assert not engine.matches(listing(title="Funda iPhone 15 Pro"))
    assert not engine.matches(listing(description="Ideal para piezas"))
    assert not engine.matches(listing(title="iPhone 15 256GB"))


def test_legacy_searches_without_advanced_fields_keep_working(database):
    with database.transaction() as session:
        legacy_id = (
            TrackedSearchRepository(session)
            .create("iphone", filters={"include": ["256gb"], "exclude": ["roto"]})
            .id
        )
        bare_id = TrackedSearchRepository(session).create("ipad").id

    with database.session() as session:
        legacy = session.get(TrackedSearchRecord, legacy_id)
        bare = session.get(TrackedSearchRecord, bare_id)
        assert legacy is not None and bare is not None
        assert bare.filters_json is None
        legacy_engine = filters_from_config(json.loads(legacy.filters_json or "{}"))

    assert legacy_engine.matches(listing())
    assert not legacy_engine.matches(listing(title="iPhone roto 256GB"))
    assert filters_from_config({}).matches(listing(title=None, description=None))


def test_repository_rejects_invalid_advanced_filter_payloads(database):
    with pytest.raises(ValueError, match="title_include_mode"):
        with database.transaction() as session:
            TrackedSearchRepository(session).create(
                "iphone", filters={"title_include_mode": "sometimes"}
            )
    with pytest.raises(ValueError, match="title_include must be a string or a list of strings"):
        with database.transaction() as session:
            TrackedSearchRepository(session).create("iphone", filters={"title_include": 3})


@pytest.mark.asyncio
async def test_search_tracker_applies_advanced_filters_before_persistence(database):
    filters = {
        "title_include": ["iphone"],
        "description_include": ["factura"],
        "title_exclude": ["funda"],
        "description_exclude": ["para piezas"],
        "title_first_word_include": ["iphone"],
    }
    with database.transaction() as session:
        search_id = (
            TrackedSearchRepository(session)
            .create("iphone", filters=filters, notify_on_first_run=True)
            .id
        )

    provider = StubSearchProvider(
        [
            listing(),
            listing(title="Funda iPhone 15"),
            listing(description="Ideal para piezas"),
            listing(title="iPhone 15 sin accesorios", description="Sin factura"),
        ]
    )
    result = await SearchTracker(provider, database).track_search(search_id)

    assert result.items_fetched == 4
    assert result.matched_listings == 1


def test_search_cli_parses_advanced_filter_options(tmp_path, monkeypatch):
    path = tmp_path / "advanced-cli.db"
    monkeypatch.setenv("WALLAPOP_TRACKER_DB_URL", f"sqlite:///{path}")
    database = Database(f"sqlite:///{path}")
    database.create_all()
    database.close()

    runner = CliRunner()
    added = runner.invoke(
        cli.app,
        [
            "search",
            "add",
            "--name",
            "iphone limpio",
            "--query",
            "iphone",
            "--title-include",
            "iphone",
            "--title-include",
            "15 pro",
            "--title-include-mode",
            "all",
            "--description-include",
            "factura",
            "--title-exclude",
            "funda",
            "--description-exclude",
            "para piezas",
            "--title-first-word-include",
            "iphone",
            "--title-first-word-exclude",
            "lote",
        ],
    )
    assert added.exit_code == 0, added.output
    assert "title_include: iphone, 15 pro (all)" in added.output

    with Database(f"sqlite:///{path}").session() as session:
        record = session.get(TrackedSearchRecord, 1)
        assert record is not None and record.filters_json is not None
        filters = json.loads(record.filters_json)
        assert filters["title_include"] == ["iphone", "15 pro"]
        assert filters["title_include_mode"] == "all"
        assert filters["description_include"] == ["factura"]
        assert filters["title_exclude"] == ["funda"]
        assert filters["description_exclude"] == ["para piezas"]
        assert filters["title_first_word_include"] == ["iphone"]
        assert filters["title_first_word_exclude"] == ["lote"]

    shown = runner.invoke(cli.app, ["search", "show", "1"])
    assert shown.exit_code == 0
    assert '"title_include": ["iphone", "15 pro"]' in shown.output

    updated = runner.invoke(
        cli.app,
        [
            "search",
            "update",
            "1",
            "--description-include",
            "garantia",
            "--title-exclude",
            "carcasa",
        ],
    )
    assert updated.exit_code == 0, updated.output

    with Database(f"sqlite:///{path}").session() as session:
        record = session.get(TrackedSearchRecord, 1)
        assert record is not None and record.filters_json is not None
        filters = json.loads(record.filters_json)
        assert filters["description_include"] == ["garantia"]
        assert filters["title_exclude"] == ["carcasa"]
        # Unrelated keys are preserved by the update.
        assert filters["title_include"] == ["iphone", "15 pro"]
        assert filters["title_first_word_include"] == ["iphone"]

    cleared = runner.invoke(cli.app, ["search", "update", "1", "--clear-text-filters"])
    assert cleared.exit_code == 0, cleared.output
    with Database(f"sqlite:///{path}").session() as session:
        record = session.get(TrackedSearchRecord, 1)
        assert record is not None and record.filters_json is not None
        filters = json.loads(record.filters_json)
        assert "title_include" not in filters
        assert "description_exclude" not in filters
        assert filters["include"] == []

    invalid = runner.invoke(cli.app, ["search", "update", "1", "--title-include-mode", "nope"])
    assert invalid.exit_code != 0
    missing = runner.invoke(cli.app, ["search", "update", "999", "--title-exclude", "funda"])
    assert missing.exit_code != 0


def test_search_api_parses_advanced_filters_on_create_and_update():
    database = Database("sqlite+pysqlite:///:memory:")
    database.create_all()
    app = create_app(database)
    filters = {
        "title_include": ["iphone"],
        "title_include_mode": "all",
        "description_include": ["factura"],
        "title_exclude": ["funda"],
        "description_exclude": ["para piezas"],
        "title_first_word_include": ["iphone"],
        "title_first_word_exclude": ["lote"],
    }
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/searches",
                json={"name": "iphone limpio", "query": "iphone", "filters": filters},
            )
            assert created.status_code == 201, created.text
            search_id = created.json()["id"]
            assert created.json()["filters"] == filters

            fetched = client.get(f"/api/v1/searches/{search_id}")
            assert fetched.status_code == 200
            assert fetched.json()["filters"] == filters

            patched = client.patch(
                f"/api/v1/searches/{search_id}",
                json={"filters": {**filters, "title_exclude": ["carcasa"]}},
            )
            assert patched.status_code == 200, patched.text
            assert patched.json()["filters"]["title_exclude"] == ["carcasa"]

            invalid = client.post(
                "/api/v1/searches",
                json={"query": "iphone", "filters": {"description_include_mode": "maybe"}},
            )
            assert invalid.status_code == 422
            wrong_type = client.post(
                "/api/v1/searches",
                json={"query": "iphone", "filters": {"title_exclude": 5}},
            )
            assert wrong_type.status_code == 422
    finally:
        database.close()
