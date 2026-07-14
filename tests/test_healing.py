"""Tests for the self-healing parser (Phase 1).

The central test proves that when the response layout DRIFTS (field indices
move), a deterministic value-search recovers the new indices from known anchor
entities and the parse becomes healthy again — no LLM, no human patch.
"""

from pathlib import Path

from gmaps.exceptions import DriftError
from gmaps.healing import (
    CallableRepair,
    LabeledRepair,
    SelfHealingParser,
    find_paths,
    rederive_schema,
)
from gmaps.schema import DEFAULT_SCHEMA, load_schema, parse_core

# Two index layouts: the default, and a "drifted" one where Google moved fields.
DEFAULT_IDX = {
    "name": [11],
    "place_id": [78],
    "hex_id": [10],
    "ftid": [89],
    "data_id": [0],
    "latitude": [9, 2],
    "longitude": [9, 3],
    "address": [18],
    "phone": [178, 0, 0],
    "website": [7, 0],
}
DRIFT_IDX = {
    "name": [40],
    "place_id": [80],
    "hex_id": [41],
    "ftid": [42],
    "data_id": [43],
    "latitude": [50, 2],
    "longitude": [50, 3],
    "address": [44],
    "phone": [45, 0, 0],
    "website": [46, 0],
}

PLACES = [
    {
        "name": "Golden Coffee Co",
        "place_id": "ChIJgolden000000000000001",
        "hex_id": "0xabc:0x1",
        "ftid": "/g/gold1",
        "data_id": "dataA",
        "latitude": 30.27,
        "longitude": -97.74,
        "address": "1008 E 6th St",
        "phone": "(512) 555-0001",
        "website": "https://gold.example",
    },
    {
        "name": "Beacon Diner",
        "place_id": "ChIJbeacon000000000000002",
        "hex_id": "0xdef:0x2",
        "ftid": "/g/beac2",
        "data_id": "dataB",
        "latitude": 30.28,
        "longitude": -97.73,
        "address": "55 Congress Ave",
        "phone": "(512) 555-0002",
        "website": "https://beacon.example",
    },
]

# Anchor labels used by the healer (a maintained set of known businesses).
LABELS = [
    {k: p[k] for k in ("name", "place_id", "hex_id", "latitude", "longitude")} for p in PLACES
]


def _set_at(container, path, value):
    cur = container
    for i, idx in enumerate(path):
        while len(cur) <= idx:
            cur.append(None)
        if i == len(path) - 1:
            cur[idx] = value
        else:
            if not isinstance(cur[idx], list):
                cur[idx] = []
            cur = cur[idx]


def build_raw(idx_map):
    results = ["__meta__"]
    for p in PLACES:
        pd: list = []
        for f, path in idx_map.items():
            _set_at(pd, path, p[f])
        entry: list = []
        _set_at(entry, [14], pd)
        results.append(entry)
    return [["query", results]]


class TestFindPaths:
    def test_locates_nested_value(self):
        assert find_paths([1, [2, 3, [4]]], 4) == [[1, 2, 0]]

    def test_float_tolerance(self):
        assert find_paths([[0, 0, 30.27]], 30.27) == [[0, 2]]

    def test_absent(self):
        assert find_paths([1, 2, 3], 99) == []


class TestRederiveOnGoldenLayout:
    def test_recovers_default_paths(self):
        raw = build_raw(DEFAULT_IDX)
        schema = rederive_schema(raw, LABELS)
        assert schema is not None
        assert schema.paths["name"] == [11]
        assert schema.paths["place_id"] == [78]
        assert schema.paths["latitude"] == [9, 2]


class TestSelfHealOnDrift:
    """The money test: a drifted response is recovered by value-search."""

    def test_default_parser_breaks_on_drift(self):
        drifted = build_raw(DRIFT_IDX)
        broken = parse_core(drifted, DEFAULT_SCHEMA)
        # names/place_ids land on the wrong (empty) indices → unhealthy
        assert all(not b["name"] for b in broken)

    def test_rederive_recovers_drifted_indices(self):
        drifted = build_raw(DRIFT_IDX)
        schema = rederive_schema(drifted, LABELS)
        assert schema is not None
        assert schema.paths["name"] == [40]
        assert schema.paths["place_id"] == [80]
        assert schema.paths["latitude"] == [50, 2]
        assert schema.paths["longitude"] == [50, 3]

    def test_reparse_with_healed_schema_is_correct(self):
        drifted = build_raw(DRIFT_IDX)
        schema = rederive_schema(drifted, LABELS)
        healed = parse_core(drifted, schema)
        assert healed[0]["name"] == "Golden Coffee Co"
        assert healed[0]["place_id"] == "ChIJgolden000000000000001"
        assert healed[0]["latitude"] == 30.27


class TestSelfHealingParser:
    def test_healthy_returns_without_repair(self):
        raw = build_raw(DEFAULT_IDX)
        parser = SelfHealingParser(repair=LabeledRepair(known=LABELS))
        out = parser.parse_search(raw)
        assert out[0]["name"] == "Golden Coffee Co"
        assert parser.schema is DEFAULT_SCHEMA  # unchanged; no heal needed

    def test_drift_triggers_self_heal(self):
        drifted = build_raw(DRIFT_IDX)
        parser = SelfHealingParser(repair=LabeledRepair(known=LABELS))
        out = parser.parse_search(drifted, query="canary")
        assert out[0]["name"] == "Golden Coffee Co"  # recovered
        assert parser.schema.paths["name"] == [40]  # schema updated

    def test_strict_raises_when_unrecoverable(self):
        drifted = build_raw(DRIFT_IDX)
        parser = SelfHealingParser(repair=None, strict=True)  # no repair available
        raised = False
        try:
            parser.parse_search(drifted)
        except DriftError:
            raised = True
        assert raised

    def test_non_strict_returns_best_effort(self):
        drifted = build_raw(DRIFT_IDX)
        parser = SelfHealingParser(repair=None, strict=False)
        out = parser.parse_search(drifted)  # logs, does not raise
        assert isinstance(out, list)

    def test_heal_writes_schema_cache(self):
        import tempfile

        cache = Path(tempfile.mkdtemp()) / "schema.json"
        drifted = build_raw(DRIFT_IDX)
        parser = SelfHealingParser(repair=LabeledRepair(known=LABELS), cache_path=str(cache))
        parser.parse_search(drifted)
        loaded = load_schema(cache)
        assert loaded is not None and loaded.paths["name"] == [40]


class TestCallableRepair:
    def test_callable_repair_used(self):
        drifted = build_raw(DRIFT_IDX)

        def fake_llm(raw, labels, base):
            # pretend a model figured out the moved indices
            return {"name": [40], "place_id": [80], "latitude": [50, 2], "longitude": [50, 3]}

        parser = SelfHealingParser(repair=CallableRepair(fn=fake_llm))
        out = parser.parse_search(drifted)
        assert out[0]["name"] == "Golden Coffee Co"

    def test_callable_returning_none_falls_back(self):
        drifted = build_raw(DRIFT_IDX)
        parser = SelfHealingParser(
            repair=CallableRepair(fn=lambda response, labels, base: None), strict=True
        )
        raised = False
        try:
            parser.parse_search(drifted)
        except DriftError:
            raised = True
        assert raised
