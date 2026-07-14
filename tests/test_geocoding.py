"""Tests for human-readable location resolution."""

from __future__ import annotations

import httpx
import pytest

from gmaps.geocoding import NominatimResolver, geojson_contains


@pytest.mark.asyncio
async def test_resolver_returns_named_location_boundary() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "Atlanta, Georgia"
        assert request.url.params["polygon_geojson"] == "1"
        assert request.headers["User-Agent"].startswith("gmaps-scraper/")
        return httpx.Response(
            200,
            json=[
                {
                    "display_name": "Atlanta, Fulton County, Georgia, United States",
                    "boundingbox": ["33.6478", "33.8868", "-84.5511", "-84.2896"],
                    "lat": "33.7488",
                    "lon": "-84.3877",
                    "type": "city",
                    "osm_type": "relation",
                    "osm_id": 119614,
                    "geojson": {
                        "type": "Polygon",
                        "coordinates": [[[-85, 33], [-84, 33], [-84, 34], [-85, 34], [-85, 33]]],
                    },
                }
            ],
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    resolver = NominatimResolver(client=client)

    resolved = await resolver.resolve("Atlanta, Georgia")

    assert resolved.display_name.startswith("Atlanta")
    assert resolved.bbox.min_lat == 33.6478
    assert resolved.bbox.max_lon == -84.2896
    assert resolved.center == (33.7488, -84.3877)
    assert resolved.provider == "nominatim"
    assert resolved.contains(33.75, -84.4) is True
    await client.aclose()


def test_geojson_polygon_rejects_point_inside_bbox_but_outside_shape() -> None:
    triangle = {
        "type": "Polygon",
        "coordinates": [[[0, 0], [2, 0], [0, 2], [0, 0]]],
    }

    assert geojson_contains(triangle, 0.5, 0.5) is True
    assert geojson_contains(triangle, 1.5, 1.5) is False


def test_non_area_geojson_falls_back_to_resolved_bbox() -> None:
    from gmaps.geocoding import ResolvedLocation
    from gmaps.grid import BoundingBox

    resolved = ResolvedLocation(
        query="postal code",
        display_name="Postal code",
        bbox=BoundingBox(1, 1, 2, 2),
        center=(1.5, 1.5),
        geometry={"type": "Point", "coordinates": [1.5, 1.5]},
    )

    assert resolved.contains(1.5, 1.5) is True


@pytest.mark.asyncio
async def test_resolver_reports_unknown_location() -> None:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
    )
    resolver = NominatimResolver(client=client)

    with pytest.raises(ValueError, match="Location not found"):
        await resolver.resolve("Not a real place")

    await client.aclose()
