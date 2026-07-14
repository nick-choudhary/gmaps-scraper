"""Closed-loop scraping control (Phase 3) — adaptive rate + adaptive grid.

Today the scraper uses a *fixed* strategy: a constant inter-request delay and a
single grid cell size / zoom for a whole run, and the block/success signals it
collects are never fed back. This module closes that loop with two independent
controllers driven by observed feedback (Sutton's "search" over the live
environment instead of hand-guessed constants):

1. `RateController` — AIMD on the inter-request delay. Speeds up on sustained
   success, backs off multiplicatively on a block/429. Finds the fastest safe
   rate instead of trusting a hand-picked 1.5s.

2. `adaptive_grid` — a quadtree. Any cell that comes back *saturated* (>= the
   ~120 result cap ⇒ almost certainly incomplete) is subdivided into four
   children searched at a **higher zoom** with a smaller viewport. This recovers
   businesses a fixed grid silently drops in dense cores, and avoids wasting
   requests on sparse areas. Zoom co-varies with subdivision depth — the zoom
   lever is a first-class input here (it is orthogonal to the rate controller).

Everything here is opt-in and standalone; it does not modify the existing
`SearchAPI.grid_search`, so default behaviour is unchanged. A `ControlReport`
records what happened (safe rate found, cells subdivided, saturated cells a
fixed grid would have capped, blocks seen) so the gain is measured, not assumed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .grid import KM_PER_DEGREE_LAT, BoundingBox

logger = logging.getLogger(__name__)


# ── AIMD rate controller ──


@dataclass
class RateController:
    """Additive-increase / multiplicative-decrease control of request delay.

    Delay is *decreased* additively after a run of successes (go faster) and
    *increased* multiplicatively on a block (back off hard). Deterministic given
    a sequence of signals, so it is fully unit-testable without a network.
    """

    min_delay: float = 0.4
    max_delay: float = 8.0
    start_delay: float = 1.5
    additive_decrease: float = 0.1  # seconds shaved per success streak
    multiplicative_increase: float = 2.0
    success_streak_needed: int = 5

    def __post_init__(self) -> None:
        self._delay = self.start_delay
        self._streak = 0
        self.successes = 0
        self.blocks = 0

    @property
    def current_delay(self) -> float:
        return self._delay

    def on_success(self) -> None:
        self.successes += 1
        self._streak += 1
        if self._streak >= self.success_streak_needed:
            self._delay = max(self.min_delay, round(self._delay - self.additive_decrease, 4))
            self._streak = 0

    def on_block(self, retry_after: float | None = None) -> None:
        self.blocks += 1
        self._streak = 0
        target = self._delay * self.multiplicative_increase
        if retry_after:
            target = max(target, float(retry_after))
        self._delay = min(self.max_delay, round(target, 4))


# ── Report ──


@dataclass
class ControlReport:
    """What the adaptive run actually did — so the gain can be measured."""

    cells_searched: int = 0
    cells_subdivided: int = 0
    saturated_cells: int = 0  # cells that hit the cap (a fixed grid loses these)
    leaf_cells: int = 0
    max_depth: int = 0
    unique_places: int = 0
    blocks: int = 0
    final_delay: float = 0.0

    def summary(self) -> str:
        return (
            f"{self.unique_places} unique places | {self.cells_searched} cells "
            f"({self.leaf_cells} leaf, {self.cells_subdivided} subdivided) | "
            f"{self.saturated_cells} saturated (recovered by subdivision) | "
            f"max depth {self.max_depth} | {self.blocks} blocks | "
            f"final delay {self.final_delay:.2f}s"
        )


# ── Geometry helpers (operate on BoundingBox; grid.py untouched) ──


def region_km(bbox: BoundingBox) -> float:
    """North-south extent of a box in km (used as the cell's characteristic size)."""
    return abs(bbox.max_lat - bbox.min_lat) * KM_PER_DEGREE_LAT


def center(bbox: BoundingBox) -> tuple[float, float]:
    return ((bbox.min_lat + bbox.max_lat) / 2.0, (bbox.min_lon + bbox.max_lon) / 2.0)


def quarter(bbox: BoundingBox) -> list[BoundingBox]:
    """Split a box into four equal children (the quadtree step)."""
    mlat = (bbox.min_lat + bbox.max_lat) / 2.0
    mlon = (bbox.min_lon + bbox.max_lon) / 2.0
    return [
        BoundingBox(bbox.min_lat, bbox.min_lon, mlat, mlon),
        BoundingBox(bbox.min_lat, mlon, mlat, bbox.max_lon),
        BoundingBox(mlat, bbox.min_lon, bbox.max_lat, mlon),
        BoundingBox(mlat, mlon, bbox.max_lat, bbox.max_lon),
    ]


def zoom_for_depth(depth: int, base_zoom: float, max_zoom: float) -> float:
    """Zoom steps up as cells shrink with subdivision depth."""
    return min(base_zoom + depth, max_zoom)


def _key(place: Any) -> Any:
    if isinstance(place, dict):
        return place.get("place_id")
    return getattr(place, "place_id", None)


# Signature of the pluggable per-cell search: (lat, lon, zoom, cell_km) -> places
SearchCell = Callable[[float, float, float, float], Awaitable[list[Any]]]


async def adaptive_grid(
    bbox: BoundingBox,
    search_cell: SearchCell,
    *,
    saturation: int = 120,
    min_cell_km: float = 0.2,
    base_zoom: float = 15.0,
    max_zoom: float = 19.0,
    max_depth: int = 6,
    max_results: int | None = None,
    dedup: bool = True,
    report: ControlReport | None = None,
) -> tuple[list[Any], ControlReport]:
    """Quadtree area search: subdivide saturated cells, zoom in with depth.

    `search_cell(lat, lon, zoom, cell_km)` returns the places for one cell.
    A cell with >= `saturation` results is treated as incomplete and split into
    four higher-zoom children (unless depth/min-cell limits are hit); otherwise
    it is a leaf and its results are kept (deduped by place_id).
    """
    report = report or ControlReport()
    seen: set[Any] = set()
    out: list[Any] = []
    stack: list[tuple[BoundingBox, int]] = [(bbox, 0)]

    while stack:
        region, depth = stack.pop()
        km = region_km(region)
        zoom = zoom_for_depth(depth, base_zoom, max_zoom)
        clat, clon = center(region)

        results = await search_cell(clat, clon, zoom, km)
        report.cells_searched += 1
        report.max_depth = max(report.max_depth, depth)

        n = len(results)
        saturated = n >= saturation
        can_split = depth < max_depth and (km / 2.0) >= min_cell_km

        if saturated and can_split:
            report.cells_subdivided += 1
            report.saturated_cells += 1
            for child in quarter(region):
                stack.append((child, depth + 1))
            continue

        if saturated:
            report.saturated_cells += 1  # hit the cap but couldn't subdivide further
        report.leaf_cells += 1
        for p in results:
            k = _key(p)
            if dedup and k is not None:
                if k in seen:
                    continue
                seen.add(k)
            out.append(p)
            if max_results is not None and len(out) >= max_results:
                report.unique_places = len(out)
                return out, report

    report.unique_places = len(out)
    return out, report


async def adaptive_grid_search(
    search: Any,
    bbox: BoundingBox,
    query: str,
    *,
    saturation: int = 120,
    controller: RateController | None = None,
    base_zoom: float = 15.0,
    max_zoom: float = 19.0,
    min_cell_km: float = 0.2,
    max_depth: int = 6,
    max_results: int | None = None,
) -> tuple[list[Any], ControlReport]:
    """Opt-in adaptive grid over a live `SearchAPI`, with AIMD rate control.

    Wraps `search.places_paginated` per cell, applies the rate controller's
    delay between cells, and feeds block signals back into it. Returns the
    deduped places and a `ControlReport`. Does not touch `grid_search`.
    """
    controller = controller or RateController()
    report = ControlReport()
    from .exceptions import RateLimitError

    async def cell(lat: float, lon: float, zoom: float, km: float) -> list[Any]:
        radius = int(km * 750)
        viewport = km * 500.0
        await asyncio.sleep(controller.current_delay)
        try:
            res = await search.places_paginated(
                query=query,
                latitude=lat,
                longitude=lon,
                max_results=saturation,
                radius_meters=radius,
                viewport_dist=viewport,
                zoom=zoom,
            )
            controller.on_success()
            return res
        except RateLimitError as e:
            controller.on_block(getattr(e, "retry_after", None))
            logger.warning(
                "adaptive grid: block at (%.4f,%.4f); delay -> %.2fs",
                lat,
                lon,
                controller.current_delay,
            )
            return []

    places, report = await adaptive_grid(
        bbox,
        cell,
        saturation=saturation,
        min_cell_km=min_cell_km,
        base_zoom=base_zoom,
        max_zoom=max_zoom,
        max_depth=max_depth,
        max_results=max_results,
        report=report,
    )
    report.blocks = controller.blocks
    report.final_delay = controller.current_delay
    logger.info("adaptive grid complete: %s", report.summary())
    return places, report
