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
- **Email extraction** (gosom's `-email` flag): implemented later as the opt-in website contact pass, including social-profile URLs. Precision hardening remains active work.
- **Rust port**: Would give 3-5x throughput and 10x memory reduction. Not justified until Python throughput becomes the bottleneck.
- **PostgreSQL/S3 output**: gosom has 6 output backends; we have JSON/CSV. Add when needed.
- **Web UI / REST API**: gosom has full SaaS mode. Not in scope for a library.

---

## 2026-07-14 — Complete-Scrape Validation and Reference Baseline

### Live findings

- Natural-language search (`chiropractors in Atlanta, Georgia`) works without coordinates.
- A 30-cell Atlanta grid returned 554 unique places when allowed to finish, but 220 were outside the requested bbox.
- A 500-place cap stopped after 20/30 cells without a sufficiently explicit incomplete-run contract.
- Combined grid + enrichment + contacts exceeded 20 minutes and lost all partial output on timeout.
- Default long-run progress is inadequate; the final cell summary counts contributing cells rather than processed cells.
- Contact extraction produced useful emails/socials but also obvious false positives (`%20...`, placeholder, and unrelated-domain addresses).

### Research baseline

- Added `docs/references/google-maps-scraper-benchmark.md` as the living, source-pinned benchmark.
- References currently include Apify's article/current Actor/video, gosom at `0ef302e`, GoogleMapsCollector at `d1edca9`, and local Atlanta evidence.
- Leading combined direction: Apify's separate what/where UX, GoogleMapsCollector's named-area resolution/filtering/incremental records, gosom's agent workflow, and this project's pure-HTTP/contact architecture.
- Added requirement: `--max-contacts N` limits contact-enrichment attempts, not discovered places or total emails.
- The user confirmed the supplied references were the complete baseline; the public
  behavior seams were then implemented in regression-tested slices.

### Complete-scrape corrections implemented

- Added `gmaps collect "query" --location "Place"` as the primary comprehensive UX;
  ordinary natural-language `search` remains unchanged and bbox/grid inputs remain
  available for advanced callers.
- Nominatim now resolves the named location to a persisted display name, provider ID,
  bbox, center, and Polygon/MultiPolygon. Results are filtered against exact geometry
  where available, with bbox fallback.
- Added automatic grid sizing, stable cell keys, full-record JSONL checkpoints, atomic
  snapshots/state, resume, and a machine-readable manifest.
- Completeness is explicit: a result cap, failed cells, or unprocessed cells produce
  `complete: false` with reasons. Saturated 120-result cells are also flagged rather
  than presented as complete. Duplicate, provenance, boundary rejection, cell, and
  phase counts are reported separately.
- Added `--max-contacts` to `search`, `grid`, and `collect`. It limits eligible website
  attempts, uses deterministic review/name/ID ordering, and leaves all map records in
  output with structured statuses.
- Hardened email precision against URL-encoded artifacts, placeholder domains, and
  unrelated custom domains. Email/social values now retain their source page.
- Added MCP `collect` parity for agents and updated `AGENTS.md` to lead with human place
  names rather than latitude/longitude.

### Live smoke evidence

Command:

```powershell
gmaps collect "chiropractors" --location "Atlanta, Georgia" --cell-size 100 `
  --max-results 10 --enrich --max-contacts 2 -o C:\tmp\gmaps-atlanta-smoke.json
```

Observed: 10 retained and enriched businesses, exactly two website attempts, valid
emails plus Facebook/Instagram profiles with source pages, no errors, and an honest
`incomplete` manifest with `result_cap_reached`.

Comprehensive 5 km Atlanta run:

- 25/25 cells completed in 18m41s with zero cell failures and no result cap.
- 368 in-bbox records retained and enriched; 305 explicitly had a chiropractic
  category. Google spillover filtering rejected 328 records and deduplication removed
  2,303 repeats.
- Exactly 20 websites were attempted: five returned emails and seven returned social
  profiles. The reproduced malformed/placeholder/unrelated-domain emails were absent.
- The first live ordering used review count alone and exposed related high-review
  businesses in the contact budget. Ordering was corrected to prefer query matches in
  name/categories, then review count/name/ID.

Final exact-geometry smoke:

- Nominatim returned Atlanta as a `MultiPolygon`; it was persisted and used for result
  filtering.
- A three-contact budget selected businesses categorized as chiropractors. Two yielded
  validated email/social data and one protected franchise site failed transparently.
- The 20-result cap correctly produced `incomplete: result_cap_reached`.

### Apify mini-map gap and duplicate diagnosis

The first comprehensive collector implemented the outer grid pattern but not the
efficient mini-map scheduling policy described by Apify. In particular,
`grid_search()` paginates every fixed cell toward Google's approximate 120-result
area cap. The 5 km Atlanta run consequently processed 2,999 raw result occurrences
to retain 368 unique in-boundary places: 2,303 duplicate encounters and 328
out-of-boundary results. Nearly every cell reached the 120-result ceiling.

Page-level diagnostics showed that the first tested Atlanta cell supplied 82 of 91
unique results found across four cells. The following 18 page requests added only
nine globally new places. Reducing the encoded radius and viewport did not
materially change the ranking, so those fields must not be treated as a strict
mini-map boundary. Stopping after two duplicate-only pages is also unsafe: later
pages occasionally added a unique result.

Apify's documented technique is: keep the search term and location separate,
resolve the location geometry, split it into mini-maps, choose a dense zoom for
each mini-map (usually 16), scrape each mini-map, and combine the results. The
article explicitly notes that grids require one page per mini-map and can therefore
be slow. It does not prescribe six pages from every fixed cell. The gosom reference
provides a concrete open-source variant: small grid cells, one 20-result map page,
and strict client-side spatial filtering.

Production correction decided:

- Preserve the pure-HTTP transport, parser, enrichment, contact extraction,
  checkpoints, natural-language location UX, and canonical output.
- Replace fixed-cell deep pagination in comprehensive collection with adaptive
  mini-map discovery: request one page, accept results only within the target
  geography, and subdivide/zoom a cell when that page is full.
- Stop sparse leaf cells immediately; continue splitting dense cells until the page
  is no longer full or an explicit depth/minimum-size safety limit is reached.
- Record parent/leaf cells, saturation, subdivision, raw occurrences, duplicates,
  boundary rejections, and unique yield per request in the manifest. Any saturated
  terminal leaf must keep `complete: false`.
- Benchmark the new scheduler against the 305 explicitly chiropractic-category
  Atlanta baseline. The acceptance criterion is at least 2x unique relevant places
  per discovery request without losing that baseline, with no false completeness
  claim. This is a measured target, not an assumed result.

#### Adaptive mini-map experiment rejected

The proposed scheduler was implemented behind an experimental search method and
temporarily connected to `collect` for live validation. It used gosom's one-page
request shape, a full-page subdivision signal, and exact location-boundary
filtering. Two interpretations were tested:

1. Strict per-mini-map footprint filtering retained only 28 of 900 raw occurrences
   in a bounded Atlanta test; 846 were outside the nominal mini-map. This proved
   that Google's returned ranking is much broader than the assumed cell square.
2. Boundary-only retention preserved recall locally, but the city-wide one-level
   A/B still underperformed the existing collector: 125 requests, 2,500 raw
   occurrences, 171 retained businesses, 151 explicitly chiropractic-category,
   1,153 duplicate encounters, and 1,176 outside Atlanta. The prior baseline
   retained 368 businesses, including 305 explicitly chiropractic-category, at
   roughly the same discovery-request scale.

Conclusion: the tested mini-map variant reduces duplicate counts only by losing
about half the relevant records. It fails the acceptance criterion and must not
replace production collection. `collect` was restored to the existing validated
grid/pagination path. The experiment demonstrates that Apify's public article
describes the high-level grid/zoom concept but does not disclose enough scheduling
or request-protocol detail to reproduce its completeness claims directly.

A separate Windows durability issue was reproduced when a reader briefly held the
manifest destination open during atomic replacement. Atomic snapshot/manifest
writes now retry transient `PermissionError` failures with bounded exponential
backoff; this correction is independent of the rejected discovery experiment.

#### Current Maps UI protocol capture and production correction (2026-07-14)

The rejected mini-map experiment was using the project's older search request
shape. A diagnostic browser capture (not retained as a runtime dependency) proved
that the current Google Maps UI sends a materially different pure-HTTP
`/search?tbm=map` payload:

- A visible `16z` map emits internal search zoom `13.1`.
- Organic results use a current envelope whose place records are structurally
  `[metadata, place_data]` in a top-level result container (observed at index 64),
  rather than only the legacy `data[0][1][n][14]` envelope.
- Offset pages are wrapped as `{"c": 0, "d": ")]}'\n[...]"}/*""*/`.
- The request works through a fresh project HTTP session without browser cookies,
  browser headers, Playwright, or an API key. Browser automation was diagnostic
  only; production remains pure HTTP.

The decoder now unwraps current offset responses, the parser accepts both current
and legacy envelopes, and the request builder uses the verified current UI field
set while preserving the user-facing zoom scale. A live downtown Atlanta CLI
search returned 20 businesses with a 2.67 km median distance and 4.18 km maximum
distance. Two Atlanta centers returned 66 unique businesses from 78 unique
first-two-page records, with 12 overlapping IDs (31.6% overlap).

Complete 72-cell Atlanta scheduler benchmarks, all error-free:

| Policy | Requests | Raw | Retained | Chiropractic category | Duplicates | Outside |
|---|---:|---:|---:|---:|---:|---:|
| One page per cell | 72 | 1,440 | 203 | 190 | 1,024 | 213 |
| Two pages per cell | 144 | 2,880 | 270 | 244 | 2,071 | 539 |
| Stop immediately at <=1 new/page | 124 | 2,478 | 322 | 286 | 1,595 | 561 |
| Stop at zero globally new/page | 167 | 3,338 | 381 | 330 | 2,145 | 812 |
| Same policy, four-page ceiling | 154 | 3,079 | 334 | 295 | 2,056 | 689 |
| Two consecutive <=1-new pages | 209 | 4,178 | 396 | 339 | 2,780 | 1,002 |

The 305 explicitly chiropractic-category baseline is the recall floor. One/two
fixed pages, the aggressive low-yield stop, and the four-page ceiling were rejected
because they fell below it. The two-strike policy was rejected because it exceeded
the old duplicate and request cost. Production now uses the only tested policy that
clears the floor without regressing duplicate count: paginate a mini-map while each
page contributes at least one globally new in-boundary business, and stop that cell
on the first zero-new page, retaining the six-page safety ceiling.

Compared with the old full run, the selected policy increased explicitly relevant
coverage from 305 to 330, reduced duplicate encounters from 2,303 to 2,145, and
reduced duplicate share from 76.8% (2,303/2,999) to 64.3% (2,145/3,338). It used
167 requests versus roughly 150 actual old discovery requests. This is a measured
improvement, not a claim that duplicates can be eliminated: overlapping mini-maps
necessarily repeat businesses, and the manifest continues to expose exact raw,
duplicate, boundary, request, saturation, and completeness counters.
Discovery request, raw-occurrence, duplicate, boundary-rejection, and saturation
counters are stored in the checkpoint and restored on `--resume`, so a resumed
manifest reports the whole run rather than only the final process.

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
│  │  58 fields → 8 grouped JSON objects  │ │
│  └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

## Key Files Reference

| File | Lines | Purpose |
|------|-------|---------|
| `rpc/parser.py` | grouped field extraction (58 fields, 8 groups) |
| `_search.py` | 422 | Search + place_details + grid_search |
| `transport.py` | 452 | HTTP with anti-detection |
| `client.py` | 195 | GMapsClient orchestrator (3 modes) |
| `cli.py` | 270 | CLI: search/grid/place/reviews |
| `mcp_server.py` | 250 | MCP server for AI agents |
| `grid.py` | 163 | BoundingBox, GridCell, generate_cells |
| `_auth/session.py` | 272 | Cookie consent flow |
| `rpc/decoder.py` | 360 | Anti-XSSI, JSON/HTML response handling |
