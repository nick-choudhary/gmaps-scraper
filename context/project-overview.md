# gmaps-scraper

## Overview

An unofficial Python library that scrapes Google Maps business data — names, addresses, phone numbers, websites, ratings, reviews, hours, coordinates — using reverse-engineered internal Google Maps APIs. No official API key required. No browser automation. Pure HTTP with the `pb=` protobuf URL parameter protocol. Built for lead generation, market research, and competitive analysis at scale.

## Goals

1. Scrape up to 10,000+ unique businesses per city via grid search with zero setup (no login, no API key, no proxy needed for moderate volume)
2. Extract 49 fields per business across 8 grouped JSON categories, matching or exceeding gosom/google-maps-scraper field coverage
3. Provide three interfaces: CLI (`gmaps`), Python API (`GMapsClient`), and MCP server (for AI agents like Claude/Cursor)
4. Operate in three modes: fast search only, enriched (no login), or full (with login cookies for gated fields)

## Core User Flow

1. User provides a search query (e.g., "restaurant") and a geographic area (bounding box or lat/lng)
2. Library divides the area into grid cells (mini-maps) at zoom level 16 for maximum pin density
3. Each cell is searched via `/search?tbm=map&pb=...` with pagination through all ~120 results per cell
4. Results are deduplicated by `place_id` across all cells
5. Optional Phase 2 enrichment fetches `/maps/preview/place?pb=...` for each business to add review_count, hours, plus_code, thumbnail, owner
6. Output as clean grouped JSON with 8 categories: identifiers, contact, address, rating, location, business, media, amenities

## Features

### Scraping

- Text search with location bias (lat/lng)
- Grid search with configurable cell size and zoom
- Pagination through all ~120 results per cell
- Place detail enrichment (Phase 2) for richer fields
- Randomized cell order (anti-detection)
- Cookie consent flow (NID/AEC/SOCS) — no login needed

### Anti-Detection

- User-Agent rotation (6 real browser UAs)
- Jittered rate limiting (±30% random)
- Jittered exponential backoff on 429/5xx
- Session freshness tracking (15 min threshold)
- Proper browser header parity (Sec-Fetch-*, Accept)

### Output

- Grouped JSON with 49 fields across 8 groups
- CSV and text output via CLI
- `to_dict()` on every ParsedPlace for clean serialization
- `ScraperStats` tracker: request count, success rate, throughput, error log

### Interfaces

- CLI: `gmaps search/grid/place/reviews` with `--enrich`, `--cookies`, `--format` flags
- Python API: `GMapsClient` async context manager with three modes
- MCP server: three tools (`search`, `grid_search`, `place_details`) for AI agents

## Scope

### In Scope

- Pure HTTP scraping (no Selenium/Playwright for core functionality)
- All Google Maps business categories (restaurants, HVAC, hotels, etc.)
- Anti-detection measures (UA rotation, jitter, cell shuffle)
- Grouped JSON output with comprehensive field extraction
- CLI, Python API, and MCP server interfaces

### Out of Scope

- Browser automation (Playwright/Selenium) — we use pure HTTP by design
- Official Google Maps Platform API integration — different product
- Web UI / SaaS platform — gosom has this; we're a library
- Email extraction from business websites — planned but not yet implemented
- Real-time monitoring or dashboard — not a server product
- Country-scale pre-indexed database — scrap.io's model, not ours

## Success Criteria

1. `pip install -e .` works and `gmaps search "test" --lat 30.27 --lng -97.74 -n 1` returns results
2. Grid search of NYC restaurants yields 5,000+ unique businesses in under 15 minutes with 0 errors
3. `pytest tests/ -v` shows 64+ passing tests
4. Place detail enrichment (Mode 2) adds review_count, hours, plus_code without login cookies
5. MCP server responds to `search` and `grid_search` tool calls from Claude Desktop
