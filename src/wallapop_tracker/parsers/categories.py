"""Parser for the observed Wallapop categories response."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wallapop_tracker.domain.metadata import Category
from wallapop_tracker.exceptions import WallapopParseError


def parse_categories(data: object) -> list[Category]:
    if not isinstance(data, Mapping) or not isinstance(data.get("categories"), list):
        raise WallapopParseError("Categories payload has no categories list")
    return [_parse_category(item) for item in data["categories"]]


def _parse_category(data: object) -> Category:
    if not isinstance(data, Mapping):
        raise WallapopParseError("Category entry is not an object")
    category_id = _required_id(data.get("id"), "category id")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise WallapopParseError("Category entry has no name")
    children_raw = data.get("subcategories", data.get("children", []))
    if not isinstance(children_raw, list):
        raise WallapopParseError("Category children must be a list")
    attributes = data.get("attributes", {})
    if not isinstance(attributes, Mapping):
        raise WallapopParseError("Category attributes must be an object")
    return Category(
        id=category_id,
        name=name,
        parent_id=_optional_id(data.get("parent_id")),
        children=tuple(_parse_category(child) for child in children_raw),
        attribute_ids=tuple(str(key) for key in attributes),
    )


def _required_id(value: Any, label: str) -> str:
    result = _optional_id(value)
    if result is None:
        raise WallapopParseError(f"{label} is missing")
    return result


def _optional_id(value: Any) -> str | None:
    if isinstance(value, (str, int)) and str(value).strip():
        return str(value)
    return None
