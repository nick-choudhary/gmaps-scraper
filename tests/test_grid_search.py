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


class FakeMiniMapAPI(SearchAPI):
    """Scripted ``places()`` responses for zoom-locked mini-map discovery."""

    def __init__(self, scripted: list[list[ParsedPlace]]) -> None:
        super().__init__(transport=None)  # type: ignore[arg-type]
        self._pages = list(scripted)
        self._index = 0
        self.calls: list[dict[str, Any]] = []
        self._request_delay = 0

    async def places(self, **kwargs: Any) -> SearchResult:
        self.calls.append(kwargs)
        if self._index >= len(self._pages):
            return SearchResult(query=kwargs.get("query", ""), places=[])
        places = self._pages[self._index]
        self._index += 1
        return SearchResult(query=kwargs.get("query", ""), places=places)


def test_viewport_meters_halves_each_ui_zoom_step() -> None:
    from gmaps._search import (
        PROTOCOL_SEARCH_ZOOM,
        VIEWPORT_METERS_AT_UI_ZOOM_16,
        _build_search_url,
        viewport_meters_for_ui_zoom,
    )

    assert viewport_meters_for_ui_zoom(16) == VIEWPORT_METERS_AT_UI_ZOOM_16
    assert abs(viewport_meters_for_ui_zoom(14) / viewport_meters_for_ui_zoom(16) - 4.0) < 1e-9
    assert abs(viewport_meters_for_ui_zoom(16) / viewport_meters_for_ui_zoom(18) - 4.0) < 1e-9

    url = _build_search_url("chiropractors", 33.749, -84.388, zoom=16.0)
    assert f"!4f{PROTOCOL_SEARCH_ZOOM:g}" in url
    assert f"!1d{VIEWPORT_METERS_AT_UI_ZOOM_16}" in url
    # Raising UI zoom must not raise protocol !4f.
    url_18 = _build_search_url(
        "chiropractors",
        33.749,
        -84.388,
        viewport_dist=viewport_meters_for_ui_zoom(18),
        zoom=18.0,
    )
    assert f"!4f{PROTOCOL_SEARCH_ZOOM:g}" in url_18
    assert f"!1d{viewport_meters_for_ui_zoom(18)}" in url_18


@pytest.mark.asyncio
async def test_minimap_partial_page_is_leaf_one_request() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.01, 0.01)
    api = FakeMiniMapAPI(
        [[ParsedPlace(place_id="only", latitude=0.005, longitude=0.005)]]
    )
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=50,
        shuffle_cells=False,
        stats=stats,
    )

    assert [place.place_id for place, _ in results] == ["only"]
    assert len(api.calls) == 1
    assert api.calls[0]["zoom"] == 16.0
    assert stats.cells_completed == 1
    assert stats.duplicates == 0
    assert stats.cells_saturated == 0


@pytest.mark.asyncio
async def test_minimap_paginates_full_pages_but_does_not_split_before_120() -> None:
    """Mild density: full pages paginate; partial end page means leaf, no split."""
    bbox = BoundingBox(0.0, 0.0, 0.01, 0.01)
    page1 = [
        ParsedPlace(place_id=f"a{i}", latitude=0.005, longitude=0.005)
        for i in range(SearchAPI.MAX_PER_PAGE)
    ]
    page2 = [
        ParsedPlace(place_id=f"b{i}", latitude=0.005, longitude=0.005)
        for i in range(5)  # partial → stop, not saturated
    ]
    api = FakeMiniMapAPI([page1, page2, [ParsedPlace(place_id="nope", latitude=0.005, longitude=0.005)]])
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=100,
        shuffle_cells=False,
        max_depth=2,
        min_cell_km=0.01,
        stats=stats,
    )

    assert len(api.calls) == 2
    assert len(results) == SearchAPI.MAX_PER_PAGE + 5
    assert stats.cells_saturated == 0
    assert all(call["zoom"] == 16.0 for call in api.calls)


@pytest.mark.asyncio
async def test_minimap_saturated_120_splits_and_zooms_viewport() -> None:
    """~6 full pages (~120) → saturated → quarter + UI zoom+1 (viewport shrinks)."""
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    parent_pages: list[list[ParsedPlace]] = []
    for page in range(6):
        parent_pages.append(
            [
                ParsedPlace(place_id=f"p{page}-{i}", latitude=0.01, longitude=0.01)
                for i in range(SearchAPI.MAX_PER_PAGE)
            ]
        )
    # 4 children @ z17: first has a new local place, others empty.
    child_pages = [
        [ParsedPlace(place_id="child-new", latitude=0.012, longitude=0.012)],
        [],
        [],
        [],
    ]
    api = FakeMiniMapAPI([*parent_pages, *child_pages])
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=500,
        shuffle_cells=False,
        max_depth=1,
        max_pages=6,  # force soft-ceiling path (default is now 2)
        min_cell_km=0.01,
        stats=stats,
    )

    assert len(api.calls) == 10  # 6 parent pages + 4 children
    assert api.calls[0]["zoom"] == 16.0
    assert api.calls[0]["offset"] == 0
    assert api.calls[5]["offset"] == 100
    assert all(call["zoom"] == 17.0 for call in api.calls[6:])
    assert api.calls[0]["viewport_dist"] > api.calls[6]["viewport_dist"]
    place_ids = {place.place_id for place, _ in results}
    assert "child-new" in place_ids
    assert len(place_ids) == 120 + 1
    # Recovered via split → not permanently saturated
    assert stats.cells_saturated == 0


@pytest.mark.asyncio
async def test_minimap_saturated_leaf_marks_incomplete_when_cannot_split() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.01, 0.01)
    pages = [
        [
            ParsedPlace(place_id=f"p{page}-{i}", latitude=0.005, longitude=0.005)
            for i in range(SearchAPI.MAX_PER_PAGE)
        ]
        for page in range(6)
    ]
    api = FakeMiniMapAPI(pages)
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=500,
        shuffle_cells=False,
        max_depth=0,  # cannot split
        max_pages=6,
        stats=stats,
    )

    assert len(api.calls) == 6
    assert len(results) == 120
    assert stats.cells_saturated == 1
    assert stats.complete is False
    assert "saturated_cells" in stats.incomplete_reasons


@pytest.mark.asyncio
async def test_minimap_rejects_far_ranking_spillover_as_outside() -> None:
    """Client-side cell radius drops metro-wide ranking hits (Chrome: local cluster ~1–3km)."""
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    # Cell center ~0.01,0.01; far place is tens of km away.
    api = FakeMiniMapAPI(
        [
            [
                ParsedPlace(place_id="near", latitude=0.0105, longitude=0.0105),
                ParsedPlace(place_id="far", latitude=0.5, longitude=0.5),
            ]
        ]
    )
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=1.0,
        max_results=50,
        shuffle_cells=False,
        stats=stats,
    )

    assert [p.place_id for p, _ in results] == ["near"]
    # Far ranking spill is a footprint reject (still inside bbox), not city-fence.
    assert stats.outside_footprint >= 1 or stats.outside_boundary >= 1


@pytest.mark.asyncio
async def test_minimap_stops_paging_when_full_page_adds_no_new_local() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    page1 = [
        ParsedPlace(place_id=f"a{i}", latitude=0.01, longitude=0.01)
        for i in range(SearchAPI.MAX_PER_PAGE)
    ]
    # Second full page is all dups → must not request a third page.
    page2 = [
        ParsedPlace(place_id=f"a{i}", latitude=0.01, longitude=0.01)
        for i in range(SearchAPI.MAX_PER_PAGE)
    ]
    page3 = [ParsedPlace(place_id="never", latitude=0.01, longitude=0.01)]
    api = FakeMiniMapAPI([page1, page2, page3])

    await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=100,
        shuffle_cells=False,
        max_depth=0,
    )

    assert len(api.calls) == 2


@pytest.mark.asyncio
async def test_diversity_pass_uses_near_subarea_query_and_dedupes() -> None:
    from gmaps.geocoding import SubArea
    from gmaps.grid import BoundingBox

    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    sub = SubArea(
        name="Midtown",
        display_name="Midtown",
        bbox=bbox,
        center=(0.01, 0.01),
        area_type="neighbourhood",
    )
    api = FakeMiniMapAPI(
        [
            [
                ParsedPlace(place_id="local", latitude=0.01, longitude=0.01),
                ParsedPlace(place_id="already", latitude=0.011, longitude=0.011),
            ]
        ]
    )
    stats = ScraperStats()
    results = await api.diversity_subarea_search(
        "chiropractors",
        [sub],
        max_results=50,
        pages_per_subarea=1,
        initial_seen_ids={"already"},
        stats=stats,
    )
    assert api.calls[0]["query"] == "chiropractors near Midtown"
    assert [p.place_id for p, _ in results] == ["local"]
    assert stats.duplicates == 1


@pytest.mark.asyncio
async def test_gap_fill_only_searches_uncovered_centers() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    # Existing place covers the SW; gap-fill should still run on other centers.
    existing = [ParsedPlace(place_id="seed", latitude=0.005, longitude=0.005)]
    api = FakeMiniMapAPI(
        [[ParsedPlace(place_id=f"g{i}", latitude=0.015, longitude=0.015)] for i in range(20)]
    )
    results = await api.gap_fill_search(
        "chiropractors",
        bbox,
        existing,
        cell_size_km=1.0,
        max_results=50,
        pages_per_gap=1,
        initial_seen_ids={"seed"},
    )
    assert api.calls
    assert any(p.place_id.startswith("g") for p, _ in results)


@pytest.mark.asyncio
async def test_minimap_does_not_split_saturated_view_with_zero_local_uniques() -> None:
    """Outside-only ranking filling ~120 must not spawn child cells."""
    bbox = BoundingBox(0.0, 0.0, 0.02, 0.02)
    # All results outside city fence.
    pages = [
        [
            ParsedPlace(place_id=f"out{page}-{i}", latitude=1.0, longitude=1.0)
            for i in range(SearchAPI.MAX_PER_PAGE)
        ]
        for page in range(6)
    ]
    api = FakeMiniMapAPI(pages)
    stats = ScraperStats()

    results = await api.minimap_grid_search(
        "coffee",
        bbox,
        cell_size_km=100,
        max_results=500,
        shuffle_cells=False,
        max_depth=2,
        min_cell_km=0.01,
        stats=stats,
    )

    assert results == []
    # First full page adds 0 local uniques → stop paging; no children spawned.
    assert len(api.calls) == 1
    assert stats.cells_saturated == 0  # never reached ~120 raw with early stop
