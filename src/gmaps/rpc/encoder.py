"""Encode RPC requests for Google Maps internal API.

Google Maps uses a variety of request formats depending on the endpoint:
1. Standard URL query parameters (for search/place pages)
2. JSON POST bodies (for internal data endpoints)
3. !-encoded parameter strings (in URL paths)
4. Protobuf binary payloads (for some internal services)

This encoder handles the common cases and provides extension points.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)


def encode_rpc_request(
    params: dict[str, Any] | list[Any],
    method: str = "POST",
) -> str | bytes:
    """Encode parameters for a Google Maps internal API request.

    Supports both dict-style (named parameters) and list-style (positional
    parameters) inputs. Returns URL-encoded query string for GET or JSON
    body for POST.

    Args:
        params: Parameters dict or list for the API call.
        method: HTTP method ("GET" or "POST").

    Returns:
        URL-encoded string (GET) or JSON bytes (POST).
    """
    if method.upper() == "GET":
        if isinstance(params, dict):
            return urlencode(params)
        return urlencode({"q": json.dumps(params, separators=(",", ":"))})

    # POST: JSON body
    if isinstance(params, list):
        body = json.dumps(params, separators=(",", ":"))
    else:
        body = json.dumps(params, separators=(",", ":"))
    return body.encode("utf-8")


def build_request_params(
    query: str,
    latitude: float | None = None,
    longitude: float | None = None,
    language: str = "en",
    **kwargs: Any,
) -> dict[str, str]:
    """Build URL query parameters for Google Maps search requests.

    Maps mimics the parameter structure used by the Google Maps web app
    when performing text searches and nearby searches.

    Args:
        query: Search query string.
        latitude: Optional center latitude for location-based search.
        longitude: Optional center longitude for location-based search.
        language: Language code (default "en").
        **kwargs: Additional parameters passed as-is.

    Returns:
        Dict of query parameters ready for URL encoding.
    """
    params: dict[str, str] = {
        "q": query,
        "hl": language,
    }
    if latitude is not None and longitude is not None:
        params["center"] = f"{latitude},{longitude}"
    params.update({k: str(v) for k, v in kwargs.items() if v is not None})
    return params


def encode_place_url(place_id: str, language: str = "en") -> str:
    """Build the URL for fetching a place page.

    Args:
        place_id: Google Maps place ID.
        language: Language code.

    Returns:
        Full URL string for the place page.
    """
    return f"https://www.google.com/maps/place/?q=place_id:{place_id}&hl={language}"


def encode_review_params(
    feature_id: str,
    page_token: str | None = None,
    sort_by: str = "relevant",
    language: str = "en",
) -> dict[str, str]:
    """Build parameters for fetching place reviews.

    Args:
        feature_id: Internal feature/place identifier.
        page_token: Pagination token for next page of reviews.
        sort_by: Sort order ("relevant", "newest", "highest", "lowest").
        language: Language code.

    Returns:
        Dict of parameters for the reviews request.
    """
    params: dict[str, str] = {
        "feature_id": feature_id,
        "sort": sort_by,
        "hl": language,
    }
    if page_token:
        params["page_token"] = page_token
    return params


def encode_photos_params(
    photo_reference: str,
    max_width: int = 800,
    max_height: int = 800,
) -> dict[str, str]:
    """Build parameters for fetching place photos.

    Args:
        photo_reference: Photo reference string from place details.
        max_width: Maximum width in pixels.
        max_height: Maximum height in pixels.

    Returns:
        Dict of parameters for the photo request.
    """
    return {
        "photo_reference": photo_reference,
        "max_width": str(max_width),
        "max_height": str(max_height),
    }


def encode_autocomplete_params(
    input_text: str,
    latitude: float | None = None,
    longitude: float | None = None,
    radius: int | None = None,
    language: str = "en",
) -> dict[str, str]:
    """Build parameters for autocomplete/suggestions.

    Args:
        input_text: Partial search text.
        latitude: Optional location bias latitude.
        longitude: Optional location bias longitude.
        radius: Optional search radius in meters.
        language: Language code.

    Returns:
        Dict of parameters for the autocomplete request.
    """
    params: dict[str, str] = {
        "input": input_text,
        "hl": language,
    }
    if latitude is not None and longitude is not None:
        params["location"] = f"{latitude},{longitude}"
    if radius is not None:
        params["radius"] = str(radius)
    return params
