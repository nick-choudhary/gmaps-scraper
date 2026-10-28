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
import warnings
from typing import Any, cast
from urllib.parse import quote

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


def encode_pb_string(value: str) -> str:
    """Escape and URL-encode a string value for a hand-built ``pb=`` parameter.

    ``!`` separates pb fields, and Google decodes ``%21`` back to ``!``, so
    URL-encoding alone lets a ``!`` in the text split the value. Like the Maps
    web app, write ``*`` as ``*2A`` and ``!`` as ``*21`` first.
    """
    return quote(_escape_pb_string(str(value)), safe="")


def _escape_pb_string(value: str) -> str:
    """Write ``*`` as ``*2A`` and ``!`` as ``*21``, as the Maps web app does."""
    return value.replace("*", "*2A").replace("!", "*21")


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
        return f"!{field_num}s{_escape_pb_string(s)}"

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


def build_place_details_path(
    place_id: str,
    hex_id: str,
    ftid: str = "",
    data_id: str = "",
    name: str = "",
    latitude: float = 0.0,
    longitude: float = 0.0,
    query: str = "",
    language: str = "en",
) -> str:
    """Build the ``/maps/preview/place`` request path for a place's details.

    This is the Phase 2 enrichment request. ``place_id`` (``ChIJ…``) and
    ``hex_id`` (``0x…:0x…``) are enough: checked live, the endpoint answers in
    full with an empty ftid, data_id and name and zero coordinates.

    Args:
        place_id: ``ChIJ…`` place ID.
        hex_id: ``0x…:0x…`` feature ID.
        ftid: ``/g/…`` feature tracking ID, if known.
        data_id: Internal data ID, if known.
        name: Business name.
        latitude: Place latitude.
        longitude: Place longitude.
        query: Original search query (defaults to the name).
        language: Language code.

    Returns:
        Path and query string, relative to ``https://www.google.com``.
    """
    hex_enc = encode_pb_string(hex_id)
    lat = latitude or 0.0
    lng = longitude or 0.0
    viewport = 898976.2597

    pb = (
        f"!1m22"
        f"!1s{hex_enc}"
        f"!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}"
        f"!2m3!1f0.0!2f0.0!3f0.0"
        f"!3m2!1i1024!2i768!4f13.1"
        f"!4m2!3d{lat}!4d{lng}"
        f"!15m4!1m3!1s{hex_enc}!4s{encode_pb_string(ftid)}!5s{encode_pb_string(place_id)}"
        f"!6s{encode_pb_string(query or name)}"
        f"!12m4!2m3!1i360!2i120!4i8"
        f"!13m57!2m2!1i203!2i100!3m2!2i4!5b1"
        f"!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        f"!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
        f"!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
        f"!14m2!1s{encode_pb_string(data_id)}!7e81"
        f"!15m111!1m29!4e2!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
        f"!18m17!3b1!4b1!5b1!6b1!9b1!13b1!14b1!17b1!20b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2"
        f"!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
        f"!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
        f"!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
        f"!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
        f"!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b0"
        f"!21m0!22m2!1e81!8e4!29m0!30m6!3b1!6m1!2b1!7m1!2b1!9b1"
        f"!34m5!7b1!10b1!14b1!15m1!1b0!37i785"
        f"!39s{encode_pb_string(name)}!40b1!41b1"
    )
    return f"/maps/preview/place?authuser=0&hl={language}&gl=us&pb={pb}&q={quote(name, safe='')}"


def build_place_details_pb(place_id: str, language: str = "en") -> str:
    """Build the old ``place_id:…`` pb= parameter for place details.

    .. deprecated::
        ``/maps/preview/place`` answers this request with HTTP 400. Use
        :func:`build_place_details_path` with the place's feature ID
        (:func:`gmaps.rpc.ids.place_id_to_hex_id`) instead.
    """
    warnings.warn(
        "build_place_details_pb() builds a request Google Maps rejects; "
        "use build_place_details_path() instead",
        DeprecationWarning,
        stacklevel=2,
    )
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
