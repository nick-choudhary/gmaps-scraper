"""Geographic grid search to overcome Google Maps' ~120 results limit.

Google Maps returns at most ~20 results per search and ~120 per area.
To extract more businesses from a large region, divide it into a grid
of small cells and search each cell's center independently.

Based on the grid approach from gosom/google-maps-scraper.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Earth's radius and degrees-per-km (approximate)
KM_PER_DEGREE_LAT = 111.32


@dataclass
class BoundingBox:
    """Geographic rectangle defined by minimum and maximum coordinates."""

    min_lat: float
    min_lon: float
    max_lat: float
    max_lon: float

    @classmethod
    def from_string(cls, s: str) -> BoundingBox:
        """Parse "minLat,minLon,maxLat,maxLon" format.

        Example: "40.30,-3.80,40.50,-3.60" for part of Madrid.
        """
        parts = [float(p.strip()) for p in s.split(",")]
        if len(parts) != 4:
            raise ValueError(f"Expected 4 values, got {len(parts)}: '{s}'")
        return cls(parts[0], parts[1], parts[2], parts[3])

    @classmethod
    def from_center_radius(cls, lat: float, lon: float, radius_km: float) -> BoundingBox:
        """Create a bounding box from a center point and radius in km."""
        lat_delta = radius_km / KM_PER_DEGREE_LAT
        lon_delta = radius_km / (KM_PER_DEGREE_LAT * math.cos(math.radians(lat)))
        return cls(
            lat - lat_delta,
            lon - lon_delta,
            lat + lat_delta,
            lon + lon_delta,
        )


@dataclass(frozen=True)
class GridCell:
    """A single cell in the search grid, defined by its center point.

    Frozen (immutable + hashable) so cells can be used in sets/dicts,
    e.g. for counting unique cells in CLI output.
    """

    lat: float
    lon: float

    def coordinates(self) -> str:
        """Return coordinates as a comma-separated string."""
        return f"{self.lat},{self.lon}"

    @property
    def latitude(self) -> float:
        return self.lat

    @property
    def longitude(self) -> float:
        return self.lon


def generate_cells(bbox: BoundingBox, cell_size_km: float = 1.0) -> list[GridCell]:
    """Divide a bounding box into a grid of cells.

    Each cell is approximately cell_size_km × cell_size_km. Returns
    the center point of every cell. The longitude step is adjusted for
    latitude so cells remain roughly square.

    Args:
        bbox: The geographic bounding box to subdivide.
        cell_size_km: Approximate side length of each cell in kilometers.
                      Default 1.0 km (good for dense urban areas).

    Returns:
        List of GridCell center points.

    Example:
        A 20×20 km area with cell_size_km=1 produces ~400 cells, each
        returning up to 20 results → up to 8000 total businesses.
    """
    if cell_size_km <= 0:
        cell_size_km = 1.0

    # Latitude step is constant everywhere
    lat_step = cell_size_km / KM_PER_DEGREE_LAT

    # Longitude step varies with latitude; use midpoint
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    cos_mid_lat = max(abs(math.cos(math.radians(mid_lat))), 1e-6)
    lon_step = cell_size_km / (KM_PER_DEGREE_LAT * cos_mid_lat)

    cells: list[GridCell] = []

    # Start at the center of the first cell (half a step from the edge)
    lat = bbox.min_lat + lat_step / 2
    while lat < bbox.max_lat:
        lon = bbox.min_lon + lon_step / 2
        while lon < bbox.max_lon:
            cells.append(GridCell(lat=lat, lon=lon))
            lon += lon_step
        lat += lat_step

    return cells


def estimate_cell_count(bbox: BoundingBox, cell_size_km: float = 1.0) -> int:
    """Estimate the number of cells without generating them."""
    if cell_size_km <= 0:
        cell_size_km = 1.0

    lat_step = cell_size_km / KM_PER_DEGREE_LAT
    mid_lat = (bbox.min_lat + bbox.max_lat) / 2
    cos_mid_lat = max(abs(math.cos(math.radians(mid_lat))), 1e-6)
    lon_step = cell_size_km / (KM_PER_DEGREE_LAT * cos_mid_lat)

    lat_cells = max(0, int(math.ceil((bbox.max_lat - bbox.min_lat) / lat_step)))
    lon_cells = max(0, int(math.ceil((bbox.max_lon - bbox.min_lon) / lon_step)))

    return lat_cells * lon_cells


def generate_zoom_level_cells(
    bbox: BoundingBox, min_cell_km: float = 1.0, max_cell_km: float = 10.0, steps: int = 3
) -> list[tuple[GridCell, float]]:
    """Generate cells at multiple zoom levels for progressive coverage.

    Starts with large cells (coarse coverage), then progressively
    subdivides into smaller cells for fine coverage. This is useful
    for adaptive searches that balance coverage with request count.

    Args:
        bbox: The bounding box.
        min_cell_km: Smallest cell size (most detailed).
        max_cell_km: Largest cell size (coarse).
        steps: Number of zoom levels.

    Returns:
        List of (GridCell, cell_size_km) tuples.
    """
    result: list[tuple[GridCell, float]] = []
    seen: set[tuple[float, float]] = set()

    for step in range(steps):
        # Interpolate cell size logarithmically
        t = step / (steps - 1) if steps > 1 else 0
        cell_size = min_cell_km * ((max_cell_km / min_cell_km) ** t)

        for cell in generate_cells(bbox, cell_size):
            key = (round(cell.lat, 6), round(cell.lon, 6))
            if key not in seen:
                seen.add(key)
                result.append((cell, cell_size))

    return result
