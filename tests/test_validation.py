"""Tests for the Phase 0 structural validation / drift detection module."""

from types import SimpleNamespace

from gmaps.exceptions import DriftError
from gmaps.validation import (
    DEFAULT_CANARIES,
    Canary,
    ParseHealth,
    assess_place,
    assess_search,
    validate_search,
)


def place(name="Cafe", pid="ChIJ1", lat=1.0, lng=2.0):
    return SimpleNamespace(name=name, place_id=pid, latitude=lat, longitude=lng)


class TestAssessSearch:
    def test_healthy_batch(self):
        h = assess_search([place(), place()])
        assert h.is_healthy
        assert h.total == 2
        assert h.name_coverage == 1.0 and h.place_id_coverage == 1.0 and h.coords_coverage == 1.0

    def test_empty_is_unhealthy(self):
        h = assess_search([])
        assert not h.is_healthy
        assert h.total == 0
        assert any("result" in p for p in h.problems)

    def test_low_name_coverage_flagged(self):
        places = [place() for _ in range(4)] + [place(name="")]  # 80% < 90%
        h = assess_search(places)
        assert not h.is_healthy
        assert any("name coverage" in p for p in h.problems)

    def test_low_coords_coverage_flagged(self):
        places = [place() for _ in range(3)] + [place(lat=None, lng=None) for _ in range(2)]
        h = assess_search(places)  # coords 60% < 80%
        assert not h.is_healthy
        assert any("coords coverage" in p for p in h.problems)

    def test_boundary_coverage_is_healthy(self):
        # Exactly 90% name coverage should pass (>= threshold).
        places = [place() for _ in range(9)] + [place(name="")]
        h = assess_search(places)
        assert h.name_coverage == 0.9
        assert h.is_healthy

    def test_min_results_flag(self):
        h = assess_search([place(), place()], min_results=5)
        assert not h.is_healthy
        assert any("≥5" in p for p in h.problems)


class TestValidateSearch:
    def test_warn_mode_does_not_raise(self):
        h = validate_search([], strict=False)  # unhealthy but warn-only
        assert not h.is_healthy  # returned without raising

    def test_strict_raises_on_drift(self):
        raised = False
        try:
            validate_search([], strict=True)
        except DriftError:
            raised = True
        assert raised

    def test_strict_healthy_does_not_raise(self):
        h = validate_search([place(), place()], strict=True)
        assert h.is_healthy

    def test_does_not_mutate_input(self):
        places = [place(), place()]
        validate_search(places, strict=False)
        assert len(places) == 2

    def test_drifterror_carries_health(self):
        try:
            validate_search([], strict=True, query="Starbucks")
        except DriftError as e:
            assert e.health is not None
            assert not e.health.is_healthy


class TestAssessPlace:
    def test_single_place(self):
        assert assess_place(place()).is_healthy

    def test_broken_place(self):
        assert not assess_place(place(name="", pid="")).is_healthy


class TestParseHealth:
    def test_coverage_and_summary(self):
        h = ParseHealth(total=4, with_name=4, with_place_id=3, with_coords=2)
        assert h.name_coverage == 1.0
        assert h.place_id_coverage == 0.75
        assert h.coords_coverage == 0.5
        assert "total=4" in h.summary()

    def test_zero_total_no_divzero(self):
        h = ParseHealth()
        assert h.name_coverage == 0.0


class TestCanaries:
    def test_defaults_present_and_valid(self):
        assert len(DEFAULT_CANARIES) >= 1
        assert all(
            isinstance(c, Canary) and c.query and c.min_results >= 1 for c in DEFAULT_CANARIES
        )
