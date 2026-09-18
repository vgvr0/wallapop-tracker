"""Typed values for explainable, non-definitive relisting candidates."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RelistingReason:
    name: str
    value: float | str | bool
    contribution: float


@dataclass(frozen=True)
class RelistingCandidate:
    previous_listing_id: int
    current_listing_id: int
    score: float
    reasons: tuple[RelistingReason, ...]
