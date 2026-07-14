"""gmaps-scraper: Unofficial Python client for Google Maps internal RPC API.

Reverse-engineered from Google Maps web application network traffic.
No official API key required for basic search/place operations.

Usage:
    async with GMapsClient() as client:
        results = await client.search.places("coffee shops in Austin TX")
        details = await client.places.get("ChIJN1t_tDeuEmsRUsoyG83frY4")
        reviews = await client.reviews.list("0x89c2...")
"""

from __future__ import annotations

__version__ = "0.1.0"

from .client import GMapsClient
from .exceptions import (
    AuthError,
    GMapsError,
    NetworkError,
    ParseError,
    RateLimitError,
    TimeoutError,
)

__all__ = [
    "GMapsClient",
    "GMapsError",
    "AuthError",
    "RateLimitError",
    "ParseError",
    "NetworkError",
    "TimeoutError",
]
