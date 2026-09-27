"""Tests for the local rank tracker web interface."""

import pytest

from gmaps.rank_web import INDEX_HTML, RankWebRequest


def payload() -> dict[str, object]:
    return {
        "target": "Kyle M. Moore, Attorney",
        "target_type": "name",
        "keywords": ["personal injury lawyer", "wrongful death lawyer"],
        "locations": [
            {"name": "Gainesville, Georgia"},
            {
                "name": "Augusta",
                "latitude": 33.4735,
                "longitude": -82.0105,
            },
        ],
        "grid_size": 3,
        "spacing_km": 2,
        "zoom": 14,
        "max_rank": 40,
        "provider": "direct",
    }


def test_rank_web_request_accepts_named_and_coordinate_locations() -> None:
    request = RankWebRequest.from_payload(payload())

    assert request.keywords == (
        "personal injury lawyer",
        "wrongful death lawyer",
    )
    assert request.locations[0].latitude is None
    assert request.locations[1].longitude == -82.0105
    assert request.grid_size == 3


def test_rank_web_request_deduplicates_keywords_and_locations() -> None:
    request_payload = payload()
    request_payload["keywords"] = ["lawyer", "lawyer", " lawyer "]
    request_payload["locations"] = [
        {"name": "Gainesville, Georgia"},
        {"name": "Gainesville, Georgia"},
    ]

    request = RankWebRequest.from_payload(request_payload)

    assert request.keywords == ("lawyer",)
    assert len(request.locations) == 1


@pytest.mark.parametrize("grid_size", [0, 2, 16])
def test_rank_web_request_rejects_invalid_grid_sizes(grid_size: int) -> None:
    request_payload = payload()
    request_payload["grid_size"] = grid_size

    with pytest.raises(ValueError):
        RankWebRequest.from_payload(request_payload)


def test_rank_web_request_caps_total_coordinate_scans() -> None:
    request_payload = payload()
    request_payload["keywords"] = [f"keyword {index}" for index in range(5)]
    request_payload["locations"] = [{"name": f"Location {index}"} for index in range(5)]
    request_payload["grid_size"] = 7

    with pytest.raises(ValueError, match="1,225 coordinate scans"):
        RankWebRequest.from_payload(request_payload)


def test_rank_web_page_exposes_batch_inputs_and_export() -> None:
    assert 'id="keywords"' in INDEX_HTML
    assert 'id="locations"' in INDEX_HTML
    assert 'id="gridSize"' in INDEX_HTML
    assert 'id="exportButton"' in INDEX_HTML
    assert "SR 515 corridor locations" in INDEX_HTML
