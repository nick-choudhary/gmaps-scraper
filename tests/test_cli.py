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


def test_rank_grid_help_exposes_provider_and_output_options() -> None:
    result = CliRunner().invoke(main, ["rank-grid", "--help"])

    assert result.exit_code == 0
    assert "--target" in result.output
    assert "--provider" in result.output
    assert "--top-profiles" in result.output
    assert "--html-output" in result.output


def test_rank_web_help_exposes_server_options() -> None:
    result = CliRunner().invoke(main, ["rank-web", "--help"])

    assert result.exit_code == 0
    assert "--host" in result.output
    assert "--port" in result.output


def test_rank_grid_rejects_even_grid_size_before_searching() -> None:
    result = CliRunner().invoke(
        main,
        [
            "rank-grid",
            "lawyer",
            "--target",
            "Target",
            "--lat",
            "33.749",
            "--lng",
            "-84.388",
            "--grid-size",
            "4",
        ],
    )

    assert result.exit_code == 2
    assert "must be odd" in result.output


def test_rank_grid_rejects_matching_output_paths() -> None:
    result = CliRunner().invoke(
        main,
        [
            "rank-grid",
            "lawyer",
            "--target",
            "Target",
            "--lat",
            "33.749",
            "--lng",
            "-84.388",
            "--output",
            "rank-result",
            "--html-output",
            "./rank-result",
        ],
    )

    assert result.exit_code == 2
    assert "must use different files" in result.output


def test_rank_grid_serper_requires_environment_key() -> None:
    result = CliRunner().invoke(
        main,
        [
            "rank-grid",
            "lawyer",
            "--target",
            "Target",
            "--lat",
            "33.749",
            "--lng",
            "-84.388",
            "--provider",
            "serper",
        ],
        env={"SERPER_API_KEY": ""},
    )

    assert result.exit_code == 2
    assert "Set SERPER_API_KEY" in result.output
