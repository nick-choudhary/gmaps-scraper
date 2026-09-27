"""Coordinate-based Google Maps rank-grid scanning."""

from __future__ import annotations

import html
import json
import math
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Protocol, cast

import httpx

from ._search import SearchAPI, SearchResult, viewport_meters_for_ui_zoom
from .exceptions import GMapsError
from .grid import KM_PER_DEGREE_LAT
from .rpc.parser import ParsedPlace

TargetKind = Literal["auto", "place_id", "cid", "hex_id", "name"]


class RankProviderError(RuntimeError):
    """A rank provider could not complete a search."""


@dataclass(frozen=True)
class RankCandidate:
    """One ranked Google Maps result."""

    position: int
    name: str = ""
    place_id: str = ""
    cid: str = ""
    hex_id: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            key: value
            for key, value in {
                "position": self.position,
                "name": self.name,
                "place_id": self.place_id,
                "cid": self.cid,
                "hex_id": self.hex_id,
                "address": self.address,
                "latitude": self.latitude,
                "longitude": self.longitude,
            }.items()
            if value not in ("", None)
        }


@dataclass(frozen=True)
class RankTarget:
    """The business identity to locate in each result set."""

    value: str
    kind: TargetKind = "auto"

    def matches(self, candidate: RankCandidate) -> bool:
        value = self.value.strip()
        if not value:
            return False

        if self.kind == "place_id":
            return value == candidate.place_id
        if self.kind == "cid":
            return bool(_cid_variants(value) & _candidate_cid_variants(candidate))
        if self.kind == "hex_id":
            return value.casefold() == candidate.hex_id.casefold()
        if self.kind == "name":
            return _normalize_name(value) == _normalize_name(candidate.name)

        if value == candidate.place_id:
            return True
        if value.casefold() == candidate.hex_id.casefold():
            return True
        if _cid_variants(value) & _candidate_cid_variants(candidate):
            return True
        return _normalize_name(value) == _normalize_name(candidate.name)

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "kind": self.kind}


@dataclass(frozen=True)
class ProviderSearchResult:
    """Candidates returned by the provider that actually served a point."""

    provider: str
    candidates: tuple[RankCandidate, ...]


class RankSearchProvider(Protocol):
    """Search contract shared by direct Google Maps and Serper."""

    name: str

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult: ...


class DirectGoogleMapsProvider:
    """Free provider backed by the existing Google Maps internal HTTP client."""

    name = "direct"

    def __init__(self, search_api: SearchAPI):
        self._search_api = search_api

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult:
        viewport_meters = viewport_meters_for_ui_zoom(zoom)
        candidates: list[RankCandidate] = []
        try:
            for offset in range(0, max_rank, SearchAPI.MAX_PER_PAGE):
                page = await self._search_api.places(
                    query=query,
                    latitude=latitude,
                    longitude=longitude,
                    max_results=min(SearchAPI.MAX_PER_PAGE, max_rank - offset),
                    offset=offset,
                    radius_meters=max(1, round(viewport_meters)),
                    viewport_dist=viewport_meters,
                    zoom=zoom,
                )
                candidates.extend(_candidates_from_page(page, max_rank))
                if page.next_offset is None:
                    break
        except GMapsError as exc:
            raise RankProviderError(f"Direct Google Maps search failed: {exc}") from exc

        return ProviderSearchResult(provider=self.name, candidates=tuple(candidates))


class SerperMapsProvider:
    """Paid fallback provider for Serper's Google Maps endpoint."""

    name = "serper"
    endpoint = "https://google.serper.dev/maps"

    def __init__(
        self,
        api_key: str,
        *,
        language: str = "en",
        country: str = "us",
        timeout: float = 30.0,
        proxy: str | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        if not api_key.strip():
            raise ValueError("Serper API key cannot be empty.")
        self._api_key = api_key
        self._language = language
        self._country = country
        self._client = client or httpx.AsyncClient(timeout=timeout, proxy=proxy)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult:
        request = {
            "q": query,
            "ll": f"@{latitude:.7f},{longitude:.7f},{zoom:g}z",
            "gl": self._country,
            "hl": self._language,
            "num": max_rank,
        }
        try:
            response = await self._client.post(
                self.endpoint,
                headers={
                    "X-API-KEY": self._api_key,
                    "Content-Type": "application/json",
                },
                json=request,
            )
            response.raise_for_status()
            raw: object = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RankProviderError(f"Serper Maps search failed: {exc}") from exc

        if not isinstance(raw, dict):
            raise RankProviderError("Serper Maps returned a non-object response.")
        payload = cast(Mapping[str, object], raw)
        raw_places = payload.get("places")
        if not isinstance(raw_places, list):
            raise RankProviderError("Serper Maps response did not contain a places list.")

        candidates: list[RankCandidate] = []
        for fallback_position, raw_place in enumerate(raw_places[:max_rank], start=1):
            if not isinstance(raw_place, dict):
                continue
            place = cast(Mapping[str, object], raw_place)
            candidates.append(_candidate_from_serper(place, fallback_position))

        return ProviderSearchResult(provider=self.name, candidates=tuple(candidates))


class FallbackRankProvider:
    """Use a primary provider, then fall back on errors or empty results."""

    name = "auto"

    def __init__(
        self,
        primary: RankSearchProvider,
        fallback: RankSearchProvider | None,
    ):
        self._primary = primary
        self._fallback = fallback

    async def search(
        self,
        query: str,
        latitude: float,
        longitude: float,
        max_rank: int,
        zoom: float,
    ) -> ProviderSearchResult:
        try:
            primary_result = await self._primary.search(query, latitude, longitude, max_rank, zoom)
        except RankProviderError as primary_error:
            if self._fallback is None:
                raise
            try:
                return await self._fallback.search(query, latitude, longitude, max_rank, zoom)
            except RankProviderError as fallback_error:
                raise RankProviderError(
                    f"{primary_error}; fallback also failed: {fallback_error}"
                ) from fallback_error

        if primary_result.candidates or self._fallback is None:
            return primary_result
        return await self._fallback.search(query, latitude, longitude, max_rank, zoom)


@dataclass(frozen=True)
class RankGridCoordinate:
    """A row/column location in the geographic scan grid."""

    row: int
    column: int
    latitude: float
    longitude: float


@dataclass(frozen=True)
class RankGridPoint:
    """The target's rank at one coordinate."""

    row: int
    column: int
    latitude: float
    longitude: float
    provider: str
    rank: int | None
    result_count: int
    matched_place: RankCandidate | None = None
    error: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "row": self.row,
            "column": self.column,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "provider": self.provider,
            "rank": self.rank,
            "result_count": self.result_count,
        }
        if self.matched_place is not None:
            data["matched_place"] = self.matched_place.to_dict()
        if self.error:
            data["error"] = self.error
        return data


@dataclass(frozen=True)
class RankGridResult:
    """Complete coordinate scan and summary."""

    query: str
    target: RankTarget
    center_latitude: float
    center_longitude: float
    grid_size: int
    spacing_km: float
    zoom: float
    max_rank: int
    points: tuple[RankGridPoint, ...]
    generated_at: str

    @property
    def found_points(self) -> tuple[RankGridPoint, ...]:
        return tuple(point for point in self.points if point.rank is not None)

    def summary(self) -> dict[str, object]:
        ranks = [point.rank for point in self.points if point.rank is not None]
        found = len(ranks)
        measured = sum(not point.error for point in self.points)
        return {
            "total_points": len(self.points),
            "measured_points": measured,
            "found_points": found,
            "not_found_points": measured - found,
            "visibility_percent": round((found / measured) * 100, 2) if measured else None,
            "best_rank": min(ranks) if ranks else None,
            "average_rank": round(sum(ranks) / found, 2) if ranks else None,
            "error_points": sum(bool(point.error) for point in self.points),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "target": self.target.to_dict(),
            "center": {
                "latitude": self.center_latitude,
                "longitude": self.center_longitude,
            },
            "grid_size": self.grid_size,
            "spacing_km": self.spacing_km,
            "zoom": self.zoom,
            "max_rank": self.max_rank,
            "generated_at": self.generated_at,
            "summary": self.summary(),
            "points": [point.to_dict() for point in self.points],
        }


class RankGridScanner:
    """Run the same Maps query across a geographic grid."""

    def __init__(self, provider: RankSearchProvider):
        self._provider = provider

    async def scan(
        self,
        *,
        query: str,
        target: RankTarget,
        center_latitude: float,
        center_longitude: float,
        grid_size: int = 5,
        spacing_km: float = 1.0,
        zoom: float = 14.0,
        max_rank: int = 20,
        on_point: Callable[[RankGridPoint], None] | None = None,
    ) -> RankGridResult:
        coordinates = generate_rank_grid(
            center_latitude,
            center_longitude,
            grid_size=grid_size,
            spacing_km=spacing_km,
        )
        points: list[RankGridPoint] = []

        for coordinate in coordinates:
            try:
                search_result = await self._provider.search(
                    query,
                    coordinate.latitude,
                    coordinate.longitude,
                    max_rank,
                    zoom,
                )
                matched = next(
                    (
                        candidate
                        for candidate in search_result.candidates
                        if target.matches(candidate)
                    ),
                    None,
                )
                point = RankGridPoint(
                    row=coordinate.row,
                    column=coordinate.column,
                    latitude=coordinate.latitude,
                    longitude=coordinate.longitude,
                    provider=search_result.provider,
                    rank=matched.position if matched is not None else None,
                    result_count=len(search_result.candidates),
                    matched_place=matched,
                )
            except RankProviderError as exc:
                point = RankGridPoint(
                    row=coordinate.row,
                    column=coordinate.column,
                    latitude=coordinate.latitude,
                    longitude=coordinate.longitude,
                    provider=self._provider.name,
                    rank=None,
                    result_count=0,
                    error=str(exc),
                )
            points.append(point)
            if on_point is not None:
                on_point(point)

        return RankGridResult(
            query=query,
            target=target,
            center_latitude=center_latitude,
            center_longitude=center_longitude,
            grid_size=grid_size,
            spacing_km=spacing_km,
            zoom=zoom,
            max_rank=max_rank,
            points=tuple(points),
            generated_at=datetime.now(timezone.utc).isoformat(),
        )


def generate_rank_grid(
    center_latitude: float,
    center_longitude: float,
    *,
    grid_size: int,
    spacing_km: float,
) -> tuple[RankGridCoordinate, ...]:
    """Generate an odd square grid ordered north-to-south, west-to-east."""
    if grid_size < 1 or grid_size % 2 == 0:
        raise ValueError("grid_size must be a positive odd integer.")
    if spacing_km <= 0:
        raise ValueError("spacing_km must be greater than zero.")
    if not -90 <= center_latitude <= 90:
        raise ValueError("center_latitude must be between -90 and 90.")
    if not -180 <= center_longitude <= 180:
        raise ValueError("center_longitude must be between -180 and 180.")

    half = grid_size // 2
    latitude_offset = half * spacing_km / KM_PER_DEGREE_LAT
    if center_latitude - latitude_offset < -90 or center_latitude + latitude_offset > 90:
        raise ValueError("grid extends beyond the valid latitude range.")

    coordinates: list[RankGridCoordinate] = []

    for row in range(grid_size):
        north_km = (half - row) * spacing_km
        latitude = center_latitude + north_km / KM_PER_DEGREE_LAT
        lon_km_per_degree = KM_PER_DEGREE_LAT * max(
            abs(math.cos(math.radians(latitude))),
            1e-6,
        )
        for column in range(grid_size):
            east_km = (column - half) * spacing_km
            longitude = center_longitude + east_km / lon_km_per_degree
            if longitude < -180 or longitude > 180:
                longitude = (longitude + 180) % 360 - 180
            coordinates.append(
                RankGridCoordinate(
                    row=row,
                    column=column,
                    latitude=round(latitude, 7),
                    longitude=round(longitude, 7),
                )
            )

    return tuple(coordinates)


def write_rank_grid_json(result: RankGridResult, output: str | Path) -> Path:
    """Write the full rank-grid result as JSON."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def write_rank_grid_html(result: RankGridResult, output: str | Path) -> Path:
    """Write a self-contained rank heatmap."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_rank_grid_html(result), encoding="utf-8")
    return path


def render_rank_grid_html(result: RankGridResult) -> str:
    """Render a portable HTML heatmap with no external assets."""
    summary = result.summary()
    visibility = summary["visibility_percent"]
    visibility_label = f"{visibility}%" if visibility is not None else "—"
    cells = "\n".join(_render_heatmap_cell(point) for point in result.points)
    query = html.escape(result.query)
    target = html.escape(result.target.value)
    generated_at = html.escape(result.generated_at)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Google Maps rank grid — {query}</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    body {{ margin: 0; background: #f5f7fb; color: #172033; }}
    main {{ max-width: 1080px; margin: 0 auto; padding: 32px 20px 48px; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    .muted {{ color: #667085; }}
    .stats {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 24px 0; }}
    .stat {{ background: white; border: 1px solid #e4e7ec; border-radius: 12px; padding: 14px; }}
    .stat strong {{ display: block; font-size: 24px; margin-top: 4px; }}
    .grid {{ display: grid; grid-template-columns: repeat({result.grid_size}, minmax(88px, 1fr)); gap: 8px; }}
    .cell {{ min-height: 88px; border-radius: 12px; padding: 12px; color: #172033; box-shadow: inset 0 0 0 1px rgba(0,0,0,.08); }}
    .rank {{ display: block; font-size: 26px; font-weight: 750; }}
    .coord, .provider {{ display: block; margin-top: 5px; font-size: 11px; opacity: .76; }}
    .rank-1 {{ background: #75e09c; }}
    .rank-2 {{ background: #b8e986; }}
    .rank-3 {{ background: #ffe082; }}
    .rank-4 {{ background: #ffb86b; }}
    .rank-5 {{ background: #ff8a80; }}
    .rank-none {{ background: #d9dee8; }}
    .rank-error {{ background: #b42318; color: white; }}
    .legend {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 18px 0; font-size: 13px; }}
    .legend span::before {{ content: ""; display: inline-block; width: 12px; height: 12px; margin-right: 5px; border-radius: 3px; vertical-align: -1px; background: var(--color); }}
    footer {{ margin-top: 24px; font-size: 12px; }}
    @media (max-width: 720px) {{
      .stats {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      .grid {{ overflow-x: auto; grid-template-columns: repeat({result.grid_size}, 92px); }}
    }}
  </style>
</head>
<body>
<main>
  <h1>Google Maps rank grid</h1>
  <div class="muted">Query: <strong>{query}</strong> · Target: <strong>{target}</strong></div>
  <div class="muted">Center: {result.center_latitude:.6f}, {result.center_longitude:.6f} · {result.spacing_km:g} km spacing · zoom {result.zoom:g}</div>
  <section class="stats">
    <div class="stat"><span class="muted">Visibility</span><strong>{visibility_label}</strong></div>
    <div class="stat"><span class="muted">Best rank</span><strong>{summary["best_rank"] or "—"}</strong></div>
    <div class="stat"><span class="muted">Average rank</span><strong>{summary["average_rank"] or "—"}</strong></div>
    <div class="stat"><span class="muted">Found points</span><strong>{summary["found_points"]}/{summary["measured_points"]}</strong></div>
  </section>
  <div class="legend">
    <span style="--color:#75e09c">1–3</span>
    <span style="--color:#b8e986">4–7</span>
    <span style="--color:#ffe082">8–10</span>
    <span style="--color:#ffb86b">11–20</span>
    <span style="--color:#ff8a80">21+</span>
    <span style="--color:#d9dee8">Not found</span>
  </div>
  <section class="grid">{cells}</section>
  <footer class="muted">Generated {generated_at}. Rankings are snapshots and can change between searches.</footer>
</main>
</body>
</html>
"""


def _candidate_from_place(place: ParsedPlace, position: int) -> RankCandidate:
    return RankCandidate(
        position=position,
        name=place.name,
        place_id=place.place_id,
        cid=place.cid,
        hex_id=place.hex_id,
        address=place.address,
        latitude=place.latitude,
        longitude=place.longitude,
    )


def _candidates_from_page(page: SearchResult, max_rank: int) -> list[RankCandidate]:
    return [
        _candidate_from_place(place, position)
        for position, place in enumerate(
            page.places,
            start=page.pagination_offset + 1,
        )
        if position <= max_rank
    ]


def _candidate_from_serper(place: Mapping[str, object], fallback_position: int) -> RankCandidate:
    position_value = place.get("position")
    position = (
        position_value
        if isinstance(position_value, int) and not isinstance(position_value, bool)
        else fallback_position
    )
    return RankCandidate(
        position=position,
        name=_as_text(place.get("title")),
        place_id=_as_text(place.get("placeId") or place.get("place_id")),
        cid=_as_text(place.get("cid")),
        hex_id=_as_text(place.get("hexId") or place.get("hex_id")),
        address=_as_text(place.get("address")),
        latitude=_as_float(place.get("latitude")),
        longitude=_as_float(place.get("longitude")),
    )


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _normalize_name(value: str) -> str:
    return " ".join(value.casefold().split())


def _cid_variants(value: str) -> set[str]:
    normalized = value.strip().casefold()
    if not normalized:
        return set()
    variants = {normalized}
    hex_component = normalized.rsplit(":", maxsplit=1)[-1]
    if hex_component.startswith("0x"):
        with suppress(ValueError):
            variants.add(str(int(hex_component, 16)))
    return variants


def _candidate_cid_variants(candidate: RankCandidate) -> set[str]:
    return _cid_variants(candidate.cid) | _cid_variants(candidate.hex_id)


def _rank_class(point: RankGridPoint) -> str:
    if point.error:
        return "rank-error"
    if point.rank is None:
        return "rank-none"
    if point.rank <= 3:
        return "rank-1"
    if point.rank <= 7:
        return "rank-2"
    if point.rank <= 10:
        return "rank-3"
    if point.rank <= 20:
        return "rank-4"
    return "rank-5"


def _render_heatmap_cell(point: RankGridPoint) -> str:
    label = "!" if point.error else str(point.rank or "—")
    detail = point.error or (
        point.matched_place.name if point.matched_place is not None else "Target not found"
    )
    title = html.escape(detail, quote=True)
    provider = html.escape(point.provider)
    return (
        f'<article class="cell {_rank_class(point)}" title="{title}">'
        f'<span class="rank">{label}</span>'
        f'<span class="coord">{point.latitude:.5f}, {point.longitude:.5f}</span>'
        f'<span class="provider">{provider} · {point.result_count} results</span>'
        "</article>"
    )
