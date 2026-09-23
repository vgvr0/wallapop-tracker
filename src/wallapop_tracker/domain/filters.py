"""Pure, reusable filters for listing-like domain objects."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import asin, cos, radians, sin, sqrt
from re import Pattern
from typing import Protocol, cast

from wallapop_tracker.models import Listing


class ListingFilter(Protocol):
    """A deterministic predicate over a listing."""

    def matches(self, listing: Listing) -> bool:
        """Return whether ``listing`` satisfies this filter."""


class IncludeMode(StrEnum):
    ANY = "any"
    ALL = "all"


class TextField(StrEnum):
    """Listing text field targeted by one advanced filter."""

    TITLE = "title"
    DESCRIPTION = "description"


_EDGE_NON_WORD = re.compile(r"^[^\w]+|[^\w]+$")


def _casefold(value: str | None) -> str:
    return value.casefold() if value else ""


def normalize_text(value: str | None) -> str:
    """Casefold ``value`` and collapse whitespace without mutating the source.

    This is the single normalization shared by every text filter: comparisons
    always normalize a copy, so the text stored in listings and snapshots is
    never rewritten. Accents are intentionally preserved because the current
    normalization never folded them.
    """

    return " ".join(_casefold(value).split())


def normalize_first_word(value: str | None) -> str:
    """Return the first normalized word of ``value``, or ``""`` when absent.

    Leading and trailing punctuation is removed so ``"¡iPhone 15!"`` matches
    the term ``"iphone"``; inner symbols such as the hyphen in ``"iphone-15"``
    are preserved. An empty, blank or ``None`` title normalizes to ``""`` and
    therefore matches no configured term.
    """

    normalized = normalize_text(value)
    if not normalized:
        return ""
    return _EDGE_NON_WORD.sub("", normalized.split(" ", 1)[0])


def _text(listing: Listing) -> str:
    """Legacy combined title+description haystack (behavior preserved)."""

    return " ".join(
        value for value in (_casefold(listing.title), _casefold(listing.description)) if value
    )


@dataclass(frozen=True)
class PriceFilter:
    min_price: Decimal | None = None
    max_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("min_price must not exceed max_price")

    def matches(self, listing: Listing) -> bool:
        if listing.price is None:
            return self.min_price is None and self.max_price is None
        if self.min_price is not None and listing.price < self.min_price:
            return False
        return not (self.max_price is not None and listing.price > self.max_price)


@dataclass(frozen=True)
class ConditionFilter:
    codes: frozenset[str]

    def __init__(self, codes: Iterable[str]) -> None:
        object.__setattr__(self, "codes", frozenset(code for code in codes if code))

    def matches(self, listing: Listing) -> bool:
        value = listing.condition_code or listing.condition
        return not self.codes or (value is not None and value.casefold() in {c.casefold() for c in self.codes})


@dataclass(frozen=True)
class CategoryFilter:
    category_id: str

    def matches(self, listing: Listing) -> bool:
        return listing.category_id == self.category_id


@dataclass(frozen=True)
class ValueFilter:
    values: frozenset[str]
    attribute: str

    def __init__(self, values: Iterable[str], attribute: str) -> None:
        object.__setattr__(self, "values", frozenset(v.casefold() for v in values if v.strip()))
        object.__setattr__(self, "attribute", attribute)

    def matches(self, listing: Listing) -> bool:
        value = getattr(listing, self.attribute, None)
        return not self.values or (isinstance(value, str) and value.casefold() in self.values)


@dataclass(frozen=True)
class DistanceFilter:
    latitude: float
    longitude: float
    max_distance_km: float

    def matches(self, listing: Listing) -> bool:
        if listing.latitude is None or listing.longitude is None:
            return False
        dlat = radians(listing.latitude - self.latitude)
        dlon = radians(listing.longitude - self.longitude)
        a = sin(dlat / 2) ** 2 + cos(radians(self.latitude)) * cos(radians(listing.latitude)) * sin(dlon / 2) ** 2
        return 6371.0088 * 2 * asin(sqrt(a)) <= self.max_distance_km


@dataclass(frozen=True)
class IncludeTextFilter:
    terms: tuple[str, ...]
    mode: IncludeMode = IncludeMode.ANY

    def __init__(self, terms: Iterable[str], mode: IncludeMode | str = IncludeMode.ANY) -> None:
        normalized = tuple(term.casefold() for term in terms if term.strip())
        object.__setattr__(self, "terms", normalized)
        object.__setattr__(self, "mode", IncludeMode(mode))

    def matches(self, listing: Listing) -> bool:
        if not self.terms:
            return True
        haystack = _text(listing)
        checks = [term in haystack for term in self.terms]
        return all(checks) if self.mode == IncludeMode.ALL else any(checks)


@dataclass(frozen=True)
class ExcludeTextFilter:
    terms: tuple[str, ...]

    def __init__(self, terms: Iterable[str]) -> None:
        object.__setattr__(self, "terms", tuple(term.casefold() for term in terms if term.strip()))

    def matches(self, listing: Listing) -> bool:
        haystack = _text(listing)
        return not any(term in haystack for term in self.terms)


@dataclass(frozen=True)
class FieldIncludeFilter:
    """Require normalized terms inside one field (title or description).

    Unlike :class:`IncludeTextFilter`, this filter never mixes both fields, so
    a term found only in the description cannot satisfy a title requirement.
    """

    terms: tuple[str, ...]
    field: TextField
    mode: IncludeMode = IncludeMode.ANY

    def __init__(
        self,
        terms: Iterable[str],
        field: TextField | str,
        mode: IncludeMode | str = IncludeMode.ANY,
    ) -> None:
        object.__setattr__(
            self,
            "terms",
            tuple(term for term in (normalize_text(item) for item in terms) if term),
        )
        object.__setattr__(self, "field", TextField(field))
        object.__setattr__(self, "mode", IncludeMode(mode))

    def matches(self, listing: Listing) -> bool:
        if not self.terms:
            return True
        haystack = normalize_text(getattr(listing, self.field.value, None))
        checks = [term in haystack for term in self.terms]
        return all(checks) if self.mode == IncludeMode.ALL else any(checks)


@dataclass(frozen=True)
class FieldExcludeFilter:
    """Reject a listing when any normalized term appears inside one field."""

    terms: tuple[str, ...]
    field: TextField

    def __init__(self, terms: Iterable[str], field: TextField | str) -> None:
        object.__setattr__(
            self,
            "terms",
            tuple(term for term in (normalize_text(item) for item in terms) if term),
        )
        object.__setattr__(self, "field", TextField(field))

    def matches(self, listing: Listing) -> bool:
        if not self.terms:
            return True
        haystack = normalize_text(getattr(listing, self.field.value, None))
        return not any(term in haystack for term in self.terms)


@dataclass(frozen=True)
class TitleFirstWordFilter:
    """Match the normalized first word of the title, exactly and never by substring.

    A ``None``, empty or punctuation-only title normalizes to ``""``: it never
    satisfies an include list and is never rejected by an exclude list.
    """

    words: frozenset[str]
    exclude: bool = False

    def __init__(self, words: Iterable[str], exclude: bool = False) -> None:
        normalized = frozenset(
            word for word in (normalize_first_word(item) for item in words) if word
        )
        object.__setattr__(self, "words", normalized)
        object.__setattr__(self, "exclude", exclude)

    def matches(self, listing: Listing) -> bool:
        if not self.words:
            return True
        found = normalize_first_word(listing.title) in self.words
        return not found if self.exclude else found


RegexTarget = str


@dataclass(frozen=True)
class RegexFilter:
    pattern: str
    target: RegexTarget = "both"
    _compiled: Pattern[str] | None = None

    def __post_init__(self) -> None:
        if self.target not in {"title", "description", "both"}:
            raise ValueError("regex target must be title, description, or both")
        try:
            compiled = re.compile(self.pattern, re.IGNORECASE)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}") from exc
        object.__setattr__(self, "_compiled", compiled)

    def matches(self, listing: Listing) -> bool:
        assert self._compiled is not None
        if self.target == "title":
            value = listing.title or ""
        elif self.target == "description":
            value = listing.description or ""
        else:
            value = f"{listing.title or ''} {listing.description or ''}"
        return self._compiled.search(value) is not None


@dataclass(frozen=True)
class FilterEngine:
    filters: tuple[ListingFilter, ...] = ()

    def __init__(self, filters: Iterable[ListingFilter] = ()) -> None:
        object.__setattr__(self, "filters", tuple(filters))

    def matches(self, listing: Listing) -> bool:
        return all(item.matches(listing) for item in self.filters)

    def apply(self, listings: Iterable[Listing]) -> list[Listing]:
        return [listing for listing in listings if self.matches(listing)]


def filters_from_config(config: Mapping[str, object]) -> FilterEngine:
    """Build an engine from the small JSON-compatible tracked-search config."""

    filters: list[ListingFilter] = []
    if "min_price" in config or "max_price" in config:
        minimum = config.get("min_price")
        maximum = config.get("max_price")
        filters.append(
            PriceFilter(
                Decimal(str(minimum)) if minimum is not None else None,
                Decimal(str(maximum)) if maximum is not None else None,
            )
        )
    conditions = config.get("conditions", config.get("condition"))
    if isinstance(conditions, str):
        conditions = (conditions,)
    if isinstance(conditions, Iterable):
        filters.append(ConditionFilter(cast(Iterable[str], conditions)))
    category = config.get("category_id")
    if category is not None:
        filters.append(CategoryFilter(str(category)))
    for key, attribute in (("brands", "brand"), ("brand", "brand"), ("models", "model"), ("model", "model")):
        values = config.get(key)
        if isinstance(values, str):
            values = (values,)
        if isinstance(values, Iterable):
            filters.append(ValueFilter(cast(Iterable[str], values), attribute))
    distance_key = "max_distance_km" if config.get("max_distance_km") is not None else "distance"
    if all(config.get(k) is not None for k in ("latitude", "longitude", distance_key)):
        filters.append(
            DistanceFilter(
                float(cast(float | int | str, config["latitude"])),
                float(cast(float | int | str, config["longitude"])),
                float(cast(float | int | str, config[distance_key])),
            )
        )
    include = config.get("include", ())
    if isinstance(include, str):
        include = (include,)
    if isinstance(include, Iterable):
        mode = config.get("include_mode", "any")
        filters.append(
            IncludeTextFilter(
                cast(Iterable[str], include),
                mode if isinstance(mode, str) else "any",
            )
        )
    exclude = config.get("exclude", ())
    if isinstance(exclude, str):
        exclude = (exclude,)
    if isinstance(exclude, Iterable):
        filters.append(ExcludeTextFilter(cast(Iterable[str], exclude)))
    title_include = _config_terms(config, "title_include", "title_must_include")
    if title_include:
        filters.append(
            FieldIncludeFilter(
                title_include,
                TextField.TITLE,
                _config_mode(config.get("title_include_mode")),
            )
        )
    description_include = _config_terms(config, "description_include", "description_must_include")
    if description_include:
        filters.append(
            FieldIncludeFilter(
                description_include,
                TextField.DESCRIPTION,
                _config_mode(config.get("description_include_mode")),
            )
        )
    title_exclude = _config_terms(config, "title_exclude")
    if title_exclude:
        filters.append(FieldExcludeFilter(title_exclude, TextField.TITLE))
    description_exclude = _config_terms(config, "description_exclude")
    if description_exclude:
        filters.append(FieldExcludeFilter(description_exclude, TextField.DESCRIPTION))
    first_word_include = _config_terms(config, "title_first_word_include")
    if first_word_include:
        filters.append(TitleFirstWordFilter(first_word_include))
    first_word_exclude = _config_terms(config, "title_first_word_exclude")
    if first_word_exclude:
        filters.append(TitleFirstWordFilter(first_word_exclude, exclude=True))
    regex = config.get("regex")
    if isinstance(regex, str) and regex:
        filters.append(RegexFilter(regex, str(config.get("regex_target", "both"))))
    return FilterEngine(filters)


def _config_terms(config: Mapping[str, object], *keys: str) -> tuple[str, ...] | None:
    """Read the first present alias and normalize it into a raw term tuple.

    Values may be a single string or a list of strings. Normalization happens
    inside each filter, so this helper only guarantees a stable ``tuple[str]``
    contract for the JSON-compatible tracked-search configuration.
    """

    for key in keys:
        value = config.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            value = (value,)
        if isinstance(value, Iterable) and not isinstance(value, Mapping):
            return tuple(str(item) for item in cast(Iterable[object], value) if item is not None)
        raise ValueError(f"{key} must be a string or a list of strings")
    return None


def _config_mode(value: object) -> IncludeMode:
    return IncludeMode(value) if isinstance(value, str) and value else IncludeMode.ANY
