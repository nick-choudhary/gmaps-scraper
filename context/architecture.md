# Architecture Context

## Stack

| Layer       | Technology                  | Role                                          |
| ----------- | --------------------------- | --------------------------------------------- |
| Runtime     | Python 3.10+                | Async-first, type hints, dataclasses          |
| HTTP        | httpx                       | Async HTTP client, cookie jar, follow redirects |
| CLI         | click + rich                | Command-line interface with JSON/CSV/text output |
| MCP         | mcp (optional)              | Model Context Protocol server for AI agents   |
| Build       | hatchling                   | PEP 621 build backend, wheel + editable installs |
| Testing     | pytest + pytest-asyncio     | 64 unit tests, no network required             |

## System Boundaries

- `src/gmaps/client.py` — `GMapsClient` orchestrator. Owns lifecycle (cookie session + transport + API objects). Three modes: default, enrich, login.
- `src/gmaps/_search.py` — `SearchAPI`. Owns search, place details, grid search, pagination. Builds pb= URLs, calls transport, returns parsed results.
- `src/gmaps/rpc/parser.py` — `ParsedPlace` dataclass (49 fields) + all extraction logic. Pure functions, no I/O. Field indices as module constants.
- `src/gmaps/rpc/decoder.py` — Anti-XSSI stripping, JSON/HTML response detection, blocked/auth page detection.
- `src/gmaps/transport.py` — `HTTPTransport`. Owns UA rotation, jittered rate limiting, retry logic, cookie injection. The only module that touches the network.
- `src/gmaps/grid.py` — `BoundingBox`, `GridCell`, `generate_cells()`. Pure geometry, no I/O.
- `src/gmaps/_auth/session.py` — `CookieSession`. Owns the consent flow (google.com → consent → maps) and cookie persistence.
- `src/gmaps/stats.py` — `ScraperStats`. Accumulates metrics during scraping runs.
- `src/gmaps/cli.py` — Click commands: `search`, `grid`, `place`, `reviews`.
- `src/gmaps/mcp_server.py` — MCP server exposing `search`, `grid_search`, `place_details` tools.

## Data Flow

```
User → GMapsClient → SearchAPI → _build_search_url()
                                         ↓
                                    HTTPTransport.get() → Google Maps
                                         ↓
                                    decoder.decode_response()
                                         ↓
                                    parser.parse_search_response()
                                         ↓
                                    ParsedPlace.to_dict() → JSON
```

Phase 2 enrichment adds:
```
ParsedPlace → SearchAPI.place_details() → /maps/preview/place
                    ↓
           parser.parse_place_details_response() → merge onto ParsedPlace
```

## Storage Model

- **Cookies**: In-memory `httpx.Cookies` jar, optionally persisted to `~/.gmaps_scraper/cookies.json`
- **Results**: In-memory `list[ParsedPlace]`, serialized to JSON/CSV files by CLI
- **No database**: This is a library, not a server. No persistent state between runs.

## Auth and Access Model

- **Mode 1 (default)**: Scraped cookies only (NID/AEC/SOCS via consent flow). No login. ~15 core fields.
- **Mode 2 (enrich)**: Same scraped cookies + place details endpoint. No login. ~30 fields.
- **Mode 3 (login)**: User provides Google account cookies (SID/HSID/SSID/SAPISID). ~40 fields including description, photos, about, popular_times.

## Invariants

1. **Never use `br` (brotli) in Accept-Encoding** — httpx can't decode it without extra deps, causing silent empty responses
2. **Never use `Sec-Fetch-Dest: document`** in API calls — Google returns HTML instead of JSON. Must use `empty`/`cors`.
3. **Never stop grid search early** based on low-yield cells — randomized cell order means the next cell may be in an uncovered area
4. **`data[0][1][0]` is metadata, not a business** — parsing starts at index 1
5. **All field indices are in `parser.py` constants** — never hardcode indices in multiple files
6. **`to_dict()` is the canonical output** — never expose raw dataclass `__dict__`
7. **Zoom 16 is the optimal density level** — lower zoom shows fewer pins, higher shows too few results per cell
