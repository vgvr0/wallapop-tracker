"""Small helpers shared by external-response parsers."""

from datetime import UTC, datetime
from typing import Any

from wallapop_tracker.exceptions import WallapopParseError


def parse_condition(raw: Any, attributes: Any = None) -> tuple[str | None, str | None]:
    """Return Wallapop's condition code and visible label from known shapes."""
    candidates = [raw]
    if isinstance(attributes, dict):
        candidates.append(attributes.get("condition"))
    for value in candidates:
        if isinstance(value, str):
            return value, None
        if isinstance(value, dict):
            code = value.get("value")
            label = value.get("text") or value.get("iconText") or value.get("icon_text")
            return (
                code if isinstance(code, str) else None,
                label if isinstance(label, str) else None,
            )
    return None, None


def first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def parse_timestamp(value: Any) -> datetime | None:
    """Parse ISO values or Unix seconds/milliseconds into aware datetimes.

    Wallapop's observed ``register_date`` is Unix milliseconds. Numeric values
    below 100_000_000_000 are treated as seconds; larger values as milliseconds.
    This explicit boundary is kept in one helper because the API has used both
    units in different contexts.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        seconds = float(value) / (1000 if abs(value) >= 100_000_000_000 else 1)
        return datetime.fromtimestamp(seconds, tz=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise WallapopParseError(f"Invalid datetime value: {value!r}") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise WallapopParseError(f"Unsupported datetime value: {value!r}")
