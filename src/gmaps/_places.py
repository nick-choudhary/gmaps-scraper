"""Places API using Google Maps internal pb= protocol."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .rpc.ids import hex_id_to_place_id, place_id_to_hex_id
from .rpc.parser import ParsedPlace, parse_place_details_response
from .rpc.pb_encoder import build_place_details_path

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)


class PlacesAPI:
    """Get details about a place using Google Maps internal API.

    Uses the same /maps/preview/place request as Phase 2 enrichment; a place
    ID is converted to the feature ID that endpoint needs (see rpc.ids).
    """

    def __init__(self, transport: HTTPTransport, language: str = "en"):
        self._transport = transport
        self._language = language

    async def get(self, place_id: str) -> ParsedPlace | None:
        """Get place details by Google Maps place ID.

        Args:
            place_id: Google Maps place ID (e.g., "ChIJN1t_tDeuEmsRUsoyG83frY4").

        Returns:
            ParsedPlace with details, or None if not found.

        Raises:
            ValueError: if ``place_id`` is not a ``ChIJ…`` business/POI ID.
        """
        hex_id = place_id_to_hex_id(place_id)
        if hex_id is None:
            raise ValueError(f"not a ChIJ… place ID: {place_id!r}")
        return await self._fetch(place_id, hex_id)

    async def get_by_hex_id(self, hex_id: str) -> ParsedPlace | None:
        """Get place details by internal hex ID.

        Args:
            hex_id: Internal hex ID (e.g., "0x89c259a6bcd5e9d1:0x...").

        Returns:
            ParsedPlace or None.

        Raises:
            ValueError: if ``hex_id`` is not ``0x…:0x…``.
        """
        place_id = hex_id_to_place_id(hex_id)
        if place_id is None:
            raise ValueError(f"not a 0x…:0x… feature ID: {hex_id!r}")
        return await self._fetch(place_id, hex_id.lower())

    async def _fetch(self, place_id: str, hex_id: str) -> ParsedPlace | None:
        logger.info("Fetching place: %s (%s)", place_id, hex_id)
        raw = await self._transport.get(
            path=build_place_details_path(
                place_id=place_id, hex_id=hex_id, language=self._language
            ),
            response_type="json",
        )
        return parse_place_details_response(raw)
