"""Model / provider evaluation harness (Phase 5) — measure, then promote.

Model-swap readiness is only real if you can *measure* a candidate before
trusting it. This harness scores providers against a labeled corpus and ranks
them, so upgrading is: register the candidate, run the eval, promote the winner
— automatically, on numbers, not vibes.

Two boundaries are scored here (the ones that warrant a model):

* contact extraction — precision / recall / F1 of emails + social platforms
* parsing — per-field accuracy of the schema-driven core parse vs a golden set

Deterministic and dependency-free; fully testable with fixtures and fakes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EvalResult:
    """Score for one provider on one corpus."""

    name: str
    score: float
    metrics: dict[str, float] = field(default_factory=dict)
    n_cases: int = 0

    def summary(self) -> str:
        parts = ", ".join(f"{k}={v:.3f}" for k, v in self.metrics.items())
        return f"{self.name}: score={self.score:.3f} ({parts}) over {self.n_cases} cases"


# ── Contact extraction eval ──


@dataclass
class ExtractionCase:
    page_text: str
    expected_emails: set[str] = field(default_factory=set)
    expected_socials: set[str] = field(default_factory=set)  # platform names


def _pairset(emails, socials) -> set[tuple[str, str]]:
    return {("e", e) for e in emails} | {("s", s) for s in socials}


def evaluate_extractor(
    name: str, extractor: Any, cases: list[ExtractionCase], *, metric: str = "f1"
) -> EvalResult:
    tp = fp = fn = 0
    t0 = time.perf_counter()
    for c in cases:
        r = extractor.extract(c.page_text, "")
        got = _pairset(getattr(r, "emails", []) or [], (getattr(r, "socials", {}) or {}).keys())
        exp = _pairset(c.expected_emails, c.expected_socials)
        tp += len(got & exp)
        fp += len(got - exp)
        fn += len(exp - got)
    latency_ms = (time.perf_counter() - t0) * 1000.0
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    metrics = {"precision": precision, "recall": recall, "f1": f1, "latency_ms": latency_ms}
    return EvalResult(name=name, score=metrics.get(metric, f1), metrics=metrics, n_cases=len(cases))


# ── Parse eval (vs golden) ──


@dataclass
class ParseCase:
    raw: Any
    expected: list[dict[str, Any]]  # expected core fields per place, in order


def evaluate_parse(name: str, schema: Any, cases: list[ParseCase]) -> EvalResult:
    from .schema import parse_core

    correct = total = 0
    t0 = time.perf_counter()
    for c in cases:
        got = parse_core(c.raw, schema)
        for g, exp in zip(got, c.expected, strict=False):
            for k, v in exp.items():
                total += 1
                if g.get(k) == v:
                    correct += 1
    latency_ms = (time.perf_counter() - t0) * 1000.0
    accuracy = correct / total if total else 1.0
    return EvalResult(
        name=name,
        score=accuracy,
        metrics={"accuracy": accuracy, "latency_ms": latency_ms},
        n_cases=len(cases),
    )


# ── Ranking / promotion ──


def rank(results: list[EvalResult]) -> list[EvalResult]:
    """Highest score first (ties broken by lower latency)."""
    return sorted(results, key=lambda r: (-r.score, r.metrics.get("latency_ms", 0.0)))


def promote(results: list[EvalResult]) -> str | None:
    """Return the name of the best-scoring provider (the auto-promotion pick)."""
    ranked = rank(results)
    return ranked[0].name if ranked else None


def compare_extractors(
    candidates: dict[str, Any], cases: list[ExtractionCase], *, metric: str = "f1"
) -> tuple[str | None, list[EvalResult]]:
    """Score every candidate extractor; return (winner_name, ranked_results)."""
    results = [evaluate_extractor(n, e, cases, metric=metric) for n, e in candidates.items()]
    ranked = rank(results)
    return (ranked[0].name if ranked else None), ranked


def compare_parsers(
    candidates: dict[str, Any], cases: list[ParseCase]
) -> tuple[str | None, list[EvalResult]]:
    """Score every candidate schema/parser; return (winner_name, ranked_results)."""
    results = [evaluate_parse(n, s, cases) for n, s in candidates.items()]
    ranked = rank(results)
    return (ranked[0].name if ranked else None), ranked
