"""Durable state and output primitives for comprehensive collection runs."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .geocoding import NominatimResolver, ResolvedLocation, geojson_contains
from .grid import BoundingBox, estimate_cell_count
from .rpc.parser import ParsedPlace
from .stats import ScraperStats


@dataclass
class CollectionState:
    """Serializable checkpoint state for a comprehensive run."""

    query: str
    location: str
    bbox: dict[str, float]
    cell_size_km: float
    max_results: int
    resolved_location: dict[str, Any] = field(default_factory=dict)
    enrich: bool = False
    contacts: bool = False
    max_contacts: int | None = None
    completed_cells: set[str] = field(default_factory=set)
    enriched_place_ids: set[str] = field(default_factory=set)
    contact_attempted_place_ids: set[str] = field(default_factory=set)
    discovery_requests: int = 0
    raw_occurrences: int = 0
    duplicate_occurrences: int = 0
    outside_boundary_occurrences: int = 0
    saturated_cells: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key in (
            "completed_cells",
            "enriched_place_ids",
            "contact_attempted_place_ids",
        ):
            data[key] = sorted(data[key])
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollectionState:
        return cls(
            query=str(data["query"]),
            location=str(data.get("location") or ""),
            bbox={key: float(value) for key, value in dict(data["bbox"]).items()},
            cell_size_km=float(data["cell_size_km"]),
            max_results=int(data["max_results"]),
            resolved_location=dict(data.get("resolved_location") or {}),
            enrich=bool(data.get("enrich", False)),
            contacts=bool(data.get("contacts", False)),
            max_contacts=(
                int(data["max_contacts"]) if data.get("max_contacts") is not None else None
            ),
            completed_cells=set(data.get("completed_cells") or []),
            enriched_place_ids=set(data.get("enriched_place_ids") or []),
            contact_attempted_place_ids=set(data.get("contact_attempted_place_ids") or []),
            discovery_requests=int(data.get("discovery_requests") or 0),
            raw_occurrences=int(data.get("raw_occurrences") or 0),
            duplicate_occurrences=int(data.get("duplicate_occurrences") or 0),
            outside_boundary_occurrences=int(data.get("outside_boundary_occurrences") or 0),
            saturated_cells=int(data.get("saturated_cells") or 0),
        )


class CollectionStore:
    """Incremental JSONL records, checkpoint state, and atomic snapshots."""

    def __init__(self, output_path: str | Path) -> None:
        self.output_path = Path(output_path)
        self.jsonl_path = self.output_path.with_suffix(".jsonl")
        self.state_path = self.output_path.with_suffix(".checkpoint.json")
        self.manifest_path = self.output_path.with_suffix(".manifest.json")

    def save_state(self, state: CollectionState) -> None:
        self._write_json_atomic(self.state_path, state.to_dict())

    def load_state(self) -> CollectionState:
        if not self.state_path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {self.state_path}")
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid checkpoint: {self.state_path}")
        return CollectionState.from_dict(payload)

    def append_discovered(self, places: list[ParsedPlace]) -> None:
        if not places:
            return
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as stream:
            for place in places:
                stream.write(json.dumps(place.to_dict(), ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def load_places(self) -> list[ParsedPlace]:
        snapshot_records: list[dict[str, Any]] = []
        discovery_records: list[dict[str, Any]] = []
        if self.output_path.exists():
            payload = json.loads(self.output_path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError(f"Invalid result snapshot: {self.output_path}")
            snapshot_records.extend(item for item in payload if isinstance(item, dict))
        if self.jsonl_path.exists():
            for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = json.loads(line)
                    if isinstance(item, dict):
                        discovery_records.append(item)
        if not snapshot_records and not discovery_records:
            return []

        ordered: dict[str, ParsedPlace] = {}
        for item in snapshot_records:
            place = ParsedPlace.from_dict(item)
            ordered[_place_key(place)] = place
        for item in discovery_records:
            place = ParsedPlace.from_dict(item)
            key = _place_key(place)
            if key in ordered:
                continue
            ordered[key] = place
        return list(ordered.values())

    def write_snapshot(self, places: list[ParsedPlace]) -> None:
        self._write_json_atomic(self.output_path, [place.to_dict() for place in places])

    def write_manifest(self, payload: dict[str, Any]) -> None:
        self._write_json_atomic(self.manifest_path, payload)

    @staticmethod
    def _write_json_atomic(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        for attempt in range(6):
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                # Windows readers, antivirus, and sync tools can briefly hold the
                # destination open. Retrying preserves the atomic replacement.
                time.sleep(0.05 * (2**attempt))


def choose_cell_size(bbox: BoundingBox, target_cells: int = 100) -> float:
    """Choose a practical grid size without asking a human for coordinates.

    Aim for tens of seeds, not hundreds. Out-of-polygon seed centers are
    dropped later; dense cells can still split only when they yield many locals.
    """
    for size in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 10.0, 15.0, 20.0, 50.0, 100.0):
        if estimate_cell_count(bbox, size) <= target_cells:
            return size
    estimated_at_100_km = estimate_cell_count(bbox, 100.0)
    computed = 100.0 * (estimated_at_100_km / target_cells) ** 0.5
    size = math.ceil(computed * 10) / 10
    while estimate_cell_count(bbox, size) > target_cells:
        size = round(size * 1.02, 1)
    return size


def _place_key(place: ParsedPlace) -> str:
    return (
        place.place_id
        or place.hex_id
        or place.cid
        or (
            f"{place.name.casefold()}|{place.address.casefold()}|{place.latitude}|{place.longitude}"
        )
    )


def _matches_query(place: ParsedPlace, query: str) -> bool:
    """Prefer clearly relevant businesses when a contact budget is bounded."""
    terms = {
        variant
        for token in query.casefold().replace("-", " ").split()
        if len(token) >= 3
        for variant in (token, token.removesuffix("s"))
        if len(variant) >= 3
    }
    haystack = " ".join([place.name, *place.categories]).casefold()
    return any(term in haystack for term in terms)


class CollectionRunner:
    """Orchestrate a resumable discovery, enrichment, and contact run."""

    def __init__(
        self,
        *,
        client: Any,
        store: CollectionStore,
        state: CollectionState,
        progress: Callable[[str], None] | None = None,
        enable_diversity_pass: bool = False,
        enable_gap_fill: bool = False,
        footprint_buffer: float = 1.5,
        minimap_max_pages: int = 6,
        minimap_max_depth: int = 1,
        on_footprint_drop: Callable[[ParsedPlace], None] | None = None,
    ) -> None:
        # Diversity/gap-fill are optional coverage polish. Defaults off: live
        # Nashville runs showed gap-fill = 0 new uniques after burning requests.
        self.client = client
        self.store = store
        self.state = state
        self.progress = progress or (lambda _message: None)
        self.enable_diversity_pass = enable_diversity_pass
        self.enable_gap_fill = enable_gap_fill
        # footprint_buffer is the P1 recall/duplicate knob; on_footprint_drop lets
        # a benchmark measure pure footprint recall leak (see scripts/).
        self.footprint_buffer = footprint_buffer
        # Pagination/split depth per mini-map. max_pages=6 (was 2): the live
        # Nashville benchmark showed 2 pages cut recall to 0.745 and mislabeled
        # 24 cells "saturated"; 6 pages reached 0.941 recall with only 2 saturated
        # at the SAME request count (the early-stop guard makes deeper pages
        # near-free). Deeper splitting (depth 2) did not add recall. See
        # context/discovery-scheduler-plan.md.
        self.minimap_max_pages = minimap_max_pages
        self.minimap_max_depth = minimap_max_depth
        self.on_footprint_drop = on_footprint_drop
        self.stats = ScraperStats()
        self.stats.total_requests = state.discovery_requests
        self.stats.total_places = state.raw_occurrences
        self.stats.duplicates = state.duplicate_occurrences
        self.stats.outside_boundary = state.outside_boundary_occurrences
        self.stats.cells_saturated = state.saturated_cells

    def _sync_discovery_counters(self) -> None:
        """Persist cumulative discovery accounting for reliable resume reports."""
        self.state.discovery_requests = self.stats.total_requests
        self.state.raw_occurrences = self.stats.total_places
        self.state.duplicate_occurrences = self.stats.duplicates
        self.state.outside_boundary_occurrences = self.stats.outside_boundary
        self.state.saturated_cells = self.stats.cells_saturated

    async def run(self) -> tuple[list[ParsedPlace], dict[str, Any]]:
        started = time.time()
        places = self.store.load_places()
        by_key = {_place_key(place): place for place in places}
        bbox = BoundingBox(**self.state.bbox)

        self.store.save_state(self.state)
        self.store.write_manifest(self._manifest("running", by_key, started))

        def on_cell(event: Any) -> None:
            discovered = list(event.new_places)
            self.store.append_discovered(discovered)
            for place in discovered:
                by_key[_place_key(place)] = place
            checkpoint_key = getattr(event, "checkpoint_key", None) or event.cell.key()
            self.state.completed_cells.add(checkpoint_key)
            self._sync_discovery_counters()
            self.store.save_state(self.state)
            self.store.write_manifest(self._manifest("running", by_key, started))
            self.progress(event.stats.progress() if event.stats else "Cell complete")

        geometry = self.state.resolved_location.get("geometry")
        boundary_filter = None
        if isinstance(geometry, dict) and geometry.get("type") in {"Polygon", "MultiPolygon"}:

            def contains_location(latitude: float | None, longitude: float | None) -> bool:
                return (
                    latitude is not None
                    and longitude is not None
                    and geojson_contains(geometry, latitude, longitude)
                )

            boundary_filter = contains_location
        fence = boundary_filter or bbox.contains

        # Phase 1 — Strategy B mini-maps (plain category query + footprint filter).
        self.progress("Discovery phase 1/3: adaptive mini-maps")
        results = await self.client.search.minimap_grid_search(
            query=self.state.query,
            bbox=bbox,
            cell_size_km=self.state.cell_size_km,
            max_results=self.state.max_results,
            base_zoom=16.0,
            max_pages=self.minimap_max_pages,
            # Allow one split level only when a cell is truly dense with locals.
            max_depth=self.minimap_max_depth,
            filter_to_bbox=True,
            footprint_buffer=self.footprint_buffer,
            boundary_contains=fence,
            stats=self.stats,
            skip_cell_keys=self.state.completed_cells,
            initial_seen_ids={_place_key(place) for place in places},
            initial_places=places,
            on_cell=on_cell,
            on_footprint_drop=self.on_footprint_drop,
        )
        for place, _cell in results:
            by_key[_place_key(place)] = place
        self._sync_discovery_counters()
        places = list(by_key.values())
        self.store.write_snapshot(places)

        # Phase 2 — neighborhood / ZIP diversity (different text, same fence).
        if (
            self.enable_diversity_pass
            and len(by_key) < self.state.max_results
            and self.state.location
        ):
            self.progress("Discovery phase 2/3: neighborhood / ZIP diversity")
            try:
                center_info = self.state.resolved_location.get("center") or {}
                parent = ResolvedLocation(
                    query=self.state.location,
                    display_name=str(
                        self.state.resolved_location.get("display_name") or self.state.location
                    ),
                    bbox=bbox,
                    center=(
                        float(center_info.get("latitude") or (bbox.min_lat + bbox.max_lat) / 2),
                        float(center_info.get("longitude") or (bbox.min_lon + bbox.max_lon) / 2),
                    ),
                    location_type=str(self.state.resolved_location.get("location_type") or ""),
                    provider_id=str(self.state.resolved_location.get("provider_id") or ""),
                    geometry=dict(geometry or {}),
                )
                resolver = NominatimResolver(timeout=12.0)
                subareas = await resolver.resolve_subareas(
                    self.state.location,
                    parent=parent,
                    max_subareas=40,
                )
            except Exception as exc:
                self.progress(f"Diversity pass skipped: {type(exc).__name__}")
                subareas = []

            if subareas:
                self.progress(f"Diversity: {len(subareas)} subareas")
                div_results = await self.client.search.diversity_subarea_search(
                    query=self.state.query,
                    subareas=subareas,
                    max_results=self.state.max_results,
                    pages_per_subarea=2,
                    filter_to_bbox=True,
                    boundary_contains=fence,
                    skip_keys=self.state.completed_cells,
                    initial_seen_ids={_place_key(place) for place in by_key.values()},
                    initial_places=list(by_key.values()),
                    stats=self.stats,
                    on_cell=on_cell,
                )
                for place, _cell in div_results:
                    by_key[_place_key(place)] = place
                self._sync_discovery_counters()
                places = list(by_key.values())
                self.store.write_snapshot(places)

        # Phase 3 — gap-fill only empty patches (avoid re-scraping dense cores).
        if self.enable_gap_fill and len(by_key) < self.state.max_results:
            self.progress("Discovery phase 3/3: gap-fill uncovered patches")
            gap_km = max(min(self.state.cell_size_km, 2.0), 1.0)
            gap_results = await self.client.search.gap_fill_search(
                query=self.state.query,
                bbox=bbox,
                places=list(by_key.values()),
                cell_size_km=gap_km,
                max_results=self.state.max_results,
                pages_per_gap=2,
                filter_to_bbox=True,
                boundary_contains=fence,
                skip_keys=self.state.completed_cells,
                initial_seen_ids={_place_key(place) for place in by_key.values()},
                stats=self.stats,
                on_cell=on_cell,
            )
            for place, _cell in gap_results:
                by_key[_place_key(place)] = place
            self._sync_discovery_counters()
            places = list(by_key.values())
            self.store.write_snapshot(places)

        if self.state.enrich:
            for index, place in enumerate(places, start=1):
                key = _place_key(place)
                if key in self.state.enriched_place_ids:
                    continue
                place.enrichment_status = "in_progress"
                place.enrichment_attempted_at = datetime.now(timezone.utc).isoformat()
                try:
                    await self.client.enrich(place, query=self.state.query)
                except Exception as exc:  # one place must not discard the discovery run
                    place.enrichment_status = "failed"
                    place.enrichment_error = f"{type(exc).__name__}: {exc}"[:500]
                else:
                    place.enrichment_status = "completed"
                self.state.enriched_place_ids.add(key)
                self.stats.enriched += 1
                self.store.write_snapshot(places)
                self.store.save_state(self.state)
                if index % 10 == 0 or index == len(places):
                    self.store.write_manifest(self._manifest("running", by_key, started))
                    self.progress(
                        f"Enrichment: {len(self.state.enriched_place_ids)}/{len(places)} places"
                    )

        if self.state.contacts:
            for place in places:
                if not place.website:
                    place.contact_status = "not_eligible_no_website"
            candidates = sorted(
                (p for p in places if p.website),
                key=lambda p: (
                    not _matches_query(p, self.state.query),
                    -p.review_count,
                    p.name.casefold(),
                    _place_key(p),
                ),
            )
            candidates = [
                p for p in candidates if _place_key(p) not in self.state.contact_attempted_place_ids
            ]
            if self.state.max_contacts is not None:
                remaining = max(
                    0,
                    self.state.max_contacts - len(self.state.contact_attempted_place_ids),
                )
                selected, deferred = candidates[:remaining], candidates[remaining:]
                for place in deferred:
                    place.contact_status = "not_attempted_limit"
            else:
                selected = candidates

            for start in range(0, len(selected), 5):
                batch = selected[start : start + 5]
                await self.client.extract_contacts(batch)
                self.state.contact_attempted_place_ids.update(_place_key(p) for p in batch)
                self.store.write_snapshot(places)
                self.store.save_state(self.state)
                self.store.write_manifest(self._manifest("running", by_key, started))
                self.progress(
                    "Contacts: "
                    f"{len(self.state.contact_attempted_place_ids)} website attempts complete"
                )

        status = "complete" if self.stats.complete else "incomplete"
        manifest = self._manifest(status, by_key, started)
        self.store.write_snapshot(places)
        self.store.save_state(self.state)
        self.store.write_manifest(manifest)
        return places, manifest

    def _manifest(
        self,
        status: str,
        places: dict[str, ParsedPlace],
        started: float,
    ) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": status,
            "complete": self.stats.complete,
            "incomplete_reasons": self.stats.incomplete_reasons,
            "query": self.state.query,
            "location": self.state.location,
            "resolved_location": self.state.resolved_location,
            "configuration": {
                "bbox": self.state.bbox,
                "cell_size_km": self.state.cell_size_km,
                "max_results": self.state.max_results,
                "enrich": self.state.enrich,
                "contacts": self.state.contacts,
                "max_contacts": self.state.max_contacts,
            },
            "coverage": {
                "cells_total": self.stats.cells_total,
                "cells_completed": self.stats.cells_completed,
                "cells_failed": self.stats.cells_failed,
                "cells_with_unique": self.stats.cells_with_unique,
                "cells_saturated": self.stats.cells_saturated,
            },
            "results": {
                "retained": len(places),
                "raw_occurrences": self.stats.total_places,
                "discovery_requests": self.stats.total_requests,
                "duplicates": self.stats.duplicates,
                "outside_boundary": self.stats.outside_boundary,
                "outside_footprint": self.stats.outside_footprint,
                "cap_reached": self.stats.cap_reached,
                "enriched": len(self.state.enriched_place_ids),
                "enrichment_succeeded": sum(
                    place.enrichment_status == "completed" for place in places.values()
                ),
                "enrichment_failed": sum(
                    place.enrichment_status == "failed" for place in places.values()
                ),
                "contact_attempted": len(self.state.contact_attempted_place_ids),
                "contact_succeeded": sum(
                    place.contact_status == "completed" for place in places.values()
                ),
                "contact_failed": sum(
                    place.contact_status == "failed" for place in places.values()
                ),
                "contact_skipped_limit": sum(
                    place.contact_status == "not_attempted_limit" for place in places.values()
                ),
            },
            "elapsed_seconds": round(time.time() - started, 3),
            "files": {
                "results": str(self.store.output_path),
                "checkpoint": str(self.store.state_path),
                "manifest": str(self.store.manifest_path),
                "incremental_jsonl": str(self.store.jsonl_path),
            },
        }
