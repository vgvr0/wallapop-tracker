"""Provider exception hierarchy."""


class ListingAnalysisError(Exception):
    """Base exception for the isolated analysis core."""


class ListingAnalysisProviderError(ListingAnalysisError):
    """Transport, HTTP, empty-response, and provider protocol failures."""


class ListingAnalysisValidationError(ListingAnalysisError):
    """The provider returned JSON that does not satisfy the domain contract."""
