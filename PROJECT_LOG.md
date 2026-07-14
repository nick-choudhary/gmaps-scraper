# Project Log — gmaps-scraper

A chronological record of key decisions, architecture changes, and findings.
Curated for handoff: only consequential decisions, not every edit.

---

## 2026-06-29 — Project Start

**Goal:** Build a Google Maps scraper Python library using the same reverse-engineering methodology as [notebooklm-py](https://github.com/teng-lin/notebooklm-py).

### Initial Research

- Studied notebooklm-py's architecture: batchexecute RPC, encoder/decoder, auth, transport
- Discovered Google Maps uses a **custom `pb=` protobuf-in-URL format** — NOT batchexecute (key difference from NotebookLM)
- Identified three endpoints: `/search?tbm=map` (search), `/maps/preview/place` (details), `/maps/rpc/listugcposts` (reviews)

### Decision: Pure HTTP, No Browser Automation
Chose pure HTTP (httpx) over Playwright/Selenium for the core scraper. Rationale: 10-50x faster, 10x less memory, no browser dependencies. Trade-off: more fragile if Google changes API format.

---

## 2026-06-30 — Core Implementation

### Cookie Session (No Login Required)
Google Maps search endpoint works with just scraped cookies: `NID`, `AEC`, `SOCS` (consent flow: google.com → consent.google.com → google.com/maps). No Google account login needed for Phase 1 or Phase 2.

### pb= Parameter Format (Search)
Reverse-engineered from `promisingcoder/GoogleMapsCollector` and verified live:
```
https://www.google.com/search?tbm=map&pb=!1s{query}!4m8!1m3!1d{viewport}!2d{lng}!3d{lat}!3m2!1i1024!2i768!4f{zoom}!7i{count}!8i{offset}!10b1!12m50!1m5!18b1!30b1!31m1!1b1!34e1!2m4!5m1!6e2!20e3!39b1!6m23!49b1!63m0!66b1!74i{radius}...(30+ more flags)
```

### Response Structure
- `data[0][1]` = results array; `[0]` = metadata (skip); `[1:]` = businesses
- Each business at `entry[14]`; 260+ sub-fields available

### Key Field Indices Discovered
| Index | Field |
|-------|-------|
| `[11]` | name |
| `[18]` | full address |
| `[7][0]` | website |
| `[78]` | place_id (ChIJ...) |
| `[10]` | hex_id (0x...) |
| `[89]` | ftid (/g/...) |
| `[4][7]` | star rating |
| `[4][8]` | review count |
| `[9][2-3]` | latitude, longitude |
| `[13]` | categories |
| `[178][0][0]` | phone |
| `[30]` | timezone |
| `[14]` | neighborhood |
| `[157]` | author photo URL |
| `[183][1]` | structured address (7 components) |

### Grid Search: Overcoming 120-Result Limit
Implemented grid subdivision (`grid.py`): divide target area into cells (default 0.5km), search each cell center independently, deduplicate by `place_id`, stop when exhausted.

**First live test:** 1,001 HVAC businesses in NYC, 749 with websites, 114 seconds.

---

## 2026-07-01 — Production Grade

### Anti-Detection Layer (from gosom/google-maps-scraper)
Studied gosom's Go source and ported these patterns:
- **UA rotation**: 6 real browser User-Agents (Chrome/Firefox/Edge, Windows/macOS)
- **Jittered rate limiting**: `min_delay ± 30%` random on every request
- **Jittered exponential backoff**: on 429/5xx, retry with jitter
- **Random grid cell order**: shuffle cells before search (breaks sequential spatial pattern)
- **Session freshness**: auto-flag stale sessions at 15 minutes

### Parser V2: 47 Fields, Grouped JSON
Complete rewrite of `rpc/parser.py`:
- `ParsedPlace` dataclass with 47 fields
- `to_dict()` produces clean grouped JSON: `identifiers`, `contact`, `address`, `rating`, `location`, `business`, `media`, `amenities`
- Helper extraction functions: `_extract_rating_new`, `_extract_hours_new`, `_extract_media_new`, `_extract_about_new`, `_extract_complete_address_new`

### Place Details Endpoint Reverse-Engineered
**pb= format for `/maps/preview/place`:**
```
!1m22!1s{hex_id_with_0x}!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}!2m3!1f0!2f0!3f0!3m2!1i1024!2i768!4f13.1!4m2!3d{lat}!4d{lng}!15m4!1m3!1s{hex_id}!4s{ftid}!5s{place_id}!6s{query}...{50+ feature flags}
```

Key insight: `!1m22` opener (not `!1m0`), hex_id WITH `0x` prefix, ALL THREE IDs (hex_id + ftid + place_id) in `!15m4` section, viewport ~900km.

**Works without login** — returns review_count, hours (structured), plus_code, thumbnail, owner. With login cookies: also gets description, photos, about/amenities, popular_times.

### Critical Bug Fix: Accept-Encoding
`Accept-Encoding: gzip, deflate, br` caused empty responses because httpx can't decode brotli without extra deps. Fix: remove `br` from Accept-Encoding header.

### Three Operating Modes
| Mode | Config | Login | Fields |
|------|--------|-------|--------|
| 1 (default) | `GMapsClient()` | None | ~15 core fields |
| 2 | `GMapsClient(enrich=True)` | None | ~30 fields |
| 3 | `GMapsClient(enrich=True, login_cookies="...")` | Google account | ~40 fields |

### Hours Parser Fix
Google's `[203]` format: `[203][0]` contains per-day entries: `['Wednesday', 3, [date], [['8AM-4PM', [[8],[16]]]], 0, 1]`. Parser now correctly extracts day name from `[0]` and time string from `[3][*][0]`.

---

## 2026-07-02 — GitHub Readiness & Polish

### Test Suite
64 tests across 4 files: `test_parser.py` (field extraction, grouped JSON), `test_grid.py` (bbox, cells), `test_transport.py` (UA rotation, jitter, headers), `test_client.py` (mode configuration, error handling). All passing in 0.25s.

### CLI
`gmaps search/enrich/grid/place/reviews` with `--enrich`, `--cookies`, `--format json/csv/text`, `--bbox`, `--cell-size` flags.

### MCP Server
`mcp_server.py` exposes three tools (search, grid_search, place_details) to AI agents via Model Context Protocol. Works with Claude Desktop, Cursor. Falls back to JSON-RPC over stdio if `mcp` package not installed.

### Proxy Guidance for 100k+ Results

**Without proxy (local):**
- ~1,000 results per ~2 minutes is safe
- 100k results would take ~3 hours at default rate (1.5s delay ± 30%)
- Google will likely rate-limit (429) after ~5,000-10,000 requests from one IP

**With proxy (recommended for 100k+):**
- Residential proxies: $0.50-3.00/GB, rotate IPs every 10-50 requests
- Datacenter proxies: cheaper but more easily detected
- gosom recommends their sponsor [scrap.io](https://scrap.io) for country-scale
- Our library supports proxies via `GMapsClient(proxy="http://user:pass@host:port")`
- Best practice: 1 proxy per ~50 concurrent requests, rotate every 100-200 requests

### Decisions Deferred
- **Email extraction** (gosom's `-email` flag): would visit each business's website and regex for emails. Not yet implemented.
- **Rust port**: Would give 3-5x throughput and 10x memory reduction. Not justified until Python throughput becomes the bottleneck.
- **PostgreSQL/S3 output**: gosom has 6 output backends; we have JSON/CSV. Add when needed.
- **Web UI / REST API**: gosom has full SaaS mode. Not in scope for a library.

---

## Architecture Summary

```
┌─────────────────────────────────────────┐
│           GMapsClient (client.py)        │
│  ┌───────────┐  ┌────────────────────┐  │
│  │ CookieSession │ │  HTTPTransport      │  │
│  │ (_auth/)    │ │  (transport.py)     │  │
│  │ NID/AEC/SOCS│ │  UA rotation+jitter │  │
│  └──────┬──────┘ └─────────┬──────────┘  │
│         │                   │             │
│  ┌──────┴───────────────────┴──────────┐ │
│  │            SearchAPI                 │ │
│  │  places() / place_details()          │ │
│  │  grid_search() / places_paginated()  │ │
│  └────────────────┬────────────────────┘ │
│                   │                       │
│  ┌────────────────┴────────────────────┐ │
│  │          Parser (parser.py)          │ │
│  │  47 fields → 8 grouped JSON objects  │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `rpc/parser.py` | 667 | Field extraction (47 fields, 8 groups) |
| `_search.py` | 422 | Search + place_details + grid_search |
| `transport.py` | 452 | HTTP with anti-detection |
| `client.py` | 195 | GMapsClient orchestrator (3 modes) |
| `cli.py` | 270 | CLI: search/grid/place/reviews |
| `mcp_server.py` | 250 | MCP server for AI agents |
| `grid.py` | 163 | BoundingBox, GridCell, generate_cells |
| `_auth/session.py` | 272 | Cookie consent flow |
| `rpc/decoder.py` | 360 | Anti-XSSI, JSON/HTML response handling |
