"""Request builder and parser for the Maps directions endpoint.

``GET /maps/preview/directions?…&pb=…`` is what google.com/maps calls to draw
a route. It needs no key, login or cookies. The minimal request and the
response layout were documented by ai-ecoverse/skills (``skills/gmaps``,
Apache-2.0) and re-verified here against live responses on 2026-10-21; the
captures are in ``tests/fixtures/live``.

Request (``pb``), one waypoint message per stop, then the options::

    !1m2!1s<text>!6e0                 waypoint by free text
    !1m3!1s<name>!2s<0x…:0x…>!6e0     waypoint by feature ID (name may be empty)
    !1m4!3m2!3d<lat>!4d<lng>!6e2      waypoint by coordinates
    !6m<n>!20m2!1e<mode>!2e3          mode: 0 drive, 2 walk (1 bicycle and 3 transit
                                      are not offered until captures verify them)
          !14b1                       also send each route's geometry (j[0][7])
          !19m2!2e2!3j<t>             depart at t
          !19m3!1e1!2e2!3j<t>         arrive by t

``t`` is the local wall-clock time written as if it were UTC (19:00 in the
waypoint's city is ``calendar.timegm`` of 19:00). ``!2e3`` is required; ``m<k>``
counts the flat tokens that follow.

Response (after the ``)]}'`` line), positional::

    j[0][0][i][0][0]     waypoint: [text, hex_id, [_, _, lat, lng], …, [18] place_id]
                          coordinates null = Google could not resolve the stop
    j[0][0][i][0][1]     [[name, address], name, address, street, locality]
    j[0][1]              routes (empty or null: nothing connects the stops)
    route[0][1]          "via" summary
    route[0][2]          [meters, text]
    route[0][3]          [seconds, text]       typical duration, ignores traffic
    route[0][10]         driving traffic block (absent otherwise):
        [0]  [seconds, text]                   duration in traffic (now or at t)
        [3]  [seconds, text]                   duration with no traffic
        [4]  [min_s, max_s, text]              range, e.g. "26–50 min"
        [7]  [epoch, tz, text, utc_offset_s]   arrival (when departing at t)
        [8]  [epoch, tz, text, utc_offset_s]   departure (when arriving by t)
    route[1][k][0]       leg k (between stop k and k+1): [_, _, [m, text], [s, text]]
    route[1][k][1][g]    segment g of leg k, the grouped lines Maps lists ("Get on A4"):
        [0]              [_, _, [m, text], [s, text], …, [14] instruction pieces]
        [1][n][0]        step n, one turn: [_, xml, [m, text], [s, text], …,
                          [7][1][1] [_, _, lat, lng] where the step starts,
                          [9] notices ([_, "Toll road", …]), [14] instruction pieces]
                          xml is "" or "<step maneuver='ON_RAMP' …>…</step>"
    instruction pieces   [[kind, [text, …]], …]; the text joined is the instruction
    j[0][7][r][0], [1]   route r's path: latitudes and longitudes x 1e7, the first
                          absolute and the rest deltas (requested with !14b1)
"""

from __future__ import annotations

import calendar
import datetime as dt
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..exceptions import ParseError
from .ids import hex_id_to_place_id
from .pb_encoder import encode_pb_string

TRAVEL_MODES = {"drive": 0, "walk": 2}

# Response indices; Google can move these, keep them in one place.
ROOT_INDEX = 0
WAYPOINTS_INDEX = 0
ROUTES_INDEX = 1
WP_RECORD_INDEX = 0
WP_POINT_INDEX = 0
WP_LABELS_INDEX = 1
WP_TEXT_INDEX = 0
WP_HEX_ID_INDEX = 1
WP_COORDS_INDEX = 2
WP_COORD_LAT_INDEX = 2
WP_COORD_LNG_INDEX = 3
WP_PLACE_ID_INDEX = 18
WP_LABEL_NAME_INDEX = 1
WP_LABEL_ADDRESS_INDEX = 2
ROUTE_SUMMARY_INDEX = 0
ROUTE_LEGS_INDEX = 1
SUMMARY_VIA_INDEX = 1
SUMMARY_DISTANCE_INDEX = 2
SUMMARY_DURATION_INDEX = 3
SUMMARY_TRAFFIC_INDEX = 10
TRAFFIC_DURATION_INDEX = 0
TRAFFIC_FREE_FLOW_INDEX = 3
TRAFFIC_RANGE_INDEX = 4
TRAFFIC_ARRIVAL_INDEX = 7
TRAFFIC_DEPARTURE_INDEX = 8
GEOMETRY_INDEX = 7
GEOMETRY_LATS_INDEX = 0
GEOMETRY_LNGS_INDEX = 1
LEG_SEGMENTS_INDEX = 1
SEGMENT_SUMMARY_INDEX = 0
SEGMENT_STEPS_INDEX = 1
STEP_RECORD_INDEX = 0
STEP_XML_INDEX = 1
STEP_DISTANCE_INDEX = 2
STEP_DURATION_INDEX = 3
STEP_VIEW_INDEX = 7
STEP_NOTICES_INDEX = 9
STEP_INSTRUCTION_INDEX = 14
LEG_SUMMARY_INDEX = 0
LEG_DISTANCE_INDEX = 2
LEG_DURATION_INDEX = 3

Waypoint = str | tuple[float, float]


def _is_hex_id(value: str) -> bool:
    return hex_id_to_place_id(value) is not None


def encode_waypoint(waypoint: Waypoint) -> str:
    """One waypoint message: (lat, lng), a ``0x…:0x…`` feature ID, a ``ChIJ…``
    place ID, or free text."""
    from .ids import place_id_to_hex_id

    if isinstance(waypoint, tuple):
        lat, lng = (float(x) for x in waypoint)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            raise ValueError(f"coordinates out of range: {waypoint!r}")
        return f"!1m4!3m2!3d{lat!r}!4d{lng!r}!6e2"
    if not isinstance(waypoint, str) or not waypoint.strip():
        raise ValueError(f"empty waypoint: {waypoint!r}")
    text = waypoint.strip()
    hex_id = text.lower() if _is_hex_id(text) else place_id_to_hex_id(text)
    if hex_id:
        return f"!1m3!1s!2s{encode_pb_string(hex_id)}!6e0"
    return f"!1m2!1s{encode_pb_string(text)}!6e0"


def _wall_clock(value: dt.datetime) -> int:
    """Local wall-clock time written as if UTC. Maps reads it in the stop's own
    time zone, so a datetime with a UTC offset is rejected rather than guessed."""
    if value.tzinfo is not None and value.utcoffset() is not None:
        raise ValueError(
            f"give the stop's local time as a naive datetime, not one with a UTC offset: {value!r}"
        )
    return calendar.timegm(value.timetuple())


def build_directions_path(
    waypoints: list[Waypoint],
    mode: str = "drive",
    departure_time: dt.datetime | None = None,
    arrival_time: dt.datetime | None = None,
    language: str = "en",
    region: str = "us",
    geometry: bool = True,
) -> str:
    """Path for ``/maps/preview/directions``. ``geometry`` adds each route's path
    (about 7 KB for a 20 km drive)."""
    if len(waypoints) < 2:
        raise ValueError("need at least an origin and a destination")
    if mode not in TRAVEL_MODES:
        raise ValueError(f"mode must be one of {sorted(TRAVEL_MODES)}, not {mode!r}")
    if departure_time is not None and arrival_time is not None:
        raise ValueError("give departure_time or arrival_time, not both")
    options = ["!14b1"] if geometry else []
    options.append(f"!20m2!1e{TRAVEL_MODES[mode]}!2e3")
    if departure_time is not None:
        options.append(f"!19m2!2e2!3j{_wall_clock(departure_time)}")
    elif arrival_time is not None:
        options.append(f"!19m3!1e1!2e2!3j{_wall_clock(arrival_time)}")
    option_tokens = sum(part.count("!") for part in options)
    pb = "".join(encode_waypoint(w) for w in waypoints) + f"!6m{option_tokens}" + "".join(options)
    return f"/maps/preview/directions?authuser=0&hl={language}&gl={region}&pb={pb}"


@dataclass
class DirectionsWaypoint:
    """A stop as Google resolved it."""

    query: str = ""
    name: str = ""
    address: str = ""
    place_id: str = ""
    hex_id: str = ""
    latitude: float | None = None
    longitude: float | None = None

    @property
    def resolved(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass
class TimePoint:
    """A clock time Google computed: epoch seconds plus the stop's time zone."""

    epoch: int
    timezone: str = ""
    text: str = ""
    utc_offset_seconds: int | None = None


@dataclass
class RouteStep:
    """One turn of the route, as Maps lists it under "Details"."""

    instruction: str = ""
    # Google's maneuver code when the step carries one (ON_RAMP, OFF_RAMP, MERGE,
    # ROUNDABOUT_…, TURN_LEFT, TURN_SLIGHT_RIGHT, …); None otherwise, never
    # inferred from the instruction text.
    maneuver: str | None = None
    distance_meters: int | None = None
    distance_text: str = ""
    duration_seconds: int | None = None
    duration_text: str = ""
    start_latitude: float | None = None
    start_longitude: float | None = None
    notices: list[str] = field(default_factory=list)


@dataclass
class RouteSegment:
    """A grouped line of the directions list ("Get on A4 from Rennweg")
    and the turns it contains."""

    instruction: str = ""
    distance_meters: int | None = None
    distance_text: str = ""
    duration_seconds: int | None = None
    duration_text: str = ""
    steps: list[RouteStep] = field(default_factory=list)


@dataclass
class RouteLeg:
    distance_meters: int | None = None
    distance_text: str = ""
    duration_seconds: int | None = None
    duration_text: str = ""
    segments: list[RouteSegment] = field(default_factory=list)


@dataclass
class Route:
    """One route alternative.

    ``duration_seconds`` is the traffic-aware time when Google gives one
    (driving: now, or at the requested departure/arrival time) and otherwise
    the typical time; ``static_duration_seconds`` is always the typical time.
    """

    summary: str = ""
    distance_meters: int | None = None
    distance_text: str = ""
    duration_seconds: int | None = None
    duration_text: str = ""
    static_duration_seconds: int | None = None
    static_duration_text: str = ""
    duration_without_traffic_seconds: int | None = None
    duration_range_seconds: tuple[int, int] | None = None
    duration_range_text: str = ""
    departure: TimePoint | None = None
    arrival: TimePoint | None = None
    legs: list[RouteLeg] = field(default_factory=list)
    # (latitude, longitude) points along the route; empty unless requested.
    path: list[tuple[float, float]] = field(default_factory=list)

    @property
    def steps(self) -> list[RouteStep]:
        """Every turn of the route, all legs and segments in order."""
        return [step for leg in self.legs for segment in leg.segments for step in segment.steps]


@dataclass
class Directions:
    """Result of one directions request.

    ``status``: ``OK``; ``NOT_FOUND`` when Google could not resolve a stop
    (see ``unresolved``); ``ZERO_RESULTS`` when the stops resolve but nothing
    connects them in this mode.
    """

    mode: str
    status: str
    waypoints: list[DirectionsWaypoint] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    unresolved: list[int] = field(default_factory=list)
    raw: Any = None

    def to_dict(self, include_path: bool = False) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw")
        if not include_path:
            for route in data["routes"]:
                route.pop("path")
        for waypoint, source in zip(data["waypoints"], self.waypoints, strict=True):
            waypoint["resolved"] = source.resolved
        return data


def _at(data: Any, *path: int) -> Any:
    for index in path:
        if not isinstance(data, list) or index >= len(data):
            return None
        data = data[index]
    return data


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _time_point(value: Any) -> TimePoint | None:
    epoch = _int(_at(value, 0))
    if epoch is None:
        return None
    return TimePoint(
        epoch=epoch,
        timezone=_str(_at(value, 1)),
        text=_str(_at(value, 2)),
        utc_offset_seconds=_int(_at(value, 3)),
    )


_MANEUVER = re.compile(r"maneuver='([A-Z_]+)'")
_TURN = re.compile(r"<turn side='(LEFT|RIGHT)'(?: type='(SLIGHT|SHARP)')?")


def _maneuver(xml: str) -> str | None:
    """Google's maneuver code from the step markup; a TURN with an explicit
    ``<turn side type>`` becomes the Routes API name (TURN_SLIGHT_RIGHT, …)."""
    found = _MANEUVER.search(xml)
    if not found:
        return None
    code = found.group(1)
    turn = _TURN.search(xml)
    if code == "TURN" and turn:
        side, kind = turn.groups()
        code = f"TURN_{kind}_{side}" if kind else f"TURN_{side}"
    return code


def _instruction(pieces: Any) -> str:
    """Join instruction pieces ``[[kind, [text, …]], …]`` into one line."""
    if not isinstance(pieces, list):
        return ""
    text = "".join(_str(_at(piece, 1, 0)) for piece in pieces)
    return " ".join(text.split())


def _parse_step(raw_step: Any) -> RouteStep:
    record = _at(raw_step, STEP_RECORD_INDEX)
    start = _at(record, STEP_VIEW_INDEX, 1, 1)
    lat, lng = _at(start, 2), _at(start, 3)
    notices = [
        _str(_at(notice, 1))
        for notice in (_at(record, STEP_NOTICES_INDEX) or [])
        if _str(_at(notice, 1))
    ]
    return RouteStep(
        instruction=_instruction(_at(record, STEP_INSTRUCTION_INDEX)),
        maneuver=_maneuver(_str(_at(record, STEP_XML_INDEX))),
        distance_meters=_int(_at(record, STEP_DISTANCE_INDEX, 0)),
        distance_text=_str(_at(record, STEP_DISTANCE_INDEX, 1)),
        duration_seconds=_int(_at(record, STEP_DURATION_INDEX, 0)),
        duration_text=_str(_at(record, STEP_DURATION_INDEX, 1)),
        start_latitude=float(lat) if isinstance(lat, (int, float)) else None,
        start_longitude=float(lng) if isinstance(lng, (int, float)) else None,
        notices=notices,
    )


def _parse_segment(raw_segment: Any) -> RouteSegment:
    summary = _at(raw_segment, SEGMENT_SUMMARY_INDEX)
    steps_raw = _at(raw_segment, SEGMENT_STEPS_INDEX)
    segment = RouteSegment(
        instruction=_instruction(_at(summary, STEP_INSTRUCTION_INDEX)),
        distance_meters=_int(_at(summary, STEP_DISTANCE_INDEX, 0)),
        distance_text=_str(_at(summary, STEP_DISTANCE_INDEX, 1)),
        duration_seconds=_int(_at(summary, STEP_DURATION_INDEX, 0)),
        duration_text=_str(_at(summary, STEP_DURATION_INDEX, 1)),
    )
    if isinstance(steps_raw, list) and steps_raw:
        segment.steps = [_parse_step(step) for step in steps_raw]
    else:
        # A segment that is a single turn carries no step list: it is its own step.
        segment.steps = [_parse_step([summary])]
    return segment


def _parse_path(raw_geometry: Any) -> list[tuple[float, float]]:
    lats = _at(raw_geometry, GEOMETRY_LATS_INDEX)
    lngs = _at(raw_geometry, GEOMETRY_LNGS_INDEX)
    if not isinstance(lats, list) or not isinstance(lngs, list) or len(lats) != len(lngs):
        return []
    path: list[tuple[float, float]] = []
    lat = lng = 0
    for d_lat, d_lng in zip(lats, lngs, strict=True):
        if not isinstance(d_lat, int) or not isinstance(d_lng, int):
            return []
        lat += d_lat
        lng += d_lng
        path.append((lat / 1e7, lng / 1e7))
    return path


def encode_polyline(points: list[tuple[float, float]]) -> str:
    """Google's encoded polyline format (precision 5), as the Routes API returns it."""
    out: list[str] = []
    prev_lat = prev_lng = 0
    for lat, lng in points:
        ilat, ilng = round(lat * 1e5), round(lng * 1e5)
        for value in (ilat - prev_lat, ilng - prev_lng):
            value = ~(value << 1) if value < 0 else value << 1
            while value >= 0x20:
                out.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            out.append(chr(value + 63))
        prev_lat, prev_lng = ilat, ilng
    return "".join(out)


def _parse_waypoint(record: Any, query: str) -> DirectionsWaypoint:
    point = _at(record, WP_RECORD_INDEX, WP_POINT_INDEX)
    labels = _at(record, WP_RECORD_INDEX, WP_LABELS_INDEX)
    hex_id = _str(_at(point, WP_HEX_ID_INDEX))
    place_id = _str(_at(point, WP_PLACE_ID_INDEX))
    if not place_id.startswith("ChIJ"):
        # Coordinates resolve to an area ID (GhIJ…), not a place.
        place_id = (hex_id_to_place_id(hex_id) or "") if hex_id else ""
    lat = _at(point, WP_COORDS_INDEX, WP_COORD_LAT_INDEX)
    lng = _at(point, WP_COORDS_INDEX, WP_COORD_LNG_INDEX)
    name = _str(_at(labels, WP_LABEL_NAME_INDEX))
    address = _str(_at(labels, WP_LABEL_ADDRESS_INDEX))
    if not name and not address:
        name = _str(_at(point, WP_TEXT_INDEX))
    return DirectionsWaypoint(
        query=query,
        name=name,
        address=address,
        place_id=place_id,
        hex_id=hex_id,
        latitude=float(lat) if isinstance(lat, (int, float)) else None,
        longitude=float(lng) if isinstance(lng, (int, float)) else None,
    )


def _parse_route(raw_route: Any) -> Route:
    summary = _at(raw_route, ROUTE_SUMMARY_INDEX)
    distance = _at(summary, SUMMARY_DISTANCE_INDEX)
    duration = _at(summary, SUMMARY_DURATION_INDEX)
    if _int(_at(distance, 0)) is None or _int(_at(duration, 0)) is None:
        raise ParseError("route without distance/duration: unexpected directions layout")
    route = Route(
        summary=_str(_at(summary, SUMMARY_VIA_INDEX)),
        distance_meters=_int(_at(distance, 0)),
        distance_text=_str(_at(distance, 1)),
        duration_seconds=_int(_at(duration, 0)),
        duration_text=_str(_at(duration, 1)),
        static_duration_seconds=_int(_at(duration, 0)),
        static_duration_text=_str(_at(duration, 1)),
    )
    traffic = _at(summary, SUMMARY_TRAFFIC_INDEX)
    if isinstance(traffic, list):
        in_traffic = _at(traffic, TRAFFIC_DURATION_INDEX)
        if _int(_at(in_traffic, 0)) is not None:
            route.duration_seconds = _int(_at(in_traffic, 0))
            route.duration_text = _str(_at(in_traffic, 1))
        route.duration_without_traffic_seconds = _int(_at(traffic, TRAFFIC_FREE_FLOW_INDEX, 0))
        low = _int(_at(traffic, TRAFFIC_RANGE_INDEX, 0))
        high = _int(_at(traffic, TRAFFIC_RANGE_INDEX, 1))
        if low is not None and high is not None:
            route.duration_range_seconds = (low, high)
            route.duration_range_text = _str(_at(traffic, TRAFFIC_RANGE_INDEX, 2))
        route.arrival = _time_point(_at(traffic, TRAFFIC_ARRIVAL_INDEX))
        route.departure = _time_point(_at(traffic, TRAFFIC_DEPARTURE_INDEX))
    for raw_leg in _at(raw_route, ROUTE_LEGS_INDEX) or []:
        leg = _at(raw_leg, LEG_SUMMARY_INDEX)
        route.legs.append(
            RouteLeg(
                distance_meters=_int(_at(leg, LEG_DISTANCE_INDEX, 0)),
                distance_text=_str(_at(leg, LEG_DISTANCE_INDEX, 1)),
                duration_seconds=_int(_at(leg, LEG_DURATION_INDEX, 0)),
                duration_text=_str(_at(leg, LEG_DURATION_INDEX, 1)),
                segments=[_parse_segment(seg) for seg in (_at(raw_leg, LEG_SEGMENTS_INDEX) or [])],
            )
        )
    return route


def parse_directions_response(
    raw: Any, mode: str = "drive", queries: list[str] | None = None
) -> Directions:
    """Parse a decoded ``/maps/preview/directions`` response.

    Raises ParseError when the envelope is not the known layout, so a format
    change is never reported as "no route".
    """
    waypoints_raw = _at(raw, ROOT_INDEX, WAYPOINTS_INDEX)
    routes_raw = _at(raw, ROOT_INDEX, ROUTES_INDEX)
    if not isinstance(waypoints_raw, list) or len(waypoints_raw) < 2:
        raise ParseError("unexpected directions layout: no waypoint list at [0][0]")
    if routes_raw is not None and not isinstance(routes_raw, list):
        raise ParseError("unexpected directions layout: routes at [0][1] is not a list")
    queries = queries or []
    waypoints = [
        _parse_waypoint(record, queries[i] if i < len(queries) else "")
        for i, record in enumerate(waypoints_raw)
    ]
    unresolved = [i for i, w in enumerate(waypoints) if not w.resolved]
    routes = [_parse_route(r) for r in routes_raw or []]
    geometries = _at(raw, ROOT_INDEX, GEOMETRY_INDEX)
    if isinstance(geometries, list):
        for route, geometry in zip(routes, geometries, strict=False):
            route.path = _parse_path(geometry)
    if unresolved:
        status = "NOT_FOUND"
    elif not routes:
        status = "ZERO_RESULTS"
    else:
        status = "OK"
    return Directions(
        mode=mode,
        status=status,
        waypoints=waypoints,
        routes=routes,
        unresolved=unresolved,
        raw=raw,
    )
