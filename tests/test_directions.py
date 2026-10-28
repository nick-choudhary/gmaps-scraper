"""Directions: request encoding, and parsing of real responses captured 2026-10-21.

Fixtures (tests/fixtures/live/directions, gzipped /maps/preview/directions bodies, pruned to
the values the parser reads, with per-request tokens and non-Google URLs replaced):
  text_walk          Wien Hauptbahnhof -> Upper Belvedere, Vienna, by text, walking
  coords_walk        two coordinates in Vienna's old town, walking
  3_stop_drive       Schönbrunn Palace -> Naschmarkt -> Wiener Riesenrad, driving
  drive_depart_0830  Vienna International Airport -> Stephansplatz, driving, depart 2026-10-29 08:30
  drive_arrive_0900  the same, arrive by 2026-10-29 09:00
  unknown            an unresolvable text stop -> Albertina
  noroute_drive      Vienna -> New York, driving
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import pytest

from gmaps._directions import DirectionsAPI
from gmaps.exceptions import ParseError
from gmaps.rpc.directions import (
    build_directions_path,
    encode_polyline,
    encode_waypoint,
    parse_directions_response,
)

FIXTURES = Path(__file__).parent / "fixtures" / "live" / "directions"


def _load(name: str) -> Any:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _pb(path: str) -> str:
    return path.split("&pb=", 1)[1]


# ── request encoding ──


def test_waypoint_kinds():
    assert encode_waypoint("Wien Hauptbahnhof") == "!1m2!1sWien%20Hauptbahnhof!6e0"
    assert encode_waypoint((48.2082, 16.3738)) == "!1m4!3m2!3d48.2082!4d16.3738!6e2"
    hex_id = "0x476d079bf27e5ac1:0x66ef8a989f37deb8"
    assert encode_waypoint(hex_id) == f"!1m3!1s!2s{hex_id.replace(':', '%3A')}!6e0"
    # A ChIJ place ID becomes the same feature-ID waypoint.
    assert encode_waypoint("ChIJwVp-8psHbUcRuN43n5iK72Y") == encode_waypoint(hex_id)


def test_pb_string_escaping():
    # ! and * are pb syntax: sent as *21 and *2A, then URL-encoded.
    assert unquote(encode_waypoint("Bar! *1")) == "!1m2!1sBar*21 *2A1!6e0"


@pytest.mark.parametrize("bad", ["", "   ", (91.0, 0.0), (0.0, 181.0)])
def test_bad_waypoints(bad: Any):
    with pytest.raises(ValueError):
        encode_waypoint(bad)


def test_mode_and_token_count():
    path = build_directions_path(["A", "B"], mode="walk")
    assert path.startswith("/maps/preview/directions?authuser=0&hl=en&gl=us&pb=")
    assert _pb(path).endswith("!6m4!14b1!20m2!1e2!2e3")
    assert _pb(build_directions_path(["A", "B"], mode="drive")).endswith("!6m4!14b1!20m2!1e0!2e3")
    assert _pb(build_directions_path(["A", "B"], geometry=False)).endswith("!6m3!20m2!1e0!2e3")


def test_departure_and_arrival_are_local_wall_clock_as_utc():
    eight_thirty = dt.datetime(2026, 10, 22, 8, 30)
    expected = 1792657800  # calendar.timegm(2026-10-22 08:30) - not the Vienna epoch
    depart = _pb(build_directions_path(["A", "B"], departure_time=eight_thirty))
    assert depart.endswith(f"!6m7!14b1!20m2!1e0!2e3!19m2!2e2!3j{expected}")
    arrive = _pb(build_directions_path(["A", "B"], arrival_time=eight_thirty))
    assert arrive.endswith(f"!6m8!14b1!20m2!1e0!2e3!19m3!1e1!2e2!3j{expected}")


@pytest.mark.parametrize("offset", [0, 2])
def test_times_with_a_utc_offset_are_rejected(offset: int):
    aware = dt.datetime(2026, 10, 22, 8, 30, tzinfo=dt.timezone(dt.timedelta(hours=offset)))
    with pytest.raises(ValueError, match="UTC offset"):
        build_directions_path(["A", "B"], departure_time=aware)
    with pytest.raises(ValueError, match="UTC offset"):
        build_directions_path(["A", "B"], arrival_time=aware)


def test_request_validation():
    with pytest.raises(ValueError):
        build_directions_path(["only one"])
    with pytest.raises(ValueError):
        build_directions_path(["A", "B"], mode="teleport")
    for unverified in ("bicycle", "transit"):
        with pytest.raises(ValueError):
            build_directions_path(["A", "B"], mode=unverified)
    with pytest.raises(ValueError):
        build_directions_path(
            ["A", "B"],
            departure_time=dt.datetime(2026, 1, 1),
            arrival_time=dt.datetime(2026, 1, 1),
        )


# ── parsing real responses ──


def test_walk_by_text():
    result = parse_directions_response(_load("text_walk"), mode="walk", queries=["a", "b"])
    assert result.status == "OK" and result.unresolved == []
    origin, destination = result.waypoints
    assert origin.name == "Wien Hauptbahnhof"
    assert origin.address == "Am Hbf 1, 1100 Wien, Austria"
    assert origin.query == "a"
    assert destination.name == "Belvedere Palace"
    assert destination.place_id == "ChIJpZ4LAn0HbUcRB3aToFL8ZUo"
    assert destination.hex_id == "0x476d077d020b9ea5:0x4a65fc52a0937607"
    assert destination.latitude == pytest.approx(48.1915, abs=1e-3)
    route = result.routes[0]
    assert (route.distance_meters, route.duration_seconds) == (774, 656)
    assert (route.distance_text, route.duration_text) == ("750 m", "11 min")
    assert route.summary == "B221"
    assert route.static_duration_seconds == route.duration_seconds  # no traffic on foot
    assert route.duration_range_seconds is None and route.arrival is None
    assert [(leg.distance_meters, leg.duration_seconds) for leg in route.legs] == [(774, 657)]


def test_coordinates_resolve_to_areas_not_places():
    result = parse_directions_response(_load("coords_walk"), mode="walk")
    assert result.status == "OK"
    origin = result.waypoints[0]
    assert (origin.latitude, origin.longitude) == (48.2082, 16.3738)
    assert origin.place_id == ""  # an area ID (GhIJ…) is not a place ID
    assert origin.name == "Stephansplatz T"


def test_multi_stop_legs():
    result = parse_directions_response(_load("3_stop_drive"), mode="drive")
    assert [w.name for w in result.waypoints] == [
        "Schönbrunn Palace",
        "Naschmarkt",
        "Viennese Giant Ferris Wheel",
    ]
    route = result.routes[0]
    assert [(leg.distance_meters, leg.duration_seconds) for leg in route.legs] == [
        (4625, 717),
        (4008, 728),
    ]
    assert route.distance_meters == 8633


def test_drive_with_departure_time_has_traffic_range_and_arrival():
    result = parse_directions_response(_load("drive_depart_0830"), mode="drive")
    assert result.status == "OK" and len(result.routes) == 2
    route = result.routes[0]
    assert route.summary == "A4"
    assert route.static_duration_seconds == 1638  # typical
    assert route.duration_seconds == 2100  # predicted traffic at 08:30
    assert route.duration_without_traffic_seconds == 1344
    assert route.duration_range_seconds == (1560, 3000)
    assert route.duration_range_text == "26–50 min"
    assert route.arrival is not None and route.departure is None
    assert route.arrival.epoch == 1793262000  # 2026-10-29 09:20 CET
    assert route.arrival.timezone == "Europe/Vienna"
    assert route.arrival.utc_offset_seconds == 3600


def test_drive_arrive_by_has_departure():
    route = parse_directions_response(_load("drive_arrive_0900"), mode="drive").routes[0]
    assert route.departure is not None and route.arrival is None
    assert route.departure.epoch == 1793257800  # 08:10 CET
    assert route.duration_seconds == 2100


def test_unknown_stop_is_not_found_not_no_route():
    result = parse_directions_response(_load("unknown"), mode="walk")
    assert result.status == "NOT_FOUND"
    assert result.unresolved == [0]
    assert result.waypoints[0].name == "Qwxzv Plaza Nowhere 99999"
    assert result.waypoints[1].resolved
    assert result.routes == []


def test_no_route_between_resolved_stops():
    result = parse_directions_response(_load("noroute_drive"), mode="drive")
    assert result.status == "ZERO_RESULTS"
    assert result.unresolved == [] and result.routes == []


def test_to_dict_is_json_ready():
    data = parse_directions_response(_load("drive_depart_0830"), mode="drive").to_dict()
    assert "raw" not in data
    assert data["waypoints"][0]["resolved"] is True
    assert data["routes"][0]["duration_range_seconds"] == (1560, 3000)
    json.dumps(data)


@pytest.mark.parametrize(
    "raw",
    [None, [], [[]], [[["only one waypoint"]]], [[[[], []], "routes-not-a-list"]], "<html>"],
)
def test_layout_change_raises_instead_of_no_route(raw: Any):
    with pytest.raises(ParseError):
        parse_directions_response(raw)


def test_route_without_duration_raises():
    raw = _load("text_walk")
    raw[0][1][0][0][3] = None
    with pytest.raises(ParseError):
        parse_directions_response(raw)


# ── API wiring ──


class FakeTransport:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.paths: list[str] = []

    async def get(self, path: str, **kwargs: Any) -> Any:
        self.paths.append(path)
        return self.response


@pytest.mark.asyncio
async def test_api_route():
    transport = FakeTransport(_load("3_stop_drive"))
    api = DirectionsAPI(transport, language="en", region="at")  # type: ignore[arg-type]
    result = await api.route(
        [(48.1869434, 16.3132568), "ChIJ…not-an-id", "Wiener Riesenrad, Prater"], mode="drive"
    )
    assert transport.paths[0].startswith("/maps/preview/directions?authuser=0&hl=en&gl=at&pb=")
    assert result.waypoints[0].query == "48.1869434,16.3132568"
    assert result.status == "OK"


# ── steps and geometry (the captures requested it with !14b1) ──


def test_drive_steps_segments_and_path():
    result = parse_directions_response(_load("drive_depart_0830"), mode="drive")
    route = result.routes[0]
    leg = route.legs[0]
    assert leg.segments[0].instruction == "Get on A4"
    first = route.steps[0]
    assert first.instruction == "Head southeast"
    assert first.maneuver is None  # no markup on this step: not guessed
    assert first.start_latitude == pytest.approx(48.1238966, abs=1e-6)
    ramp = next(s for s in route.steps if s.maneuver == "ON_RAMP")
    assert ramp.instruction == "Slight right onto the ramp to Wien/Linz/Graz"
    merge = next(s for s in route.steps if s.maneuver == "MERGE")
    assert merge.instruction == "Merge onto A4" and "Toll road" in merge.notices
    roundabout = next(s for s in route.steps if s.maneuver == "ROUNDABOUT_ENTER_AND_EXIT")
    assert roundabout.instruction == "At the roundabout, take the 2nd exit onto Marc-Aurel-Straße"
    # Step distances add up to the leg; the path runs stop to stop.
    assert (
        abs(sum(s.distance_meters for s in route.steps) - leg.distance_meters)
        <= 0.02 * leg.distance_meters
    )
    assert len(route.path) == 1077
    assert route.path[0] == pytest.approx((48.1238966, 16.5592155))
    assert route.path[-1] == pytest.approx((48.2091592, 16.3732236))
    # Each alternative has its own path.
    assert len(result.routes[1].path) == 1309


def test_walk_turn_maneuvers_come_from_google_markup():
    route = parse_directions_response(_load("text_walk"), mode="walk").routes[0]
    maneuvers = [s.maneuver for s in route.steps]
    assert "TURN_RIGHT" in maneuvers and "TURN_LEFT" in maneuvers
    assert "TURN" not in maneuvers  # refined whenever the side is given
    turn = next(s for s in route.steps if s.maneuver == "TURN_RIGHT")
    assert turn.instruction == "Turn right onto Wiedner Gürtel/B221"


def test_multi_stop_path_and_steps_per_leg():
    route = parse_directions_response(_load("3_stop_drive"), mode="drive").routes[0]
    assert len(route.legs) == 2 and all(leg.segments for leg in route.legs)
    assert len(route.steps) == sum(len(seg.steps) for leg in route.legs for seg in leg.segments)
    assert "TURN_SLIGHT_LEFT" in [s.maneuver for s in route.steps]
    assert len(route.path) == 610


def test_old_responses_without_geometry_still_parse():
    raw = _load("text_walk")
    raw[0][7] = None  # as answered before the request asked for geometry (!14b1)
    route = parse_directions_response(raw, mode="walk").routes[0]
    assert route.path == [] and route.steps


def test_to_dict_leaves_the_path_out_unless_asked():
    result = parse_directions_response(_load("text_walk"), mode="walk")
    assert "path" not in result.to_dict()["routes"][0]
    assert len(result.to_dict(include_path=True)["routes"][0]["path"]) == 68
    assert result.to_dict()["routes"][0]["legs"][0]["segments"][0]["steps"][0]["instruction"]


def test_encode_polyline_matches_googles_reference_example():
    # The worked example in Google's "Encoded Polyline Algorithm Format" documentation.
    points = [(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]
    assert encode_polyline(points) == "_p~iF~ps|U_ulLnnqC_mqNvxq`@"
