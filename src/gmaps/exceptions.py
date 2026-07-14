"""Exception hierarchy for gmaps-scraper."""

from __future__ import annotations

from typing import Any


class GMapsError(Exception):
    """Base exception for all gmaps-scraper errors."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message)
        self.details = kwargs


class AuthError(GMapsError):
    """Authentication-related errors."""

    def __init__(self, message: str, **kwargs: Any):
        super().__init__(message, **kwargs)


class RateLimitError(GMapsError):
    """Rate limiting or blocking errors."""

    def __init__(
        self,
        message: str,
        retry_after: float | None = None,
        **kwargs: Any,
    ):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class ParseError(GMapsError):
    """Response parsing errors."""

    def __init__(self, message: str, raw_response: str | None = None, **kwargs: Any):
        super().__init__(message, raw_response=raw_response, **kwargs)
        self.raw_response = raw_response


class NetworkError(GMapsError):
    """Network/connection errors."""

    def __init__(self, message: str, original_error: Exception | None = None, **kwargs: Any):
        super().__init__(message, original_error=original_error, **kwargs)
        self.original_error = original_error


class TimeoutError(GMapsError):
    """Request timeout errors."""

    def __init__(
        self,
        message: str,
        timeout_seconds: float | None = None,
        **kwargs: Any,
    ):
        super().__init__(message, timeout_seconds=timeout_seconds, **kwargs)
        self.timeout_seconds = timeout_seconds


class ConfigurationError(GMapsError):
    """Configuration-related errors."""

    pass


class PlaceNotFoundError(GMapsError):
    """Place not found errors."""

    pass


class DriftError(GMapsError):
    """Raised (in strict mode) when parsed output fails structural health checks.

    A healthy Google Maps response yields places with names, place_ids, and
    coordinates. When the upstream response format changes, the index-based
    parser silently returns empty/partial objects instead of erroring. This
    exception turns that silent degradation into a loud, catchable signal so a
    format change is detected immediately rather than by a downstream consumer.
    """

    def __init__(self, message: str, health: Any = None, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.health = health
