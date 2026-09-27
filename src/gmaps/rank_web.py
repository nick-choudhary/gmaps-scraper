"""Local web interface for multi-keyword, multi-location rank scans."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast

MAX_BATCH_POINTS = 1_000
MAX_REQUEST_BYTES = 1_000_000


@dataclass(frozen=True)
class RankWebLocation:
    """One named location, optionally with explicit coordinates."""

    name: str
    latitude: float | None = None
    longitude: float | None = None


@dataclass(frozen=True)
class RankWebRequest:
    """Validated browser request for a batch of rank-grid scans."""

    target: str
    target_type: str
    keywords: tuple[str, ...]
    locations: tuple[RankWebLocation, ...]
    grid_size: int
    spacing_km: float
    zoom: float
    max_rank: int
    provider: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> RankWebRequest:
        target = _required_string(payload, "target")
        target_type = _choice(
            payload,
            "target_type",
            {"auto", "place_id", "cid", "hex_id", "name"},
            "auto",
        )
        provider = _choice(payload, "provider", {"auto", "direct", "serper"}, "auto")
        keywords = _string_tuple(payload, "keywords")
        locations = _locations(payload.get("locations"))
        grid_size = _integer(payload, "grid_size", default=3, minimum=1, maximum=15)
        if grid_size % 2 == 0:
            raise ValueError("Grid size must be odd.")
        spacing_km = _number(payload, "spacing_km", default=2.0, minimum=0.01, maximum=100)
        zoom = _number(payload, "zoom", default=14.0, minimum=1, maximum=22)
        max_rank = _integer(payload, "max_rank", default=20, minimum=1, maximum=100)

        point_count = len(keywords) * len(locations) * grid_size * grid_size
        if point_count > MAX_BATCH_POINTS:
            raise ValueError(
                f"This batch contains {point_count:,} coordinate scans; "
                f"reduce it to {MAX_BATCH_POINTS:,} or fewer."
            )

        return cls(
            target=target,
            target_type=target_type,
            keywords=keywords,
            locations=locations,
            grid_size=grid_size,
            spacing_km=spacing_km,
            zoom=zoom,
            max_rank=max_rank,
            provider=provider,
        )


def _required_string(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key.replace('_', ' ').title()} is required.")
    return value.strip()


def _choice(
    payload: Mapping[str, object],
    key: str,
    choices: set[str],
    default: str,
) -> str:
    raw = payload.get(key, default)
    if not isinstance(raw, str) or raw not in choices:
        allowed = ", ".join(sorted(choices))
        raise ValueError(f"{key.replace('_', ' ').title()} must be one of: {allowed}.")
    return raw


def _string_tuple(payload: Mapping[str, object], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if not isinstance(raw, list):
        raise ValueError(f"{key.title()} must be a list.")
    values = tuple(
        dict.fromkeys(item.strip() for item in raw if isinstance(item, str) and item.strip())
    )
    if not values:
        raise ValueError(f"At least one {key[:-1]} is required.")
    if len(values) > 25:
        raise ValueError(f"No more than 25 {key} may be scanned at once.")
    return values


def _locations(raw: object) -> tuple[RankWebLocation, ...]:
    if not isinstance(raw, list):
        raise ValueError("Locations must be a list.")
    locations: list[RankWebLocation] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = cast(dict[object, object], item)
        name = data.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        if latitude is None and longitude is None:
            locations.append(RankWebLocation(name=name.strip()))
            continue
        if not isinstance(latitude, int | float) or not isinstance(longitude, int | float):
            raise ValueError(f"Coordinates for {name.strip()} must be numeric.")
        if not -90 <= float(latitude) <= 90 or not -180 <= float(longitude) <= 180:
            raise ValueError(f"Coordinates for {name.strip()} are outside valid ranges.")
        locations.append(
            RankWebLocation(
                name=name.strip(),
                latitude=float(latitude),
                longitude=float(longitude),
            )
        )
    unique = tuple(
        dict.fromkeys(
            (location.name, location.latitude, location.longitude) for location in locations
        )
    )
    if not unique:
        raise ValueError("At least one location is required.")
    if len(unique) > 25:
        raise ValueError("No more than 25 locations may be scanned at once.")
    return tuple(RankWebLocation(*values) for values in unique)


def _integer(
    payload: Mapping[str, object],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value:
        raise ValueError(f"{key.replace('_', ' ').title()} must be an integer.")
    integer = int(value)
    if not minimum <= integer <= maximum:
        raise ValueError(
            f"{key.replace('_', ' ').title()} must be between {minimum} and {maximum}."
        )
    return integer


def _number(
    payload: Mapping[str, object],
    key: str,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key.replace('_', ' ').title()} must be numeric.")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(
            f"{key.replace('_', ' ').title()} must be between {minimum} and {maximum}."
        )
    return number


ScanCallback = Callable[[Mapping[str, object]], Mapping[str, object]]


def build_rank_web_handler(
    scan: ScanCallback,
) -> type[BaseHTTPRequestHandler]:
    """Create an HTTP handler bound to a synchronous scan callback."""

    class RankWebHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/":
                self._send_bytes(200, INDEX_HTML.encode(), "text/html; charset=utf-8")
                return
            if self.path == "/health":
                self._send_json(200, {"status": "ok"})
                return
            self._send_json(404, {"error": "Not found."})

        def do_POST(self) -> None:
            if self.path != "/api/scan":
                self._send_json(404, {"error": "Not found."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "Invalid Content-Length."})
                return
            if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
                self._send_json(400, {"error": "Request body is empty or too large."})
                return
            try:
                decoded = json.loads(self.rfile.read(content_length))
                if not isinstance(decoded, dict):
                    raise ValueError("Request body must be a JSON object.")
                response = scan(cast(dict[str, object], decoded))
            except ValueError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            except Exception as exc:
                self.log_error("rank scan failed: %s", exc)
                self._send_json(500, {"error": f"Rank scan failed: {exc}"})
                return
            self._send_json(200, response)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _send_json(self, status: int, payload: Mapping[str, object]) -> None:
            self._send_bytes(
                status,
                json.dumps(payload, ensure_ascii=False).encode(),
                "application/json; charset=utf-8",
            )

        def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return RankWebHandler


def serve_rank_web(
    scan: ScanCallback,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the local rank tracker until interrupted."""
    server = ThreadingHTTPServer((host, port), build_rank_web_handler(scan))
    try:
        server.serve_forever()
    finally:
        server.server_close()


INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Maps Visibility Grid</title>
  <style>
    :root {
      color-scheme: light;
      --ink: #152019;
      --muted: #64706a;
      --paper: #f4f3ed;
      --card: #fffef9;
      --line: #d9ded7;
      --accent: #195f42;
      --accent-dark: #10432f;
      --soft: #e8f0eb;
      --warn: #d9822b;
      --danger: #b94335;
      --shadow: 0 18px 50px rgba(30, 50, 38, .09);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at 8% 0%, rgba(25, 95, 66, .11), transparent 28rem),
        var(--paper);
      color: var(--ink);
      font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .shell { width: min(1240px, calc(100% - 32px)); margin: 0 auto; padding: 44px 0 80px; }
    header { display: flex; justify-content: space-between; align-items: end; gap: 24px; margin-bottom: 28px; }
    .eyebrow { color: var(--accent); font-size: 12px; font-weight: 800; letter-spacing: .15em; text-transform: uppercase; }
    h1 { margin: 6px 0 4px; font: 700 clamp(34px, 5vw, 62px)/.98 Georgia, serif; letter-spacing: -.035em; }
    .subhead { margin: 0; color: var(--muted); max-width: 720px; font-size: 16px; }
    .status-pill { padding: 9px 13px; border: 1px solid var(--line); border-radius: 999px; background: rgba(255,255,255,.65); color: var(--muted); white-space: nowrap; }
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 350px; gap: 22px; align-items: start; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 20px; box-shadow: var(--shadow); }
    .form-card { padding: 24px; }
    .side-card { padding: 21px; position: sticky; top: 18px; }
    h2 { margin: 0 0 18px; font: 700 24px/1.15 Georgia, serif; }
    h3 { margin: 0 0 10px; font-size: 14px; }
    label { display: block; font-weight: 700; margin: 0 0 7px; }
    .hint { color: var(--muted); font-size: 12px; font-weight: 500; }
    input, textarea, select {
      width: 100%;
      border: 1px solid #cbd2cc;
      border-radius: 11px;
      background: #fff;
      color: var(--ink);
      padding: 11px 12px;
      font: inherit;
      outline: none;
      transition: border .15s, box-shadow .15s;
    }
    input:focus, textarea:focus, select:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(25,95,66,.12); }
    textarea { min-height: 162px; resize: vertical; }
    .target-row { display: grid; grid-template-columns: 1fr 165px; gap: 12px; }
    .two-col { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 18px; }
    .options { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-top: 18px; }
    .option label { font-size: 12px; }
    .actions { display: flex; align-items: center; gap: 10px; margin-top: 22px; flex-wrap: wrap; }
    button {
      border: 0;
      border-radius: 11px;
      padding: 11px 16px;
      font: 750 14px/1 inherit;
      cursor: pointer;
      transition: transform .12s, background .12s;
    }
    button:hover { transform: translateY(-1px); }
    .primary { background: var(--accent); color: #fff; min-width: 170px; }
    .primary:hover { background: var(--accent-dark); }
    .secondary { background: var(--soft); color: var(--accent-dark); }
    button:disabled { cursor: wait; opacity: .55; transform: none; }
    .estimate { color: var(--muted); margin-left: auto; }
    .preset-list { display: grid; gap: 8px; }
    .preset { width: 100%; text-align: left; background: var(--soft); color: var(--accent-dark); }
    .side-copy { color: var(--muted); margin: -4px 0 16px; }
    .legend { display: grid; gap: 7px; margin-top: 20px; }
    .legend-row { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 12px; }
    .swatch { width: 14px; height: 14px; border-radius: 4px; }
    .results { margin-top: 24px; display: none; }
    .results.visible { display: block; }
    .result-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
    .metrics { display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 15px; }
    .metric { padding: 16px; background: var(--card); border: 1px solid var(--line); border-radius: 14px; }
    .metric strong { display: block; font: 700 27px/1 Georgia, serif; }
    .metric span { color: var(--muted); font-size: 12px; }
    .scan-list { display: grid; gap: 10px; }
    details { background: var(--card); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
    summary { cursor: pointer; list-style: none; display: grid; grid-template-columns: minmax(0, 1fr) auto auto; gap: 14px; align-items: center; padding: 15px 17px; }
    summary::-webkit-details-marker { display: none; }
    .scan-title { min-width: 0; }
    .scan-title strong { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .scan-title span { color: var(--muted); font-size: 12px; }
    .badge { border-radius: 999px; padding: 5px 9px; font-size: 12px; font-weight: 800; background: var(--soft); color: var(--accent-dark); }
    .badge.zero { background: #ececea; color: #686b68; }
    .heatmap-wrap { border-top: 1px solid var(--line); padding: 17px; overflow: auto; }
    .heatmap { display: grid; gap: 6px; width: max-content; min-width: 100%; }
    .point { min-width: 68px; min-height: 58px; border-radius: 9px; padding: 8px; display: grid; place-items: center; text-align: center; font-weight: 850; }
    .point small { display: block; font-size: 9px; font-weight: 600; opacity: .72; }
    .rank-good { background: #bce6c9; color: #174529; }
    .rank-mid { background: #f2de98; color: #654c08; }
    .rank-low { background: #efb8a7; color: #6d2319; }
    .rank-none { background: #e6e7e4; color: #686b68; }
    .rank-error { background: #f6d4cf; color: #842e24; }
    .error { margin-top: 14px; padding: 13px 15px; border-radius: 12px; background: #f8deda; color: #7e2f26; display: none; }
    .error.visible { display: block; }
    .loading { display: none; align-items: center; gap: 9px; color: var(--accent-dark); font-weight: 700; }
    .loading.visible { display: flex; }
    .spinner { width: 18px; height: 18px; border: 2px solid #c5d6cb; border-top-color: var(--accent); border-radius: 50%; animation: spin .7s linear infinite; }
    @keyframes spin { to { transform: rotate(360deg); } }
    @media (max-width: 960px) {
      .layout { grid-template-columns: 1fr; }
      .side-card { position: static; }
      .options { grid-template-columns: repeat(3, 1fr); }
      .metrics { grid-template-columns: repeat(3, 1fr); }
    }
    @media (max-width: 640px) {
      header { display: block; }
      .status-pill { display: inline-block; margin-top: 14px; }
      .target-row, .two-col { grid-template-columns: 1fr; }
      .options { grid-template-columns: repeat(2, 1fr); }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      summary { grid-template-columns: 1fr auto; }
      summary .badge:last-child { display: none; }
      .estimate { width: 100%; margin: 2px 0 0; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <div>
        <div class="eyebrow">Local search intelligence</div>
        <h1>Maps Visibility Grid</h1>
        <p class="subhead">Measure one business across many keywords, markets, and exact coordinate grids—without building spreadsheets or repeating CLI commands.</p>
      </div>
      <div class="status-pill">Runs locally · Direct Maps + optional Serper</div>
    </header>

    <section class="layout">
      <div class="card form-card">
        <h2>Configure a scan</h2>
        <div class="target-row">
          <div>
            <label for="target">Business target <span class="hint">Place ID is most reliable</span></label>
            <input id="target" placeholder="ChIJ… or exact business name">
          </div>
          <div>
            <label for="targetType">Target type</label>
            <select id="targetType">
              <option value="auto">Auto-detect</option>
              <option value="place_id">Place ID</option>
              <option value="cid">CID</option>
              <option value="hex_id">Hex ID</option>
              <option value="name">Exact name</option>
            </select>
          </div>
        </div>

        <div class="two-col">
          <div>
            <label for="keywords">Keywords <span class="hint">one per line</span></label>
            <textarea id="keywords" placeholder="personal injury lawyer&#10;wrongful death lawyer&#10;car accident lawyer"></textarea>
          </div>
          <div>
            <label for="locations">Locations <span class="hint">name or Name | lat | lng</span></label>
            <textarea id="locations" placeholder="Gainesville, Georgia&#10;Blue Ridge, Georgia&#10;Augusta | 33.4735 | -82.0105"></textarea>
          </div>
        </div>

        <div class="options">
          <div class="option">
            <label for="gridSize">Grid size</label>
            <select id="gridSize">
              <option value="1">1 × 1</option>
              <option value="3" selected>3 × 3</option>
              <option value="5">5 × 5</option>
              <option value="7">7 × 7</option>
            </select>
          </div>
          <div class="option">
            <label for="spacing">Spacing (km)</label>
            <input id="spacing" type="number" min=".01" step=".25" value="2">
          </div>
          <div class="option">
            <label for="maxRank">Check top</label>
            <select id="maxRank">
              <option value="20" selected>20</option>
              <option value="40">40</option>
              <option value="60">60</option>
              <option value="100">100</option>
            </select>
          </div>
          <div class="option">
            <label for="zoom">Maps zoom</label>
            <input id="zoom" type="number" min="1" max="22" step=".5" value="14">
          </div>
          <div class="option">
            <label for="provider">Provider</label>
            <select id="provider">
              <option value="auto">Auto</option>
              <option value="direct">Direct/free</option>
              <option value="serper">Serper</option>
            </select>
          </div>
        </div>

        <div class="actions">
          <button class="primary" id="runButton">Run visibility scan</button>
          <button class="secondary" id="exportButton" hidden>Export JSON</button>
          <div class="loading" id="loading"><span class="spinner"></span><span id="loadingText">Scanning…</span></div>
          <div class="estimate" id="estimate">0 coordinate searches</div>
        </div>
        <div class="error" id="error"></div>
      </div>

      <aside class="card side-card">
        <h2>Quick starts</h2>
        <p class="side-copy">Load a sensible starting configuration, then adjust the grid for the market density.</p>
        <div class="preset-list">
          <button class="preset" id="kylePreset">Kyle Moore Law · Georgia</button>
          <button class="preset" id="corridorPreset">SR 515 corridor locations</button>
          <button class="preset" id="cityPreset">Gainesville + Augusta + Columbus</button>
        </div>
        <div class="legend">
          <h3>Rank colors</h3>
          <div class="legend-row"><span class="swatch rank-good"></span> Rank 1–3</div>
          <div class="legend-row"><span class="swatch rank-mid"></span> Rank 4–10</div>
          <div class="legend-row"><span class="swatch rank-low"></span> Rank 11+</div>
          <div class="legend-row"><span class="swatch rank-none"></span> Not found</div>
          <div class="legend-row"><span class="swatch rank-error"></span> Search error</div>
        </div>
      </aside>
    </section>

    <section class="results" id="results">
      <div class="result-head">
        <h2>Visibility results</h2>
        <span class="hint" id="resultCaption"></span>
      </div>
      <div class="metrics" id="metrics"></div>
      <div class="scan-list" id="scanList"></div>
    </section>
  </main>

  <script>
    const $ = id => document.getElementById(id);
    const fields = ["keywords", "locations", "gridSize"];
    let latestResult = null;

    const kyleKeywords = [
      "personal injury lawyer",
      "wrongful death lawyer",
      "car accident lawyer",
      "medical malpractice lawyer",
      "truck accident lawyer",
      "motorcycle accident lawyer",
      "catastrophic injury lawyer",
      "vaccine injury lawyer",
      "product liability lawyer",
      "trial lawyer"
    ].join("\\n");
    const corridorLocations = [
      "Jasper, Georgia",
      "Ellijay, Georgia",
      "Blue Ridge, Georgia",
      "Blairsville, Georgia",
      "Young Harris, Georgia",
      "Hiawassee, Georgia"
    ].join("\\n");
    const cityLocations = [
      "Gainesville, Georgia",
      "Augusta, Georgia",
      "Columbus, Georgia"
    ].join("\\n");

    function lines(id) {
      return $(id).value.split("\\n").map(value => value.trim()).filter(Boolean);
    }

    function parseLocations() {
      return lines("locations").map(line => {
        const parts = line.split("|").map(value => value.trim());
        if (parts.length === 3 && Number.isFinite(Number(parts[1])) && Number.isFinite(Number(parts[2]))) {
          return {name: parts[0], latitude: Number(parts[1]), longitude: Number(parts[2])};
        }
        return {name: line};
      });
    }

    function updateEstimate() {
      const total = lines("keywords").length * lines("locations").length * Number($("gridSize").value) ** 2;
      $("estimate").textContent = `${total.toLocaleString()} coordinate searches`;
    }

    function loadKyle() {
      $("target").value = "Kyle M. Moore, Attorney";
      $("targetType").value = "name";
      $("keywords").value = kyleKeywords;
      $("locations").value = `${corridorLocations}\\n${cityLocations}`;
      $("gridSize").value = "1";
      $("spacing").value = "2";
      $("maxRank").value = "20";
      updateEstimate();
    }

    $("kylePreset").addEventListener("click", loadKyle);
    $("corridorPreset").addEventListener("click", () => {
      $("locations").value = corridorLocations;
      updateEstimate();
    });
    $("cityPreset").addEventListener("click", () => {
      $("locations").value = cityLocations;
      updateEstimate();
    });
    fields.forEach(id => $(id).addEventListener("input", updateEstimate));

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      })[character]);
    }

    function rankClass(point) {
      if (point.error) return "rank-error";
      if (point.rank === null) return "rank-none";
      if (point.rank <= 3) return "rank-good";
      if (point.rank <= 10) return "rank-mid";
      return "rank-low";
    }

    function metric(label, value) {
      return `<div class="metric"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>`;
    }

    function render(data) {
      latestResult = data;
      const summary = data.summary;
      $("metrics").innerHTML = [
        metric("Keyword × location scans", summary.total_scans),
        metric("Coordinate points", summary.total_points),
        metric("Visible points", summary.found_points),
        metric("Best rank", summary.best_rank ?? "—"),
        metric("Errors", summary.error_points)
      ].join("");
      $("resultCaption").textContent = `${data.request.keywords.length} keywords · ${data.request.locations.length} locations`;
      $("scanList").innerHTML = data.scans.map(scan => {
        const result = scan.result;
        const scanSummary = result.summary;
        const columns = result.grid_size;
        const points = result.points.map(point => {
          const label = point.error ? "Error" : point.rank === null ? "—" : `#${point.rank}`;
          return `<div class="point ${rankClass(point)}" title="${escapeHtml(point.error || `${point.latitude}, ${point.longitude}`)}">
            <div>${escapeHtml(label)}<small>${point.latitude.toFixed(3)}, ${point.longitude.toFixed(3)}</small></div>
          </div>`;
        }).join("");
        const visibility = scanSummary.visibility_percent === null ? "—" : `${scanSummary.visibility_percent}%`;
        return `<details>
          <summary>
            <div class="scan-title"><strong>${escapeHtml(result.query)}</strong><span>${escapeHtml(scan.location.name)}</span></div>
            <span class="badge ${scanSummary.found_points ? "" : "zero"}">${visibility} visible</span>
            <span class="badge">${scanSummary.best_rank ? `Best #${scanSummary.best_rank}` : "Not found"}</span>
          </summary>
          <div class="heatmap-wrap"><div class="heatmap" style="grid-template-columns: repeat(${columns}, minmax(68px, 1fr))">${points}</div></div>
        </details>`;
      }).join("");
      $("results").classList.add("visible");
      $("exportButton").hidden = false;
      $("results").scrollIntoView({behavior: "smooth", block: "start"});
    }

    $("runButton").addEventListener("click", async () => {
      $("error").classList.remove("visible");
      $("runButton").disabled = true;
      $("loading").classList.add("visible");
      $("loadingText").textContent = "Resolving locations and scanning Google Maps…";
      const payload = {
        target: $("target").value.trim(),
        target_type: $("targetType").value,
        keywords: lines("keywords"),
        locations: parseLocations(),
        grid_size: Number($("gridSize").value),
        spacing_km: Number($("spacing").value),
        zoom: Number($("zoom").value),
        max_rank: Number($("maxRank").value),
        provider: $("provider").value
      };
      try {
        const response = await fetch("/api/scan", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(payload)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Scan failed.");
        render(data);
      } catch (error) {
        $("error").textContent = error.message;
        $("error").classList.add("visible");
      } finally {
        $("runButton").disabled = false;
        $("loading").classList.remove("visible");
      }
    });

    $("exportButton").addEventListener("click", () => {
      if (!latestResult) return;
      const blob = new Blob([JSON.stringify(latestResult, null, 2)], {type: "application/json"});
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = "maps-visibility-results.json";
      link.click();
      URL.revokeObjectURL(link.href);
    });

    updateEstimate();
  </script>
</body>
</html>
"""
