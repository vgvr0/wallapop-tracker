"""Versioned, declarative import/export for tracked searches."""

from __future__ import annotations

import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy.orm import Session

from ..domain.search_location import location_from_filters, validate_search_location
from ..storage.models import TrackedSearchRecord
from ..storage.repositories import TrackedSearchRepository


class SearchConfigError(ValueError):
    """A user-facing configuration or import error."""


class FiltersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_price: Decimal | None = Field(default=None, ge=0)
    max_price: Decimal | None = Field(default=None, ge=0)
    include: str | list[str] | None = None
    exclude: str | list[str] | None = None
    include_mode: Literal["any", "all"] = "any"
    title_include: str | list[str] | None = None
    title_include_mode: Literal["any", "all"] = "any"
    description_include: str | list[str] | None = None
    description_include_mode: Literal["any", "all"] = "any"
    title_exclude: str | list[str] | None = None
    description_exclude: str | list[str] | None = None
    title_first_word_include: str | list[str] | None = None
    title_first_word_exclude: str | list[str] | None = None
    regex: str | None = None
    regex_target: str = "both"
    category_id: str | None = None
    brand: str | list[str] | None = None
    model: str | list[str] | None = None
    condition: str | list[str] | None = None

    @model_validator(mode="after")
    def valid_price_range(self) -> FiltersConfig:
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("min_price must not exceed max_price")
        if self.regex_target not in {"title", "description", "both"}:
            raise ValueError("regex_target must be 'title', 'description' or 'both'")
        return self

    def to_storage(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True, exclude_defaults=True)
        data.pop("min_price", None)
        data.pop("max_price", None)
        for source, target in (
            ("brand", "brands"),
            ("model", "models"),
            ("condition", "conditions"),
        ):
            if source in data:
                value = data.pop(source)
                data[target] = [value] if isinstance(value, str) else value
        return data


class LocationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    latitude: float
    longitude: float
    max_distance_km: float = Field(gt=0)

    @model_validator(mode="after")
    def valid_coordinates(self) -> LocationConfig:
        validate_search_location(self.latitude, self.longitude, self.max_distance_km)
        return self


class AlertsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_price: Decimal | None = Field(default=None, ge=0)
    percentage_drop: Decimal | None = Field(default=None, gt=0, le=100)
    deal_score_threshold: Decimal | None = Field(default=None, ge=0, le=100)
    notify_30d_low: bool = False
    notify_90d_low: bool = False
    notify_all_time_low: bool = False
    notify_on_first_run: bool = False


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    query: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    interval_seconds: int = Field(default=600, gt=0)
    filters: FiltersConfig | None = None
    location: LocationConfig | None = None
    alerts: AlertsConfig | None = None
    marketplace: str = "wallapop"


class SearchConfigDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    searches: list[SearchConfig]

    @model_validator(mode="after")
    def unique_names(self) -> SearchConfigDocument:
        names = [search.name for search in self.searches]
        if len(names) != len(set(names)):
            raise ValueError("search names must be unique")
        return self


def load_document(path: Path) -> SearchConfigDocument:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise SearchConfigError(f"cannot read configuration {path}: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise SearchConfigError("configuration must be an object containing version and searches")
    if raw.get("version") != 1:
        raise SearchConfigError(f"unsupported configuration version: {raw.get('version')!r}")
    try:
        return SearchConfigDocument.model_validate(raw)
    except ValidationError as exc:
        messages = "; ".join(error["msg"] for error in exc.errors())
        raise SearchConfigError(f"invalid search configuration: {messages}") from exc


def _config_dict(record: TrackedSearchRecord) -> dict[str, Any]:
    filters = json.loads(record.filters_json) if record.filters_json else {}
    if not isinstance(filters, dict):
        raise SearchConfigError(f"stored filters for search {record.id} are not an object")
    canonical_filters: dict[str, Any] = {}
    ordered_filter_keys = (
        "include",
        "exclude",
        "include_mode",
        "title_include",
        "title_include_mode",
        "description_include",
        "description_include_mode",
        "title_exclude",
        "description_exclude",
        "title_first_word_include",
        "title_first_word_exclude",
        "regex",
        "regex_target",
        "category_id",
        "brands",
        "models",
        "conditions",
    )
    for key in ordered_filter_keys:
        if key not in filters or filters[key] in (None, [], ""):
            continue
        output_key = {"brands": "brand", "models": "model", "conditions": "condition"}.get(key, key)
        value = filters[key]
        if (
            output_key in {"brand", "model", "condition"}
            and isinstance(value, list)
            and len(value) == 1
        ):
            value = value[0]
        canonical_filters[output_key] = value
    if record.min_price is not None:
        canonical_filters["min_price"] = record.min_price
    if record.max_price is not None:
        canonical_filters["max_price"] = record.max_price

    location_values = validate_search_location(
        record.latitude, record.longitude, record.max_distance_km
    )
    if location_values == (None, None, None):
        location_values = location_from_filters(filters)
    location = None
    if all(value is not None for value in location_values):
        location = dict(
            zip(("latitude", "longitude", "max_distance_km"), location_values, strict=True)
        )
    for key in ("latitude", "longitude", "max_distance_km"):
        filters.pop(key, None)
    alerts: dict[str, Any] = {}
    for key, value in (
        ("target_price", record.target_price),
        ("percentage_drop", record.percentage_drop_threshold),
        ("deal_score_threshold", record.deal_score_threshold),
        ("notify_30d_low", record.notify_on_30d_low),
        ("notify_90d_low", record.notify_on_90d_low),
        ("notify_all_time_low", record.notify_on_all_time_low),
        ("notify_on_first_run", record.notify_on_first_run),
    ):
        if value not in (None, False):
            alerts[key] = value
    result: dict[str, Any] = {
        "name": record.name or record.query,
        "query": record.query,
        "enabled": record.enabled,
        "interval_seconds": record.interval_seconds,
    }
    if canonical_filters:
        result["filters"] = canonical_filters
    if location:
        result["location"] = location
    if alerts:
        result["alerts"] = alerts
    if record.marketplace != "wallapop":
        result["marketplace"] = record.marketplace
    return result


def export_document(records: list[TrackedSearchRecord]) -> SearchConfigDocument:
    ordered = sorted(
        records,
        key=lambda row: ((row.name or row.query).casefold(), row.query.casefold()),
    )
    return SearchConfigDocument.model_validate(
        {"version": 1, "searches": [_config_dict(row) for row in ordered]}
    )


def serialize_document(document: SearchConfigDocument, format_name: str) -> str:
    payload = _json_safe(
        {"version": 1, "searches": [_config_dict_from_model(item) for item in document.searches]}
    )
    if format_name == "json":
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, default_flow_style=False)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _config_dict_from_model(item: SearchConfig) -> dict[str, Any]:
    data: dict[str, Any] = {
        "name": item.name,
        "query": item.query,
        "enabled": item.enabled,
        "interval_seconds": item.interval_seconds,
    }
    if item.filters is not None:
        data["filters"] = item.filters.model_dump(exclude_none=True, exclude_defaults=True)
    if item.location is not None:
        data["location"] = item.location.model_dump()
    if item.alerts is not None:
        data["alerts"] = item.alerts.model_dump(exclude_none=True, exclude_defaults=True)
    if item.marketplace != "wallapop":
        data["marketplace"] = item.marketplace
    return data


def record_values(item: SearchConfig) -> dict[str, Any]:
    filters = item.filters.to_storage() if item.filters else {}
    if item.location:
        filters.update(item.location.model_dump())
    alerts = item.alerts or AlertsConfig()
    return {
        "query": item.query,
        "name": item.name,
        "min_price": (item.filters.min_price if item.filters else None),
        "max_price": (item.filters.max_price if item.filters else None),
        **(
            {
                "latitude": item.location.latitude,
                "longitude": item.location.longitude,
                "max_distance_km": item.location.max_distance_km,
            }
            if item.location is not None
            else {}
        ),
        "filters": filters,
        "interval_seconds": item.interval_seconds,
        "notify_on_first_run": alerts.notify_on_first_run,
        "target_price": alerts.target_price,
        "percentage_drop_threshold": alerts.percentage_drop,
        "deal_score_threshold": alerts.deal_score_threshold,
        "notify_on_30d_low": alerts.notify_30d_low,
        "notify_on_90d_low": alerts.notify_90d_low,
        "notify_on_all_time_low": alerts.notify_all_time_low,
        "marketplace": item.marketplace,
    }


def import_document(
    session: Session,
    document: SearchConfigDocument,
    *,
    update_existing: bool = False,
) -> tuple[int, int]:
    repository = TrackedSearchRepository(session)
    existing = {row.name: row for row in repository.list_all() if row.name}
    conflicts = [
        item.name for item in document.searches if item.name in existing and not update_existing
    ]
    if conflicts:
        raise SearchConfigError("search name already exists: " + ", ".join(sorted(conflicts)))
    created = updated = 0
    for item in document.searches:
        values = record_values(item)
        record = existing.get(item.name)
        if record is None:
            record = repository.create(**values)
            record.enabled = item.enabled
            session.flush()
            created += 1
        else:
            for key, value in values.items():
                if key == "filters":
                    record.filters_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
                else:
                    setattr(record, key, value)
            record.enabled = item.enabled
            from datetime import UTC, datetime

            record.updated_at = datetime.now(UTC)
            session.flush()
            updated += 1
    for item in document.searches:
        if item.name in existing:
            existing[item.name].enabled = item.enabled
    return created, updated
