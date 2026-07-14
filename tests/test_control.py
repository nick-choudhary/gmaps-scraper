"""Tests for Phase 3 closed-loop control: AIMD rate + adaptive quadtree grid."""

from gmaps.control import (
    RateController,
    adaptive_grid,
    adaptive_grid_search,
    center,
    quarter,
    region_km,
    zoom_for_depth,
)
from gmaps.exceptions import RateLimitError
from gmaps.grid import BoundingBox

SAT = 120


# ── RateController (AIMD) ──


class TestRateController:
    def test_success_streak_decreases_delay(self):
        rc = RateController(start_delay=1.5, additive_decrease=0.1, success_streak_needed=5)
        for _ in range(5):
            rc.on_success()
        assert rc.current_delay == 1.4
        for _ in range(5):
            rc.on_success()
        assert rc.current_delay == 1.3

    def test_partial_streak_no_change(self):
        rc = RateController(start_delay=1.5, success_streak_needed=5)
        for _ in range(4):
            rc.on_success()
        assert rc.current_delay == 1.5

    def test_block_multiplies_delay(self):
        rc = RateController(start_delay=1.5, multiplicative_increase=2.0)
        rc.on_block()
        assert rc.current_delay == 3.0
        assert rc.blocks == 1

    def test_delay_bounds(self):
        rc = RateController(
            start_delay=1.0,
            min_delay=0.5,
            max_delay=4.0,
            additive_decrease=1.0,
            success_streak_needed=1,
        )
        for _ in range(10):
            rc.on_success()
        assert rc.current_delay == 0.5  # floored
        for _ in range(10):
            rc.on_block()
        assert rc.current_delay == 4.0  # capped

    def test_retry_after_honored(self):
        rc = RateController(start_delay=1.0, max_delay=8.0)
        rc.on_block(retry_after=5.0)
        assert rc.current_delay == 5.0


# ── Geometry ──


class TestGeometry:
    def test_quarter_covers_box(self):
        b = BoundingBox(0.0, 0.0, 2.0, 2.0)
        kids = quarter(b)
        assert len(kids) == 4
        assert BoundingBox(0.0, 0.0, 1.0, 1.0) in kids
        assert BoundingBox(1.0, 1.0, 2.0, 2.0) in kids

    def test_center(self):
        assert center(BoundingBox(0.0, 0.0, 2.0, 4.0)) == (1.0, 2.0)

    def test_region_km(self):
        assert round(region_km(BoundingBox(0.0, 0.0, 1.0, 1.0))) == 111

    def test_zoom_caps(self):
        assert zoom_for_depth(0, 15.0, 19.0) == 15.0
        assert zoom_for_depth(3, 15.0, 19.0) == 18.0
        assert zoom_for_depth(10, 15.0, 19.0) == 19.0  # capped


# ── Adaptive quadtree over a synthetic world ──


def make_world_search(world, captured=None, saturation=SAT):
    """Fake per-cell search: returns businesses inside the cell box, capped."""

    async def search_cell(lat, lon, zoom, km):
        if captured is not None:
            captured.append((round(km, 4), zoom))
        d = (km / 2.0) / 111.32
        inbox = [b for b in world if (lat - d <= b[1] <= lat + d) and (lon - d <= b[2] <= lon + d)]
        return [{"place_id": b[0]} for b in inbox[:saturation]]

    return search_cell


# 400 businesses tightly clustered (~0.5 km) + 5 scattered across the root box.
CLUSTER = [(f"c{i}", 0.0005 + (i % 20) * 0.0002, 0.0005 + (i // 20) * 0.0002) for i in range(400)]
SCATTER = [
    ("s1", 0.030, 0.030),
    ("s2", 0.035, 0.010),
    ("s3", 0.010, 0.035),
    ("s4", 0.038, 0.038),
    ("s5", 0.025, 0.005),
]
WORLD = CLUSTER + SCATTER
ROOT = BoundingBox(0.0, 0.0, 0.04, 0.04)  # ~4.4 km


class TestAdaptiveGrid:
    async def test_single_cell_would_cap(self):
        # Baseline: one coarse search over the whole root returns only the cap.
        search = make_world_search(WORLD)
        got = await search(*center(ROOT), 15.0, region_km(ROOT))
        assert len(got) == SAT  # 405 businesses, but capped at 120 -> data lost

    async def test_subdivision_recovers_all(self):
        search = make_world_search(WORLD)
        places, report = await adaptive_grid(ROOT, search, saturation=SAT, min_cell_km=0.05)
        ids = {p["place_id"] for p in places}
        assert len(ids) == len(WORLD)  # every business recovered (405)
        assert report.saturated_cells >= 1  # dense area hit the cap
        assert report.cells_subdivided >= 1
        assert report.unique_places == len(WORLD)

    async def test_zoom_increases_with_depth(self):
        captured: list = []
        search = make_world_search(WORLD, captured=captured)
        await adaptive_grid(
            ROOT, search, saturation=SAT, min_cell_km=0.05, base_zoom=15.0, max_zoom=19.0
        )
        # the smallest cells searched must use a higher zoom than the coarsest
        by_km = sorted(captured)
        smallest_km, smallest_zoom = by_km[0]
        largest_km, largest_zoom = by_km[-1]
        assert smallest_zoom > largest_zoom

    async def test_sparse_area_no_oversubdivision(self):
        sparse = [("a", 0.02, 0.02), ("b", 0.021, 0.021)]  # 2 businesses only
        search = make_world_search(sparse)
        places, report = await adaptive_grid(ROOT, search, saturation=SAT)
        assert report.cells_subdivided == 0  # never saturated -> never split
        assert report.cells_searched == 1
        assert len(places) == 2

    async def test_max_results_cap(self):
        search = make_world_search(WORLD)
        places, report = await adaptive_grid(
            ROOT, search, saturation=SAT, min_cell_km=0.05, max_results=50
        )
        assert len(places) == 50

    async def test_min_cell_km_stops_subdivision(self):
        search = make_world_search(WORLD)
        # very large min_cell_km -> cannot subdivide below root; stays 1 cell
        places, report = await adaptive_grid(ROOT, search, saturation=SAT, min_cell_km=100.0)
        assert report.cells_subdivided == 0
        assert report.saturated_cells >= 1  # saturated but couldn't split


# ── adaptive_grid_search with a live-like SearchAPI + rate control ──


class _FakeSearch:
    def __init__(self, world=None, raise_block=False):
        self.world = world or []
        self.raise_block = raise_block
        self.calls = 0

    async def places_paginated(
        self, query, latitude, longitude, max_results, radius_meters, viewport_dist, zoom
    ):
        self.calls += 1
        if self.raise_block:
            raise RateLimitError("429", retry_after=3.0)
        # viewport_dist == km * 500 (lossless) -> reconstruct cell size exactly
        km = viewport_dist / 500.0
        d = (km / 2.0) / 111.32
        inbox = [
            b
            for b in self.world
            if (latitude - d <= b[1] <= latitude + d) and (longitude - d <= b[2] <= longitude + d)
        ]
        return [{"place_id": b[0]} for b in inbox[:max_results]]


class TestAdaptiveGridSearch:
    async def test_end_to_end_recovers_and_reports(self):
        search = _FakeSearch(WORLD)
        rc = RateController(start_delay=0.0, min_delay=0.0)  # no real sleeping
        places, report = await adaptive_grid_search(
            search,
            ROOT,
            "restaurants",
            saturation=SAT,
            controller=rc,
            min_cell_km=0.05,
        )
        ids = {p["place_id"] for p in places}
        assert len(ids) == len(WORLD)
        assert report.cells_searched == search.calls
        assert "unique places" in report.summary()

    async def test_block_feeds_back_into_controller(self):
        search = _FakeSearch(raise_block=True)
        rc = RateController(start_delay=1.0, min_delay=0.0)
        places, report = await adaptive_grid_search(
            search,
            ROOT,
            "x",
            controller=rc,
            max_depth=0,
        )
        assert report.blocks >= 1
        assert report.final_delay > 1.0  # backed off after the block
        assert places == []
