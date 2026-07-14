"""Tests for the data-driven field schema (Phase 1)."""

import json
from pathlib import Path

from gmaps.rpc.parser import parse_search_response
from gmaps.schema import (
    DEFAULT_SCHEMA,
    FieldSchema,
    load_schema,
    parse_core,
    save_schema,
    traverse,
)

GOLDEN = Path(__file__).parent / "golden"
CORE = [
    "name",
    "place_id",
    "hex_id",
    "ftid",
    "data_id",
    "latitude",
    "longitude",
    "address",
    "phone",
    "website",
]


class TestTraverse:
    def test_simple(self):
        assert traverse([10, 20, 30], [1]) == 20

    def test_nested(self):
        assert traverse([0, [1, [2, 99]]], [1, 1, 1]) == 99

    def test_out_of_range_is_none(self):
        assert traverse([1, 2], [5]) is None
        assert traverse([1, 2], [0, 0]) is None  # not a list at [0]


class TestSchemaSerialization:
    def test_roundtrip(self):
        d = DEFAULT_SCHEMA.to_dict()
        back = FieldSchema.from_dict(d)
        assert back.paths == DEFAULT_SCHEMA.paths
        assert back.results_path == DEFAULT_SCHEMA.results_path
        assert back.entry_index == DEFAULT_SCHEMA.entry_index

    def test_save_load(self, tmp_path=None):
        import tempfile

        p = Path(tempfile.mkdtemp()) / "schema.json"
        save_schema(DEFAULT_SCHEMA, p)
        loaded = load_schema(p)
        assert loaded is not None and loaded.paths == DEFAULT_SCHEMA.paths

    def test_load_missing_is_none(self):
        assert load_schema("/nonexistent/schema.json") is None


class TestSchemaMatchesLegacyParser:
    """The schema-driven core extractor must agree with the hardcoded parser."""

    def test_core_fields_equivalent_on_golden(self):
        raw = json.loads((GOLDEN / "search_raw.json").read_text())
        legacy = parse_search_response(raw)
        schema_core = parse_core(raw)
        assert len(legacy) == len(schema_core)
        for lp, sc in zip(legacy, schema_core, strict=False):
            assert sc["name"] == lp.name
            assert sc["place_id"] == lp.place_id
            assert sc["hex_id"] == lp.hex_id
            assert sc["ftid"] == lp.ftid
            assert sc["data_id"] == lp.data_id
            assert sc["latitude"] == lp.latitude
            assert sc["longitude"] == lp.longitude
            assert sc["address"] == lp.address
            assert sc["phone"] == lp.phone
            assert sc["website"] == lp.website

    def test_extract_core_all_keys_present(self):
        raw = json.loads((GOLDEN / "search_raw.json").read_text())
        first = parse_core(raw)[0]
        assert set(first.keys()) == set(CORE)
