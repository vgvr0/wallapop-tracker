"""Exceptions raised by the Wallapop integration."""


class WallapopError(Exception):
    """Base exception for expected Wallapop errors."""


class WallapopHTTPError(WallapopError):
    """An HTTP response was unsuccessful and is not more specific below."""


class WallapopNotFoundError(WallapopHTTPError):
    """The requested public resource does not exist."""


class WallapopRateLimitError(WallapopHTTPError):
    """Wallapop rejected the request due to rate limiting."""


class WallapopParseError(WallapopError):
    """The external response cannot be normalized safely."""


class WallapopPaginationError(WallapopError):
    """Pagination returned a cursor that would cause a loop."""
