# FAQ

Honest answers to the questions people actually ask before using this scraper.
Each answer marks what works **today** vs. what is on the **roadmap**, so you
know exactly what you're getting.

- [1. What does "recall" mean, and how do I know a scrape is complete?](#1-what-does-recall-mean-and-how-do-i-know-a-scrape-is-complete)
- [2. How does the scraping actually work? (grids and mini-maps)](#2-how-does-the-scraping-actually-work-grids-and-mini-maps)
- [3. How is this different from gosom and GoogleMapsCollector? Which finds more?](#3-how-is-this-different-from-gosom-and-googlemapscollector-which-finds-more)
- [4. Do all the fields come in one request, or is there a second pass?](#4-do-all-the-fields-come-in-one-request-or-is-there-a-second-pass)
- [5. Does it use Google's geocoding API or OpenStreetMap? Why?](#5-does-it-use-googles-geocoding-api-or-openstreetmap-why)
- [6. How does it handle a ZIP code vs a city vs a state?](#6-how-does-it-handle-a-zip-code-vs-a-city-vs-a-state)
- [7. Can it scrape a whole country (e.g. "all restaurants in the US")?](#7-can-it-scrape-a-whole-country-eg-all-restaurants-in-the-us)
- [8. If I enable enrichment, does it waste requests on duplicates?](#8-if-i-enable-enrichment-does-it-waste-requests-on-duplicates)
- [9. Can I plug in my own B2B enrichment providers / API keys?](#9-can-i-plug-in-my-own-b2b-enrichment-providers--api-keys)
- [10. What machine / infrastructure do I need?](#10-what-machine--infrastructure-do-i-need)
- [11. Roadmap](#11-roadmap)
- [12. Is this legal? Responsible-use note](#12-is-this-legal-responsible-use-note)

---

## 1. What does "recall" mean, and how do I know a scrape is complete?

**Recall** is the completeness score: of all the businesses that truly exist in
your area, what fraction did the scrape actually capture.

> recall = (real businesses captured) ÷ (all real businesses that exist)

Google never tells you the true total, so we approximate it by pooling every
business ID found across many runs of an area (the "union floor"). On the primary
test (Nashville chiropractors) the current engine reaches **~0.94 recall**.

You are never left guessing whether a run finished. Every run writes a
`*.manifest.json` scorecard with `complete: true/false` and explicit reasons
(e.g. `saturated_cells`, `cells_unprocessed`, `result_cap_reached`). If a dense
area hit Google's per-view limit and couldn't be fully subdivided, the run says
so honestly rather than pretending it got everything.

---

## 2. How does the scraping actually work? (grids and mini-maps)

Pure HTTP — no browser, no login, no API key. Step by step, for
`collect "chiropractors" --location "Nashville, Tennessee"`:

1. **Resolve the location** via OpenStreetMap/Nominatim → a center, a bounding
   box, and the real **city polygon**.
2. **Tile the box into a grid** of cells sized automatically for the area.
3. **Drop cells** whose center falls outside the city polygon (skip the
   countryside in the corners of the rectangle), then shuffle the rest.
4. **Search each cell** as a "mini-map": one Google Maps search at the cell
   center, 20 results per page.
5. **Filter each result** in order: no coordinates → drop; outside the city
   polygon → drop (counted `outside_boundary`); inside the city but too far from
   this cell → drop (a neighbor cell owns it); already seen → drop as a
   duplicate; otherwise **keep**.
6. **Paginate** a cell while pages keep yielding new businesses (up to 6 pages),
   with early-stops when a page adds nothing new.
7. **Subdivide** any cell that genuinely maxes out: split into four smaller cells
   and zoom in, recovering businesses a coarse view drowns out.
8. Everything is **deduplicated globally** by place ID and written continuously
   to `.jsonl` + a resumable checkpoint.

**Why grids at all?** A single Google Maps view returns at most ~120 results.
Tiling + zoom + dedup is the only way to exceed that over pure HTTP.

---

## 3. How is this different from gosom and GoogleMapsCollector? Which finds more?

The discovery method is ~90% the same across all three, because all pure-HTTP
scrapers tile the same Google endpoint. **Google's ranking is the shared ceiling
— no grid trick punches through it — so on raw business count, expect a tie.**

Where the differences actually are:

| | this scraper | GoogleMapsCollector | gosom |
|---|---|---|---|
| businesses found (recall) | ~tie | ~tie | ~tie |
| density handling | adaptive paginate + subdivide-and-zoom | plain grid (+ optional subdivide) | neighborhood split |
| **contact emails + socials** | ✅ 9 platforms, budgeted | ❌ none | basic emails only |
| output richness | 58 fields | fewer | 36 fields |
| **honest completeness report** | ✅ | ❌ | ❌ |
| runs with no browser/Docker/key | ✅ pure HTTP | ✅ | ❌ needs Playwright + Docker |
| maturity / country-scale infra | newer | newer | ✅ most mature |

**Bottom line:** for "businesses in a city, with contact info, and a trustworthy
completeness answer," this tool is the better *product* — same recall ceiling,
but richer records, real contact extraction, and an honest manifest. For raw
"dump an entire country behind proxies today," gosom's maturity currently wins
(see the roadmap for how we close that).

---

## 4. Do all the fields come in one request, or is there a second pass?

**Two tiers — this matters for speed and cost:**

- **Tier 1 — the search request (free with discovery, ~20 businesses per
  request):** name, IDs, **phone, website, rating, review count, categories, full
  + structured address, coordinates**, neighborhood, timezone. This is the bulk of
  what most buyers want.
- **Tier 2 — enrichment (`--enrich`, one extra request *per business*):** hours,
  plus_code, thumbnail, images, owner, description, popular_times, review
  breakdowns, menu/reservation/order links, status.
- **Tier 3 — contacts (`--contacts`, visits each website):** emails + social
  profiles.

So core commercial fields arrive in one pass; depth costs one request each. You
only pay for the depth you ask for.

---

## 5. Does it use Google's geocoding API or OpenStreetMap? Why?

**OpenStreetMap / Nominatim — deliberately, and it's the better choice here.**

Google's Geocoding API returns only **rectangles** (viewport/bounds), never
polygons, and needs a paid, ToS-restricted API key. Our precise out-of-city
filtering depends on the **real city polygon** (Nashville is a Polygon, Austin a
MultiPolygon) — something only OSM provides for free. Switching to Google would
*weaken* the boundary filter and add cost and a key requirement.

(Roadmap: an optional Google/US-Census resolver for users who want it, but
Nominatim stays the default.)

---

## 6. How does it handle a ZIP code vs a city vs a state?

It auto-sizes the grid, and today it is **well-tuned for city/metro scale**.
Measured behavior (cell/view = cell size vs the ~6.6 km a single search sees):

| location | cell size | cells | cell/view | today |
|---|---|---|---|---|
| ZIP (~4×4 km) | 0.5 km | 56 | 0.1× | over-gridded (correct, but ~10× more requests than needed) |
| city (Nashville) | 5 km | 100 | 0.8× | **sweet spot** ✅ |
| metro (Atlanta) | 8 km | 49 | 1.2× | well matched ✅ |
| state (Tennessee) | 50 km | 64 | 7.5× | under-covered (rural gaps missed) |

**City and metro are the sweet spot and give ~0.94 recall.** A ZIP works fine but
fires more requests than necessary; a whole state under-covers. Fixing both is the
**top roadmap item** (anchor cell size to the viewport and let the cell *count*
scale with area). See the roadmap.

---

## 7. Can it scrape a whole country (e.g. "all restaurants in the US")?

**Not today — this is the north-star roadmap goal, and it's an infrastructure
project, not a setting.** Honestly:

- The continental US at working resolution is **hundreds of thousands of cells**;
  a single IP would be **blocked** long before finishing.
- Doing it *effectively* needs four things working together: (a) hierarchical
  decomposition (country → states → populated places, gridding only where people
  live), (b) a **rotating proxy pool** (real cost), (c) **concurrency**, and (d) a
  **database backend** so memory stays flat.

All four are on the roadmap, and the scaling fix in item 6 is the foundation they
build on. It's achievable and would be a genuine differentiator — but it depends
on proxy budget and reliability engineering, not just cleverness. We won't claim
it works until it's measured.

---

## 8. If I enable enrichment, does it waste requests on duplicates?

**No.** Discovery runs to completion and **fully deduplicates first**; only then
does enrichment loop over the **unique** businesses, enriching each **exactly
once** (and skipping already-enriched ones on resume). A business found in five
overlapping cells is enriched a single time. The order is
discover → dedup → enrich → contacts, precisely so no enrichment request is spent
on a duplicate.

---

## 9. Can I plug in my own B2B enrichment providers / API keys?

**Not yet as a built-in, but the architecture is designed for it** and it's a
priority roadmap item. Today, contact enrichment works by visiting the business
**website** and extracting emails + social profiles.

The codebase already uses a **provider-chain pattern** (content fetchers try
TinyFish → Firecrawl → proxy → basic, first success wins, each keyed by its own
env API key). Adding B2B enrichers (Hunter, Apollo, Clearbit, Snov, …) that take a
**domain or company name** and return contacts fits the *same* seam: a provider
chain with fallback, multiple API keys round-robined, and a spend cap like the
existing `--max-contacts`. So it's plug-and-play by design, not a rewrite.

Design notes we'll honor: match by **domain** (reliable) over company name
(fuzzy, needs a confidence threshold); enforce a per-run budget; and respect
privacy law for personal contact data.

---

## 10. What machine / infrastructure do I need?

Because it's **pure HTTP and I/O-bound, your bottleneck is Google's rate limit
and your proxy budget — not CPU or RAM.** (This is a big edge over browser-based
scrapers that need gigabytes per worker.)

| scale | RAM | CPU | proxies | backend |
|---|---|---|---|---|
| ZIP / city (works today) | < 1 GB | 1 core (any laptop) | none | JSON / CSV |
| large metro (10k–50k) | 2–4 GB | 2 cores | 1 optional | JSONL |
| state (100k+) *(roadmap)* | 4–8 GB | 2–4 cores | pool of 5–20 | Postgres |
| country (1M+) *(roadmap)* | 8–16 GB* | 4–8 cores | dozens–hundreds ($) | Postgres / S3 |

\* RAM only grows if all records are held in memory; the database backend streams
them out and keeps memory flat, which is why it's required at country scale.

---

## 11. Roadmap

Ordered; the scaling item is the current top priority because it's the gateway to
everything larger.

1. **Geographic scale decomposition (top priority).**
   - Anchor cell size to the search viewport (~5–6 km) so ZIPs stop over-gridding
     and coverage stays even — low risk, proven ratio, verified on the benchmark
     harness before shipping.
   - A **scale guard** that estimates requests/time and asks for confirmation
     before very large runs.
   - **Hierarchical decomposition** (country → states → populated places) — the
     basis for country-scale.
2. **Rotating proxy pool** — the real unlock for scale; single-IP tolerance is
   limited.
3. **Concurrency** — many mini-maps in parallel, bounded by proxies/rate limits.
4. **Docker + REST API** — spin up a container, POST a job, GET results. The tiny
   pure-HTTP image (~150 MB) is a deployment advantage.
5. **PostgreSQL / S3 output backends** — for 100k+ scale and flat memory.
6. **Pluggable B2B enrichment chain** — Hunter/Apollo/Clearbit/etc. by
   domain/company, multiple keys, budgeted (see Q9).
7. **Semantic query expansion** — search multiple category phrasings and union
   them to find businesses a single query's ranking buries.
8. **Thin Web UI** — a form over the REST API plus a results table and map.
9. **Contact-moat deepening** — email verification (MX/SMTP), phone-from-website,
   more platforms.

---

## 12. Is this legal? Responsible-use note

Scraping Google Maps is against Google's Terms of Service and lives in a legal
gray area; you are responsible for how you use it. B2B and personal contact data
carry privacy-law obligations (GDPR/CCPA and similar). Use reasonable rates,
respect robots and local law, and treat scraped personal data lawfully. This tool
reports completeness honestly and does not attempt to evade detection; it is
intended for legitimate research and lead-generation use, not abuse.
