"""Validation for explicit tracked-search geolocation."""

from typing import Any


def validate_search_location(
    latitude: float | int | str | None,
    longitude: float | int | str | None,
    max_distance_km: float | int | str | None,
) -> tuple[float | None, float | None, float | None]:
    """Normalize and validate the optional location tuple."""
    values = (latitude, longitude, max_distance_km)
    try:
        normalized = tuple(None if value is None else float(value) for value in values)
    except (TypeError, ValueError) as exc:
        raise ValueError("search location values must be numeric") from exc
    lat, lon, distance = normalized
    if (lat is None) != (lon is None):
        raise ValueError("latitude and longitude must be provided together")
    if distance is not None and lat is None:
        raise ValueError("max_distance_km requires latitude and longitude")
    if lat is not None and not -90 <= lat <= 90:
        raise ValueError("latitude must be between -90 and 90")
    if lon is not None and not -180 <= lon <= 180:
        raise ValueError("longitude must be between -180 and 180")
    if distance is not None and distance <= 0:
        raise ValueError("max_distance_km must be positive")
    return lat, lon, distance


def location_from_filters(
    filters: dict[str, Any] | None,
) -> tuple[float | None, float | None, float | None]:
    """Read the pre-column location representation used by legacy searches."""
    if not filters:
        return None, None, None
    distance = filters.get("max_distance_km", filters.get("distance"))
    return validate_search_location(filters.get("latitude"), filters.get("longitude"), distance)
