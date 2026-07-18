"""Coverage helpers for gap-fill discovery inside a fixed fence."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from ._search import haversine_meters
from .grid import BoundingBox, GridCell, generate_cells
from .rpc.parser import ParsedPlace


def uncovered_cell_centers(
    bbox: BoundingBox,
    places: Iterable[ParsedPlace],
    *,
    cell_size_km: float = 2.0,
    cover_radius_km: float | None = None,
    boundary_contains: Callable[[float | None, float | None], bool] | None = None,
) -> list[GridCell]:
    """Return grid centers that still have no nearby discovered place.

    Used after the main mini-map pass: only re-photograph empty patches so we
    do not re-scrape already-covered dense cores.
    """
    radius_m = (cover_radius_km if cover_radius_km is not None else cell_size_km / 2.0) * 1000.0
    known = [
        (place.latitude, place.longitude)
        for place in places
        if place.latitude is not None and place.longitude is not None
    ]
    empty: list[GridCell] = []
    for cell in generate_cells(bbox, cell_size_km):
        if boundary_contains is not None and not boundary_contains(cell.lat, cell.lon):
            continue
        covered = False
        for plat, plon in known:
            if haversine_meters(cell.lat, cell.lon, plat, plon) <= radius_m:
                covered = True
                break
        if not covered:
            empty.append(cell)
    return empty
