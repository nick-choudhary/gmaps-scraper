# Interface Context

## CLI

The `gmaps` command provides four subcommands.

### search

```bash
gmaps search "restaurant" --lat 40.71 --lng -74.00 -n 20
gmaps search "restaurant" --lat 40.71 --lng -74.00 -n 20 --enrich
gmaps search "coffee" -o results.json --format json
```

| Flag          | Type    | Default | Description                          |
| ------------- | ------- | ------- | ------------------------------------ |
| `--lat`       | float   | 0       | Center latitude                      |
| `--lng`       | float   | 0       | Center longitude                     |
| `-n`          | int     | 20      | Max results                          |
| `--offset`    | int     | 0       | Pagination offset                    |
| `-o`          | path    | stdout  | Output file                          |
| `--format`    | choice  | text    | text, json, csv                      |
| `--enrich`    | flag    | off     | Enable Phase 2 place details         |
| `--contacts`  | flag    | off     | Extract emails + social URLs from business websites |
| `--cookies`   | string  | none    | Login cookie string or file path     |

### grid

```bash
gmaps grid "restaurant" --bbox 40.55,-74.05,40.90,-73.70 --cell-size 1.5 -n 5000
gmaps grid "hvac" --bbox 40.4,-74.3,40.9,-73.6 --cell-size 0.5 --enrich
```

| Flag           | Type   | Default | Description                          |
| -------------- | ------ | ------- | ------------------------------------ |
| `--bbox`       | string | req     | min_lat,min_lon,max_lat,max_lon      |
| `--cell-size`  | float  | 0.5     | Grid cell size in km                 |
| `--zoom`       | float  | 16.0    | Zoom level (15-17)                   |
| `-n`           | int    | 500     | Max total unique results             |
| `--enrich`     | flag   | off     | Enable Phase 2                       |
| `--contacts`   | flag   | off     | Extract emails + social URLs from business websites |
| `--cookies`    | string | none    | Login cookies                        |

### place

```bash
gmaps place ChIJ123456789 --enrich
```

### reviews

```bash
gmaps reviews "0x89c259a6bcd5e9d1:0x..." --sort newest --max 50
```

## Python API

```python
from gmaps import GMapsClient

# Mode 1: Fast search
async with GMapsClient() as client:
    results = await client.search.places("restaurant", latitude=40.71, longitude=-74.00)
    for place in results.places:
        print(place.to_dict())

# Mode 2: Enriched (no login)
async with GMapsClient(enrich=True) as client:
    results = await client.search.places("restaurant", latitude=40.71, longitude=-74.00)
    for place in results.places:
        await client.enrich(place)
        print(place.review_count)

# Mode 3: With login cookies
async with GMapsClient(enrich=True, login_cookies="SID=abc;HSID=xyz") as client:
    results = await client.search.places("restaurant", latitude=40.71, longitude=-74.00)

# Contact extraction (any mode): visit business websites, extract
# emails + social media URLs (linkedin, facebook, instagram, twitter,
# youtube, tiktok, pinterest, whatsapp, telegram).
# Performance is auto-tuned from batch size — no parameters needed.
async with GMapsClient() as client:
    results = await client.search.places("hvac", latitude=40.71, longitude=-74.00)
    await client.extract_contacts(results.places)  # concurrency/timeout auto-decided
    for place in results.places:
        print(place.emails, place.social_links)

# Lower-level API (no Google client needed):
from gmaps.website import WebsiteContactExtractor
async with WebsiteContactExtractor() as extractor:
    info = await extractor.extract("https://some-business.com")
    print(info.emails, info.social_links)
```

## MCP Server

```json
{
  "mcpServers": {
    "gmaps": {
      "command": "python",
      "args": ["-m", "gmaps.mcp_server"]
    }
  }
}
```

Contact extraction performance (concurrency, per-site timeout, pages per
site) is auto-tuned from the batch size and tightens adaptively at runtime;
there are deliberately no user-facing knobs for it.

Website content fetching (for contact extraction) uses a provider fallback
chain that is auto-detected from the environment — no flags. Order: TinyFish
Fetch (`TINYFISH_API_KEY`) → Firecrawl (`FIRECRAWL_API_KEY`) → proxied HTTP
(`--proxy` / `GMAPS_PROXY` / `HTTPS_PROXY`) → basic direct HTTP (always on).
With nothing configured it is the plain direct fetch, so default behaviour is
unchanged. See `docs/content-fetching.md`.

Three tools: `search`, `grid_search`, `place_details`. `search` and
`grid_search` accept a `contacts` boolean to extract emails + social URLs
from each result's website.

## Output Format

Every place produces grouped JSON via `to_dict()`:

```
{
  "name": "...",
  "place_id": "ChIJ...",
  "hex_id": "0x...",
  "contact": { "phone": "...", "website": "...", "emails": [...], "social_links": {"linkedin": "...", ...}, ... },
  "address": { "full": "...", "street": "...", "city": "...", ... },
  "rating": { "rating": 4.5, "review_count": 120, ... },
  "location": { "latitude": 40.71, "longitude": -74.00 },
  "business": { "categories": [...], "hours": {...}, ... },
  "media": { "thumbnail": "...", "author_photo": "..." },
  "amenities": { "owner": {...}, "menu": {...}, ... }
}
```

Empty values are omitted. `is_ad` only appears when `true`.
