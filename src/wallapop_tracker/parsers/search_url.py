"""Pure parsing of public Wallapop search URLs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlsplit


class SearchURLParseError(ValueError):
    """A Wallapop search URL cannot be imported safely."""


@dataclass(frozen=True)
class SearchImportResult:
    """Semantic values observed directly in a Wallapop search URL."""

    query: str | None
    category_id: str | None
    min_price: Decimal | None
    max_price: Decimal | None
    latitude: float | None
    longitude: float | None
    distance: float | None
    shipping_required: bool | None
    condition: str | None
    brand: str | None
    unknown_params: Mapping[str, tuple[str, ...]]
    ignored_params: tuple[str, ...] = ()

    def search_filters(self) -> dict[str, object]:
        """Return JSON-compatible filters for ``TrackedSearch`` creation."""
        filters: dict[str, object] = {}
        for key in ("category_id", "condition", "brand"):
            value = getattr(self, key)
            if value is not None:
                filters[key] = value
        if self.shipping_required is not None:
            filters["shipping_required"] = self.shipping_required
        for key in ("latitude", "longitude", "distance"):
            value = getattr(self, key)
            if value is not None:
                filters[key] = value
        return filters


_ALIASES: dict[str, str] = {
    "keywords": "query",
    "query": "query",
    "kws": "query",
    "category_id": "category_id",
    "catIds": "category_id",
    "min_price": "min_price",
    "minPrice": "min_price",
    "min_sale_price": "min_price",
    "max_price": "max_price",
    "maxPrice": "max_price",
    "max_sale_price": "max_price",
    "latitude": "latitude",
    "longitude": "longitude",
    "distance": "distance",
    "distance_in_km": "distance",
    "dist": "distance",
    "shipping": "shipping_required",
    "shipping_required": "shipping_required",
    "condition": "condition",
    "brand": "brand",
}

_IGNORED_PARAMS = frozenset(
    {
        "source",
        "filters_source",
        "order_by",
        "orderBy",
        "search_id",
        "next_page",
        "section_type",
        "page",
        "_p",
    }
)


def parse_search_url(url: str) -> SearchImportResult:
    """Parse a compatible Wallapop search URL without network access."""
    if not isinstance(url, str) or not url.strip():
        raise SearchURLParseError("search URL is required")
    parsed = urlsplit(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"es.wallapop.com", "www.wallapop.com"}:
        raise SearchURLParseError("URL must use HTTPS and a Wallapop host")
    if parsed.username or parsed.password or parsed.port is not None:
        raise SearchURLParseError("URL must not contain credentials or a custom port")
    if parsed.path.rstrip("/") not in {"/app/search", "/search"}:
        raise SearchURLParseError("URL is Wallapop but not a search URL")
    try:
        values = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=False)
    except ValueError as exc:
        raise SearchURLParseError("URL query is malformed") from exc

    canonical: dict[str, str] = {}
    unknown: dict[str, tuple[str, ...]] = {}
    ignored: set[str] = set()
    for raw_name, raw_values in values.items():
        name = _ALIASES.get(raw_name)
        if name is None:
            if raw_name in _IGNORED_PARAMS:
                ignored.add(raw_name)
            else:
                unknown[raw_name] = tuple(raw_values)
            continue
        value = _one_value(raw_name, name, raw_values)
        if name in canonical and canonical[name] != value:
            raise SearchURLParseError(f"parameter {name!r} appears with conflicting values")
        canonical[name] = value

    query = _text(canonical.get("query"))
    category_id = _text(canonical.get("category_id"))
    minimum = _decimal(canonical.get("min_price"), "min_price")
    maximum = _decimal(canonical.get("max_price"), "max_price")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise SearchURLParseError("min_price must not exceed max_price")
    latitude = _coordinate(canonical.get("latitude"), "latitude", -90, 90)
    longitude = _coordinate(canonical.get("longitude"), "longitude", -180, 180)
    distance = _positive_float(canonical.get("distance"), "distance")
    shipping = _boolean(canonical.get("shipping_required"), "shipping_required")
    return SearchImportResult(
        query=query,
        category_id=category_id,
        min_price=minimum,
        max_price=maximum,
        latitude=latitude,
        longitude=longitude,
        distance=distance,
        shipping_required=shipping,
        condition=_text(canonical.get("condition")),
        brand=_text(canonical.get("brand")),
        unknown_params=unknown,
        ignored_params=tuple(sorted(ignored)),
    )


def _one_value(name: str, canonical: str, values: list[str]) -> str:
    if not values:
        raise SearchURLParseError(f"parameter {name!r} has no value")
    if len(set(values)) != 1:
        raise SearchURLParseError(f"parameter {canonical!r} appears with conflicting values")
    return values[0].strip()


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _decimal(value: str | None, name: str) -> Decimal | None:
    if value is None or not value:
        return None
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise SearchURLParseError(f"{name} must be a valid decimal") from exc
    if not result.is_finite() or result < 0:
        raise SearchURLParseError(f"{name} must be a non-negative decimal")
    return result


def _coordinate(value: str | None, name: str, minimum: int, maximum: int) -> float | None:
    result = _number(value, name)
    if result is not None and not minimum <= result <= maximum:
        raise SearchURLParseError(f"{name} must be between {minimum} and {maximum}")
    return result


def _positive_float(value: str | None, name: str) -> float | None:
    result = _number(value, name)
    if result is not None and result <= 0:
        raise SearchURLParseError(f"{name} must be greater than zero")
    return result


def _number(value: str | None, name: str) -> float | None:
    if value is None or not value:
        return None
    try:
        result = float(value)
    except ValueError as exc:
        raise SearchURLParseError(f"{name} must be a valid number") from exc
    if result != result or result in {float("inf"), float("-inf")}:
        raise SearchURLParseError(f"{name} must be finite")
    return result


def _boolean(value: str | None, name: str) -> bool | None:
    if value is None or not value:
        return None
    normalized = value.casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    raise SearchURLParseError(f"{name} must be a boolean")
