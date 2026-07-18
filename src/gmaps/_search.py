"""Search API with grid-based coverage and pagination.

Overcomes Google Maps' ~120 results-per-area limit using:
1. Grid subdivision (divide area into cells, search each center)
2. Pagination via offset (max ~20 per page, ~6 pages = ~120 per cell)
3. Radius filtering (post-process results to enforce geographic bounds)

Based on verified patterns from gosom/google-maps-scraper and
promisingcoder/GoogleMapsCollector.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .control import center, quarter, region_km
from .grid import KM_PER_DEGREE_LAT, BoundingBox, GridCell, generate_cells
from .rpc.parser import ParsedPlace, parse_search_response

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)

# Live Chrome capture (2026-07-16, Atlanta chiropractors):
#   UI 14z/16z/18z all sent !4f13.1; only !1d viewport changed.
#   16z → 6634.9 m, 14z → 26539.6 m, 18z → 1658.7 m (halves each +1 zoom).
PROTOCOL_SEARCH_ZOOM = 13.1
VIEWPORT_METERS_AT_UI_ZOOM_16 = 6634.902757720493

# Strategy B adaptive mini-maps: UI zoom drives viewport (!1d), not !4f.
DEFAULT_MINIMAP_ZOOM = 16.0
DEFAULT_MINIMAP_MAX_ZOOM = 19.0
DEFAULT_MINIMAP_MAX_DEPTH = 4
DEFAULT_MINIMAP_MIN_CELL_KM = 0.25
# Soft ceiling for one map view. Deep pages rank farther (Chrome-verified);
# keep this low and split dense cells instead of paginating metro-wide lists.
DEFAULT_MINIMAP_MAX_PAGES = 2


def viewport_meters_for_ui_zoom(ui_zoom: float) -> float:
    """Map visible Maps zoom → search ``!1d`` viewport meters.

    Captured live: protocol ``!4f`` stays 13.1; each +1 UI zoom halves viewport.
    """
    return VIEWPORT_METERS_AT_UI_ZOOM_16 * (2.0 ** (16.0 - float(ui_zoom)))


def haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters between two WGS84 points."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def cell_accept_radius_meters(cell_km: float, buffer: float = 1.5) -> float:
    """Max distance from cell center to accept a place for that mini-map.

    Half-diagonal of the cell square, times ``buffer``. Chrome z16 Atlanta
    listings clustered ~1–3 km from center; this keeps ranking spillover from
    far across the metro out of this cell's unique set (neighbors cover them).
    """
    half_diagonal_m = (max(cell_km, 0.01) / 2.0) * math.sqrt(2.0) * 1000.0
    return half_diagonal_m * buffer


def _place_dedup_key(place: ParsedPlace) -> str:
    """Use stable identifiers first, with a deterministic fallback."""
    return (
        place.place_id
        or place.hex_id
        or place.cid
        or (
            f"{place.name.casefold()}|{place.address.casefold()}|{place.latitude}|{place.longitude}"
        )
    )


@dataclass
class SearchResult:
    """Result from a Google Maps search."""

    query: str
    places: list[ParsedPlace]
    total_results: int = 0
    pagination_offset: int = 0
    next_offset: int | None = None
    raw: Any = None


@dataclass(frozen=True)
class GridCellProgress:
    """One successfully completed grid cell and its newly retained places."""

    cell: GridCell
    index: int
    total: int
    new_places: tuple[ParsedPlace, ...]
    stats: Any
    checkpoint_key: str | None = None


@dataclass(frozen=True)
class _MiniMapTask:
    """One zoom-locked mini-map search over a rectangular footprint."""

    region: BoundingBox
    zoom: float
    depth: int


def _region_to_cell(region: BoundingBox) -> GridCell:
    lat, lon = center(region)
    return GridCell(lat=lat, lon=lon)


def _cell_to_region(cell: GridCell, cell_size_km: float) -> BoundingBox:
    """Approximate the square footprint whose center is ``cell``."""
    half = max(cell_size_km, 0.01) / 2.0
    lat_delta = half / KM_PER_DEGREE_LAT
    cos_lat = max(abs(math.cos(math.radians(cell.lat))), 1e-6)
    lon_delta = half / (KM_PER_DEGREE_LAT * cos_lat)
    return BoundingBox(
        cell.lat - lat_delta,
        cell.lon - lon_delta,
        cell.lat + lat_delta,
        cell.lon + lon_delta,
    )


class SearchAPI:
    """Search for places using verified Google Maps pb= protocol.

    Supports single search, paginated search, and grid-based search
    for area coverage beyond the 120-result limit.
    """

    # Maximum results Google returns per single search request
    MAX_PER_PAGE = 20

    # Maximum results per area (beyond this, use grid search)
    MAX_PER_AREA = 120

    def __init__(
        self,
        transport: HTTPTransport,
        language: str = "en",
        validate: str | bool = "warn",
    ):
        self._transport = transport
        self._language = language
        self._request_delay = 1.0  # seconds between requests
        # Drift validation mode: "warn" (log only, default, non-breaking),
        # "strict" (raise DriftError on unhealthy first page), or False (off).
        self._validate = validate

    async def place_details(
        self,
        place_id: str,
        hex_id: str,
        ftid: str,
        data_id: str,
        name: str,
        latitude: float = 0.0,
        longitude: float = 0.0,
        query: str = "",
    ) -> Any:
        """Fetch full place details via /maps/preview/place (Phase 2).

        Works with scraped cookies (Mode 2) or login cookies (Mode 3).
        With scraped cookies: gets review_count, hours, thumbnail, plus_code, owner.
        With login cookies: also gets description, photos, about, popular_times.

        Args:
            place_id: ChIJ... place ID from Phase 1.
            hex_id: 0x... hex ID from Phase 1.
            ftid: /g/... feature tracking ID from Phase 1.
            data_id: Internal data ID from Phase 1.
            name: Business name (for query parameter).
            latitude: Place latitude.
            longitude: Place longitude.
            query: Original search query.

        Returns:
            Decoded JSON response from /maps/preview/place.
        """
        from urllib.parse import quote

        hex_enc = quote(hex_id, safe="")
        ftid_enc = quote(ftid, safe="")
        lat = latitude or 0.0
        lng = longitude or 0.0
        viewport = 898976.2597

        pb = (
            f"!1m22"
            f"!1s{hex_enc}"
            f"!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}"
            f"!2m3!1f0.0!2f0.0!3f0.0"
            f"!3m2!1i1024!2i768!4f13.1"
            f"!4m2!3d{lat}!4d{lng}"
            f"!15m4!1m3!1s{hex_enc}!4s{ftid_enc}!5s{place_id}!6s{quote(query or name, safe='')}"
            f"!12m4!2m3!1i360!2i120!4i8"
            f"!13m57!2m2!1i203!2i100!3m2!2i4!5b1"
            f"!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
            f"!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
            f"!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
            f"!14m2!1s{data_id}!7e81"
            f"!15m111!1m29!4e2!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
            f"!18m17!3b1!4b1!5b1!6b1!9b1!13b1!14b1!17b1!20b1!21b1!22b1!30b1!32b1!33m1!1b1!34b1!36e2"
            f"!10m1!8e3!11m1!3e1!17b1!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
            f"!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
            f"!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
            f"!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
            f"!6m1!1e1!9b1!89b1!90m2!1m1!1e2!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1!122m1!1b1!126b1!127b1!128m1!1b0"
            f"!21m0!22m2!1e81!8e4!29m0!30m6!3b1!6m1!2b1!7m1!2b1!9b1"
            f"!34m5!7b1!10b1!14b1!15m1!1b0!37i785"
            f"!39s{quote(name, safe='')}!40b1!41b1"
        )

        raw = await self._transport.get(
            path=f"/maps/preview/place?authuser=0&hl={self._language}&gl=us&pb={pb}&q={quote(name, safe='')}",
            response_type="json",
        )
        return raw

    async def places(
        self,
        query: str,
        latitude: float | None = None,
        longitude: float | None = None,
        max_results: int = 20,
        offset: int = 0,
        radius_meters: int = 5000,
        viewport_dist: float = 10000.0,
        zoom: float = 16.0,
    ) -> SearchResult:
        """Search for places by text query (single page).

        Args:
            query: Search text.
            latitude: Center latitude.
            longitude: Center longitude.
            max_results: Maximum results (max 20 per page).
            offset: Pagination offset (multiples of 20).
            radius_meters: Search radius.
            viewport_dist: Viewport distance in meters.
            zoom: Google Maps zoom level (0-22, higher = more detail).
                  Default 16 for max pin density; use 13 for broad.

        Returns:
            SearchResult with list of ParsedPlace.
        """
        lat = latitude or 0.0
        lng = longitude or 0.0
        count = min(max_results, self.MAX_PER_PAGE)

        url = _build_search_url(
            query,
            lat,
            lng,
            count,
            radius_meters,
            viewport_dist,
            offset,
            zoom,
            language=self._language,
        )
        logger.info(
            "Search: '%s' at (%.4f, %.4f) offset=%d zoom=%.1f", query, lat, lng, offset, zoom
        )

        raw = await self._transport.get(
            path=url.replace("https://www.google.com", ""),
            response_type="json",
        )

        places = parse_search_response(raw)

        # Phase 0 drift guard: check structural health of the FIRST page only
        # (later pages may legitimately be empty at the end of pagination).
        # Warn-only by default — never alters output or raises unless strict.
        if self._validate and offset == 0:
            from .validation import validate_search

            validate_search(
                places,
                query=query,
                strict=(self._validate == "strict"),
                min_results=1,
            )

        return SearchResult(
            query=query,
            places=places[:max_results],
            total_results=len(places),
            pagination_offset=offset,
            next_offset=offset + count if len(places) >= count else None,
            raw=raw,
        )

    async def places_paginated(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_results: int = 120,
        radius_meters: int = 5000,
        viewport_dist: float = 10000.0,
        zoom: float = 16.0,
        stop_seen_ids: set[str] | None = None,
        boundary_contains: Callable[[float | None, float | None], bool] | None = None,
        filter_to_boundary: bool = False,
        on_page: Callable[[SearchResult], None] | None = None,
    ) -> list[ParsedPlace]:
        """Search with automatic pagination to get up to ~120 results.

        Google Maps returns ~20 per page. This paginates through all
        available pages until max_results or no more results.

        Args:
            query: Search text.
            latitude: Center latitude.
            longitude: Center longitude.
            max_results: Maximum total results (capped at ~120 per area).
            radius_meters: Search radius.
            viewport_dist: Viewport distance.

        Returns:
            List of all ParsedPlace found across all pages.
        """
        all_places: list[ParsedPlace] = []
        offset = 0
        seen_ids: set[str] = set()
        provisional_global_seen = set(stop_seen_ids or ())

        while len(all_places) < max_results and offset < self.MAX_PER_AREA:
            result = await self.places(
                query=query,
                latitude=latitude,
                longitude=longitude,
                max_results=self.MAX_PER_PAGE,
                offset=offset,
                radius_meters=radius_meters,
                viewport_dist=viewport_dist,
                zoom=zoom,
            )
            if on_page is not None:
                on_page(result)

            if not result.places:
                break

            new_count = 0
            new_global_count = 0
            for p in result.places:
                place_key = _place_dedup_key(p)
                if place_key not in seen_ids:
                    seen_ids.add(place_key)
                    all_places.append(p)
                    new_count += 1
                    if len(all_places) >= max_results:
                        break

                inside_boundary = boundary_contains or (lambda _lat, _lng: True)
                if filter_to_boundary and not inside_boundary(p.latitude, p.longitude):
                    continue
                if place_key not in provisional_global_seen:
                    provisional_global_seen.add(place_key)
                    new_global_count += 1

            if new_count == 0:
                break
            if stop_seen_ids is not None and new_global_count == 0:
                logger.debug(
                    "Pagination stopped at offset %d: page added no globally new places",
                    offset,
                )
                break

            offset += self.MAX_PER_PAGE
            await asyncio.sleep(self._request_delay)

        logger.info(
            "Paginated '%s': %d places across %d pages",
            query,
            len(all_places),
            offset // self.MAX_PER_PAGE,
        )
        return all_places

    async def minimap_grid_search(
        self,
        query: str,
        bbox: BoundingBox,
        cell_size_km: float = 1.0,
        max_results: int = 500,
        *,
        base_zoom: float = DEFAULT_MINIMAP_ZOOM,
        max_zoom: float = DEFAULT_MINIMAP_MAX_ZOOM,
        max_depth: int = DEFAULT_MINIMAP_MAX_DEPTH,
        min_cell_km: float = DEFAULT_MINIMAP_MIN_CELL_KM,
        max_pages: int = DEFAULT_MINIMAP_MAX_PAGES,
        dedup: bool = True,
        filter_to_bbox: bool = True,
        footprint_buffer: float = 1.5,
        boundary_contains: Callable[[float | None, float | None], bool] | None = None,
        shuffle_cells: bool = True,
        skip_cell_keys: set[str] | None = None,
        initial_seen_ids: set[str] | None = None,
        initial_places: list[ParsedPlace] | None = None,
        stats: Any = None,
        on_cell: Callable[[GridCellProgress], None] | None = None,
        on_footprint_drop: Callable[[ParsedPlace], None] | None = None,
    ) -> list[tuple[ParsedPlace, GridCell]]:
        """Adaptive mini-maps inside a fixed fence (Strategy B).

        Outer ``bbox`` never grows. For each mini-map (seed tile or child):

        1. Search at dense UI zoom (default 16). Protocol ``!4f`` stays 13.1;
           denser levels shrink ``!1d`` viewport (Chrome-verified).
        2. 0 results → empty leaf. Partial page (<20) → leaf done.
        3. Full pages → paginate same view up to ``max_pages`` (~6 ≈ 120).
        4. If the view hits the soft ceiling (~120 / 6 full pages) →
           **saturated**: quarter the cell, UI zoom+1, search children.
           Do not trust the coarse view as complete.
        5. Global ``place_id`` dedupe across all cells; re-hits are expected tax.
        6. Optional polygon/bbox filter drops outside-fence points.
        """
        inside = boundary_contains or bbox.contains
        seed_cells = list(generate_cells(bbox, cell_size_km))
        # Rectangular bbox often covers land outside a city polygon. Searching
        # those centers returns almost entirely fence-filtered ranking waste.
        if boundary_contains is not None:
            before = len(seed_cells)
            seed_cells = [cell for cell in seed_cells if boundary_contains(cell.lat, cell.lon)]
            dropped = before - len(seed_cells)
            if dropped:
                logger.info(
                    "Dropped %d/%d seed cells whose center is outside the fence",
                    dropped,
                    before,
                )
        if not seed_cells:
            # Fall back to bbox grid if polygon filter wiped everything (bad geom).
            seed_cells = list(generate_cells(bbox, cell_size_km))
        if shuffle_cells:
            random.shuffle(seed_cells)

        queue: deque[_MiniMapTask] = deque(
            _MiniMapTask(region=_cell_to_region(cell, cell_size_km), zoom=base_zoom, depth=0)
            for cell in seed_cells
        )
        skipped_keys = set(skip_cell_keys or ())
        seen_ids: set[str] = set(initial_seen_ids or ())
        seen_places = {_place_dedup_key(place): place for place in initial_places or []}
        all_results: list[tuple[ParsedPlace, GridCell]] = []
        tasks_done = 0
        tasks_planned = len(queue)
        page_cap = max(1, min(max_pages, self.MAX_PER_AREA // self.MAX_PER_PAGE))

        if stats:
            stats.cells_total = tasks_planned
            stats.cells_completed = len(
                skipped_keys & {_region_to_cell(t.region).key() for t in queue}
            )
            for place_id in seen_ids:
                stats.record_unique(place_id)

        logger.info(
            "Adaptive mini-map: '%s' | %d seed cells @ UI z%.1f "
            "(protocol %.1f) | cell≈%.2f km | page-then-split on ~%d",
            query,
            len(seed_cells),
            base_zoom,
            PROTOCOL_SEARCH_ZOOM,
            cell_size_km,
            page_cap * self.MAX_PER_PAGE,
        )

        while queue:
            if len(seen_ids) >= max_results:
                if stats:
                    stats.cap_reached = True
                break

            task = queue.popleft()
            cell = _region_to_cell(task.region)
            if cell.key() in skipped_keys:
                continue

            cell_km = max(region_km(task.region), min_cell_km)
            lat, lon = center(task.region)
            # Chrome: densify via viewport, not by raising !4f.
            viewport_dist = viewport_meters_for_ui_zoom(task.zoom)
            # Accept radius ≈ cell half-diagonal × buffer (gosom-style client filter).
            # Ranking still returns metro-wide hits; we only *own* nearby ones.
            # ``footprint_buffer`` is the recall/duplicate knob (P1 sweep target).
            accept_radius_m = cell_accept_radius_meters(cell_km, footprint_buffer)
            search_radius = int(max(viewport_dist, accept_radius_m))

            def absorb(
                places: list[ParsedPlace],
                *,
                lat: float,
                lon: float,
                accept_radius_m: float,
                cell: GridCell,
            ) -> list[ParsedPlace]:
                kept: list[ParsedPlace] = []
                for place in places:
                    if place.latitude is None or place.longitude is None:
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    # City/polygon fence first.
                    if filter_to_bbox and not inside(place.latitude, place.longitude):
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    # Mini-map footprint: drop far ranking spillover (cuts dups +
                    # outside-looking waste). Neighbors that contain the place keep it.
                    dist_m = haversine_meters(lat, lon, place.latitude, place.longitude)
                    if dist_m > accept_radius_m:
                        # Not a fence miss — cell-local reject (still counted separately).
                        if stats:
                            if hasattr(stats, "outside_footprint"):
                                stats.outside_footprint += 1
                            else:
                                stats.outside_boundary += 1
                        # In-fence but footprint-dropped: a neighbor cell must
                        # recover it, else it is a pure footprint recall leak.
                        # The hook lets a benchmark measure that (see
                        # scripts/recall_floor.py --leak).
                        if on_footprint_drop is not None:
                            on_footprint_drop(place)
                        continue
                    place_key = _place_dedup_key(place)
                    if dedup and place_key in seen_ids:
                        existing = seen_places.get(place_key)
                        if existing is not None and cell.key() not in existing.found_in_cells:
                            existing.found_in_cells.append(cell.key())
                        if stats:
                            stats.duplicates += 1
                        continue
                    if dedup:
                        seen_ids.add(place_key)
                        seen_places[place_key] = place
                        if stats:
                            stats.record_unique(place_key)
                    place.found_in_cells.append(cell.key())
                    all_results.append((place, cell))
                    kept.append(place)
                    if len(seen_ids) >= max_results:
                        if stats:
                            stats.cap_reached = True
                        break
                return kept

            retained: list[ParsedPlace] = []
            raw_from_view = 0
            full_pages = 0
            last_page_len = 0
            search_failed = False

            for page_idx in range(page_cap):
                if len(seen_ids) >= max_results:
                    break
                offset = page_idx * self.MAX_PER_PAGE
                try:
                    page = await self.places(
                        query=query,
                        latitude=lat,
                        longitude=lon,
                        max_results=self.MAX_PER_PAGE,
                        offset=offset,
                        radius_meters=search_radius,
                        viewport_dist=viewport_dist,
                        zoom=task.zoom,
                    )
                except Exception as exc:
                    error_type = type(exc).__name__
                    if stats:
                        stats.record_error(
                            error_type,
                            str(exc),
                            {
                                "cell": cell.key(),
                                "query": query,
                                "zoom": task.zoom,
                                "offset": offset,
                            },
                        )
                        stats.cells_failed += 1
                    logger.warning(
                        "Mini-map cell %s failed @ offset %d: %s: %s",
                        cell.key(),
                        offset,
                        error_type,
                        str(exc)[:100],
                    )
                    search_failed = True
                    break

                if stats:
                    stats.record_request()
                    stats.record_success(len(page.places))

                last_page_len = len(page.places)
                if last_page_len == 0:
                    break

                raw_from_view += last_page_len
                if last_page_len >= self.MAX_PER_PAGE:
                    full_pages += 1
                new_here = absorb(
                    page.places,
                    lat=lat,
                    lon=lon,
                    accept_radius_m=accept_radius_m,
                    cell=cell,
                )
                retained.extend(new_here)

                # Partial page → this view is exhausted.
                if last_page_len < self.MAX_PER_PAGE:
                    break
                # No new local uniques on a full page → further pages are pure waste.
                if not new_here:
                    break
                # Soft ceiling for one ranking/view.
                if raw_from_view >= self.MAX_PER_AREA or full_pages >= page_cap:
                    break

                await asyncio.sleep(self._request_delay)

            if search_failed and raw_from_view == 0:
                continue

            saturated = raw_from_view >= self.MAX_PER_AREA or full_pages >= page_cap
            # Only split when the coarse view both maxed out AND found enough
            # *local* uniques to justify 4 child requests. One lucky local on a
            # full page of metro ranking must not explode the queue (sparse
            # categories were burning minutes on empty children).
            min_local_to_split = max(8, page_cap * 4)
            can_split = (
                saturated
                and len(retained) >= min_local_to_split
                and task.depth < max_depth
                and (cell_km / 2.0) >= min_cell_km
                and task.zoom < max_zoom
                and len(seen_ids) < max_results
            )

            tasks_done += 1
            if stats:
                stats.cells_completed += 1
                if retained:
                    stats.cells_with_unique += 1
                # Incomplete if maxed and we could not recover via children.
                if saturated and not can_split:
                    stats.cells_saturated += 1

            if on_cell is not None:
                on_cell(
                    GridCellProgress(
                        cell=cell,
                        index=tasks_done,
                        total=max(tasks_planned, tasks_done + len(queue)),
                        new_places=tuple(retained),
                        stats=stats,
                    )
                )

            if can_split:
                child_zoom = min(task.zoom + 1.0, max_zoom)
                children = [
                    _MiniMapTask(region=child, zoom=child_zoom, depth=task.depth + 1)
                    for child in quarter(task.region)
                ]
                if shuffle_cells:
                    random.shuffle(children)
                queue.extend(children)
                tasks_planned += len(children)
                if stats:
                    stats.cells_total = tasks_planned
                logger.info(
                    "Saturated view %s raw=%d pages=%d @ UI z%.1f viewport=%.0fm "
                    "→ split %d children @ UI z%.1f viewport=%.0fm",
                    cell.key(),
                    raw_from_view,
                    full_pages,
                    task.zoom,
                    viewport_dist,
                    len(children),
                    child_zoom,
                    viewport_meters_for_ui_zoom(child_zoom),
                )

            if (tasks_done % 10 == 0 or not queue) and stats:
                logger.info(
                    "Adaptive progress: %d done | %d queued | %s",
                    tasks_done,
                    len(queue),
                    stats.progress(),
                )

            await asyncio.sleep(self._request_delay)

        logger.info(
            "Adaptive mini-map complete: %d unique from %d searched views",
            len(all_results),
            tasks_done,
        )
        return all_results

    async def diversity_subarea_search(
        self,
        query: str,
        subareas: list[Any],
        *,
        max_results: int = 500,
        pages_per_subarea: int = 2,
        zoom: float = DEFAULT_MINIMAP_ZOOM,
        dedup: bool = True,
        filter_to_bbox: bool = True,
        boundary_contains: Callable[[float | None, float | None], bool] | None = None,
        skip_keys: set[str] | None = None,
        initial_seen_ids: set[str] | None = None,
        initial_places: list[ParsedPlace] | None = None,
        stats: Any = None,
        on_cell: Callable[[GridCellProgress], None] | None = None,
    ) -> list[tuple[ParsedPlace, GridCell]]:
        """Second pass: different text per neighborhood/ZIP to surface buried places.

        Uses ``"{query} near {subarea.name}"`` (no city stuffed into every query —
        live tests showed city-in-query hurts locality). Still filters to the
        fixed parent fence + local footprint.
        """
        inside = boundary_contains or (lambda _lat, _lng: True)
        seen_ids: set[str] = set(initial_seen_ids or ())
        seen_places = {_place_dedup_key(place): place for place in initial_places or []}
        skipped = set(skip_keys or ())
        all_results: list[tuple[ParsedPlace, GridCell]] = []
        done = 0
        total = len(subareas)

        if stats:
            for place_id in seen_ids:
                stats.record_unique(place_id)
            stats.cells_total = max(getattr(stats, "cells_total", 0), total)

        for index, sub in enumerate(subareas, start=1):
            if len(seen_ids) >= max_results:
                if stats:
                    stats.cap_reached = True
                break

            lat = float(sub.center[0])
            lon = float(sub.center[1])
            cell = GridCell(lat=lat, lon=lon)
            key = f"div:{sub.name}:{cell.key()}"
            if key in skipped:
                continue

            # Characteristic size from subarea bbox for footprint + viewport.
            sub_bbox = sub.bbox
            cell_km = max(region_km(sub_bbox), 0.8)
            viewport_dist = min(
                viewport_meters_for_ui_zoom(zoom),
                max(cell_km * 1000.0, 1500.0),
            )
            accept_radius_m = cell_accept_radius_meters(cell_km, buffer=1.75)
            local_query = f"{query} near {sub.name}"
            retained: list[ParsedPlace] = []

            for page_idx in range(max(1, pages_per_subarea)):
                if len(seen_ids) >= max_results:
                    break
                offset = page_idx * self.MAX_PER_PAGE
                try:
                    page = await self.places(
                        query=local_query,
                        latitude=lat,
                        longitude=lon,
                        max_results=self.MAX_PER_PAGE,
                        offset=offset,
                        radius_meters=int(accept_radius_m),
                        viewport_dist=viewport_dist,
                        zoom=zoom,
                    )
                except Exception as exc:
                    if stats:
                        stats.record_error(type(exc).__name__, str(exc), {"subarea": sub.name})
                        stats.cells_failed += 1
                    break

                if stats:
                    stats.record_request()
                    stats.record_success(len(page.places))
                if not page.places:
                    break

                new_here = 0
                for place in page.places:
                    if place.latitude is None or place.longitude is None:
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    if filter_to_bbox and not inside(place.latitude, place.longitude):
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    if (
                        haversine_meters(lat, lon, place.latitude, place.longitude)
                        > accept_radius_m
                    ):
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    place_key = _place_dedup_key(place)
                    if dedup and place_key in seen_ids:
                        if stats:
                            stats.duplicates += 1
                        continue
                    if dedup:
                        seen_ids.add(place_key)
                        seen_places[place_key] = place
                        if stats:
                            stats.record_unique(place_key)
                    place.found_in_cells.append(key)
                    all_results.append((place, cell))
                    retained.append(place)
                    new_here += 1
                    if len(seen_ids) >= max_results:
                        if stats:
                            stats.cap_reached = True
                        break

                if len(page.places) < self.MAX_PER_PAGE or new_here == 0:
                    break
                await asyncio.sleep(self._request_delay)

            done += 1
            if stats:
                stats.cells_completed += 1
                if retained:
                    stats.cells_with_unique += 1
            if on_cell is not None:
                on_cell(
                    GridCellProgress(
                        cell=cell,
                        index=index,
                        total=total,
                        new_places=tuple(retained),
                        stats=stats,
                        checkpoint_key=key,
                    )
                )
            await asyncio.sleep(self._request_delay)

        logger.info(
            "Diversity pass complete: %d new uniques from %d/%d subareas",
            len(all_results),
            done,
            total,
        )
        return all_results

    async def gap_fill_search(
        self,
        query: str,
        bbox: BoundingBox,
        places: list[ParsedPlace],
        *,
        cell_size_km: float = 2.0,
        max_results: int = 500,
        pages_per_gap: int = 2,
        zoom: float = DEFAULT_MINIMAP_ZOOM,
        dedup: bool = True,
        filter_to_bbox: bool = True,
        boundary_contains: Callable[[float | None, float | None], bool] | None = None,
        skip_keys: set[str] | None = None,
        initial_seen_ids: set[str] | None = None,
        stats: Any = None,
        on_cell: Callable[[GridCellProgress], None] | None = None,
    ) -> list[tuple[ParsedPlace, GridCell]]:
        """Third pass: only search empty hex/grid patches still uncovered."""
        from .coverage import uncovered_cell_centers

        inside = boundary_contains or bbox.contains
        gaps = uncovered_cell_centers(
            bbox,
            places,
            cell_size_km=cell_size_km,
            boundary_contains=boundary_contains,
        )
        seen_ids: set[str] = set(initial_seen_ids or ())
        skipped = set(skip_keys or ())
        all_results: list[tuple[ParsedPlace, GridCell]] = []

        if stats:
            for place_id in seen_ids:
                stats.record_unique(place_id)
            stats.cells_total = max(getattr(stats, "cells_total", 0), len(gaps))

        logger.info("Gap-fill: %d uncovered centers at %.2f km", len(gaps), cell_size_km)

        # Nashville full run: ~260 empty-gap requests for +1 unique. Abort once the
        # long tail stops paying for itself.
        empty_streak = 0
        max_empty_streak = 12
        gaps_searched = 0

        for index, cell in enumerate(gaps, start=1):
            if len(seen_ids) >= max_results:
                if stats:
                    stats.cap_reached = True
                break
            if empty_streak >= max_empty_streak:
                logger.info(
                    "Gap-fill early stop after %d consecutive empty gaps (%d searched)",
                    empty_streak,
                    gaps_searched,
                )
                break
            key = f"gap:{cell.key()}"
            if key in skipped:
                continue

            viewport_dist = min(
                viewport_meters_for_ui_zoom(zoom),
                max(cell_size_km * 1000.0, 1500.0),
            )
            accept_radius_m = cell_accept_radius_meters(cell_size_km, buffer=1.5)
            retained: list[ParsedPlace] = []

            for page_idx in range(max(1, pages_per_gap)):
                if len(seen_ids) >= max_results:
                    break
                offset = page_idx * self.MAX_PER_PAGE
                try:
                    page = await self.places(
                        query=query,
                        latitude=cell.lat,
                        longitude=cell.lon,
                        max_results=self.MAX_PER_PAGE,
                        offset=offset,
                        radius_meters=int(accept_radius_m),
                        viewport_dist=viewport_dist,
                        zoom=zoom,
                    )
                except Exception as exc:
                    if stats:
                        stats.record_error(type(exc).__name__, str(exc), {"gap": cell.key()})
                        stats.cells_failed += 1
                    break

                if stats:
                    stats.record_request()
                    stats.record_success(len(page.places))
                if not page.places:
                    break

                new_here = 0
                for place in page.places:
                    if place.latitude is None or place.longitude is None:
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    if filter_to_bbox and not inside(place.latitude, place.longitude):
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    if (
                        haversine_meters(cell.lat, cell.lon, place.latitude, place.longitude)
                        > accept_radius_m
                    ):
                        if stats:
                            stats.outside_boundary += 1
                        continue
                    place_key = _place_dedup_key(place)
                    if dedup and place_key in seen_ids:
                        if stats:
                            stats.duplicates += 1
                        continue
                    if dedup:
                        seen_ids.add(place_key)
                        if stats:
                            stats.record_unique(place_key)
                    place.found_in_cells.append(key)
                    all_results.append((place, cell))
                    retained.append(place)
                    new_here += 1
                    if len(seen_ids) >= max_results:
                        if stats:
                            stats.cap_reached = True
                        break

                if len(page.places) < self.MAX_PER_PAGE or new_here == 0:
                    break
                await asyncio.sleep(self._request_delay)

            gaps_searched += 1
            if retained:
                empty_streak = 0
            else:
                empty_streak += 1

            if stats:
                stats.cells_completed += 1
                if retained:
                    stats.cells_with_unique += 1
            if on_cell is not None:
                on_cell(
                    GridCellProgress(
                        cell=cell,
                        index=index,
                        total=len(gaps),
                        new_places=tuple(retained),
                        stats=stats,
                        checkpoint_key=key,
                    )
                )
            await asyncio.sleep(self._request_delay)

        logger.info(
            "Gap-fill complete: %d new uniques from %d/%d gaps searched",
            len(all_results),
            gaps_searched,
            len(gaps),
        )
        return all_results

    async def grid_search(
        self,
        query: str,
        bbox: BoundingBox,
        cell_size_km: float = 1.0,
        max_results: int = 500,
        dedup: bool = True,
        zoom: float = 16.0,
        detect_exhaustion: bool = True,
        stats: Any = None,
        paginate: bool = True,
        filter_to_bbox: bool = False,
        boundary_contains: Callable[[float | None, float | None], bool] | None = None,
        shuffle_cells: bool = True,
        skip_cell_keys: set[str] | None = None,
        initial_seen_ids: set[str] | None = None,
        initial_places: list[ParsedPlace] | None = None,
        on_cell: Callable[[GridCellProgress], None] | None = None,
    ) -> list[tuple[ParsedPlace, GridCell]]:
        """Search a geographic area by subdividing into grid cells.

        Apify methodology:
        1. Split area into grid cells (mini-maps)
        2. Each cell uses zoom 16 for max pin density
        3. Paginate while pages add globally new in-boundary results
        4. Search EVERY cell — never stop early
        5. Deduplicate by place_id across all cells
        """
        cells = generate_cells(bbox, cell_size_km)

        # gosom anti-detection: randomize cell order to avoid sequential
        # spatial scanning pattern that Google can detect
        cells = list(cells)
        if shuffle_cells:
            random.shuffle(cells)
        cell_count = len(cells)
        skipped_keys = skip_cell_keys or set()
        planned_keys = {cell.key() for cell in cells}
        resumed_cells = len(skipped_keys & planned_keys)
        if stats:
            stats.cells_total = cell_count
            stats.cells_completed = resumed_cells
        logger.info(
            "Grid search: '%s' across %d cells (%.1f km, zoom %.1f)",
            query,
            cell_count,
            cell_size_km,
            zoom,
        )

        all_results: list[tuple[ParsedPlace, GridCell]] = []
        seen_ids: set[str] = set(initial_seen_ids or ())
        seen_places = {_place_dedup_key(place): place for place in initial_places or []}
        if stats:
            for place_id in seen_ids:
                stats.record_unique(place_id)
        consecutive_empty = 0  # track exhaustion
        cells_processed = 0

        for i, cell in enumerate(cells):
            if cell.key() in skipped_keys:
                continue
            if len(seen_ids) >= max_results:
                if stats:
                    stats.cap_reached = True
                break
            if stats and stats.unique_places >= max_results:
                stats.cap_reached = True
                break

            # Adaptive viewport: match cell size for proper zoom-in
            cell_diag_m = cell_size_km * 500
            search_radius = int(cell_size_km * 750)

            try:
                if paginate:
                    pages_observed = 0

                    def observe_page(page: SearchResult) -> None:
                        nonlocal pages_observed
                        pages_observed += 1
                        if stats:
                            stats.record_request()
                            stats.record_success(len(page.places))

                    # Search deeper only while each page contributes at least
                    # one globally new in-boundary business. This preserves
                    # coverage without blindly replaying duplicate-heavy pages.
                    cell_places = await self.places_paginated(
                        query=query,
                        latitude=cell.lat,
                        longitude=cell.lon,
                        max_results=self.MAX_PER_AREA,
                        radius_meters=search_radius,
                        viewport_dist=cell_diag_m,
                        zoom=zoom,
                        stop_seen_ids=seen_ids if dedup else None,
                        boundary_contains=boundary_contains or bbox.contains,
                        filter_to_boundary=filter_to_bbox,
                        on_page=observe_page,
                    )
                    result_places = cell_places
                    if stats and pages_observed == 0:
                        # Test doubles and third-party subclasses may override
                        # places_paginated without invoking the page observer.
                        stats.record_request()
                        stats.record_success(len(result_places))
                else:
                    if stats:
                        stats.record_request()
                    result = await self.places(
                        query=query,
                        latitude=cell.lat,
                        longitude=cell.lon,
                        max_results=self.MAX_PER_PAGE,
                        radius_meters=search_radius,
                        viewport_dist=cell_diag_m,
                        zoom=zoom,
                    )
                    result_places = result.places
                    if stats:
                        stats.record_success(len(result_places))

            except Exception as e:
                error_type = type(e).__name__
                if stats:
                    stats.record_error(
                        error_type,
                        str(e),
                        {"cell": f"({cell.lat:.4f}, {cell.lon:.4f})", "query": query},
                    )
                    stats.cells_failed += 1
                logger.warning(
                    "Cell %d/%d failed: %s: %s", i + 1, cell_count, error_type, str(e)[:100]
                )
                consecutive_empty += 1
                continue

            cells_processed += 1
            if stats:
                stats.cells_completed += 1
                if len(result_places) >= self.MAX_PER_AREA:
                    stats.cells_saturated += 1

            new_in_cell = 0
            retained_in_cell: list[ParsedPlace] = []
            for p in result_places:
                inside_boundary = boundary_contains or bbox.contains
                if filter_to_bbox and not inside_boundary(p.latitude, p.longitude):
                    if stats:
                        stats.outside_boundary += 1
                    continue
                place_key = _place_dedup_key(p)
                if dedup:
                    if place_key in seen_ids:
                        existing = seen_places.get(place_key)
                        if existing is not None and cell.key() not in existing.found_in_cells:
                            existing.found_in_cells.append(cell.key())
                        if stats:
                            stats.duplicates += 1
                        continue
                    seen_ids.add(place_key)
                    seen_places[place_key] = p
                    p.found_in_cells.append(cell.key())
                    if stats:
                        stats.record_unique(place_key)
                all_results.append((p, cell))
                retained_in_cell.append(p)
                new_in_cell += 1
                if len(seen_ids) >= max_results:
                    if stats:
                        stats.cap_reached = True
                    break

            if stats and new_in_cell:
                stats.cells_with_unique += 1

            if on_cell is not None:
                on_cell(
                    GridCellProgress(
                        cell=cell,
                        index=i + 1,
                        total=cell_count,
                        new_places=tuple(retained_in_cell),
                        stats=stats,
                    )
                )

            # Exhaustion tracking: skip low-yield cells but DON'T stop.
            # Apify/gosom pattern: search ALL cells, just skip ones that
            # return < 5 new results. Never break the loop early —
            # randomized cell order means the next cell might be in an
            # uncovered area.
            if new_in_cell < 5:
                consecutive_empty += 1
                logger.debug(
                    "Cell %d/%d: low yield (%d new), empty-streak=%d (continuing)",
                    i + 1,
                    cell_count,
                    new_in_cell,
                    consecutive_empty,
                )
            else:
                consecutive_empty = 0

            # Progress logging
            if (i + 1) % 10 == 0 or i == cell_count - 1:
                if stats:
                    logger.info(
                        "Grid progress: %d/%d cells | %s", i + 1, cell_count, stats.progress()
                    )
                else:
                    logger.info(
                        "Grid: %d/%d cells | %d results | empty-streak=%d",
                        i + 1,
                        cell_count,
                        len(all_results),
                        consecutive_empty,
                    )

            await asyncio.sleep(self._request_delay)

        logger.info(
            "Grid complete: %d results from %d cells (%.1f%% coverage)",
            len(all_results),
            cells_processed,
            100 * (cells_processed + resumed_cells) / cell_count if cell_count else 0,
        )
        return all_results

    async def nearby(
        self,
        latitude: float,
        longitude: float,
        query: str = "",
        radius_meters: int = 5000,
        max_results: int = 20,
    ) -> SearchResult:
        """Search near a location (convenience wrapper)."""
        return await self.places(
            query=query or "*",
            latitude=latitude,
            longitude=longitude,
            max_results=max_results,
            radius_meters=radius_meters,
        )


def _build_search_url(
    query: str,
    lat: float,
    lng: float,
    count: int = 20,
    radius: int = 5000,
    viewport_dist: float = 10000.0,
    offset: int = 0,
    zoom: float = 16.0,
    language: str = "en",
    region: str = "us",
) -> str:
    """Build the verified Google Maps search URL.

    Live Chrome capture (2026-07-16): UI zoom does **not** change ``!4f``.
    Maps keeps protocol zoom at 13.1 and varies ``!1d`` viewport meters
    (16z≈6635 m, 14z≈26540 m, 18z≈1659 m). The ``zoom`` argument is the
    visible UI zoom used only to derive a default viewport when the caller
    still passes the legacy default 10000 m.

    Key parameters:
    - !4f: fixed protocol zoom 13.1 (current Maps UI).
    - !1d{viewport_dist}: viewport extent in meters (the real zoom lever).
    - !7i{count}!8i{offset}: Pagination (20 per page, offset in 20s).
    Geographic enforcement remains client-side (resolved boundary / bbox).
    """
    enc = quote(query, safe="")
    q_param = enc.replace("%20", "+")
    # Prefer an explicit viewport. If the caller left the historical 10 km
    # default, derive viewport from UI zoom so "zoom=16/18" behaves like Maps.
    effective_viewport = float(viewport_dist)
    if abs(effective_viewport - 10000.0) < 1e-6 and zoom != 16.0:
        effective_viewport = viewport_meters_for_ui_zoom(zoom)
    elif abs(effective_viewport - 10000.0) < 1e-6 and zoom == 16.0:
        # Default zoom 16 with default viewport → browser-accurate 16z viewport.
        effective_viewport = viewport_meters_for_ui_zoom(16.0)
    offset_field = f"!8i{offset}" if offset else ""

    return (
        "https://www.google.com/search"
        f"?tbm=map&authuser=0&hl={language}&gl={region}"
        f"&q={q_param}"
        f"&pb=!1s{enc}"
        f"!4m8!1m3!1d{effective_viewport}!2d{lng}!3d{lat}"
        f"!3m2!1i1024!2i768!4f{PROTOCOL_SEARCH_ZOOM:g}"
        f"!7i{count}{offset_field}"
        f"!10b1"
        "!12m53!1m5!18b1!30b1!31m1!1b1!34e1"
        "!2m4!5m1!6e2!20e3!39b1"
        "!6m25!32i1!49b1!63m0!66b1!85b1!114b1!149b1!206b1"
        "!209b1!212b1!216b1!222b1!223b1!232b1!234b1!235b1"
        "!239b1!246b1!253b1!260b1!266b1!270b1!273b1!280b1!291m0"
        "!10b1!12b1!13b1!14b1!16b1"
        "!17m1!3e1!20m4!5e2!6b1!8b1!14b1!46m1!1b0!96b1!99b1"
        "!19m4!2m3!1i360!2i120!4i8"
        "!20m57!2m2!1i203!2i100!3m2!2i4!5b1"
        "!6m6!1m2!1i86!2i86!1m2!1i408!2i240"
        "!7m33!1m3!1e1!2b0!3e3!1m3!1e2!2b1!3e2!1m3!1e2!2b0!3e3"
        "!1m3!1e8!2b0!3e3!1m3!1e10!2b0!3e3!1m3!1e10!2b1!3e2"
        "!1m3!1e10!2b0!3e4!1m3!1e9!2b1!3e2!2b1!9b0"
        "!15m8!1m7!1m2!1m1!1e2!2m2!1i195!2i195!3i20"
        "!24m107!1m25!13m9!2b1!3b1!4b1!6i1!8b1!9b1!14b1!20b1!25b1"
        "!18m14!3b1!4b1!5b1!6b1!13b1!14b1!17b1!21b1!22b1!32b1"
        "!33m1!1b1!34b1!36e2!10m1!8e3!11m1!3e1!17b1"
        "!20m2!1e3!1e6!24b1!25b1!26b1!27b1!29b1!30m1!2b1!36b1!37b1"
        "!39m3!2m2!2i1!3i1!43b1!52b1!54m1!1b1!55b1!56m1!1b1"
        "!61m2!1m1!1e1!65m5!3m4!1m3!1m2!1i224!2i298"
        "!72m22!1m8!2b1!5b1!7b1!12m4!1b1!2b1!4m1!1e1!4b1"
        "!8m10!1m6!4m1!1e1!4m1!1e3!4m1!1e4"
        "!3sother_user_google_review_posts__and__hotel_and_vr_partner_review_posts"
        "!6m1!1e1!9b1!89b1!90m2!1m1!1e2"
        "!98m3!1b1!2b1!3b1!103b1!113b1!114m3!1b1!2m1!1b1!117b1"
        "!122m1!1b1!126b1!127b1!128m1!1b0"
        "!26m4!2m3!1i80!2i92!4i8"
        "!30m28!1m6!1m2!1i0!2i0!2m2!1i530!2i768"
        "!1m6!1m2!1i974!2i0!2m2!1i1024!2i768"
        "!1m6!1m2!1i0!2i0!2m2!1i1024!2i20"
        "!1m6!1m2!1i0!2i748!2m2!1i1024!2i768"
        "!34m19!2b1!3b1!4b1!6b1!8m6!1b1!3b1!4b1!5b1!6b1!7b1"
        "!9b1!12b1!14b1!20b1!23b1!25b1!26b1!31b1!37m1!1e81!42b1"
        "!49m10!3b1!6m2!1b1!2b1!7m2!1e3!2b1!8b1!9b1!10e2"
        "!50m3!2e2!3m1!3b1!61b1!67m5!7b1!10b1!14b1!15m1!1b0"
        "!69i786!77b1"
    )
