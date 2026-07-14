"""Human-readable location resolution for comprehensive searches.

The default resolver uses OpenStreetMap Nominatim for one named-area lookup per
run. Google Maps discovery remains pure HTTP and does not depend on an official
Google API key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from . import __version__
from .grid import BoundingBox

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"gmaps-scraper/{__version__} (+https://github.com/nick-choudhary/gmaps-scraper)"


@dataclass(frozen=True)
class ResolvedLocation:
    """A human-readable location resolved to a search boundary."""

    query: str
    display_name: str
    bbox: BoundingBox
    center: tuple[float, float]
    provider: str = "nominatim"
    location_type: str = ""
    provider_id: str = ""
    geometry: dict[str, Any] = field(default_factory=dict)

    def contains(self, latitude: float | None, longitude: float | None) -> bool:
        """Check the exact resolved geometry, falling back to its bounding box."""
        if latitude is None or longitude is None:
            return False
        if self.geometry.get("type") in {"Polygon", "MultiPolygon"}:
            return geojson_contains(self.geometry, latitude, longitude)
        return self.bbox.contains(latitude, longitude)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "display_name": self.display_name,
            "provider": self.provider,
            "location_type": self.location_type,
            "provider_id": self.provider_id,
            "center": {"latitude": self.center[0], "longitude": self.center[1]},
            "bbox": {
                "min_lat": self.bbox.min_lat,
                "min_lon": self.bbox.min_lon,
                "max_lat": self.bbox.max_lat,
                "max_lon": self.bbox.max_lon,
            },
            "geometry": self.geometry,
        }


def _ring_contains(ring: list[Any], latitude: float, longitude: float) -> bool:
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        crosses = (y1 > latitude) != (y2 > latitude)
        if crosses:
            edge_longitude = (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
            if longitude < edge_longitude:
                inside = not inside
        previous = current
    return inside


def geojson_contains(geometry: dict[str, Any], latitude: float, longitude: float) -> bool:
    """Return whether a WGS84 point is inside a Polygon or MultiPolygon."""
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return False
    polygons = [coordinates] if geometry_type == "Polygon" else coordinates
    if geometry_type not in {"Polygon", "MultiPolygon"}:
        return False
    for polygon in polygons:
        if not polygon or not _ring_contains(polygon[0], latitude, longitude):
            continue
        if not any(_ring_contains(hole, latitude, longitude) for hole in polygon[1:]):
            return True
    return False


class NominatimResolver:
    """Resolve a city, region, postal code, or country through Nominatim."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        endpoint: str = NOMINATIM_SEARCH_URL,
        timeout: float = 20.0,
    ) -> None:
        self._client = client
        self._endpoint = endpoint
        self._timeout = timeout

    async def resolve(self, location: str, *, language: str = "en") -> ResolvedLocation:
        """Resolve ``location`` to its first named-area boundary."""
        query = location.strip()
        if not query:
            raise ValueError("Location must not be empty")

        params: dict[str, str | int] = {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
            "polygon_geojson": 1,
        }
        headers = {"User-Agent": USER_AGENT, "Accept-Language": language}

        if self._client is None:
            async with httpx.AsyncClient(timeout=self._timeout, follow_redirects=True) as client:
                response = await client.get(self._endpoint, params=params, headers=headers)
        else:
            response = await self._client.get(
                self._endpoint,
                params=params,
                headers=headers,
                timeout=self._timeout,
            )

        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list) or not payload:
            raise ValueError(f"Location not found: {query}")

        item = payload[0]
        try:
            south, north, west, east = (float(value) for value in item["boundingbox"])
            latitude = float(item["lat"])
            longitude = float(item["lon"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Location resolver returned an invalid boundary for: {query}"
            ) from exc

        bbox = BoundingBox(min_lat=south, min_lon=west, max_lat=north, max_lon=east)
        return ResolvedLocation(
            query=query,
            display_name=str(item.get("display_name") or query),
            bbox=bbox,
            center=(latitude, longitude),
            location_type=str(item.get("type") or ""),
            provider_id=f"{item.get('osm_type', '')}:{item.get('osm_id', '')}".strip(":"),
            geometry=dict(item.get("geojson") or {}),
        )
