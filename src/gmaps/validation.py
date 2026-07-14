"""Structural validation & drift detection for parsed Google Maps output.

Phase 0 of the Bitter-Lesson upgrade plan: turn silent format-drift into a
loud, detectable signal.

The parser reads Google's undocumented response by fixed array indices
(``F_NAME = 11`` etc.). When Google shifts that layout, those accessors return
empty strings instead of raising, so a format change degrades output to empty
or partial *without any error* — the worst failure mode, invisible until a
customer notices.

These helpers assess the structural health of a parse and, in strict mode,
raise :class:`DriftError` the moment health drops below expectations.

Design notes:
- Deterministic and dependency-free — no models, no network. Pure inspection
  of already-parsed objects.
- Non-breaking — by default it only *computes* health and logs a warning; it
  never alters parsed output and only raises when a caller opts into strict
  mode. Default runtime behaviour of the scraper is unchanged.
- The thresholds below are hand-chosen, conservative constants. That is
  appropriate for a deterministic guard (Phase 0); a later phase replaces the
  fixed strategy elsewhere with feedback-driven control, but a smoke detector
  legitimately has a fixed trip point. Tune these from your own golden corpus.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .exceptions import DriftError

if TYPE_CHECKING:
    from .rpc.parser import ParsedPlace

logger = logging.getLogger(__name__)

# Fraction of results in a healthy search response expected to carry each core
# field. A drop below these strongly suggests the response layout changed.
MIN_NAME_COVERAGE = 0.90
MIN_PLACE_ID_COVERAGE = 0.90
MIN_COORDS_COVERAGE = 0.80


@dataclass
class ParseHealth:
    """Structural health of a parsed batch of places."""

    total: int = 0
    with_name: int = 0
    with_place_id: int = 0
    with_coords: int = 0
    problems: list[str] = field(default_factory=list)

    @property
    def name_coverage(self) -> float:
        return self.with_name / self.total if self.total else 0.0

    @property
    def place_id_coverage(self) -> float:
        return self.with_place_id / self.total if self.total else 0.0

    @property
    def coords_coverage(self) -> float:
        return self.with_coords / self.total if self.total else 0.0

    @property
    def is_healthy(self) -> bool:
        return not self.problems

    def summary(self) -> str:
        return (
            f"total={self.total} "
            f"name={self.name_coverage:.0%} "
            f"place_id={self.place_id_coverage:.0%} "
            f"coords={self.coords_coverage:.0%}"
        )


def _has_coords(place: Any) -> bool:
    return (
        getattr(place, "latitude", None) is not None
        and getattr(place, "longitude", None) is not None
    )


def assess_search(places: Sequence[Any], *, min_results: int = 1) -> ParseHealth:
    """Compute the structural health of a parsed search batch (no side effects).

    Args:
        places: Parsed places from one search/grid response.
        min_results: Minimum result count expected to consider the batch
            populated. Use 0 for pagination pages that may legitimately be empty.
    """
    health = ParseHealth(total=len(places))

    if health.total < min_results:
        health.problems.append(
            f"only {health.total} result(s) parsed (expected ≥{min_results}) "
            "— possible format drift, empty area, or block"
        )
        # No results to measure coverage on; return early.
        if health.total == 0:
            return health

    for p in places:
        if getattr(p, "name", ""):
            health.with_name += 1
        if getattr(p, "place_id", ""):
            health.with_place_id += 1
        if _has_coords(p):
            health.with_coords += 1

    if health.total:
        if health.name_coverage < MIN_NAME_COVERAGE:
            health.problems.append(
                f"name coverage {health.name_coverage:.0%} < {MIN_NAME_COVERAGE:.0%}"
            )
        if health.place_id_coverage < MIN_PLACE_ID_COVERAGE:
            health.problems.append(
                f"place_id coverage {health.place_id_coverage:.0%} < {MIN_PLACE_ID_COVERAGE:.0%}"
            )
        if health.coords_coverage < MIN_COORDS_COVERAGE:
            health.problems.append(
                f"coords coverage {health.coords_coverage:.0%} < {MIN_COORDS_COVERAGE:.0%}"
            )

    return health


def validate_search(
    places: list[ParsedPlace],
    *,
    query: str = "",
    strict: bool = False,
    min_results: int = 1,
    log: logging.Logger | None = None,
) -> ParseHealth:
    """Assess a parsed batch; warn (default) or raise (strict) on drift.

    Returns the :class:`ParseHealth` regardless. In strict mode an unhealthy
    result raises :class:`DriftError`; otherwise it logs a warning and returns.
    This function never mutates ``places``.
    """
    health = assess_search(places, min_results=min_results)
    if not health.is_healthy:
        msg = (
            "parse health check failed"
            + (f" for '{query}'" if query else "")
            + f": {'; '.join(health.problems)} ({health.summary()})"
        )
        if strict:
            raise DriftError(msg, health=health)
        (log or logger).warning("possible drift — %s", msg)
    return health


def assess_place(place: Any) -> ParseHealth:
    """Health of a single enriched place-detail parse (treated as a batch of 1)."""
    return assess_search([place], min_results=1)


# ── Canary: live drift probe (ops helper; needs network, not run in CI) ──


@dataclass(frozen=True)
class Canary:
    """A known query whose healthy response shape is well understood.

    Running a canary periodically against the live API is the cheapest way to
    detect that Google changed its format: a well-known chain in a busy area
    should always return many results with names, place_ids, and coordinates.
    """

    query: str
    latitude: float
    longitude: float
    min_results: int = 5
    label: str = ""


# A few dense, stable queries. Coordinates are approximate city centres.
DEFAULT_CANARIES: tuple[Canary, ...] = (
    Canary("Starbucks", 47.6062, -122.3321, min_results=5, label="Seattle Starbucks"),
    Canary("McDonald's", 40.7580, -73.9855, min_results=5, label="NYC McDonald's"),
    Canary("pharmacy", 51.5074, -0.1278, min_results=5, label="London pharmacy"),
)


async def run_canary(
    client: Any, canary: Canary | None = None, *, strict: bool = True
) -> ParseHealth:
    """Run one canary query against a live client and validate its health.

    Intended for scheduled ops checks / CI-with-network, not the default unit
    suite. Raises :class:`DriftError` in strict mode if the well-known query
    comes back structurally unhealthy — a strong signal Google's format moved.
    """
    canary = canary or DEFAULT_CANARIES[0]
    result = await client.search.places(
        query=canary.query,
        latitude=canary.latitude,
        longitude=canary.longitude,
        max_results=20,
    )
    return validate_search(
        result.places,
        query=canary.label or canary.query,
        strict=strict,
        min_results=canary.min_results,
    )
