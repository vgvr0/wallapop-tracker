"""Pure, reusable filters for listing-like domain objects."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from math import asin, cos, radians, sin, sqrt
from re import Pattern
from typing import Protocol, cast, runtime_checkable

from wallapop_tracker.models import Listing


class ListingFilter(Protocol):
    """A deterministic predicate over a listing.

    Built-in filters also implement :meth:`ExplainableFilter.explain` so the
    engine can describe what each configured condition evaluated.
    """

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
class FilterTrace:
    """Why a single configured condition accepted, rejected or skipped a listing.

    ``passed`` is local to the condition and has three states: ``True`` means
    the filter lets the listing continue (PASS), ``False`` means it rejects it
    (FAIL) and ``None`` means the condition could not be evaluated because the
    data it needs is unavailable (UNKNOWN). The engine never produces ``None``
    by itself; that state belongs to callers that know their input is
    incomplete, such as the storage-backed explanation service.

    One filter can emit several traces (a price filter configured with both
    bounds emits ``min_price`` and ``max_price``) and a filter with nothing
    configured emits none.

    ``actual_value`` holds the value observed on the listing, ``expected_value``
    the configured expectation and ``matched_values`` the configured terms that
    were found. ``reason`` is an optional short hint for cases where the values
    alone are ambiguous, such as a missing price or an unevaluated condition.
    Traces are runtime diagnostics: they are never persisted.
    """

    filter_name: str
    passed: bool | None
    actual_value: object | None = None
    expected_value: object | None = None
    matched_values: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class FilterEvaluation:
    """Explanation of one listing against one engine.

    ``traces`` always covers every configured filter, even the ones evaluated
    after a failure, because diagnosis is the point of
    :meth:`FilterEngine.evaluate`. ``complete`` is ``False`` as soon as one
    condition is unknown.

    ``matched`` is ``True`` when every condition passed, ``False`` as soon as a
    condition failed, and ``None`` when there is no known failure but some
    condition is unknown. A known failure therefore wins over an unknown
    condition, while an unknown condition only prevents asserting a match.
    """

    matched: bool | None
    traces: tuple[FilterTrace, ...] = ()
    complete: bool = True

    @classmethod
    def from_traces(cls, traces: Iterable[FilterTrace]) -> FilterEvaluation:
        """Aggregate traces into ``matched`` and ``complete``.

        This is the only place where the tri-state verdict is derived, so the
        engine and the storage-backed explanation always agree.
        """

        items = tuple(traces)
        unknown = any(trace.passed is None for trace in items)
        if any(trace.passed is False for trace in items):
            return cls(False, items, complete=not unknown)
        if unknown:
            return cls(None, items, complete=False)
        return cls(True, items)


@runtime_checkable
class ExplainableFilter(Protocol):
    """A filter able to report every condition it evaluated."""

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        """Return one trace per configured condition, in evaluation order."""


def _explain_filter(item: ListingFilter, listing: Listing) -> tuple[FilterTrace, ...]:
    """Trace one filter, tolerating custom filters that only implement ``matches``."""

    if isinstance(item, ExplainableFilter):
        return item.explain(listing)
    return (FilterTrace(filter_name=type(item).__name__, passed=item.matches(listing)),)


@dataclass(frozen=True)
class PriceFilter:
    min_price: Decimal | None = None
    max_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.min_price is not None and self.max_price is not None:
            if self.min_price > self.max_price:
                raise ValueError("min_price must not exceed max_price")

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        traces: list[FilterTrace] = []
        price = listing.price
        missing = "price missing" if price is None else None
        if self.min_price is not None:
            traces.append(
                FilterTrace(
                    filter_name="min_price",
                    passed=price is not None and price >= self.min_price,
                    actual_value=price,
                    expected_value=self.min_price,
                    reason=missing,
                )
            )
        if self.max_price is not None:
            traces.append(
                FilterTrace(
                    filter_name="max_price",
                    passed=price is not None and price <= self.max_price,
                    actual_value=price,
                    expected_value=self.max_price,
                    reason=missing,
                )
            )
        return tuple(traces)

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class ConditionFilter:
    codes: frozenset[str]

    def __init__(self, codes: Iterable[str]) -> None:
        object.__setattr__(self, "codes", frozenset(code for code in codes if code))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.codes:
            return ()
        value = listing.condition_code or listing.condition
        passed = value is not None and value.casefold() in {code.casefold() for code in self.codes}
        return (
            FilterTrace(
                filter_name="condition",
                passed=passed,
                actual_value=value,
                expected_value=tuple(sorted(self.codes)),
                reason=None if value is not None else "condition missing",
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class CategoryFilter:
    category_id: str

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        return (
            FilterTrace(
                filter_name="category_id",
                passed=listing.category_id == self.category_id,
                actual_value=listing.category_id,
                expected_value=self.category_id,
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class ValueFilter:
    values: frozenset[str]
    attribute: str

    def __init__(self, values: Iterable[str], attribute: str) -> None:
        object.__setattr__(self, "values", frozenset(v.casefold() for v in values if v.strip()))
        object.__setattr__(self, "attribute", attribute)

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.values:
            return ()
        value = getattr(listing, self.attribute, None)
        passed = isinstance(value, str) and value.casefold() in self.values
        return (
            FilterTrace(
                filter_name=self.attribute,
                passed=passed,
                actual_value=value,
                expected_value=tuple(sorted(self.values)),
                reason=None if value is not None else f"{self.attribute} missing",
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class DistanceFilter:
    latitude: float
    longitude: float
    max_distance_km: float

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if listing.latitude is None or listing.longitude is None:
            return (
                FilterTrace(
                    filter_name="distance",
                    passed=False,
                    expected_value=self.max_distance_km,
                    reason="listing coordinates missing",
                ),
            )
        dlat = radians(listing.latitude - self.latitude)
        dlon = radians(listing.longitude - self.longitude)
        a = (
            sin(dlat / 2) ** 2
            + cos(radians(self.latitude)) * cos(radians(listing.latitude)) * sin(dlon / 2) ** 2
        )
        distance_km = 6371.0088 * 2 * asin(sqrt(a))
        return (
            FilterTrace(
                filter_name="distance",
                passed=distance_km <= self.max_distance_km,
                actual_value=distance_km,
                expected_value=self.max_distance_km,
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class IncludeTextFilter:
    terms: tuple[str, ...]
    mode: IncludeMode = IncludeMode.ANY

    def __init__(self, terms: Iterable[str], mode: IncludeMode | str = IncludeMode.ANY) -> None:
        normalized = tuple(term.casefold() for term in terms if term.strip())
        object.__setattr__(self, "terms", normalized)
        object.__setattr__(self, "mode", IncludeMode(mode))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.terms:
            return ()
        haystack = _text(listing)
        matched = tuple(term for term in self.terms if term in haystack)
        passed = len(matched) == len(self.terms) if self.mode == IncludeMode.ALL else bool(matched)
        return (
            FilterTrace(
                filter_name="include",
                passed=passed,
                expected_value=self.terms,
                matched_values=matched,
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


@dataclass(frozen=True)
class ExcludeTextFilter:
    terms: tuple[str, ...]

    def __init__(self, terms: Iterable[str]) -> None:
        object.__setattr__(self, "terms", tuple(term.casefold() for term in terms if term.strip()))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.terms:
            return ()
        haystack = _text(listing)
        matched = tuple(term for term in self.terms if term in haystack)
        return (
            FilterTrace(
                filter_name="exclude",
                passed=not matched,
                expected_value=self.terms,
                matched_values=matched,
            ),
        )

    def matches(self, listing: Listing) -> bool:
        return all(trace.passed for trace in self.explain(listing))


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
        return all(trace.passed for trace in self.explain(listing))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.terms:
            return ()
        haystack = normalize_text(getattr(listing, self.field.value, None))
        matched = tuple(term for term in self.terms if term in haystack)
        passed = len(matched) == len(self.terms) if self.mode == IncludeMode.ALL else bool(matched)
        return (
            FilterTrace(
                filter_name=f"{self.field.value}_include",
                passed=passed,
                expected_value=self.terms,
                matched_values=matched,
            ),
        )


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
        return all(trace.passed for trace in self.explain(listing))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.terms:
            return ()
        haystack = normalize_text(getattr(listing, self.field.value, None))
        matched = tuple(term for term in self.terms if term in haystack)
        return (
            FilterTrace(
                filter_name=f"{self.field.value}_exclude",
                passed=not matched,
                expected_value=self.terms,
                matched_values=matched,
            ),
        )


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
        return all(trace.passed for trace in self.explain(listing))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        if not self.words:
            return ()
        first_word = normalize_first_word(listing.title)
        found = first_word in self.words
        name = "title_first_word_exclude" if self.exclude else "title_first_word_include"
        return (
            FilterTrace(
                filter_name=name,
                passed=not found if self.exclude else found,
                actual_value=first_word,
                expected_value=tuple(sorted(self.words)),
                matched_values=(first_word,) if found else (),
            ),
        )


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
        return all(trace.passed for trace in self.explain(listing))

    def explain(self, listing: Listing) -> tuple[FilterTrace, ...]:
        assert self._compiled is not None
        if self.target == "title":
            value = listing.title or ""
        elif self.target == "description":
            value = listing.description or ""
        else:
            value = f"{listing.title or ''} {listing.description or ''}"
        match = self._compiled.search(value)
        return (
            FilterTrace(
                filter_name="regex",
                passed=match is not None,
                actual_value=self.target,
                expected_value=self.pattern,
                matched_values=(match.group(0),) if match is not None else (),
            ),
        )


@dataclass(frozen=True)
class FilterEngine:
    filters: tuple[ListingFilter, ...] = ()

    def __init__(self, filters: Iterable[ListingFilter] = ()) -> None:
        object.__setattr__(self, "filters", tuple(filters))

    def matches(self, listing: Listing) -> bool:
        """Return whether every configured filter accepts ``listing``.

        Kept behaviour-compatible with the original engine: the test
        short-circuits and never returns ``None``. :meth:`evaluate` reports the
        same verdict together with a trace per configured filter.
        """

        return all(item.matches(listing) for item in self.filters)

    def evaluate(self, listing: Listing) -> FilterEvaluation:
        """Evaluate every configured filter and explain the outcome.

        Unlike :meth:`matches`, evaluation never stops at the first failure:
        the goal is diagnosis, so ``traces`` always covers every configured
        filter. Every trace is a plain PASS/FAIL for a real listing;
        :class:`FilterEvaluation` then derives ``matched`` and ``complete``.
        """

        traces = tuple(trace for item in self.filters for trace in _explain_filter(item, listing))
        return FilterEvaluation.from_traces(traces)

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
