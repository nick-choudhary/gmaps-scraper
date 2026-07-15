"""Decode RPC responses from Google Maps internal API.

Google Maps responses come in several formats:
1. HTML pages with embedded JSON (via script tags or JS initialization)
2. Direct JSON responses from internal endpoints
3. Anti-XSSI prefixed JSON ()]}'  prefix)
4. Protobuf binary responses

This decoder handles the common formats.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from ..exceptions import (
    AuthError,
    ParseError,
    RateLimitError,
)

logger = logging.getLogger(__name__)

# Known anti-XSSI prefixes Google uses
_ANTI_XSSI_PREFIXES = [
    b")]}'\n",
    b")]}'\r\n",
    b")]}'\n",
    b")]}'\r\n",
    b"//*/",
    b"while(1);",
    b"for(;;);",
    b"throw 1; < don't be evil' >",
    b")]}',\n",
]


def strip_anti_xssi(raw: str | bytes) -> str:
    """Remove anti-XSSI prefix from Google API responses.

    Google prefixes some JSON responses with anti-XSSI sequences to
    prevent cross-site script inclusion attacks. This must be stripped
    before JSON parsing.

    Args:
        raw: Raw response as string or bytes.

    Returns:
        Cleaned string with anti-XSSI prefix removed.
    """
    if isinstance(raw, bytes):
        data = raw
        for prefix in _ANTI_XSSI_PREFIXES:
            if data.startswith(prefix):
                return data[len(prefix) :].decode("utf-8", errors="replace")
        return data.decode("utf-8", errors="replace")

    text = raw
    for prefix in _ANTI_XSSI_PREFIXES:
        p = prefix.decode("utf-8", errors="replace") if isinstance(prefix, bytes) else prefix
        if text.startswith(p):
            return text[len(p) :]
    return text


def _extract_json_from_html(html: str) -> list[dict[str, Any]]:
    """Extract embedded JSON data from Google Maps HTML responses.

    Google Maps embeds place data and search results in HTML pages
    through various mechanisms:
    - window.APP_INITIALIZATION_STATE
    - __NEXT_DATA__ or similar SSR hydration
    - Inline script tags with structured data
    - Self.__AP data in JavaScript

    Args:
        html: Raw HTML response from Google Maps.

    Returns:
        List of extracted JSON objects found in the HTML.
    """
    results: list[dict[str, Any]] = []

    # Pattern 1: window.APP_INITIALIZATION_STATE = [...];
    init_state_match = re.search(
        r"window\.APP_INITIALIZATION_STATE\s*=\s*(\[.*?\]);",
        html,
        re.DOTALL,
    )
    if init_state_match:
        try:
            data = json.loads(init_state_match.group(1))
            results.append({"source": "APP_INITIALIZATION_STATE", "data": data})
        except json.JSONDecodeError:
            logger.debug("Failed to parse APP_INITIALIZATION_STATE")

    # Pattern 2: JSON-LD structured data
    for match in re.finditer(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
            results.append({"source": "jsonld", "data": data})
        except json.JSONDecodeError:
            continue

    # Pattern 3: self.__AP data blocks
    for match in re.finditer(
        r"self\.__AP\s*=\s*({.*?});",
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(match.group(1))
            results.append({"source": "__AP", "data": data})
        except json.JSONDecodeError:
            continue

    # Pattern 4: Generic JSON objects in script tags
    # Look for large JSON arrays/blocks commonly used for place data
    for match in re.finditer(
        r"\[\[\[\[\d+.*?\]\]\]\]",
        html,
        re.DOTALL,
    ):
        try:
            data = json.loads(match.group(0))
            results.append({"source": "inline_array", "data": data})
        except json.JSONDecodeError:
            continue

    return results


def parse_search_results(data: Any) -> dict[str, Any]:
    """Parse raw search response into structured place results.

    Attempts to handle multiple response formats and extract:
    - Place list with names, addresses, ratings, place IDs
    - Pagination tokens
    - Total result counts

    Args:
        data: Raw decoded response data.

    Returns:
        Structured dict with 'places' list and metadata.
    """
    places: list[dict[str, Any]] = []

    if isinstance(data, list):
        # Try to navigate Google's nested array structure
        # Common pattern: data[0][1][...] containing place results
        try:
            if len(data) > 0 and isinstance(data[0], list):
                results_container = data[0]

                # Search for place arrays - they typically contain objects with
                # specific field structures (name, address, rating, etc.)
                for section in results_container:
                    if isinstance(section, list):
                        for item in section:
                            if (
                                isinstance(item, list)
                                and len(item) >= 4
                                and isinstance(item[0], str)
                                and len(item[0]) > 2
                            ):
                                # Heuristic: a name-like first string followed by IDs/coordinates.
                                place = _parse_place_item(item)
                                if place:
                                    places.append(place)
        except (IndexError, TypeError) as e:
            logger.debug("Failed to navigate search result structure: %s", e)

    elif isinstance(data, dict):
        # Dictionary-based response (less common but possible)
        if "results" in data:
            for item in data["results"]:
                places.append(_normalize_place(item))
        elif "places" in data:
            for item in data["places"]:
                places.append(_normalize_place(item))

    return {
        "places": places,
        "total": len(places),
    }


def _parse_place_item(item: list[Any]) -> dict[str, Any] | None:
    """Parse a single place item from the nested array structure.

    Google's internal format varies, but common patterns:
    - [name, id, coords, rating_info, address, ...]
    - [null, null, null, null, [name, ...], id, ...]

    Args:
        item: A list element that might be a place item.

    Returns:
        Parsed place dict or None if the item doesn't look like a place.
    """
    try:
        place: dict[str, Any] = {}

        # Name is typically at index 14 or at specific sub-structures
        # Coordinates: lat at index 9, lng at index 10 (varies)
        # Rating: typically in a sub-array
        # Address: typically in a sub-array with address lines

        for elem in item:
            if (
                isinstance(elem, str)
                and elem
                and not elem.startswith("0x")
                and "name" not in place
                and 3 < len(elem) < 200
                and any(c.isalpha() for c in elem)
            ):
                place["name"] = elem

            if isinstance(elem, list) and len(elem) >= 2:
                # Coordinates: [lat, lng] pairs
                if all(isinstance(x, (int, float)) for x in elem[:2]) and (
                    -90 <= elem[0] <= 90 and -180 <= elem[1] <= 180
                ):
                    place.setdefault("latitude", elem[0])
                    place.setdefault("longitude", elem[1])

                # Rating info: [rating, review_count] or similar
                if len(elem) == 2 and isinstance(elem[0], (int, float)) and 1 <= elem[0] <= 5:
                    place.setdefault("rating", elem[0])
                    if isinstance(elem[1], (int, float)):
                        place.setdefault("review_count", int(elem[1]))

        if "name" in place:
            return place
    except (IndexError, TypeError):
        pass

    return None


def _normalize_place(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a place dict from various response formats to a standard schema."""
    return {
        "name": raw.get("name", raw.get("title", "")),
        "address": raw.get("address", raw.get("formatted_address", raw.get("vicinity", ""))),
        "rating": raw.get("rating", raw.get("star_rating")),
        "review_count": raw.get("review_count", raw.get("user_ratings_total")),
        "place_id": raw.get("place_id", raw.get("id", "")),
        "latitude": raw.get("latitude", raw.get("lat")),
        "longitude": raw.get("longitude", raw.get("lng")),
        "phone": raw.get("phone", raw.get("formatted_phone_number")),
        "website": raw.get("website", raw.get("url")),
        "photos": raw.get("photos", []),
        "price_level": raw.get("price_level"),
        "categories": raw.get("categories", raw.get("types", [])),
        "hours": raw.get("hours", raw.get("opening_hours")),
    }


def decode_response(
    raw_response: str | bytes,
    response_type: str = "json",
) -> Any:
    """Complete decode pipeline for Google Maps responses.

    Args:
        raw_response: Raw HTTP response body.
        response_type: Expected format ("json", "html", "auto").

    Returns:
        Decoded data (list or dict).

    Raises:
        ParseError: If the response cannot be parsed.
        AuthError: If the response indicates authentication is required.
        RateLimitError: If the response indicates rate limiting.
    """
    text = (
        raw_response
        if isinstance(raw_response, str)
        else raw_response.decode("utf-8", errors="replace")
    )

    # Check for common error pages
    if _is_blocked_response(text):
        raise RateLimitError(
            "Request blocked or rate limited by Google. Try reducing request rate.",
        )

    if _is_auth_required(text):
        raise AuthError(
            "Authentication required. Some operations require Google login.",
        )

    # Auto-detect response type
    if response_type == "auto":
        if text.strip().startswith(("{", "[")) or _looks_like_json(text):
            response_type = "json"
        elif "<html" in text.lower()[:200]:
            response_type = "html"
        else:
            response_type = "json"  # Default to JSON parsing

    if response_type == "html":
        # Extract embedded data from HTML
        results = _extract_json_from_html(text)
        if not results:
            raise ParseError(
                "No structured data found in Google Maps HTML response. "
                "The page format may have changed.",
            )
        return results

    # JSON response
    cleaned = strip_anti_xssi(text).strip()
    # Pagination responses from the current Maps UI use an XHR wrapper:
    # {"c": 0, "d": ")]}'\n[...]"}/*""*/
    # The trailing JavaScript comment is not part of the JSON object.
    if cleaned.endswith('/*""*/'):
        cleaned = cleaned[: -len('/*""*/')]
    try:
        decoded = json.loads(cleaned)
        if isinstance(decoded, dict) and isinstance(decoded.get("d"), str):
            inner = strip_anti_xssi(decoded["d"]).strip()
            if inner.startswith(("[", "{")):
                return json.loads(inner)
        return decoded
    except json.JSONDecodeError as e:
        raise ParseError(
            f"Failed to parse Google Maps response as JSON: {e}",
            raw_response=text[:500],
        ) from e


def _is_blocked_response(text: str) -> bool:
    """Check if the response indicates blocking/rate limiting."""
    blocked_indicators = [
        "Our systems have detected unusual traffic",
        "Sorry, that page can not be found",
        "Access Denied",
        "Error 403",
        "Error 429",
        "Too Many Requests",
        "CAPTCHA",
        "recaptcha",
        "show captcha",
    ]
    return any(indicator.lower() in text.lower() for indicator in blocked_indicators)


def _is_auth_required(text: str) -> bool:
    """Check if the response indicates authentication is required."""
    auth_indicators = [
        "Sign in",
        "Log in",
        "signin",
        "accounts.google.com/ServiceLogin",
        "Not logged in",
    ]
    # Only flag if it's clearly a sign-in redirect, not just a "Sign in" button
    indicator_count = sum(1 for ind in auth_indicators if ind.lower() in text.lower())
    is_html = "<html" in text.lower()[:200]
    return is_html and indicator_count >= 2


def _looks_like_json(text: str) -> bool:
    """Quick check if text looks like JSON (starts with anti-XSSI prefix)."""
    stripped = text.lstrip()
    anti_xssi_starts = (
        ")]}'",
        ")]}',",
        "//*/",
        "while(1)",
        "for(;;)",
        "throw 1;",
    )
    return any(stripped.startswith(p) for p in anti_xssi_starts)
