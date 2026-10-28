"""Place ID <-> feature ID conversion and PlacesAPI lookups by ID."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from gmaps._places import PlacesAPI
from gmaps.rpc.ids import hex_id_to_place_id, place_id_to_hex_id

LIVE = Path(__file__).parent / "fixtures" / "live"

# Pairs seen together in live search results (2026-10-21).
PAIRS = [
    ("ChIJwVp-8psHbUcRuN43n5iK72Y", "0x476d079bf27e5ac1:0x66ef8a989f37deb8"),
    ("ChIJ2XE5E4UHbUcRnNXnuwsVah8", "0x476d0785133971d9:0x1f6a150bbbe7d59c"),
]


@pytest.mark.parametrize(("place_id", "hex_id"), PAIRS)
def test_conversion_both_ways(place_id: str, hex_id: str) -> None:
    assert place_id_to_hex_id(place_id) == hex_id
    assert hex_id_to_place_id(hex_id) == place_id


@pytest.mark.parametrize(
    "value",
    [
        "",
        "GhIJQWDl0CIeQUARxks3icF8U8A",
        "ChIJ",
        "ChIJ!!!",
        "EiQxIE1haW4gU3Q",
        None,
        "ChIJwVp-8psHbUcRuN43n5iK72Y=&x=1",  # trailing characters base64 decoding would ignore
        "ChIJwVp-8p!&%#sHbUcRuN43n5iK72Y",  # characters outside the alphabet in the middle
        "ChIJwVp-8psHbUcRuN43n5iK72Y\n",
    ],
)
def test_non_feature_place_ids_convert_to_none(value: Any) -> None:
    assert place_id_to_hex_id(value) is None


@pytest.mark.parametrize(
    "value", ["", "0x1", "1234:5678", "0xzz:0x1", None, "0x1:0x2\n", "0x1:0x2&a=b"]
)
def test_bad_hex_ids_convert_to_none(value: Any) -> None:
    assert hex_id_to_place_id(value) is None


class FakeTransport:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.paths: list[str] = []

    async def get(self, path: str, **kwargs: Any) -> Any:
        self.paths.append(path)
        return self.response


def _details() -> Any:
    return json.loads((LIVE / "place_details_2026-10-21.json").read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_get_by_place_id_uses_preview_place_with_feature_id() -> None:
    transport = FakeTransport(_details())
    place = await PlacesAPI(transport).get("ChIJwVp-8psHbUcRuN43n5iK72Y")  # type: ignore[arg-type]
    path = unquote(transport.paths[0])
    assert path.startswith("/maps/preview/place?")
    assert "!1s0x476d079bf27e5ac1:0x66ef8a989f37deb8" in path
    assert "!5sChIJwVp-8psHbUcRuN43n5iK72Y" in path
    assert place is not None
    assert place.name == "Albertina"
    assert place.phone == "+43 1 534830"
    # The fixture's non-Google URLs are placeholders.
    assert place.website == "https://www.example.com/"
    assert place.rating == 4.7 and place.review_count == 36294
    assert place.timezone == "Europe/Vienna"


@pytest.mark.asyncio
async def test_get_by_hex_id_derives_place_id() -> None:
    transport = FakeTransport(_details())
    place = await PlacesAPI(transport).get_by_hex_id("0x476D079BF27E5AC1:0x66EF8A989F37DEB8")  # type: ignore[arg-type]
    assert "!5sChIJwVp-8psHbUcRuN43n5iK72Y" in unquote(transport.paths[0])
    assert place is not None and place.place_id == "ChIJwVp-8psHbUcRuN43n5iK72Y"


@pytest.mark.asyncio
async def test_get_rejects_ids_it_cannot_convert() -> None:
    api = PlacesAPI(FakeTransport(None))  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        await api.get("GhIJQWDl0CIeQUARxks3icF8U8A")
    with pytest.raises(ValueError):
        await api.get_by_hex_id("not-a-hex-id")


def test_build_place_details_pb_kept_as_deprecated_alias() -> None:
    from gmaps.rpc import build_place_details_pb

    with pytest.warns(DeprecationWarning, match="build_place_details_path"):
        pb = build_place_details_pb("ChIJwVp-8psHbUcRuN43n5iK72Y", "de")
    assert pb == "!1m2!2splace_id:ChIJwVp-8psHbUcRuN43n5iK72Y!3sde"
