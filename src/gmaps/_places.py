"""Updated Places API using Google Maps internal pb= protocol."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .rpc.parser import ParsedPlace, parse_place_details_response
from .rpc.pb_encoder import build_place_details_pb

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)


class PlacesAPI:
    """Get details about a place using Google Maps internal API.

    Uses /maps/preview/place pb= protocol.
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
        """
        pb_param = build_place_details_pb(place_id, self._language)

        params: dict[str, str] = {
            "hl": self._language,
            "pb": pb_param,
        }

        logger.info("Fetching place: %s", place_id)

        raw = await self._transport.get(
            path="/maps/preview/place",
            params=params,
            response_type="json",
        )

        return parse_place_details_response(raw)

    async def get_by_hex_id(self, hex_id: str) -> ParsedPlace | None:
        """Get place details by internal hex ID.

        Args:
            hex_id: Internal hex ID (e.g., "0x89c259a6bcd5e9d1:0x...").

        Returns:
            ParsedPlace or None.
        """
        # Use the same endpoint with hex_id in the pb param
        pb_param = build_place_details_pb(hex_id, self._language)

        params = {
            "hl": self._language,
            "pb": pb_param,
        }

        raw = await self._transport.get(
            path="/maps/preview/place",
            params=params,
            response_type="json",
        )

        return parse_place_details_response(raw)
