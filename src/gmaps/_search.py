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
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .grid import BoundingBox, GridCell, generate_cells
from .rpc.parser import ParsedPlace, parse_search_response

if TYPE_CHECKING:
    from .transport import HTTPTransport

logger = logging.getLogger(__name__)


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
        import random

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

    Format verified against:
    - gosom/google-maps-scraper buildGoogleMapsParams()
    - promisingcoder/GoogleMapsCollector build_search_url()
    - Apify blog on Google Maps scraping limits
    - Live Google Maps July 2026 traffic analysis

    Key parameters:
    - !4f{zoom}: Map zoom level (0-22). Higher = more pins visible.
      Use 15-17 for dense results; 13 for broader coverage.
    - !1d{viewport_dist}: Viewport extent in meters. Should match
      cell_size_km * 500-1000 for grid searches.
    - !7i{count}!8i{offset}: Pagination (20 per page, offset in 20s).
    The current UI request does not expose a strict radius field. Geographic
    enforcement remains client-side through the resolved location boundary.
    """
    enc = quote(query, safe="")
    q_param = enc.replace("%20", "+")
    # Google Maps' visible URL zoom and its internal search-request zoom use
    # different scales. A 16z map emitted 13.1 in verified July 2026 traffic.
    protocol_zoom = max(0.0, zoom - 2.9)
    offset_field = f"!8i{offset}" if offset else ""

    return (
        "https://www.google.com/search"
        f"?tbm=map&authuser=0&hl={language}&gl={region}"
        f"&q={q_param}"
        f"&pb=!1s{enc}"
        f"!4m8!1m3!1d{viewport_dist}!2d{lng}!3d{lat}"
        f"!3m2!1i1024!2i768!4f{protocol_zoom:g}"
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
