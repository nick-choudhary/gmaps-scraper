# AGENTS.md — gmaps-scraper

> Guide for AI agents (Claude, Cursor, Copilot, etc.) working on this codebase.
> Read this first before making any changes.

## What This Project Is

A Python library that scrapes Google Maps business data using reverse-engineered
internal APIs. No official API key. No browser automation. Pure HTTP with the
`pb=` protobuf URL parameter protocol.

**If you're an AI agent and a user asks you to:**
- "Find coffee shops in Austin" → use `gmaps search "coffee shops in Austin"`
- "Comprehensively scrape chiropractors in Atlanta" → use `gmaps collect "chiropractors" --location "Atlanta, Georgia" -o atlanta.json`
- "Scrape at most 20 business websites for contacts" → add `--max-contacts 20`
- Use `gmaps grid ... --bbox ...` only when an advanced caller already has an exact boundary.
- "Get details for this place" → use `gmaps place ChIJ... --enrich`
- "Modify the scraper" → read the sections below

## Before You Start

### Quick Health Check

```bash
cd gmaps-scraper
python -m pytest tests/ -q          # should show 266 passed
python -c "import gmaps; print('OK')" # should print OK
```

If tests fail or import breaks, **stop and investigate before making changes.**

### Understand the Architecture (2-minute read)

```
GMapsClient (client.py)          ← entry point, 3 modes
  ├── CookieSession (_auth/)     ← NID/AEC/SOCS consent flow
  ├── HTTPTransport (transport.py) ← UA rotation, jitter, retries
  ├── SearchAPI (_search.py)     ← Phase 1 search + Phase 2 details
  │     ├── places()             ← /search?tbm=map
  │     ├── place_details()      ← /maps/preview/place
  │     └── grid_search()        ← grid subdivision for area coverage
  └── Parser (rpc/parser.py)     ← 58 fields → 8 grouped JSON objects
```

Three operating modes:
1. **Default**: Fast search only (~15 fields, no login)
2. **Enrich**: `GMapsClient(enrich=True)` → search + place details (~30 fields)
3. **Login**: `GMapsClient(enrich=True, login_cookies="...")` → full data (~40 fields)

## Key Knowledge

### Google's pb= Format

Google Maps uses a custom URL parameter format (NOT standard protobuf):
```
!1m2!2scoffee!3d30.2672!4d-97.7431
```
- `!` separates fields
- Number+letter = type+length marker (`m`=message, `s`=string, `d`=double, `i`=integer, `f`=float, `b`=boolean, `e`=enum)

**Search pb** starts with `!1s{query}!4m8!1m3!1d{viewport}...`
**Place details pb** starts with `!1m22!1s{hex_id}!3m12!...!15m4!1m3!1s{hex_id}!4s{ftid}!5s{place_id}!6s{query}...`

### Response Structure

Responses are JSON arrays with anti-XSSI prefix `)]}'`:
```
data[0][1]     → results array
data[0][1][0]  → metadata (SKIP THIS)
data[0][1][1:] → business entries
entry[14]      → place data (260+ sub-fields)
```

### Critical Field Indices

These change when Google updates their API. Last verified: 2026-07-01.

| Index | Field | Notes |
|-------|-------|-------|
| `[11]` | name | |
| `[18]` | full address | |
| `[7][0]` | website | |
| `[78]` | place_id | ChIJ... format |
| `[10]` | hex_id | 0x... format |
| `[89]` | ftid | /g/... format |
| `[4][7]` | star rating | |
| `[4][8]` | review count | |
| `[9][2]`, `[9][3]` | lat, lng | |
| `[13]` | categories | list |
| `[178][0][0]` | phone | |
| `[30]` | timezone | |
| `[14]` | neighborhood | |
| `[157]` | author photo URL | |
| `[183][1]` | structured address | 7 components: borough, street, city, postal, state, country |
| `[203][0]` | structured hours | Per-day entries: `['Wednesday', 3, [date], [['8AM-4PM', [[8],[16]]]], ...]` |
| `[175][3]` | reviews per rating | [1-star, 2-star, 3-star, 4-star, 5-star] counts |
| `[100][1]` | about/amenities | Sections like "Credit cards", "Service options" |
| `[72][0][*][6][0]` | thumbnail URL | |
| `[88]` | quick amenities | Fast preview list |

### When Google Changes the API (and they will)

1. Run a live search and inspect raw response:
   ```python
   import httpx, asyncio
   from gmaps._search import _build_search_url
   from gmaps.rpc.decoder import decode_response

   async def check():
       c = httpx.AsyncClient(headers={'User-Agent':'Mozilla/5.0 Chrome/131'}, follow_redirects=True)
       await c.get('https://www.google.com/')
       await c.get('https://consent.google.com/ml?continue=https://www.google.com/maps&gl=US&hl=en')
       await c.get('https://www.google.com/maps')
       path = _build_search_url('coffee', 30.2672, -97.7431, 3).replace('https://www.google.com', '')
       r = await c.get(f'https://www.google.com{path}')
       data = decode_response(r.text, 'json')
       pd = data[0][1][1][14]  # first business place_data
       # Check field indices here
       print(f"name at [11]: {pd[11]}")
       print(f"rating at [4][7]: {pd[4][7] if len(pd[4])>7 else 'MISSING'}")
       await c.aclose()

   asyncio.run(check())
   ```
2. Update indices in `rpc/parser.py` constants
3. Update tests in `tests/test_parser.py` mock data
4. Run `pytest tests/ -v` to verify

## Common Tasks

### Add a New Field
1. Find the index in the raw response (see inspection script above)
2. Add field to `ParsedPlace` dataclass in `rpc/parser.py`
3. Add extraction in the appropriate `_extract_*` helper or inline in `parse_search_response`
4. Add to `to_dict()` output groups
5. Write a test

### Add a CLI Flag
1. Add the `@click.option` in `cli.py`
2. Pass it through to `GMapsClient` or the relevant API call
3. Test: `gmaps search "test" --lat 30.27 --lng -97.74 -n 1`

### Fix a Bug
1. Reproduce with a minimal test case
2. Add a test that reproduces the bug
3. Fix the code
4. Verify the test passes

## What NOT to Do

- **Don't add Selenium/Playwright** for core scraping. We use pure HTTP by design.
- **Don't add `br` (brotli) to Accept-Encoding**. httpx can't decode it without extra deps, causing silent failures (empty responses).
- **Don't use `Sec-Fetch-Dest: document`** in transport headers. Google returns HTML pages instead of JSON. Must use `empty`/`cors`.
- **Don't hardcode field indices** in multiple places. Use the constants at the top of `parser.py`.
- **Don't skip the metadata entry** at `data[0][1][0]`. It's NOT a business — parsing starts at index 1.

## Project Conventions

- **Python 3.10+** — use `X | None` not `Optional[X]`
- **Async-first** — all network calls are `async def`
- **`to_dict()`** — the canonical JSON output format. Never expose raw dataclass `__dict__`.
- **Grouped JSON** — output is always in 8 groups: identifiers, contact, address, rating, location, business, media, amenities
- **Tests are synchronous** where possible (no network needed). Use mock data structures matching live response format.

## File Map

| File | What's Inside |
|------|--------------|
| `src/gmaps/client.py` | `GMapsClient` — main entry, 3 modes, `enrich()` method |
| `src/gmaps/_search.py` | `SearchAPI` — search, place_details, grid_search, pagination |
| `src/gmaps/rpc/parser.py` | `ParsedPlace` (58 fields), all extraction logic, `to_dict()` |
| `src/gmaps/rpc/decoder.py` | Anti-XSSI stripping, JSON/HTML detection |
| `src/gmaps/transport.py` | HTTP client, UA rotation, jitter, retries |
| `src/gmaps/grid.py` | `BoundingBox`, `GridCell`, `generate_cells()` |
| `src/gmaps/_auth/session.py` | Cookie consent flow (NID/AEC/SOCS) |
| `src/gmaps/cli.py` | CLI: search, grid, place, reviews commands |
| `src/gmaps/mcp_server.py` | MCP server for AI agent integration |
| `tests/` | 64 tests: parser, grid, transport, client |
| `PROJECT_LOG.md` | Decision log — read this for context on why things are the way they are |
