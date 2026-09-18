"""Small typed models for Wallapop discovery metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    name: str
    parent_id: str | None = None
    children: tuple[Category, ...] = ()
    attribute_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MetadataOption:
    id: str
    name: str


@dataclass(frozen=True)
class Brand:
    id: str | None
    name: str


@dataclass(frozen=True)
class ProductModel:
    id: str | None
    name: str


@dataclass(frozen=True)
class AvailableFilter:
    id: str
    title: str
    filter_type: str
    selection: str | None = None
    parameter_keys: tuple[str, ...] = ()
    options: tuple[MetadataOption, ...] = ()
