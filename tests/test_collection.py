"""Durable comprehensive-run storage and manifest behavior."""

from __future__ import annotations

import json
import os
from typing import Any
from unittest import mock

import pytest

from gmaps._search import SearchAPI, SearchResult
from gmaps.collection import CollectionRunner, CollectionState, CollectionStore, choose_cell_size
from gmaps.grid import BoundingBox, estimate_cell_count
from gmaps.rpc.parser import ParsedPlace


def test_store_resumes_full_business_records_not_only_ids(tmp_path) -> None:
    output = tmp_path / "atlanta.json"
    store = CollectionStore(output)
    state = CollectionState(
        query="chiropractors",
        location="Atlanta, Georgia",
        bbox={"min_lat": 33.64, "min_lon": -84.55, "max_lat": 33.89, "max_lon": -84.29},
        cell_size_km=5.0,
        max_results=1000,
    )
    place = ParsedPlace(
        name="Acme Chiropractic",
        place_id="ChIJ-acme",
        phone="(404) 555-0100",
        latitude=33.75,
        longitude=-84.39,
    )

    store.save_state(state)
    store.append_discovered([place])
    state.completed_cells.add("33.750000,-84.390000")
    store.save_state(state)

    resumed_store = CollectionStore(output)
    resumed_state = resumed_store.load_state()
    resumed_places = resumed_store.load_places()

    assert resumed_state.completed_cells == {"33.750000,-84.390000"}
    assert [p.to_dict() for p in resumed_places] == [place.to_dict()]


def test_snapshot_is_atomic_canonical_json_and_becomes_resume_source(tmp_path) -> None:
    output = tmp_path / "results.json"
    store = CollectionStore(output)
    first = ParsedPlace(name="First", place_id="one")
    second = ParsedPlace(name="Second", place_id="two", emails=["hello@second.example"])

    store.append_discovered([first])
    store.write_snapshot([first, second])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["place_id"] for item in payload] == ["one", "two"]
    assert [p.place_id for p in store.load_places()] == ["one", "two"]
    assert not output.with_suffix(".json.tmp").exists()


def test_atomic_snapshot_retries_a_transient_windows_reader_lock(tmp_path) -> None:
    store = CollectionStore(tmp_path / "results.json")
    real_replace = os.replace
    attempts = 0

    def briefly_locked(source, destination) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("destination is briefly open")
        real_replace(source, destination)

    with mock.patch("gmaps.collection.os.replace", side_effect=briefly_locked):
        store.write_manifest({"status": "running"})

    assert attempts == 3
    assert json.loads(store.manifest_path.read_text(encoding="utf-8")) == {"status": "running"}


def test_resume_merges_snapshot_with_records_discovered_after_snapshot(tmp_path) -> None:
    output = tmp_path / "results.json"
    store = CollectionStore(output)
    first = ParsedPlace(name="First", place_id="one")
    second = ParsedPlace(name="Second", place_id="two")

    store.write_snapshot([first])
    store.append_discovered([second])

    assert [place.place_id for place in store.load_places()] == ["one", "two"]


def test_snapshot_enrichment_wins_over_older_discovery_jsonl(tmp_path) -> None:
    output = tmp_path / "results.json"
    store = CollectionStore(output)
    discovered = ParsedPlace(name="Business", place_id="one")
    enriched = ParsedPlace(name="Business", place_id="one", description="Full details")

    store.append_discovered([discovered])
    store.write_snapshot([enriched])

    assert store.load_places()[0].description == "Full details"


def test_country_scale_auto_size_respects_target_cell_count() -> None:
    australia = BoundingBox(-43.7, 113.0, -10.6, 153.7)
    size = choose_cell_size(australia, target_cells=120)

    assert estimate_cell_count(australia, size) <= 120


class _CollectionSearch(SearchAPI):
    def __init__(self, places: list[ParsedPlace]) -> None:
        super().__init__(transport=None)  # type: ignore[arg-type]
        self.places_for_cell = places
        self._request_delay = 0

    async def places_paginated(self, **kwargs: Any) -> list[ParsedPlace]:
        return self.places_for_cell

    async def places(self, **kwargs: Any) -> SearchResult:
        return SearchResult(query=kwargs.get("query", ""), places=self.places_for_cell)


class _CollectionClient:
    def __init__(self, places: list[ParsedPlace]) -> None:
        self.search = _CollectionSearch(places)
        self.contact_batches: list[list[str]] = []

    async def enrich(self, place: ParsedPlace, query: str = "") -> ParsedPlace:
        place.description = f"Enriched for {query}"
        return place

    async def extract_contacts(self, places: list[ParsedPlace]) -> list[ParsedPlace]:
        self.contact_batches.append([place.place_id for place in places])
        for place in places:
            place.contact_status = "completed"
            place.emails = [f"hello@{place.place_id}.example"]
        return places


@pytest.mark.asyncio
async def test_runner_writes_complete_manifest_and_honors_contact_attempt_cap(tmp_path) -> None:
    output = tmp_path / "atlanta.json"
    store = CollectionStore(output)
    places = [
        ParsedPlace(
            name="Popular Store",
            place_id="popular",
            website="https://popular.example",
            review_count=100,
            latitude=33.75,
            longitude=-84.39,
        ),
        ParsedPlace(
            name="Quiet Chiropractor",
            place_id="quiet",
            website="https://quiet.example",
            review_count=5,
            latitude=33.76,
            longitude=-84.38,
        ),
    ]
    state = CollectionState(
        query="chiropractors",
        location="Atlanta, Georgia",
        bbox={"min_lat": 33.64, "min_lon": -84.55, "max_lat": 33.89, "max_lon": -84.29},
        cell_size_km=100,
        max_results=100,
        enrich=True,
        contacts=True,
        max_contacts=1,
    )
    client = _CollectionClient(places)

    saved, manifest = await CollectionRunner(
        client=client,
        store=store,
        state=state,
        enable_diversity_pass=False,
        enable_gap_fill=False,
    ).run()

    assert manifest["status"] == "complete"
    assert manifest["results"]["retained"] == 2
    assert manifest["results"]["raw_occurrences"] == 2
    assert manifest["results"]["discovery_requests"] == 1
    assert manifest["results"]["contact_attempted"] == 1
    assert manifest["results"]["enrichment_succeeded"] == 2
    assert manifest["results"]["enrichment_failed"] == 0
    assert manifest["results"]["contact_succeeded"] == 1
    assert manifest["results"]["contact_failed"] == 0
    assert manifest["results"]["contact_skipped_limit"] == 1
    assert client.contact_batches == [["quiet"]]
    assert {place.place_id: place.contact_status for place in saved} == {
        "popular": "not_attempted_limit",
        "quiet": "completed",
    }
    assert json.loads(store.manifest_path.read_text(encoding="utf-8"))["complete"] is True


def test_checkpoint_round_trip_preserves_discovery_counters() -> None:
    state = CollectionState(
        query="coffee",
        location="Austin, Texas",
        bbox={"min_lat": 30.0, "min_lon": -98.0, "max_lat": 31.0, "max_lon": -97.0},
        cell_size_km=3.0,
        max_results=500,
    )
    state.discovery_requests = 17
    state.raw_occurrences = 340
    state.duplicate_occurrences = 120
    state.outside_boundary_occurrences = 30
    state.saturated_cells = 2

    restored = CollectionState.from_dict(state.to_dict())

    assert restored.discovery_requests == 17
    assert restored.raw_occurrences == 340
    assert restored.duplicate_occurrences == 120
    assert restored.outside_boundary_occurrences == 30
    assert restored.saturated_cells == 2
