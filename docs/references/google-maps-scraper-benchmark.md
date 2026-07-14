# Google Maps scraper benchmark and product requirements

Status: living reference baseline  
First recorded: 2026-07-14  
Purpose: keep product and implementation decisions traceable to supplied references,
upstream source code, and reproduced local evidence. New references should be added
here before they change the implementation plan.

## Product objective

The project must serve two first-class users:

1. A person who wants to describe what and where to scrape in ordinary language.
2. An agent that must discover capabilities, estimate work, run non-interactively,
   monitor progress, recover from interruption, and interpret completeness without
   guessing.

The pure-HTTP architecture is a project constraint. Coordinates, map grids, protobuf
fields, and zoom calculations are implementation details unless an advanced user
explicitly opts into them.

## Source registry

All observations below are pinned or dated so upstream changes do not silently alter
our rationale.

| Reference | Version inspected | Role |
|---|---|---|
| [Apify: Google Places API limits](https://blog.apify.com/google-places-api-limits/) | Article published 2025-09-23; read 2026-07-14 | Explains the 120-result map limitation and product-level area strategies |
| [Apify Google Maps Scraper](https://apify.com/compass/crawler-google-places) | Actor build `0.14.713`; metadata modified 2026-07-14 | Current input, geolocation, enrichment, output, and automation reference |
| [Apify video: overcoming the 120-place limit](https://www.youtube.com/watch?v=op9MabaZNZo) | Published 2023-01-12; English captions inspected | Visual/operational explanation of grids, zoom, and location modes |
| [gosom/google-maps-scraper](https://github.com/gosom/google-maps-scraper/tree/0ef302ecc72a8872d5dac68cbbeab78800f80fdd) | `0ef302e`, version 1.16.3 | Mature open-source scraper and strongest agent-workflow reference |
| [promisingcoder/GoogleMapsCollector](https://github.com/promisingcoder/GoogleMapsCollector/tree/d1edca99fa8f6a7812385bbf2dd3d430aac84055) | `d1edca9` | Pure-HTTP Python reference for human-readable areas, grids, filtering, and incremental output |
| Local Atlanta chiropractor validation | Run 2026-07-14 | Reproduced evidence for correctness, performance, boundary, contact, and durability gaps |

## What the references establish

### Apify product and Actor

Observed:

- A single Google Maps view exposes at most about 120 listings. Comprehensive
  collection therefore requires multiple smaller map searches, deduplication, and
  automatic zoom/grid handling.
- Beginner input separates **what** to find from **where** to search. Apify accepts a
  free-text location and also exposes structured country/state/county/city/postal-code
  fields. Its documentation explicitly warns that combining location and search term
  into one field does not overcome the 120-place limitation.
- Apify resolves human-readable locations with OpenStreetMap/Nominatim. Advanced users
  can supply GeoJSON Point/circle, Polygon, or MultiPolygon areas.
- The grid and zoom are generated internally. Users may override advanced geospatial
  settings, but they are not the primary interface.
- Place discovery, additional place details, reviews, images, company contacts,
  business leads, email verification, and social-profile enrichment are distinct
  capabilities with distinct costs and limits. This validates treating contacts as a
  separately bounded phase.
- Output is broad and structured: identifiers, address, coordinates, phone, website,
  categories, hours, prices, reviews, images, amenities, booking/menu/order links,
  owner data, contact enrichment, and specialized hotel/restaurant fields.
- The Actor can run through a UI, API, clients, schedules, webhooks, and integrations.
  It produces machine-readable datasets and a visual map.

Implications for this project:

- Natural-language location must be primary; bbox/coordinates remain advanced.
- A comprehensive run needs an independently resolved area, not just a text query.
- Completeness is a grid/area property and must be reported explicitly.
- Expensive enrichment phases require independent deterministic budgets.
- Human CLI, Python API, MCP/agent interface, and machine-readable output should expose
  the same concepts.

### Apify video

The video independently reinforces the article's mechanism:

1. Large Google Maps views stop at about 120 results.
2. Higher zoom displays more local pins.
3. A large area is split into smaller maps, usually searched around zoom 16.
4. Results from all mini-maps are combined and deduplicated.
5. Search-term plus city/country is the straightforward mode; URL and custom
   circle/polygon/multipolygon modes are advanced alternatives.

The video is useful as an operational explanation, but the current Actor documentation
and source references above take precedence when behavior differs.

### gosom/google-maps-scraper

Observed:

- Human input uses natural-language query lines such as `cafes in Peristeri, Greece`.
  Coordinates are optional advanced controls. Docker is the recommended setup; a Web
  UI and REST API are also available.
- Its [agent skill](https://github.com/gosom/google-maps-scraper/blob/0ef302ecc72a8872d5dac68cbbeab78800f80fdd/skills/google-maps-scraper/SKILL.md)
  is the strongest agent UX reference: it turns intent into location-specific queries,
  splits large cities into neighborhoods, estimates runtime, monitors work, previews a
  small result subset, retains full output, and offers next actions.
- Comprehensive mode creates every query/cell job and shares a deduper. Its grid uses
  latitude-aware longitude spacing and can estimate cell count.
- Grid results are explicitly **not clipped to the bounding box**. Strict radius
  filtering exists in another mode or must be performed afterward. This matches the
  out-of-boundary behavior reproduced locally and is a warning, not a pattern to copy.
- It documents 36 output fields, including rich place details, reviews, media,
  amenities, owner data, and emails.
- Email extraction is optional and uses `mailto:` plus a page-wide parser. It has no
  maximum contact/email flag and no core social-profile output.
- It uses Playwright/browser automation and recommends Docker. Those architectural
  choices conflict with this project's pure-HTTP core and must not be copied.

### promisingcoder/GoogleMapsCollector

Observed:

- The primary interface is human-readable: `gmaps-collect-v2 "Manhattan, New York"
  "lawyers"`. Area and category are separate positional inputs.
- It resolves the area through Nominatim, generates a grid from the returned boundary,
  and applies a separate filter boundary with a configurable default buffer.
- Cell size is automatically scaled from small local areas through countries. Optional
  subdivision mode resolves neighborhoods/districts and falls back to an ordinary grid.
- It deduplicates by place ID and hex ID, retries cells, reports rate/ETA, filters
  outside results, appends JSONL records immediately, and updates CSV incrementally.
- Its output has fewer place fields than this project or gosom. It has review limits but
  no website email/social extraction or contact-attempt limit.
- Its advertised resume behavior has correctness gaps and must not be copied blindly:
  checkpoints retain IDs/counters but not full business records; resumed final output
  can overwrite prior records; interrupt-time saving is not reliably implemented; and
  modulo-based checkpoint thresholds can be skipped by batch-size jumps.
- Its CLI documentation requires a separately running API server, while its Python API
  defaults to direct HTTP. That split is confusing and is a caution for human and agent
  usability.

## Reproduced local evidence

### Natural-language search

Command:

```powershell
gmaps search "chiropractors in Atlanta, Georgia" -n 20 --enrich --contacts --format json
```

Observed:

- 20 unique businesses, all with address, phone, website, and chiropractor category.
- 11 businesses had at least one extracted email; 10 had social profiles.
- Social platforms included Facebook, Instagram, LinkedIn, X/Twitter, YouTube, and
  TikTok.
- Contact false positives included `%20inman@thetaylordocs.com`,
  `email@youremail.com`, and `free.estimates@fullcoveragellc.com`.

Conclusion: ordinary text-query search works and must remain unchanged. Contact
precision needs improvement.

### Comprehensive Atlanta grid

Internal diagnostic area: `33.64,-84.55,33.89,-84.29`, 5 km cells.

Observed:

- A 500-place cap stopped after only 20 of 30 planned cells. The output did not make
  incompleteness sufficiently explicit.
- Raising the cap to 1,000 allowed the complete loop to finish in 6m41s with 554 unique
  records. Only 334 were inside the target box and 286 were both inside and explicitly
  categorized as chiropractors.
- 220 returned records were outside the target boundary.
- The CLI reported 28 cells because it counted only cells represented in retained
  results, not all processed cells. That summary is ambiguous.
- Combining discovery, enrichment, and contacts exceeded a 20-minute diagnostic limit.
  No partial output survived because the CLI writes only after all phases finish.
- Long runs expose no useful default progress information.

Conclusion: discovery works, but current `grid --enrich --contacts` is not a trustworthy
or recoverable complete-run product.

## Requirements baseline

These are requirements derived from the user's objective and the evidence above. They
are not yet an implementation claim.

### Human usability

- Keep `gmaps search "chiropractors in Atlanta"` as the fast, simple path.
- Comprehensive mode must accept separate human-readable **search term** and
  **location**. Coordinates/bbox remain available as advanced overrides.
- Estimate and display planned cells, expected request scale, and enabled enrichment
  phases before work begins.
- Avoid mandatory Docker, browsers, servers, accounts, or API keys for the default path.

Candidate interface (command name remains an open decision):

```powershell
gmaps collect "chiropractors" --location "Atlanta, Georgia" --max-results 1000
gmaps collect "chiropractors" --location "Atlanta, Georgia" --max-contacts 20
gmaps grid "chiropractor" --bbox 33.64,-84.55,33.89,-84.29  # advanced
```

### Agent usability

- One discoverable non-interactive CLI with stable `--help`, deterministic defaults,
  documented exit codes, and no hidden prompts.
- Equivalent Python and MCP inputs using the same vocabulary.
- A machine-readable run manifest containing at least:
  target query/location, resolved geometry, resolution provider/confidence, cells
  planned/completed/failed, discovered/retained/outside/duplicate counts, result-cap
  status, enrichment/contact attempted/succeeded/failed/skipped counts, elapsed time,
  output paths, and `complete: true|false` with reasons.
- Human progress on stderr and clean data on stdout/file so agents can parse results.
- Preview only a bounded sample while preserving the complete machine-readable file.
- A project agent guide/skill with workload estimation, monitoring, recovery, and
  follow-up examples.

### Completeness and boundaries

- Resolve a named location to a real boundary where possible; expose the resolved
  geometry in the manifest.
- Filter returned businesses to that boundary (with an explicit configurable buffer,
  if used) and count rejected outside results.
- Search every planned cell unless interrupted or capped.
- A result cap is a budget, not proof of completeness. Hitting it must set
  `complete: false` and explain that unsearched cells may remain.
- Report processed cells separately from cells that contributed unique records.
- Deduplicate with stable identifiers and retain provenance (`found_in` cells/areas).

### Durable execution

- Persist discovered records incrementally (JSONL is the leading reference pattern).
- Save atomic run state after completed cells and on graceful interruption.
- Resume by loading durable business records as well as IDs/counters.
- Build final JSON/CSV from the complete durable record set, never only current-memory
  records.
- Discovery results must survive even if enrichment or contact crawling later fails.

### Contact and social enrichment

- `--max-contacts N` means: attempt website contact enrichment for at most the first
  `N` deduplicated eligible businesses. It does **not** limit discovered businesses and
  does not mean “stop after finding N email addresses.”
- Selection order must be deterministic and recorded.
- Later records remain in output with a structured status such as
  `not_attempted_limit`.
- If needed, a separate `--max-emails-per-business` option can bound values per place;
  one flag must not carry both meanings.
- Store contact status, provenance/source URL, errors, and attempted timestamp.
- Reject malformed/URL-encoded addresses, known placeholders, disposable/test values,
  and unrelated custom domains while retaining legitimate consumer-address domains.
- Keep email and social results separately structured.

## Patterns to adopt and cautions

Adopt:

- Apify: separate what/where input, hidden grid/zoom, rich structured output, modular
  enrichment, API/automation parity.
- GoogleMapsCollector: named-area resolution, automatic cell sizing, strict filtering,
  subdivision fallback, incremental JSONL, retries, ETA/progress.
- gosom: task-oriented agent workflow, workload estimation, bounded preview, monitoring,
  full-output retention, explicit deeper-search option.
- This project: pure HTTP, async-first library, grouped JSON, existing email/social
  extraction, and backward-compatible fast search.

Do not copy:

- Required browser/Docker architecture.
- Unfiltered grid output presented as area-complete.
- Checkpoints that persist IDs without the associated records.
- A CLI that secretly requires a separate server.
- One overloaded limit for places, contact attempts, and emails.

## Open decisions before implementation

1. Keep the command name `grid`, add a clearer `collect`, or provide `collect` as the
   friendly command with `grid` retained as an advanced alias.
2. Use Nominatim directly for named-area resolution, support a provider interface from
   day one, or implement another resolver. Provider usage policy, caching, attribution,
   and failure behavior must be reviewed before adoption.
3. Default boundary buffer and whether it changes by locality type.
4. Automatic cell-size policy and pre-run workload thresholds for cities, states, and
   countries.
5. Deterministic ordering used by `--max-contacts` (discovery order, rating/reviews, or
   another documented policy).
6. Default contact budget: off unless requested, a small safe default, or inherited from
   an explicit `--contacts` flag.
7. Scope and packaging of the agent-facing skill/MCP changes.

## Implementation gate

No large workflow change should begin until the supplied reference set is complete
enough for the user to approve the public seams above. Once approved, implementation
should proceed in vertical regression-tested slices:

1. Location resolution and manifest contract.
2. Boundary filtering and honest completeness accounting.
3. Incremental output, checkpoint, interruption, and resume.
4. Progress and workload estimation.
5. Bounded staged enrichment with `--max-contacts`.
6. Contact precision and provenance.
7. Python/MCP/agent documentation parity and live regression testing.
