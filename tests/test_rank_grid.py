"""Tests for coordinate rank-grid scanning."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import httpx
import pytest

from gmaps._search import SearchAPI
from gmaps.rank_grid import (
    DirectGoogleMapsProvider,
    FallbackRankProvider,
    ProviderSearchResult,
    RankCandidate,
    RankGridScanner,
    RankProviderError,
    RankSearchProvider,
    RankTarget,
    SerperMapsProvider,
    generate_rank_grid,
    render_rank_grid_html,
    write_rank_grid_html,
    write_rank_grid_json,
)
from gmaps.rpc.parser import ParsedPlace


class StaticProvider:
    name = "static"

    def __init__(self, candidates: tuple[RankCandidate, ...]):
        self.candidates = candidates
        self.calls: list[tuple[float, float]] = []

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult:
        self.calls.append((latitude, longitude))
        return ProviderSearchResult(self.name, self.candidates[:max_rank])


class ErrorProvider:
    name = "error"

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult:
        raise RankProviderError("blocked")


class FakeSearchAPI:
    def __init__(self, places: list[ParsedPlace]):
        self.places = places
        self.kwargs: dict[str, object] = {}

    async def places_paginated(self, **kwargs: object) -> list[ParsedPlace]:
        self.kwargs = kwargs
        return self.places


def test_generate_rank_grid_has_center_and_compass_order() -> None:
    points = generate_rank_grid(33.749, -84.388, grid_size=3, spacing_km=1)

    assert len(points) == 9
    assert points[4].latitude == 33.749
    assert points[4].longitude == -84.388
    assert points[0].latitude > points[4].latitude
    assert points[0].longitude < points[4].longitude
    assert points[8].latitude < points[4].latitude
    assert points[8].longitude > points[4].longitude


@pytest.mark.parametrize(
    ("grid_size", "spacing_km"),
    [(2, 1), (0, 1), (3, 0)],
)
def test_generate_rank_grid_rejects_invalid_geometry(grid_size: int, spacing_km: float) -> None:
    with pytest.raises(ValueError):
        generate_rank_grid(33.749, -84.388, grid_size=grid_size, spacing_km=spacing_km)


def test_target_matches_stable_ids_and_normalized_name() -> None:
    candidate = RankCandidate(
        position=1,
        name="Kyle   Moore Law",
        place_id="ChIJ-place",
        cid="12345",
        hex_id="0xabc:0x3039",
    )

    assert RankTarget("ChIJ-place", "place_id").matches(candidate)
    assert RankTarget("12345", "cid").matches(candidate)
    assert RankTarget("0xabc:0x3039", "hex_id").matches(candidate)
    assert RankTarget("kyle moore law", "name").matches(candidate)
    assert RankTarget("12345").matches(candidate)
    assert not RankTarget("Another Firm").matches(candidate)


async def test_scanner_records_rank_summary_and_progress() -> None:
    provider = StaticProvider(
        (
            RankCandidate(position=1, name="Other"),
            RankCandidate(position=2, name="Target", place_id="ChIJ-target"),
        )
    )
    completed = []

    result = await RankGridScanner(provider).scan(
        query="lawyer",
        target=RankTarget("ChIJ-target"),
        center_latitude=33.749,
        center_longitude=-84.388,
        grid_size=3,
        spacing_km=0.5,
        max_rank=20,
        on_point=completed.append,
    )

    assert len(provider.calls) == 9
    assert len(completed) == 9
    assert all(point.rank == 2 for point in result.points)
    assert result.summary() == {
        "total_points": 9,
        "found_points": 9,
        "visibility_percent": 100.0,
        "best_rank": 2,
        "average_rank": 2.0,
        "error_points": 0,
    }


async def test_scanner_records_provider_errors_per_point() -> None:
    result = await RankGridScanner(ErrorProvider()).scan(
        query="lawyer",
        target=RankTarget("Target"),
        center_latitude=33.749,
        center_longitude=-84.388,
        grid_size=1,
        spacing_km=1,
    )

    assert result.points[0].rank is None
    assert result.points[0].error == "blocked"
    assert result.summary()["error_points"] == 1


async def test_fallback_provider_uses_secondary_on_error_and_empty_results() -> None:
    fallback = StaticProvider((RankCandidate(position=1, name="Target"),))
    on_error = FallbackRankProvider(ErrorProvider(), fallback)
    on_empty = FallbackRankProvider(StaticProvider(()), fallback)

    error_result = await on_error.search("lawyer", 1, 2, 20, 14)
    empty_result = await on_empty.search("lawyer", 1, 2, 20, 14)

    assert error_result.provider == "static"
    assert empty_result.provider == "static"
    assert len(fallback.calls) == 2


async def test_direct_provider_uses_existing_search_api() -> None:
    search_api = FakeSearchAPI(
        [
            ParsedPlace(
                name="Target",
                place_id="ChIJ-target",
                cid="123",
                latitude=33.749,
                longitude=-84.388,
            )
        ]
    )
    provider = DirectGoogleMapsProvider(cast(SearchAPI, search_api))

    result = await provider.search("lawyer", 33.749, -84.388, 20, 14)

    assert result.provider == "direct"
    assert result.candidates[0].place_id == "ChIJ-target"
    assert search_api.kwargs["latitude"] == 33.749
    assert search_api.kwargs["longitude"] == -84.388
    assert search_api.kwargs["zoom"] == 14


async def test_serper_provider_sends_coordinate_request_and_parses_places() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.headers["X-API-KEY"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "places": [
                    {
                        "position": 3,
                        "title": "Target",
                        "placeId": "ChIJ-target",
                        "cid": "123",
                        "latitude": 33.749,
                        "longitude": -84.388,
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SerperMapsProvider("secret", client=client)
        result = await provider.search("lawyer", 33.749, -84.388, 20, 14)

    assert captured["api_key"] == "secret"
    assert captured["body"] == {
        "q": "lawyer",
        "ll": "@33.7490000,-84.3880000,14z",
        "gl": "us",
        "hl": "en",
        "num": 20,
    }
    assert result.provider == "serper"
    assert result.candidates[0].position == 3
    assert result.candidates[0].place_id == "ChIJ-target"


async def test_serper_provider_wraps_http_errors() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SerperMapsProvider("secret", client=client)
        with pytest.raises(RankProviderError, match="Serper Maps search failed"):
            await provider.search("lawyer", 33.749, -84.388, 20, 14)


async def test_json_and_html_outputs_are_self_contained_and_escaped(
    tmp_path: Path,
) -> None:
    provider = StaticProvider((RankCandidate(position=1, name="<script>Target</script>"),))
    result = await RankGridScanner(cast(RankSearchProvider, provider)).scan(
        query="<lawyer>",
        target=RankTarget("<script>Target</script>", "name"),
        center_latitude=33.749,
        center_longitude=-84.388,
        grid_size=1,
        spacing_km=1,
    )

    json_path = write_rank_grid_json(result, tmp_path / "nested" / "rank.json")
    html_path = write_rank_grid_html(result, tmp_path / "nested" / "rank.html")
    rendered = render_rank_grid_html(result)

    assert json.loads(json_path.read_text())["points"][0]["rank"] == 1
    assert html_path.read_text() == rendered
    assert "&lt;lawyer&gt;" in rendered
    assert "<script>" not in rendered
