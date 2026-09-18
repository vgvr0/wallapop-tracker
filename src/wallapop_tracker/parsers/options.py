"""Shared parser for remote option lists used by metadata endpoints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wallapop_tracker.domain.metadata import MetadataOption
from wallapop_tracker.exceptions import WallapopParseError


def parse_options(data: object, expected_id: str) -> list[MetadataOption]:
    if not isinstance(data, Mapping) or data.get("id") != expected_id:
        raise WallapopParseError(f"{expected_id} payload has an unexpected id")
    options = data.get("options")
    if not isinstance(options, list):
        raise WallapopParseError(f"{expected_id} payload has no options list")
    result: list[MetadataOption] = []
    for option in options:
        if not isinstance(option, Mapping):
            raise WallapopParseError(f"{expected_id} option is not an object")
        option_id = option.get("id")
        title = option.get("title")
        if not isinstance(title, str) or not title:
            raise WallapopParseError(f"{expected_id} option has no title")
        result.append(MetadataOption(_optional_id(option_id), title))
    return result


def _optional_id(value: Any) -> str:
    return str(value) if isinstance(value, (str, int)) else ""
