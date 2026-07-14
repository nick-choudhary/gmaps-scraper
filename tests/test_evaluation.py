"""Tests for the Phase 5 evaluation harness + auto-promotion."""

import json
import re
from pathlib import Path
from types import SimpleNamespace

from gmaps.evaluation import (
    EvalResult,
    ExtractionCase,
    ParseCase,
    compare_extractors,
    compare_parsers,
    evaluate_extractor,
    evaluate_parse,
    promote,
    rank,
)
from gmaps.schema import DEFAULT_SCHEMA, FieldSchema

GOLDEN = Path(__file__).parent / "golden"


# ── fake extractors ──


class _RegexLike:
    """Finds only literal `x@y` emails (misses obfuscated ones)."""

    def extract(self, text, url=""):
        return SimpleNamespace(emails=re.findall(r"[\w.\-]+@[\w.\-]+\.\w+", text), socials={})


class _ModelLike:
    """Answers from a lookup — stands in for a model that reads obfuscated text."""

    def __init__(self, answers):
        self.answers = answers

    def extract(self, text, url=""):
        return SimpleNamespace(emails=self.answers.get(text, []), socials={})


CASES = [
    ExtractionCase("contact us at real@acme.com", {"real@acme.com"}),
    ExtractionCase("email: sales [at] acme [dot] com", {"sales@acme.com"}),  # obfuscated
]


class TestEvaluateExtractor:
    def test_perfect_extractor_scores_one(self):
        answers = {c.page_text: list(c.expected_emails) for c in CASES}
        res = evaluate_extractor("perfect", _ModelLike(answers), CASES)
        assert res.score == 1.0
        assert res.metrics["recall"] == 1.0 and res.metrics["precision"] == 1.0

    def test_regex_misses_obfuscated(self):
        res = evaluate_extractor("regex", _RegexLike(), CASES)
        assert res.metrics["recall"] == 0.5  # catches 1 of 2

    def test_empty_extractor_zero_recall(self):
        empty = _ModelLike({})
        res = evaluate_extractor("empty", empty, CASES)
        assert res.metrics["recall"] == 0.0


class TestPromotion:
    def test_model_auto_wins_over_regex(self):
        answers = {c.page_text: list(c.expected_emails) for c in CASES}
        candidates = {"regex": _RegexLike(), "model": _ModelLike(answers)}
        winner, ranked = compare_extractors(candidates, CASES)
        assert winner == "model"
        assert ranked[0].name == "model" and ranked[0].score > ranked[1].score

    def test_promote_picks_highest_score(self):
        results = [EvalResult("a", 0.5), EvalResult("b", 0.9), EvalResult("c", 0.7)]
        assert promote(results) == "b"

    def test_rank_tiebreak_by_latency(self):
        results = [
            EvalResult("slow", 0.9, {"latency_ms": 100.0}),
            EvalResult("fast", 0.9, {"latency_ms": 10.0}),
        ]
        assert rank(results)[0].name == "fast"


class TestEvaluateParse:
    EXPECTED = [
        {
            "name": "Golden Coffee Co",
            "place_id": "ChIJgolden000000000000001",
            "latitude": 30.27,
            "longitude": -97.74,
        },
        {
            "name": "Beacon Diner",
            "place_id": "ChIJbeacon000000000000002",
            "latitude": 30.28,
            "longitude": -97.73,
        },
    ]

    def _case(self):
        raw = json.loads((GOLDEN / "search_raw.json").read_text())
        return ParseCase(raw=raw, expected=self.EXPECTED)

    def test_default_schema_is_perfect_on_golden(self):
        res = evaluate_parse("default", DEFAULT_SCHEMA, [self._case()])
        assert res.score == 1.0

    def test_broken_schema_scores_lower(self):
        broken = FieldSchema(paths={**DEFAULT_SCHEMA.paths, "name": [99]})  # wrong name index
        res = evaluate_parse("broken", broken, [self._case()])
        assert res.score < 1.0

    def test_compare_parsers_promotes_default(self):
        broken = FieldSchema(paths={**DEFAULT_SCHEMA.paths, "name": [99]})
        winner, ranked = compare_parsers(
            {"default": DEFAULT_SCHEMA, "broken": broken}, [self._case()]
        )
        assert winner == "default"


class TestEvalResult:
    def test_summary(self):
        r = EvalResult("m", 0.83, {"f1": 0.83, "precision": 0.9, "recall": 0.77}, n_cases=5)
        s = r.summary()
        assert "m" in s and "score=0.830" in s and "5 cases" in s
