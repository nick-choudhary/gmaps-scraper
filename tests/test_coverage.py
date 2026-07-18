"""Coverage / gap-fill helpers."""

from __future__ import annotations

from gmaps.coverage import uncovered_cell_centers
from gmaps.grid import BoundingBox
from gmaps.rpc.parser import ParsedPlace


def test_uncovered_centers_skip_cells_that_already_have_nearby_places() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.03, 0.03)
    # One place near the first cell region.
    places = [ParsedPlace(place_id="a", latitude=0.005, longitude=0.005)]
    empty = uncovered_cell_centers(bbox, places, cell_size_km=1.0)
    # Not every cell is covered by one place near the SW corner.
    assert empty
    assert all(not (abs(c.lat - 0.005) < 0.002 and abs(c.lon - 0.005) < 0.002) for c in empty)


def test_boundary_filter_drops_centers_outside_fence() -> None:
    bbox = BoundingBox(0.0, 0.0, 0.04, 0.04)
    empty = uncovered_cell_centers(
        bbox,
        places=[],
        cell_size_km=2.0,
        boundary_contains=lambda lat, lon: lat is not None and lat < 0.02,
    )
    assert empty
    assert all(c.lat < 0.02 for c in empty)
