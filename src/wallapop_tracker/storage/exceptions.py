"""Persistence-specific errors."""


class PersistenceError(Exception):
    """Base error for invalid persistence operations."""


class InvalidTrackingRunError(PersistenceError):
    """An operation requiring a valid run received another status."""


class ConflictingSnapshotError(PersistenceError):
    """The same entity/run already has a different snapshot."""


class ListingProfileConflictError(PersistenceError):
    """An existing listing was associated with another profile."""
