# Progress Tracker

Update this file after every meaningful implementation change.

## Current Phase

- **Production-ready** — core scraping, enrichment, CLI, MCP server, tests all working

## Current Goal

- Feature parity with gosom/google-maps-scraper (minus extended review pagination)

## Completed

### Core Engine (2026-06-29 to 2026-07-01)
- [x] Cookie consent flow (NID/AEC/SOCS, no login)
- [x] Search API (`/search?tbm=map&pb=...`) with verified pb= format
- [x] Response decoder (anti-XSSI, JSON/HTML detection, blocked page detection)
- [x] Parser with 58 fields, 8 grouped JSON output categories
- [x] Grid subdivision (`BoundingBox`, `GridCell`, `generate_cells()`)
- [x] Pagination within cells (`places_paginated`, 6 pages × 20 = 120 per cell)

### Anti-Detection (2026-07-01)
- [x] UA rotation pool (6 real browser UAs)
- [x] Jittered rate limiting (min_delay ± 30%)
- [x] Jittered exponential backoff on retries
- [x] Randomized grid cell order
- [x] Session freshness tracking (15 min threshold)
- [x] Proper Sec-Fetch headers (empty/cors, not document/navigate)
- [x] Accept-Encoding without brotli (critical fix)

### Place Details Enrichment (2026-07-01)
- [x] Phase 2 endpoint `/maps/preview/place` with verified `!1m22!1s{hex_id}...` pb format
- [x] Works with scraped-only cookies (no login needed for review_count, hours, plus_code, thumbnail, owner)
- [x] Login cookie support for gated fields (description, images, about, popular_times)
- [x] Hours parser for `[203][0]` day-entry format
- [x] Thumbnail from `[72][0][*][6][0]`
- [x] Images from `[171]` category blocks

### Interfaces (2026-07-01 to 2026-07-02)
- [x] CLI: `gmaps search/grid/place/reviews` with `--enrich`, `--cookies`, `--format` flags
- [x] Python API: `GMapsClient` with three modes (default, enrich, login)
- [x] MCP server: `search`, `grid_search`, `place_details` tools

### Quality & Packaging (2026-07-02)
- [x] 235 pytest tests across parser, grid, transport, client, contacts, drift safety,
  self-healing, adaptive control, identity, registry, evaluation, and fetcher modules
- [x] `pip install -e .` working with hatchling
- [x] GitHub-ready: LICENSE, CONTRIBUTING.md, AGENTS.md, CLAUDE.md, CI/CD
- [x] ScraperStats: progress tracking, error collection, throughput metrics
- [x] Six-file context methodology (CLAUDE.md + context/)

### Scale Tests (2026-07-02)
- [x] 1,001 HVAC businesses in NYC (114s, grid v1)
- [x] 2,133 restaurants in NYC (4.8 min, grid v1 with early-exit bug)
- [x] 5,000 restaurants in NYC (~12 min, grid v2 with pagination + no early exit)

### Production-Readiness Audit Fixes (2026-07-07)
- [x] `GridCell` made `frozen=True` (was unhashable — CLI `grid` command crashed with TypeError on `set()` at end of every run)
- [x] MCP server startup fixed: `mcp_types.InitializationOptions()` → `server.create_initialization_options()` (crashed on launch with mcp installed); removed `sys.path.insert(0, ".")` hack
- [x] `language` now propagates to search URL (`hl`/`gl` were hard-coded `en`/`us` in `_build_search_url` — `--lang` flag had no effect on search)
- [x] `zoom` now propagates through `places_paginated` → `grid_search` paginate path (CLI `--zoom` was silently ignored in default paginate mode)
- [x] Removed `br` from `_auth/session.py` BROWSER_HEADERS (same silent-empty-response risk as the transport.py critical fix)
- [x] `GMapsClient.__aenter__` now cleans up cookie session + transport if setup fails partway (no leaked connections)
- [x] CLI JSON stdout uses `ensure_ascii=False` (was inconsistent with file output)
- [x] `.gitignore`: added nyc_restaurants_5k*.json data dumps (~15 MB, shouldn't be committed)

### Contact Extraction — Emails + Social URLs (2026-07-07)
- [x] New `src/gmaps/website.py`: `WebsiteContactExtractor` — visits business websites (homepage + up to 2 contact/about pages), extracts emails and social media URLs
- [x] Emails: plain-text regex, `mailto:` links, Cloudflare `data-cfemail` deobfuscation; junk filtering (image filenames, example/sentry/wix domains, hex build hashes)
- [x] Socials: linkedin, facebook, instagram, twitter/x, youtube, tiktok, pinterest, whatsapp, telegram — share/intent links rejected, tracking params stripped
- [x] Concurrent batch (`asyncio.Semaphore`, default 5), per-site error isolation, separate HTTP client (doesn't touch Google rate budget)
- [x] `ParsedPlace.emails` + `ParsedPlace.social_links` in contact group of `to_dict()`
- [x] `GMapsClient.extract_contacts(places)`; CLI `--contacts` on search/grid; MCP `contacts` param; CSV emails/socials columns
- [x] **Auto-tuned performance (no user flags)** — `auto_params(n)` decides concurrency (4→24, capped), per-site timeout (12s→5s), and pages-per-site (3→1) from batch size; `_effective_timeout()` adaptively tightens to p90×1.5 (floor 3s) at runtime to shed slow sites. Constructor args are optional overrides only; every user-facing path is fully automatic.
- [x] 40 tests in `tests/test_website.py` — 29 extraction + 11 auto-tuning/adaptive (104 total)

### Content-Fetching Provider Chain (2026-07-07)
- [x] New `src/gmaps/fetchers.py`: pluggable `ContentFetcher` interface + `FetcherChain` with availability-based fallback
- [x] Providers: **TinyFish Fetch** (`TINYFISH_API_KEY`), **Firecrawl** (`FIRECRAWL_API_KEY`, `onlyMainContent=false`), **Proxied HTTP** (`--proxy`/`GMAPS_PROXY`/`HTTPS_PROXY`), **Basic HTTP** (always-on final fallback)
- [x] Order: TinyFish → Firecrawl → Proxy → Basic; first success wins, failures fall through per-URL
- [x] Auto-detected from env — no flags, no user intervention (matches auto-decide preference)
- [x] **Behavior preserved:** no keys + no proxy → chain is `[basic]` = original direct-HTTP fetch, output shape unchanged
- [x] `WebsiteContactExtractor` delegates `_fetch_html` to the chain; all providers requested as HTML so extraction is identical; `client.extract_contacts` threads the transport proxy through
- [x] 22 tests in `tests/test_fetchers.py` (parsers, availability, chain ordering, fallback — no network); existing 40 website tests still green; end-to-end injection check passes
- [x] Docs: `docs/content-fetching.md`
- [x] APIs grounded from official docs (TinyFish Fetch `api.fetch.tinyfish.ai`, Firecrawl v2 `api.firecrawl.dev/v2/scrape`); AgentQL query-data deliberately NOT used (extraction-query-oriented, not raw content)

### Bitter-Lesson Phase 0 — Drift Safety Net (2026-07-07)
- [x] `DriftError` exception (`exceptions.py`) — carries `ParseHealth`
- [x] New `src/gmaps/validation.py`: `ParseHealth`, `assess_search`, `validate_search`, `assess_place`; coverage thresholds (name/place_id 90%, coords 80%); `Canary` + `DEFAULT_CANARIES` + `run_canary` live drift probe
- [x] Non-breaking integration: `SearchAPI` + `GMapsClient` gain `validate` param — `"warn"` (default, log-only), `"strict"` (raise on unhealthy first page), `False` (off). First-page-only check; output & control flow unchanged by default
- [x] Golden corpus: `tests/golden/search_raw.json` (synthetic response at real parser indices) + `search_expected.json`; `tests/test_golden.py` locks the index contract and proves a simulated name-index shift is caught
- [x] Deterministic, no LLM, no new deps — converts silent-empty-on-drift into a loud, catchable signal
- [x] 19 tests (16 validation + 3 golden) pass; package-import smoke test confirms no regression
- [x] **Phases 1–5 NOT started** (self-healing parser, model extraction, control loop, capture-replay, model-swap) — those introduce models/cost/non-determinism and need a go-ahead

### Bitter-Lesson Phase 1 — Self-Healing Parser Adapter (2026-07-07)
- [x] `src/gmaps/schema.py`: field indices lifted out of source into a `FieldSchema` value (`DEFAULT_SCHEMA` mirrors rpc/parser.py constants exactly); `traverse`, `extract_core`, `parse_core`, JSON `save_schema`/`load_schema`
- [x] `src/gmaps/healing.py`: **deterministic** self-heal — `rederive_schema` locates moved fields by searching the response for known anchor values (no LLM). `SchemaRepair` interface; `LabeledRepair` (value-search) + optional `CallableRepair` (pluggable model hook, no API wired). `SelfHealingParser` orchestrator: default parse → validate → repair-on-drift → adopt+cache healed schema
- [x] Improvement over the audit's LLM-first framing: the common case heals via search (Sutton's other pillar) — free, deterministic, testable; model is only an optional fallback for fields with no anchor
- [x] **Default path unchanged** — new modules are standalone, opt-in; the hardcoded parser still does 100% of steady-state work. No behavior/output change, no required deps
- [x] 22 tests: schema↔legacy-parser equivalence on golden; the "money" test (drifted indices recovered by value-search → healthy); orchestrator (heal, strict-raise, cache); CallableRepair hook
- [x] Not yet live-wired into the scrape path (needs a maintained known-entity label set) — natural follow-on with Phase 3's canary/control loop

### Bitter-Lesson Phase 2 — Model-Native Contact Extraction (opt-in) (2026-07-07)
- [x] `src/gmaps/contacts.py`: `ContactExtractor` protocol; `RegexContactExtractor` (default, wraps existing pure funcs); `ModelContactExtractor` (pluggable `fn`, no API wired)
- [x] **Red-team safety rails enforced:** source-grounding (`email_grounded` token-based — allows de-obfuscation, blocks hallucination; `url_grounded`) + schema-stable `ModelContacts` output; model errors/junk degrade to empty, never break a batch
- [x] **Residual-only + opt-in:** `WebsiteContactExtractor(model_extractor=...)` and `client.extract_contacts(model_extractor=...)`; model runs ONLY when the regex pass left a gap; `ContactInfo.used_model` flag. Default (`None`) = pure regex, unchanged behavior & zero cost
- [x] Honest cost note (kept from red-team): unlike the Phase-1 parser heal, contact extraction has no single cacheable rule — model runs on many residual sites, so this phase has real per-site cost; that's why it's opt-in
- [x] Verified: 13/13 core checks (grounding + adapter) + 4/4 residual-merge checks (incl. hallucination blocked on residual); `tests/test_contacts.py` added (run under local pytest — sandbox verified via reproduction as the mount truncated website.py on read)

### Bitter-Lesson Phase 3 — Closed-Loop Control (opt-in) (2026-07-07)
- [x] `src/gmaps/control.py`: `RateController` (AIMD — additive-decrease delay on success streaks, multiplicative-increase on block/429, honors retry_after, bounded) — finds the fastest safe rate vs the hand-picked fixed 1.5s
- [x] `adaptive_grid` quadtree: any cell hitting the ~120 cap is subdivided into 4 children searched at **higher zoom + smaller viewport** (zoom co-varies with subdivision depth — folds in the user's zoom lever, which is orthogonal to AIMD). Recovers dense-core businesses a fixed grid silently drops; avoids over-searching sparse areas
- [x] `ControlReport` metrics (cells searched/subdivided, saturated cells recovered, blocks, final delay) — the gain is measured, not assumed
- [x] `adaptive_grid_search(search, bbox, query, ...)` opt-in convenience wraps a live SearchAPI + rate controller; **does NOT touch `grid_search`** → default behavior unchanged
- [x] **Measured result:** synthetic 405-business city (400 in a dense cluster) → adaptive recovers **405/405**; a fixed single cell caps at **120** (285 leads silently lost). 5 saturated cells subdivided, max depth 5
- [x] Determinism note: changes *how* it traverses (timing/cells/rotation), not *what* data is returned for an area
- [x] Verified: 21 checks (RateController AIMD, geometry/quarter/zoom, quadtree subdivision+recovery+zoom-step+sparse+caps, end-to-end recovery + block feedback). `tests/test_control.py` added (run via local pytest)

### Bitter-Lesson Phase 4 — Capture-and-Replay Identity (opt-in) (2026-07-07)
- [x] `src/gmaps/identity.py`: `CapturedIdentity` (real cookies + UA + pb templates as data); save/load; `age_hours`/`is_fresh`
- [x] `parameterize_pb` / `render_pb`: turn a **captured real `pb=`** into a reusable template by swapping only query/coords/zoom/pagination for placeholders — every genuine `!Nb1` flag rides along; retires the hand-authored flag soup (T1)
- [x] `apply_identity`: inject real cookies + UA into the transport → **supersedes the fabricated SOCS cookie** (T2) and fixes the consent-UA≠scrape-UA mismatch (H2)
- [x] Capture backends: `PlaywrightCapture` (lazy import, ops-only — visits Maps, accepts consent, exports cookie jar + UA, intercepts a real search `pb=`); `ManualCapture` (feed devtools-exported artifacts, testable)
- [x] Opt-in `GMapsClient(identity=path_or_object)`; `None` (default) = existing fabricated-cookie behavior unchanged
- [x] Verified: 11 tests (pb parameterize/render round-trip incl. flag preservation, serialization, freshness, apply_identity injection, ManualCapture, Playwright-missing error). Client wiring confirmed via Read tool (mount truncated client.py on bash read — run local pytest)

### Bitter-Lesson Phase 5 — Model-Swap Readiness (2026-07-07)
- [x] `src/gmaps/registry.py`: `ProviderRegistry` over the capability boundaries (`parse_repair`, `contact_extractor`, `content_fetcher`); `build_registry` defaults (regex extractor); `resolve_providers(config)` — flipping a config value (`"regex"`→`"model"`) swaps the implementation with no other code change
- [x] `src/gmaps/evaluation.py`: eval harness — `evaluate_extractor` (precision/recall/F1), `evaluate_parse` (per-field accuracy vs golden), `rank`/`promote`/`compare_*` for **auto-promotion by score** (ties broken by latency)
- [x] Verified: 18 tests — registry config-bump swap; extractor scoring; **model auto-wins over regex** on an obfuscated corpus; parse accuracy on the golden fixture (default schema = 1.0, broken schema < 1.0, promotion picks default)
- [x] Standalone modules, default path untouched

### ✅ Bitter-Lesson roadmap COMPLETE (Phases 0–5)
- All six phases shipped, each opt-in / non-breaking (default scrape behavior and output unchanged; deterministic).
- New modules: validation, schema, healing, control, identity, contacts, fetchers, registry, evaluation.
- Test files added: test_validation, test_golden, test_schema, test_healing, test_contacts, test_control, test_identity, test_registry, test_evaluation, test_fetchers.
- Recurring caveat: run `pytest tests/ -v` locally (sandbox has no PyPI; verified via isolated harness) and trust `git diff` (workspace mount truncated some files on read).

## In Progress

- [x] Reference benchmark completed and pinned in `docs/references/google-maps-scraper-benchmark.md`.
- [x] Friendly `collect --location` workflow with exact resolved geometry, automatic grid sizing, and advanced bbox compatibility.
- [x] Honest completeness/boundary reporting, durable JSONL/checkpoint/snapshot resume, staged enrichment, and progress/manifest output.
- [x] `--max-contacts` website-attempt budget, deterministic ordering/statuses, email precision fixes, social extraction, and source-page provenance.
- [x] Agent parity through `AGENTS.md` and the MCP `collect` tool.
- [x] Live Atlanta validation: 25/25 cells, 368 records enriched, exact 20-contact
  budget, zero cell failures; exact MultiPolygon filtering verified in a follow-up.
- [x] Full regression suite and two-axis standards/spec review; all actionable findings
  corrected. Final result: 266 tests, Ruff clean, package build clean.
- [x] Implementation committed and ready to push to the public GitHub repository.

## In Progress (2026-07-16)

- [x] **Strategy B adaptive mini-maps for `collect`** — fixed fence; page mild
  density; if view hits ~120 / 6 full pages → quarter + UI zoom+1; protocol
  `!4f` fixed at 13.1, densify via `!1d` viewport; cell footprint filter + early
  stop on zero new locals.
- [x] **Multi-pass discovery** — phase1 mini-maps; phase2 Nominatim
  neighborhood/ZIP diversity (`"{query} near {subarea}"`); phase3 gap-fill of
  uncovered hex centers only. Global place_id dedupe; exact polygon fence.
- [ ] Full-city multi-pass benchmarks (Austin/Nashville/Atlanta recall gates).

## Discovery-scheduler work (2026-07-17 → 2026-07-18)

Full diagnosis + plan: [discovery-scheduler-plan.md](discovery-scheduler-plan.md).

- [x] **Regression fixed:** restored the `saturated_cells` incompleteness reason in
  `stats.py` (the uncommitted change had removed it, breaking 2 tests and the
  "saturated terminal leaf must keep `complete: false`" invariant). 285 tests green,
  ruff clean.
- [x] **`scripts/recall_floor.py`** — offline union recall floor + per-run
  recall/waste table. Empirical floors: atlanta 301 / austin 493 / nashville 249.
- [x] **`scripts/benchmark_scheduler.py`** — fixed-geometry harness driving the real
  `CollectionRunner` across policies P0–P4; resolves geometry once so runs are
  comparable. Exposed `footprint_buffer` knob + `on_footprint_drop` leak hook;
  added `outside_footprint` to the manifest.
- [x] **Live Nashville benchmark run** — the lever is **pagination depth**, not the
  footprint/diversity knobs. Old default `max_pages=2` scored 0.745 recall with 24
  mislabeled-saturated cells; `max_pages=6` reached **0.941 recall at the same request
  count** with 2 saturated. Diversity added nothing; depth-2 splitting didn't help;
  buffer 1.0 cuts duplicates ~80% but costs ~4% relevant recall.
- [x] **Wired the winner:** `CollectionRunner` mini-map default `max_pages` 2 → 6
  (recall-positive, ~zero request cost). `footprint_buffer` kept 1.5 (buffer 1.0 is a
  knob pending Atlanta confirmation). PROJECT_LOG updated with the measured table.
- [x] **Confirmed on Atlanta (2026-07-18):** `max_pages=6` positive again (P0 0.876 →
  P5 0.901 recall; never negative). Fix is settled across two cities. **New finding:**
  on Atlanta `footprint_buffer=1.0` (P7) was strictly better — recall 0.912, duplicates
  266→128, fewest requests, and the only `complete: True` run (0 saturated). On
  Nashville the same buffer cost ~4% relevant recall. So buffer 1.0 is a real decision
  point, not a clear "leave off"; still no policy clears the 2× unique/request gate.
- [ ] **Decide `footprint_buffer` default** (1.5 vs 1.0) — needs one more city or a
  values call (max recall vs. much lower duplicates). Left at 1.5 for now.

## Next Up

1. **Validate Strategy B live** on Atlanta chiropractors.
2. **Neighborhood/admin-area query sweep** if pure geo mini-maps still re-rank the
   same city-wide set at every center.
3. **Extended reviews (300+)** — paginate `/maps/rpc/listugcposts` beyond current 20-result limit.
4. **Multi-query grid search** — list of queries, merge+dedup globally.
5. **Concurrent mini-maps** — `asyncio.gather` on 4-8 cells.
6. **PostgreSQL/S3 output** — for 100k+ scale batch jobs.

## Parked roadmap — "make it a product" (decided 2026-07-18)

Decision: ship the current recall fix first, keep the codebase clean, defer all of
the below to a later version.

**TOP PRIORITY for next update (user call 2026-07-18):** the geographic-scaling
roadblock — item "Cell-sizing / scale decomposition" below. It is the gateway to
country-scale (the north-star feature) and fixes both ZIP over-gridding and
state/country under-coverage. Do this first next version.

Remaining items ordered by value/effort:

1. **Semantic query expansion** (highest discovery upside, unproven) — search multiple
   category phrasings (`chiropractor`, `chiropractic clinic`, `sports chiropractor`,
   `spinal decompression`) and union them. Attacks Google's *ranking* ceiling, not the
   geography. Geographic subarea diversity already tested and failed; semantic is the
   promising untested lever. Test on `benchmark_scheduler.py` before adopting.
2. **Docker + REST API** (high value, low effort) — FastAPI wrapper (POST job / GET
   status+results; manifest already gives status) + Dockerfile + compose. Our tiny
   pure-HTTP image (~150 MB) is a real edge over gosom's Playwright+Docker.
3. **Rotating proxy pool** (unlocks scale) — upgrade single `--proxy` to a
   health-checked pool with 429 cooldown. Answers the open IP-rate-limit question.
4. **PostgreSQL + S3 output backends** — generalize `CollectionStore` (already the
   seam) into pluggable writers; add one at a time.
5. **Thin WebUI** — last; a form over the REST API + results table + map.
6. **[TOP PRIORITY] Cell-sizing / scale decomposition** — `choose_cell_size` targets a
   fixed ~100 cells regardless of area, which **over-grids small areas** (a ZIP →
   0.5 km / 56 cells though one 6.6 km viewport covers it ~10× over → ~10–14× wasted
   requests) and **under-covers states** (50 km cells, cell/view 7.5×, rural gaps
   unsearched). City/metro (cell/view ≈ 0.8–1.2×) is the proven sweet spot that hits
   0.94 recall. Staged fix, each provable on `benchmark_scheduler.py` before commit:
   - **6a. Anchor cell size to viewport (~5–6 km), cap the ladder** — reproduces the
     working city ratio at all scales. LOW RISK (one function, downstream unchanged).
     Verify on a ZIP: same recall, far fewer requests. Fixes over-gridding.
   - **6b. Scale guard** — when the anchored cell count is huge (state/country),
     warn + require confirmation + estimate requests/time. Prevents a 13k-request
     surprise. Must land WITH 6a.
   - **6c. Hierarchical decomposition (country north-star)** — for very large areas,
     decompose admin-wise (country → states → cities/populated places via Nominatim /
     a populated-places list) and grid only populated areas; skip empty land cheaply.
     This is what makes "all restaurants in the US" tractable. Requires the proxy pool
     (item 3), concurrency (Next Up #5), and a real DB backend (item 4) to be viable —
     it is the capstone that ties the roadmap together, not a standalone change.
7. **Optional Google/Census geocoder** — Nominatim stays default (only source giving
   real polygons, which precise fencing depends on). Google Geocoding gives rectangles
   only + needs a paid key + ToS limits → a downgrade for our filter. US Census
   TIGER/ZCTA is the better route if we ever want finer ZIP/admin polygons.
8. **Contact moat deepening** — email verification (MX/SMTP), phone-from-website, more
   platforms. Already ahead of both references here; this widens the lead.

## Open Questions

- Does Google rate-limit (429) after a specific request count per IP per hour? We've seen 0 429s at 700+ requests over 12 minutes. Need larger test (5,000+ requests) to find the threshold.
- Should we add a `--zip-codes` CLI option that reads a file of ZIP codes and searches each one? Apify and gosom both support this via their input files.
- Rust port: worth it for 3-5x throughput? Python is doing 500 places/min which is sufficient for most use cases.

## Architecture Decisions

- **Pure HTTP over Playwright** (2026-06-29): 10-50x faster, 10x less memory. Trade-off: more fragile if Google changes API format.
- **pb= format over batchexecute** (2026-06-29): Google Maps uses custom protobuf-in-URL, NOT the batchexecute RPC that NotebookLM uses.
- **Three modes** (2026-07-01): Default (fast), Enrich (no login), Login (full). Covers all use cases from quick lookup to full-scale scraping.
- **No early exit in grid search** (2026-07-02): Apify and gosom both search ALL cells. Randomized cell order means low-yield cells don't indicate area exhaustion.
- **Pagination per cell** (2026-07-02): Each cell paginates through all ~120 results (6 pages). Larger cells (1.5km) + pagination = 6x more data per cell, fewer total requests.
- **Accept-Encoding: gzip, deflate (no br)** (2026-07-01): httpx can't decode brotli without extra deps. Removing `br` fixed silent empty responses.
- **Sec-Fetch-Dest: empty** (2026-07-01): Google returns HTML for document/navigate, JSON for empty/cors. Critical header fix.

## Session Notes

- **5k test (2026-07-02)**: NYC restaurants, 520 cells × 1.5km, pagination enabled. Hit 5,000 cap at ~cell 120/520. 0 errors, 0 rate limits, 97% phone coverage, 99% rating coverage. No proxy needed.
- **Field parity**: 26/34 gosom fields available without login (Phase 2). 32/34 with login. Email and social extraction are implemented as an opt-in website pass; extended review pagination remains outstanding.
- **Anti-detection working**: 700+ requests to Google Maps from one IP, 0 blocks, 0 CAPTCHAs, 0 429s. Default jitter of ±30% on 0.8-1.5s delay is sufficient.
