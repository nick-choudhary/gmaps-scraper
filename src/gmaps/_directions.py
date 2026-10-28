"""Travel time and distance between stops, via the Maps directions endpoint."""

from __future__ import annotations

import datetime as dt
import logging
from typing import TYPE_CHECKING

from .rpc.directions import (
    Directions,
    Waypoint,
    build_directions_path,
    parse_directions_response,
)

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)


class DirectionsAPI:
    """Routes between two or more stops, as google.com/maps computes them.

    Usage:
        async with GMapsClient() as client:
            result = await client.directions.route(
                ["Wien Hauptbahnhof", "Belvedere Palace, Vienna"], mode="walk"
            )
            fastest = result.routes[0]
            print(fastest.duration_text, fastest.distance_text)
    """

    def __init__(self, transport: HTTPTransport, language: str = "en", region: str = "us"):
        self._transport = transport
        self._language = language
        self._region = region

    async def route(
        self,
        waypoints: list[Waypoint],
        mode: str = "drive",
        departure_time: dt.datetime | None = None,
        arrival_time: dt.datetime | None = None,
    ) -> Directions:
        """Route through ``waypoints`` in order.

        Args:
            waypoints: Two or more stops. Each is free text ("Albertina,
                Vienna"), a ``ChIJ…`` place ID, a ``0x…:0x…`` feature ID,
                or a ``(latitude, longitude)`` tuple. Free text is geocoded by
                Google; pass IDs or coordinates when the stop must be exact.
            mode: "drive" or "walk".
            departure_time / arrival_time: Local wall-clock time at the stop
                (naive datetime; one with a UTC offset raises ValueError).
                Driving then returns traffic predicted for that time. At most
                one of the two.

        Returns:
            Directions with status OK, NOT_FOUND (a stop could not be
            resolved; see ``unresolved``) or ZERO_RESULTS (nothing connects
            the stops in this mode).
        """
        path = build_directions_path(
            waypoints,
            mode=mode,
            departure_time=departure_time,
            arrival_time=arrival_time,
            language=self._language,
            region=self._region,
        )
        logger.info("Directions: %d stops, mode=%s", len(waypoints), mode)
        raw = await self._transport.get(path=path, response_type="json")
        queries = [w if isinstance(w, str) else f"{w[0]},{w[1]}" for w in waypoints]
        return parse_directions_response(raw, mode=mode, queries=queries)
