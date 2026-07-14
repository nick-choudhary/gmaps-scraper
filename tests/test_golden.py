"""Golden-corpus regression test.

Locks the parser's brittle index contract: a committed raw response (built at
the exact array indices the parser reads) must keep re-parsing to the committed
expected output. If someone changes a field index (F_NAME, F_PLACE_ID, the
data[0][1][*][14] navigation, ...), or Google's format assumptions shift in
code, this fails loudly instead of silently emptying output.
"""

import copy
import json
from pathlib import Path

from gmaps.rpc.parser import parse_search_response
from gmaps.validation import assess_search

GOLDEN = Path(__file__).parent / "golden"


def _load(name):
    return json.loads((GOLDEN / name).read_text())


class TestGoldenParse:
    def test_reparse_matches_expected(self):
        raw = _load("search_raw.json")
        expected = _load("search_expected.json")
        places = parse_search_response(raw)
        assert [p.to_dict() for p in places] == expected

    def test_golden_is_healthy(self):
        places = parse_search_response(_load("search_raw.json"))
        health = assess_search(places)
        assert health.is_healthy, health.problems
        assert health.name_coverage == 1.0
        assert health.place_id_coverage == 1.0
        assert health.coords_coverage == 1.0

    def test_format_shift_is_caught(self):
        # Simulate Google moving the name field: null out the name slot (index
        # 11) in each entry's place_data. Parsed names collapse -> the health
        # check must flag it. This is the silent-drift failure Phase 0 exists
        # to surface.
        raw = copy.deepcopy(_load("search_raw.json"))
        for entry in raw[0][1][1:]:  # skip results[0] metadata
            entry[14][11] = None
        places = parse_search_response(raw)
        health = assess_search(places)
        assert not health.is_healthy
        assert any("name coverage" in p for p in health.problems)
