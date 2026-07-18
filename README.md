# gmaps-scraper

**Google Maps scraper** — an async Python library, CLI, and MCP server that extracts
structured **Google Maps business data** (names, addresses, phone numbers, websites,
ratings, reviews, categories, hours) plus **emails and social profiles for lead
generation** — over **pure HTTP**. No official API key, no Google account login, and no
browser automation.

It calls the same internal `pb=` protobuf endpoints the Google Maps web app uses, with
grid / adaptive mini-map search to go beyond the ~120-result-per-view limit, honest
completeness reporting, optional place-details enrichment, and website contact
extraction. Use it for local-business lead generation, market research, and data mining.

**No official API key required. No Google account login required.**

Built with the same reverse-engineering methodology as
[notebooklm-py](https://github.com/teng-lin/notebooklm-py), and the anti-detection
patterns from [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper).

---

## Three Scraping Modes

The library supports three operating modes, from lightweight to full enrichment:

### Mode 1: Phase 1 Only — Search (Default)

```
cookie: none (scraped NID/AEC/SOCS only)
login:  not required
speed:  ~0.5s per search request
cost:   free
fields: ~15 core fields
```

Fast grid search with zero setup. Gets name, address, phone, website, rating,
lat/lng, categories, timezone, borough, neighborhood, quick amenities, and
author photo. **Does NOT get** review_count, description, thumbnail, hours
(structured), about/amenities, photos, popular_times.

### Mode 2: Phase 1 + Phase 2 — Enriched (No Login)

```
cookie: scraped NID/AEC/SOCS
login:  not required
speed:  Phase 1 + ~0.5s per place for details
cost:   free
fields: ~30 fields (all Mode 1 + enrichment)
```

After grid search, fetches place details for each result via
`/maps/preview/place`. Adds: review_count, reviews_per_rating, plus_code,
thumbnail, structured hours, owner info. Still **missing**: description
(editorial summary), photos, about/amenities, popular_times — Google gates
these behind login.

### Mode 3: Phase 1 + Phase 2 — With Login Cookies

```
cookie: full Google account cookies (SID, HSID, SSID, SAPISID, etc.)
login:  required
speed:  same as Mode 2
cost:   free (but uses your Google account)
fields: ~40 fields (everything available)
```

Full enrichment. Adds everything Mode 2 misses: editorial description,
photo gallery, about/amenities sections (accessibility, service options,
crowd, planning), popular_times histograms, and more detailed review data.

### Comparison Table

| Field | Mode 1 (Search) | Mode 2 (+Details, No Login) | Mode 3 (+Details, Login) |
|-------|:---:|:---:|:---:|
| **Identifiers** | | | |
| name | ✅ | ✅ | ✅ |
| place_id | ✅ | ✅ | ✅ |
| hex_id, ftid, data_id | ✅ | ✅ | ✅ |
| **Contact** | | | |
| phone | ✅ | ✅ | ✅ |
| website | ✅ | ✅ | ✅ |
| google_maps_url | ✅ | ✅ | ✅ |
| plus_code | — | ✅ | ✅ |
| **Address** | | | |
| full address | ✅ | ✅ | ✅ |
| street, city, state, postal, country | ✅ | ✅ | ✅ |
| borough, neighborhood | ✅ | ✅ | ✅ |
| **Ratings** | | | |
| rating (stars) | ✅ | ✅ | ✅ |
| review_count | — | ✅ | ✅ |
| reviews_per_rating (1-5 breakdown) | — | ✅ | ✅ |
| reviews_link | ✅ | ✅ | ✅ |
| price_range, price_level | ✅ | ✅ | ✅ |
| **Location** | | | |
| latitude, longitude | ✅ | ✅ | ✅ |
| **Business** | | | |
| categories | ✅ | ✅ | ✅ |
| timezone | ✅ | ✅ | ✅ |
| hours (structured) | — | ✅ | ✅ |
| status (open/closed) | ✅ | ✅ | ✅ |
| description (editorial) | — | — | ✅ |
| popular_times | — | — | ✅ |
| quick_amenities | ✅ | ✅ | ✅ |
| **Media** | | | |
| thumbnail | — | ✅ | ✅ |
| author_photo | ✅ | ✅ | ✅ |
| photos (gallery) | — | — | ✅ |
| images | — | — | ✅ |
| street_view_url | — | — | ✅ |
| **Amenities & Links** | | | |
| about (accessibility, service, etc.) | — | — | ✅ |
| credit_cards | — | — | ✅ |
| reservations | — | — | ✅ |
| order_online | — | — | ✅ |
| menu | — | — | ✅ |
| owner | — | ✅ | ✅ |

---

## Anti-Detection Features

Patterns adapted from [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper):

- **User-Agent rotation** — 6 real browser UAs (Chrome/Firefox/Edge, Windows/macOS), rotated per request
- **Jittered rate limiting** — configurable base delay with ±30% random jitter
- **Jittered exponential backoff** — on 429/5xx, retry with jittered delays
- **Browser header parity** — full `Sec-Fetch-*`, `Sec-Ch-Ua`, `Upgrade-Insecure-Requests`
- **Session freshness tracking** — auto-flag stale sessions (>15min), `refresh_session()` to cycle
- **Random grid cell order** — shuffles cell search order to avoid sequential spatial patterns

---

## How It Works

Google Maps does **not** have an official public API for these operations.
The web app at maps.google.com communicates with Google's servers using a
custom `pb=` (protobuf-encoded) URL parameter format.

This library reverse-engineers that protocol:

1. **Cookie session** — establishes valid NID/AEC/SOCS cookies via consent flow
2. **Query encoding** — builds `pb=` parameters using `!field{type}{value}` notation
3. **HTTP requests** — calls internal endpoints (search, place details, reviews)
4. **Response parsing** — strips anti-XSSI prefix, parses deeply nested JSON arrays

### Two-Phase Architecture

```
Phase 1: Grid Search (/search?tbm=map&pb=...)
  ├─ Divide area into grid cells
  ├─ Search each cell center independently
  ├─ Overcomes Google's ~120 results-per-area limit
  ├─ Returns ~15-20 sparse fields per place
  └─ Randomized cell order (anti-detection)

Phase 2: Place Detail Enrichment (/maps/preview/place?pb=...)  [optional]
  ├─ One request per unique place_id from Phase 1
  ├─ Returns 30-40 enriched fields
  ├─ Works with or without Google login cookies
  └─ Login cookies unlock: description, photos, about, popular_times
```

### Internal Endpoints

| Feature | Endpoint | pb= Opener |
|---------|----------|------------|
| Search | `https://www.google.com/search?tbm=map&pb=...` | `!1s{query}!4m8!1m3!1d{viewport}...` |
| Place details | `https://www.google.com/maps/preview/place?pb=...` | `!1m22!1s{hex_id}!3m12!1m3!1d{viewport}...` |
| Reviews | `https://www.google.com/maps/rpc/listugcposts?pb=...` | `!1m...` |

### Grid Search: Overcoming the 120-Result Limit

Google Maps caps search results at ~120 per area. To get comprehensive coverage:

1. Divide the target area into grid cells (configurable size, default 0.5km)
2. Search each cell center independently
3. Deduplicate with stable Google identifiers
4. Search every planned cell unless interrupted or capped
5. Randomize cell order to avoid detection

### Protocol

Google Maps uses a custom `!field{type}{value}` notation in the `pb=` URL parameter:

```
!1m2!2scoffee shop!3d30.2672!4d-97.7431
```
→ field 1 = message with 2 sub-fields
→ field 2 = string "coffee shop"
→ field 3 = double 30.2672
→ field 4 = double -97.7431

**Place Details pb format** (key insight from reverse-engineering):

```
!1m22                          # opener (m22, not m0!)
!1s{hex_id_with_0x}            # hex_id WITH 0x prefix
!3m12!1m3!1d{viewport}!2d{lng}!3d{lat}
!2m3!1f0!2f0!3f0
!3m2!1i{width}!2i{height}!4f{zoom}
!4m2!3d{center_lat}!4d{center_lng}
!15m4!1m3!1s{hex_id}!4s{ftid}!5s{place_id}!6s{query}
...{50+ feature flags}
```

Responses come back as JSON with `)]}'` anti-XSSI prefix and deeply nested arrays.

---

## Installation

```bash
pip install -e .
```

Or with dev dependencies:

```bash
pip install -e ".[dev]"
```

## Quick Start

### CLI

```bash
# Fast search: write the place naturally (no coordinates required)
gmaps search "coffee shops in Austin TX"

# Comprehensive, boundary-filtered collection from a named location
gmaps collect "chiropractors" --location "Atlanta, Georgia" \
  --enrich --max-contacts 20 -o atlanta-chiropractors.json

# If interrupted, continue from the durable checkpoint
gmaps collect "chiropractors" --location "Atlanta, Georgia" \
  --enrich --max-contacts 20 -o atlanta-chiropractors.json --resume

# Mode 2: Search + enrichment (no login)
gmaps search "coffee shops in Austin TX" --enrich

# Mode 3: Search + enrichment (with login cookies)
gmaps search "coffee shops in Austin TX" --enrich --cookies cookies.json

# Advanced: provide the boundary and cell size yourself
gmaps grid "hvac" --bbox 40.4,-74.3,40.9,-73.6 --cell-size 0.5

# JSON output
gmaps search "hotels in Manhattan" -o results.json

# Attempt contacts for at most 10 eligible business websites
gmaps search "plumbers in Austin TX" -n 50 \
  --max-contacts 10 --format json -o contacts.json
```

`collect` resolves the named area, chooses a grid size unless overridden,
filters spillover results outside the resolved boundary, and writes four files:
the final JSON, incremental JSONL records, an atomic checkpoint, and a run
manifest. The manifest reports `complete`, any incompleteness reasons, cells
processed/failed, duplicates, boundary rejections, and phase counts. A result
cap or a saturated 120-result cell is treated as a completeness warning and
produces `complete: false`. Each record also retains the grid cells where it
was found. Before work starts, the CLI prints planned cells, approximate Google
request volume, and enabled phases.

### Python API

```python
import asyncio
from gmaps import GMapsClient

async def main():
    # Mode 1: Phase 1 only (default)
    async with GMapsClient() as client:
        results = await client.search.places("coffee shops", latitude=30.2672, longitude=-97.7431)
        for place in results.places:
            print(f"{place.name} — {place.rating}★")

    # Mode 2: Phase 1 + Phase 2 (no login)
    async with GMapsClient(enrich=True) as client:
        results = await client.search.places("coffee shops", latitude=30.2672, longitude=-97.7431)
        for place in results.places:
            print(f"{place.name} — {place.rating}★ ({place.review_count} reviews)")

    # Mode 3: With raw login cookies exported from your own Google session
    login_cookies = "SID=...; HSID=...; SSID=..."
    async with GMapsClient(enrich=True, login_cookies=login_cookies) as client:
        results = await client.search.places("coffee shops", latitude=30.2672, longitude=-97.7431)
        for place in results.places:
            print(f"{place.name} — {place.description}")

    # Best-effort contacts from each business's own website
    async with GMapsClient() as client:
        results = await client.search.places("plumbers", latitude=30.2672, longitude=-97.7431)
        await client.extract_contacts(results.places)
        for place in results.places:
            print(place.name, place.emails, place.social_links)

    # Grid search
    from gmaps.grid import BoundingBox
    bbox = BoundingBox(min_lat=40.4, min_lon=-74.3, max_lat=40.9, max_lon=-73.6)
    results = await client.search.grid_search("hvac", bbox, cell_size_km=0.5)

asyncio.run(main())
```

### Grouped JSON Output

Every place produces clean grouped JSON:

```python
place = results.places[0]
print(place.to_dict())
```

```json
{
  "name": "Comunidad Specialty Coffee",
  "place_id": "ChIJE6IviwK1RIYRjsGXkaRQa_c",
  "hex_id": "0x8644b5028b2fa213:0xf76b50a49197c18e",
  "ftid": "/g/11mlg2rrdy",
  "contact": {
    "phone": "(512) 504-0023",
    "website": "https://example.com",
    "emails": ["hello@example.com"],
    "social_links": {
      "linkedin": "https://www.linkedin.com/company/example",
      "instagram": "https://www.instagram.com/example"
    },
    "google_maps_url": "https://www.google.com/maps/place/?q=place_id:ChIJ...",
    "plus_code": "7789+27 Austin, Texas"
  },
  "address": {
    "full": "1008 E 6th St, Austin, TX 78702",
    "street": "1008 E 6th St",
    "city": "Austin",
    "state": "Texas",
    "postal_code": "78702",
    "country": "US",
    "borough": "East Austin",
    "neighborhood": "East Austin"
  },
  "rating": {
    "rating": 4.9,
    "review_count": 92,
    "reviews_per_rating": {"5": 89, "1": 3},
    "price_range": "$"
  },
  "location": {"latitude": 30.265, "longitude": -97.732},
  "business": {
    "categories": ["Coffee shop"],
    "hours": {"Wednesday": ["8AM-4PM"], ...},
    "timezone": "America/Chicago",
    "quick_amenities": ["Dogs allowed"]
  },
  "media": {
    "thumbnail": "https://lh3.googleusercontent.com/...",
    "author_photo": "https://lh6.googleusercontent.com/..."
  }
}
```

## Email and Social Contact Extraction

Contact extraction is opt-in with `--contacts` or `GMapsClient.extract_contacts()`.
It visits each business's own website and extracts contacts that are present in
the returned page content.

Use `--max-contacts N` to cap the number of eligible business websites attempted.
This does not limit discovered Google Maps businesses or mean "find N emails."
Every retained record receives a contact status; deferred records use
`not_attempted_limit`. Extracted emails and social profiles include their source
page in `contact_sources`.

Email handling includes plain-text addresses, `mailto:` links, and Cloudflare
`data-cfemail` deobfuscation. Social link handling covers LinkedIn, Facebook,
Instagram, Twitter/X, YouTube, TikTok, Pinterest, WhatsApp, and Telegram while
filtering common share/intent links and tracking parameters. Email precision
filters URL-encoded artifacts, placeholder domains, and unrelated custom domains;
common consumer mail providers remain allowed.

The default fetcher is direct HTTP. When configured, the fallback chain can use
TinyFish, Firecrawl, or a proxy before falling back to direct HTTP:

```bash
export TINYFISH_API_KEY=tf-...
export FIRECRAWL_API_KEY=fc-...
gmaps grid "hvac" --bbox 40.4,-74.3,40.9,-73.6 --contacts -o leads.json
```

This feature is best-effort: it does not guess addresses, search external
directories or social networks, bypass logins, or guarantee a contact for every
business. JavaScript-only and heavily protected sites may require a managed
fetcher. Model-assisted extraction is available only through the opt-in Python
adapter in `gmaps.contacts`; no hosted model provider is enabled by default.

See [Content Fetching](docs/content-fetching.md) for provider configuration.

## Local Development and Verification

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"

python -m pytest tests -q
python -m ruff check src tests
python -m ruff format --check src tests
python -c "import gmaps; print(gmaps.__version__)"
gmaps --help
```

Focused contact checks:

```bash
python -m pytest tests/test_website.py tests/test_contacts.py -v
```

Live smoke test (uses the network and Google's current internal endpoint):

```bash
gmaps search "coffee" --lat 30.2672 --lng -97.7431 -n 3 -o smoke-search.json
gmaps search "plumber" --lat 30.2672 --lng -97.7431 -n 3 \
  --contacts -o smoke-contacts.json
```

---

## Requirements

- Python 3.10+
- `httpx` — HTTP client
- `click` — CLI framework
- `rich` — terminal formatting
- No Google account needed (for Modes 1 and 2)

## Rate Limiting & Anti-Detection

Built-in protection against rate limiting and IP blocking:

| Feature | Config | Default |
|---------|--------|---------|
| Min request interval | `min_delay` | 1.5s |
| Jitter | `jitter_pct` | ±30% |
| Max retries | `max_retries` | 3 |
| Retry backoff | exponential | 2s base |
| UA rotation pool | fixed | 6 UAs |
| Session freshness | `is_session_stale` | 15min |
| Grid cell order | shuffled | always |

For heavy scraping, use residential proxies:

```python
client = GMapsClient(proxy="http://residential-proxy:8080")
```

## Caveats

- This is an **unofficial** client using reverse-engineered protocols
- Google may change their internal API format at any time
- Response field indices may shift with Google Maps updates
- Using proxies is recommended for high-volume scraping
- Login cookies expire — refresh periodically
- Respect Google's Terms of Service

## Project Structure

```
gmaps-scraper/
├── src/gmaps/
│   ├── __init__.py          # Package entry
│   ├── client.py            # Main GMapsClient
│   ├── transport.py         # HTTP transport + anti-detection
│   ├── exceptions.py        # Error hierarchy
│   ├── cli.py               # CLI interface
│   ├── website.py           # Email + social-profile extraction
│   ├── fetchers.py          # TinyFish/Firecrawl/proxy/direct fallback chain
│   ├── validation.py        # Parser-drift health checks
│   ├── schema.py            # Swappable parser field schema
│   ├── healing.py           # Opt-in deterministic schema repair
│   ├── control.py           # Opt-in adaptive grid/rate controller
│   ├── identity.py          # Opt-in captured browser identity replay
│   ├── contacts.py          # Regex/model contact-extractor adapters
│   ├── registry.py          # Provider registry
│   ├── evaluation.py        # Provider evaluation harness
│   ├── _search.py           # Search API + grid search
│   ├── _places.py           # Places API (Phase 2 enrichment)
│   ├── _reviews.py          # Reviews API
│   ├── _auth/
│   │   ├── __init__.py
│   │   └── session.py       # Cookie session management
│   ├── grid.py              # Grid subdivision for area coverage
│   └── rpc/
│       ├── __init__.py
│       ├── types.py         # Constants, enums
│       ├── encoder.py       # Request parameter encoding
│       ├── decoder.py       # Response decoding (anti-XSSI)
│       ├── pb_encoder.py    # pb= protobuf format encoding
│       └── parser.py        # Response field extraction (58 fields)
├── scripts/                 # Test scripts
├── docs/                    # Protocol, fetching, and implementation guides
├── pyproject.toml
└── README.md
```

## References

- [notebooklm-py](https://github.com/teng-lin/notebooklm-py) — similar reverse-engineering approach for NotebookLM
- [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper) — Go scraper, anti-detection patterns, field indices
- [GoogleMapsCollector](https://github.com/promisingcoder/GoogleMapsCollector) — HTTP-based GMaps scraper with protobuf decoding
- [SerpApi blog](https://serpapi.com/blog/how-we-reverse-engineered-google-maps-pagination/) — pagination reverse-engineering deep-dive
