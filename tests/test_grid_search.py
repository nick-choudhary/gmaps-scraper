"""Public grid-search behavior for trustworthy comprehensive runs."""

from __future__ import annotations

from typing import Any

import pytest

from gmaps._search import GridCellProgress, SearchAPI, SearchResult
from gmaps.grid import BoundingBox, generate_cells
from gmaps.rpc.parser import ParsedPlace
from gmaps.stats import ScraperStats


class FakeGridSearchAPI(SearchAPI):
    def __init__(self, pages: list[list[ParsedPlace]]) -> None:
        super().__init__(transport=None)  # type: ignore[arg-type]
        self._pages = iter(pages)
        self._request_delay = 0

    async def places_paginated(self, **kwargs: Any) -> list[ParsedPlace]:
        return next(self._pages)


class FakePageAPI(SearchAPI):
    def __init__(self, pages: list[list[ParsedPlace]]) -> None:
        super().__init__(transport=None)  # type: ignore[arg-type]
        self._pages = iter(pages)
        self.calls = 0
        self._request_delay = 0

    async def places(self, **kwargs: Any) -> SearchResult:
        self.calls += 1
        places = next(self._pages)
        return SearchResult(query="test", places=places)


@pytest.mark.asyncio
async def test_pagination_stops_when_page_adds_nothing_globally_new() -> None:
    api = FakePageAPI(
        [
            [ParsedPlace(place_id="new", latitude=1.0, longitude=1.0)],
            [ParsedPlace(place_id="already-seen", latitude=1.0, longitude=1.0)],
            [ParsedPlace(place_id="must-not-be-requested", latitude=1.0, longitude=1.0)],
        ]
    )

    places = await api.places_paginated(
        "test",
        latitude=1.0,
        longitude=1.0,
        stop_seen_ids={"already-seen"},
        boundary_contains=lambda _lat, _lng: True,
        filter_to_boundary=True,
    )

    assert api.calls == 2
    assert [place.place_id for place in places] == ["new", "already-seen"]


@pytest.mark.asyncio
async def test_outside_place_does_not_keep_global_pagination_running() -> None:
    api = FakePageAPI(
        [
            [ParsedPlace(place_id="outside", latitude=5.0, longitude=5.0)],
            [ParsedPlace(place_id="must-not-be-requested", latitude=1.0, longitude=1.0)],
        ]
    )

    await api.places_paginated(
        "test",
        latitude=1.0,
        longitude=1.0,
        stop_seen_ids=set(),
        boundary_contains=lambda lat, lng: lat == 1.0 and lng == 1.0,
        filter_to_boundary=True,
    )

    assert api.calls == 1


@pytest.mark.asyncio
async def test_grid_can_filter_to_boundary_and_report_complete_progress() -> None:
    bbox = BoundingBox(33.64, -84.55, 33.89, -84.29)
    api = FakeGridSearchAPI(
        [
            [
                ParsedPlace(place_id="inside", latitude=33.75, longitude=-84.40),
                ParsedPlace(place_id="outside", latitude=34.10, longitude=-84.40),
            ]
        ]
    )
    stats = ScraperStats()

    results = await api.grid_search(
        "chiropractor",
        bbox,
        cell_size_km=100,
        max_results=10,
        filter_to_bbox=True,
        shuffle_cells=False,
        stats=stats,
    )

    assert [place.place_id for place, _ in results] == ["inside"]
    assert stats.cells_total == 1
    assert stats.cells_completed == 1
    assert stats.outside_boundary == 1
    assert stats.complete is True
    assert stats.incomplete_reasons == []


@pytest.mark.asyncio
async def test_result_cap_is_reported_as_incomplete() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    api = FakeGridSearchAPI(
        [[ParsedPlace(place_id=f"place-{i}", latitude=0.01, longitude=0.01)] for i in range(9)]
    )
    stats = ScraperStats()

    results = await api.grid_search(
        "coffee",
        bbox,
        cell_size_km=1,
        max_results=1,
        shuffle_cells=False,
        stats=stats,
    )

    assert len(results) == 1
    assert stats.cap_reached is True
    assert stats.complete is False
    assert "result_cap_reached" in stats.incomplete_reasons
    assert stats.cells_completed < stats.cells_total


@pytest.mark.asyncio
async def test_result_cap_counts_places_without_place_ids() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    api = FakeGridSearchAPI(
        [[ParsedPlace(name=f"Business {i}", address=f"{i} Main St")] for i in range(9)]
    )
    stats = ScraperStats()

    results = await api.grid_search(
        "business",
        bbox,
        cell_size_km=1,
        max_results=1,
        shuffle_cells=False,
        stats=stats,
    )

    assert len(results) == 1
    assert stats.cap_reached is True


@pytest.mark.asyncio
async def test_saturated_cell_prevents_false_complete_claim() -> None:
    bbox = BoundingBox(0, 0, 0.01, 0.01)
    places = [
        ParsedPlace(place_id=f"place-{index}", latitude=0.005, longitude=0.005)
        for index in range(SearchAPI.MAX_PER_AREA)
    ]
    stats = ScraperStats()

    await FakeGridSearchAPI([places]).grid_search(
        "coffee", bbox, cell_size_km=100, max_results=500, stats=stats
    )

    assert stats.cells_saturated == 1
    assert stats.complete is False
    assert "saturated_cells" in stats.incomplete_reasons


@pytest.mark.asyncio
async def test_grid_emits_durable_cell_progress_and_skips_resumed_cells() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.01)
    cells = generate_cells(bbox, cell_size_km=1)
    skipped = {cells[0].key()}
    api = FakeGridSearchAPI(
        [
            [ParsedPlace(place_id=f"place-{index}", latitude=cell.lat, longitude=cell.lon)]
            for index, cell in enumerate(cells[1:], start=1)
        ]
    )
    events: list[GridCellProgress] = []
    stats = ScraperStats()

    results = await api.grid_search(
        "coffee",
        bbox,
        cell_size_km=1,
        max_results=100,
        shuffle_cells=False,
        stats=stats,
        skip_cell_keys=skipped,
        initial_seen_ids={"already-saved"},
        on_cell=events.append,
    )

    assert len(results) == len(cells) - 1
    assert len(events) == len(cells) - 1
    assert all(event.cell.key() not in skipped for event in events)
    assert all(len(event.new_places) == 1 for event in events)
    assert stats.cells_completed == len(cells)
    assert stats.unique_places == len(cells)
    assert stats.complete is True


@pytest.mark.asyncio
async def test_duplicate_place_retains_every_source_cell() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.01)
    cells = generate_cells(bbox, cell_size_km=1)
    api = FakeGridSearchAPI(
        [[ParsedPlace(place_id="same", latitude=0.005, longitude=0.005)] for _cell in cells]
    )

    results = await api.grid_search(
        "coffee", bbox, cell_size_km=1, max_results=100, shuffle_cells=False
    )

    assert len(results) == 1
    assert results[0][0].found_in_cells == [cell.key() for cell in cells]
