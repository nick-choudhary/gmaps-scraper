"""Protobuf-style URL parameter encoder for Google Maps internal API.

Google Maps uses a custom text-encoded protobuf format in the ``pb=`` URL
parameter. This module encodes Python values into the ``!{field}{type}{value}``
notation that Google Maps' internal endpoints require.

Format reference:
    !{field_number}{type_code}{value}

Type codes:
    m  - sub-message (nested group, value is another sequence)
    s  - string (empty strings typically omitted)
    d  - double (floating-point, including booleans as 0/1)
    b  - boolean (0 or 1)
    i  - integer (base-10)
    e  - enum (integer)
    en - enum with name (integer!string)
    z  - varint as string (rare)

Example (conceptual):
    !1m2!2sCoffee!3d25.7617!4d-80.1918
    → field 1 = sub-message with 2 fields
    → field 2 = string "Coffee"
    → field 3 = double 25.7617
    → field 4 = double -80.1918

Based on reverse-engineering by promisingcoder/GoogleMapsCollector
and SerpApi's research.
"""

from __future__ import annotations

import re
from typing import Any, cast

# Type aliases
PbValue = str | int | float | bool | list | dict | None
PbMessage = list[tuple[int, str, PbValue]]

# Regex for decoding pb parameters
_PB_TOKEN_RE = re.compile(r"!(\d+)([a-z]+)")


def encode_pb(message: PbMessage) -> str:
    """Encode a protobuf message into Google Maps ``pb=`` format.

    Args:
        message: List of (field_number, type_code, value) tuples.

    Returns:
        URL-safe pb parameter string.

    Example:
        >>> encode_pb([
        ...     (1, "m", [
        ...         (2, "s", "coffee shop"),
        ...         (3, "d", 30.2672),
        ...         (4, "d", -97.7431),
        ...     ]),
        ... ])
        '!1m3!2scoffee shop!3d30.2672!4d-97.7431'
    """
    parts: list[str] = []
    for field_num, type_code, value in message:
        encoded = _encode_field(field_num, type_code, value)
        if encoded is not None:
            parts.append(encoded)
    return "".join(parts)


def _encode_field(field_num: int, type_code: str, value: PbValue) -> str | None:
    """Encode a single protobuf field."""
    if value is None:
        return None

    if type_code == "m":
        if isinstance(value, list):
            # value is a list of sub-fields
            inner = encode_pb(value)
            return f"!{field_num}m{len(value)}{inner}"
        return None

    if type_code in ("s", "str"):
        s = str(value)
        if not s:
            return None
        return f"!{field_num}s{s}"

    if type_code in ("d", "double"):
        if isinstance(value, bool):
            return f"!{field_num}d{1 if value else 0}"
        return f"!{field_num}d{value}"

    if type_code in ("b", "bool"):
        return f"!{field_num}b{1 if value else 0}"

    if type_code in ("i", "int", "z"):
        return f"!{field_num}i{int(cast(str | int | float, value))}"

    if type_code == "e":
        return f"!{field_num}e{int(cast(str | int | float, value))}"

    if type_code == "en":
        if isinstance(value, tuple) and len(value) == 2:
            return f"!{field_num}en{value[0]}!{value[1]}"
        return f"!{field_num}en{int(cast(str | int | float, value))}"

    return f"!{field_num}{type_code}{value}"


def decode_pb(pb_string: str) -> list[dict[str, Any]]:
    """Decode a Google Maps pb= parameter string into Python objects.

    This is useful for analyzing captured network traffic.

    Args:
        pb_string: The pb= parameter value from a Google Maps URL.

    Returns:
        List of decoded field groups.
    """
    results: list[dict[str, Any]] = []
    tokens = _PB_TOKEN_RE.split(pb_string)
    # tokens alternate: '', field_num, type_str, value, field_num, type_str, value, ...
    # Skip first empty element from split
    i = 1
    while i < len(tokens) - 1:
        try:
            field_num = int(tokens[i])
            type_str = tokens[i + 1]
            value_str = tokens[i + 2] if i + 2 < len(tokens) else ""

            value = _parse_pb_value(type_str, value_str)
            results.append({"field": field_num, "type": type_str, "value": value})
            i += 3
        except (ValueError, IndexError):
            i += 1

    return results


def _parse_pb_value(type_str: str, value_str: str) -> Any:
    """Parse a pb value based on its type code."""
    if type_str in ("d", "double"):
        try:
            return float(value_str)
        except ValueError:
            return value_str
    if type_str in ("i", "int", "e", "b", "z"):
        try:
            return int(value_str)
        except ValueError:
            return value_str
    if type_str == "en":
        parts = value_str.split("!", 1)
        if len(parts) == 2:
            try:
                return (int(parts[0]), parts[1])
            except ValueError:
                return value_str
        return value_str
    return value_str


# ── High-level builders for common Google Maps operations ──


def build_search_pb(
    query: str,
    latitude: float = 0.0,
    longitude: float = 0.0,
    zoom: int = 10,
    language: str = "en",
) -> str:
    """Build the pb= parameter for a Google Maps text search.

    This constructs the protobuf message used by the Google Maps web app
    when performing a search via /search?tbm=map.

    Args:
        query: Search text (e.g., "coffee shops in Austin TX").
        latitude: Center latitude.
        longitude: Center longitude.
        zoom: Map zoom level (higher = more local).
        language: Language code.

    Returns:
        Encoded pb= parameter string.
    """
    message: PbMessage = [
        # Field 1: Query sub-message
        (
            1,
            "m",
            [
                (2, "s", query),  # Search query text
                (3, "d", latitude),  # Center lat
                (4, "d", longitude),  # Center lng
                (5, "i", zoom),  # Zoom level
                (6, "s", language),  # Language
            ],
        ),
    ]
    return encode_pb(message)


def build_place_details_pb(place_id: str, language: str = "en") -> str:
    """Build the pb= parameter for fetching place details.

    Args:
        place_id: Google Maps place ID.
        language: Language code.

    Returns:
        Encoded pb= parameter string.
    """
    message: PbMessage = [
        (
            1,
            "m",
            [
                (2, "s", f"place_id:{place_id}"),
                (3, "s", language),
            ],
        ),
    ]
    return encode_pb(message)


def build_reviews_pb(
    feature_id: str,
    sort_by: int = 0,
    start_index: int = 0,
    page_size: int = 10,
    language: str = "en",
) -> str:
    """Build the pb= parameter for fetching place reviews.

    Args:
        feature_id: Internal feature/place identifier.
        sort_by: Sort order (0=relevant, 1=newest, 2=highest, 3=lowest).
        start_index: Pagination start index.
        page_size: Number of reviews per page.
        language: Language code.

    Returns:
        Encoded pb= parameter string.
    """
    message: PbMessage = [
        (
            1,
            "m",
            [
                (2, "s", feature_id),
                (3, "i", sort_by),
                (4, "i", start_index),
                (5, "i", page_size),
                (6, "s", language),
            ],
        ),
    ]
    return encode_pb(message)


def build_autocomplete_pb(
    input_text: str,
    latitude: float = 0.0,
    longitude: float = 0.0,
    language: str = "en",
) -> str:
    """Build the pb= parameter for autocomplete suggestions.

    Args:
        input_text: Partial search text.
        latitude: Location bias latitude.
        longitude: Location bias longitude.
        language: Language code.

    Returns:
        Encoded pb= parameter string.
    """
    message: PbMessage = [
        (
            1,
            "m",
            [
                (2, "s", input_text),
                (3, "d", latitude),
                (4, "d", longitude),
                (5, "s", language),
            ],
        ),
    ]
    return encode_pb(message)
