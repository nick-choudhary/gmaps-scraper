"""Conversions between Google Maps place identifiers.

A ``ChIJ…`` place ID is URL-safe base64 of a small protobuf message that wraps
the place's feature ID: ``0x0a 0x12`` (field 1, 18 bytes) followed by two
fixed64 fields (``0x09`` and ``0x11``), little-endian. Those two numbers are
the ``0x<hi>:0x<lo>`` feature ID (``hex_id``) that the internal endpoints take.
Verified against live search results 2026-10-21, e.g.
``ChIJwVp-8psHbUcRuN43n5iK72Y`` <-> ``0x476d079bf27e5ac1:0x66ef8a989f37deb8``.
Other place-ID shapes (addresses, ``GhIJ…`` areas) are not feature IDs and
convert to ``None``.
"""

from __future__ import annotations

import base64
import binascii
import re
import struct

_HEX_ID = re.compile(r"0x([0-9a-f]{1,16}):0x([0-9a-f]{1,16})")
# Exactly the 27 characters of a feature place ID: base64 decoding alone would skip stray
# characters (and stop at "="), so a malformed ID could decode.
_PLACE_ID = re.compile(r"ChIJ[A-Za-z0-9_-]{23}")
_PREFIX = b"\x0a\x12\x09"
_SECOND = 0x11


def place_id_to_hex_id(place_id: str) -> str | None:
    """Return the ``0x…:0x…`` feature ID inside a ``ChIJ…`` place ID, or None."""
    if not isinstance(place_id, str) or not _PLACE_ID.fullmatch(place_id):
        return None
    try:
        raw = base64.urlsafe_b64decode(place_id + "=" * (-len(place_id) % 4))
    except (binascii.Error, ValueError):
        return None
    if len(raw) != 20 or raw[:3] != _PREFIX or raw[11] != _SECOND:
        return None
    high = struct.unpack("<Q", raw[3:11])[0]
    low = struct.unpack("<Q", raw[12:20])[0]
    return f"0x{high:x}:0x{low:x}"


def hex_id_to_place_id(hex_id: str) -> str | None:
    """Return the ``ChIJ…`` place ID for a ``0x…:0x…`` feature ID, or None."""
    match = _HEX_ID.fullmatch(hex_id.lower()) if isinstance(hex_id, str) else None
    if not match:
        return None
    high, low = (int(part, 16) for part in match.groups())
    raw = _PREFIX + struct.pack("<Q", high) + bytes([_SECOND]) + struct.pack("<Q", low)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
