"""Supported marketplace identities."""

from enum import StrEnum


class Marketplace(StrEnum):
    WALLAPOP = "wallapop"


def require_supported_marketplace(value: str | Marketplace) -> Marketplace:
    """Normalize a marketplace value and reject adapters not implemented yet."""
    try:
        return Marketplace(value)
    except ValueError as exc:
        raise ValueError(f"unsupported marketplace: {value}") from exc
