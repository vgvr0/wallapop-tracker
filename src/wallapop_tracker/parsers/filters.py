"""Parser for observed search filter metadata."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from wallapop_tracker.domain.metadata import AvailableFilter, MetadataOption
from wallapop_tracker.exceptions import WallapopParseError


def parse_available_filters(data: object) -> list[AvailableFilter]:
    if not isinstance(data, Mapping) or not isinstance(data.get("filter_sections"), list):
        raise WallapopParseError("Filters payload has no filter_sections list")
    result: list[AvailableFilter] = []
    for section in data["filter_sections"]:
        if not isinstance(section, Mapping) or not isinstance(section.get("filters"), list):
            raise WallapopParseError("Filter section has no filters list")
        for item in section["filters"]:
            result.append(_parse_filter(item))
    return result


def _parse_filter(data: object) -> AvailableFilter:
    if not isinstance(data, Mapping):
        raise WallapopParseError("Filter entry is not an object")
    filter_id = data.get("id")
    title = data.get("title")
    filter_type = data.get("type")
    if not isinstance(filter_id, str) or not filter_id:
        raise WallapopParseError("Filter entry is missing id, title or type")
    if not isinstance(title, str) or not title:
        raise WallapopParseError("Filter entry is missing id, title or type")
    if not isinstance(filter_type, str) or not filter_type:
        raise WallapopParseError("Filter entry is missing id, title or type")
    type_data = data.get("type_data", {})
    if not isinstance(type_data, Mapping):
        raise WallapopParseError("Filter type_data must be an object")
    search_params = data.get("search_params", {})
    if not isinstance(search_params, Mapping):
        raise WallapopParseError("Filter search_params must be an object")
    parameter_keys = tuple(
        str(value["param_key"])
        for value in search_params.values()
        if isinstance(value, Mapping) and isinstance(value.get("param_key"), str)
    )
    selection = type_data.get("selection")
    options = _options(type_data)
    return AvailableFilter(
        id=filter_id,
        title=title,
        filter_type=filter_type,
        selection=selection if isinstance(selection, str) else None,
        parameter_keys=parameter_keys,
        options=options,
    )


def _options(type_data: Mapping[str, Any]) -> tuple[MetadataOption, ...]:
    source_data = type_data.get("source_data")
    if not isinstance(source_data, Mapping):
        return ()
    options_data = source_data.get("options_data")
    if not isinstance(options_data, Mapping) or not isinstance(options_data.get("options"), list):
        return ()
    result: list[MetadataOption] = []
    for option in options_data["options"]:
        if not isinstance(option, Mapping):
            continue
        option_id = option.get("id")
        title = option.get("title")
        if isinstance(option_id, (str, int)) and isinstance(title, str):
            result.append(MetadataOption(str(option_id), title))
    return tuple(result)
