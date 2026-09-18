"""Parser for the observed remote model-filter response."""

from __future__ import annotations

from wallapop_tracker.domain.metadata import ProductModel

from .options import parse_options


def parse_models(data: object) -> list[ProductModel]:
    return [ProductModel(option.id, option.name) for option in parse_options(data, "model")]
