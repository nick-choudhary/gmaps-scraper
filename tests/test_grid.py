"""Tests for grid subdivision and bounding box logic."""

from __future__ import annotations

from gmaps.grid import BoundingBox, GridCell, estimate_cell_count, generate_cells


class TestBoundingBox:
    def test_creation(self):
        bbox = BoundingBox(min_lat=40.0, min_lon=-74.0, max_lat=41.0, max_lon=-73.0)
        assert bbox.min_lat == 40.0
        assert bbox.min_lon == -74.0
        assert bbox.max_lat == 41.0
        assert bbox.max_lon == -73.0

    def test_fields(self):
        bbox = BoundingBox(min_lat=40.0, min_lon=-74.0, max_lat=41.0, max_lon=-73.0)
        # BoundingBox stores raw coords, derived props may not exist
        assert bbox.max_lat - bbox.min_lat == 1.0
        assert bbox.max_lon - bbox.min_lon == 1.0


class TestGenerateCells:
    def test_generates_cells(self):
        bbox = BoundingBox(min_lat=30.26, min_lon=-97.75, max_lat=30.28, max_lon=-97.73)
        cells = generate_cells(bbox, cell_size_km=0.5)
        assert len(cells) > 0
        assert all(isinstance(c, GridCell) for c in cells)

    def test_smaller_cell_size_more_cells(self):
        bbox = BoundingBox(min_lat=30.0, min_lon=-98.0, max_lat=31.0, max_lon=-97.0)
        large = generate_cells(bbox, cell_size_km=5.0)
        small = generate_cells(bbox, cell_size_km=1.0)
        assert len(small) > len(large)

    def test_cell_centers_within_bbox(self):
        bbox = BoundingBox(min_lat=30.0, min_lon=-98.0, max_lat=31.0, max_lon=-97.0)
        cells = generate_cells(bbox, cell_size_km=2.0)
        for cell in cells:
            assert bbox.min_lat <= cell.lat <= bbox.max_lat
            assert bbox.min_lon <= cell.lon <= bbox.max_lon


class TestEstimateCellCount:
    def test_returns_positive(self):
        bbox = BoundingBox(min_lat=30.0, min_lon=-98.0, max_lat=31.0, max_lon=-97.0)
        count = estimate_cell_count(bbox, cell_size_km=2.0)
        assert count > 0
