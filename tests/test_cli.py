"""Tests for CLI rendering and command startup."""

import click
import pytest
from click.testing import CliRunner

from gmaps.cli import _output_places, main
from gmaps.rpc.parser import ParsedPlace


def test_json_stdout_is_ascii_safe() -> None:
    runner = CliRunner()
    place = ParsedPlace(name="Coffee\u202fShop", place_id="ChIJ-test")

    @click.command()
    def render() -> None:
        _output_places([place], "json", None)

    result = runner.invoke(render)

    assert result.exit_code == 0
    assert "Coffee\\u202fShop" in result.output


def test_help_starts() -> None:
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "gmaps-scraper" in result.output


def test_collect_help_uses_human_location_and_contact_limit() -> None:
    result = CliRunner().invoke(main, ["collect", "--help"])

    assert result.exit_code == 0
    assert "--location" in result.output
    assert "--max-contacts" in result.output
    assert "--resume" in result.output


def test_search_and_grid_expose_contact_attempt_limit() -> None:
    runner = CliRunner()

    assert "--max-contacts" in runner.invoke(main, ["search", "--help"]).output
    assert "--max-contacts" in runner.invoke(main, ["grid", "--help"]).output


@pytest.mark.parametrize("when", ["2026-10-29T08:30+01:00", "2026-10-29T07:30Z"])
def test_route_rejects_times_with_a_utc_offset(when: str) -> None:
    result = CliRunner().invoke(main, ["route", "A", "B", "--depart", when])

    assert result.exit_code == 2
    assert "without a UTC offset" in result.output


def test_route_offers_only_verified_modes() -> None:
    result = CliRunner().invoke(main, ["route", "A", "B", "--mode", "transit"])

    assert result.exit_code == 2


def test_place_rejects_an_id_it_cannot_look_up() -> None:
    result = CliRunner().invoke(main, ["place", "not-a-place-id"])

    assert result.exit_code == 2
    assert "not a ChIJ... place ID" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
