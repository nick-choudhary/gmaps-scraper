"""Tests for CLI rendering and command startup."""

import click
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
