"""Parser for the observed remote brand-filter response."""

from __future__ import annotations

from wallapop_tracker.domain.metadata import Brand

from .options import parse_options


def parse_brands(data: object) -> list[Brand]:
    return [Brand(option.id, option.name) for option in parse_options(data, "brand")]
