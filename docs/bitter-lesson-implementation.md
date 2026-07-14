# Bitter-Lesson Implementation Guide

This maps each shipped module to its phase from `docs/bitter-lesson-audit.md`, and
shows the **opt-in usage** for each. Every phase is additive and non-breaking:
with nothing enabled, the scraper's output and behavior are unchanged and fully
deterministic. Nothing here puts a model in the hot path.

## Module → phase map

| Phase | Module(s) | What it adds | Default |
|-------|-----------|--------------|---------|
| 0 — Drift safety net | `validation.py`, `exceptions.DriftError`, `tests/golden/` | Detects silent format-drift; canary probe | **warn-only** (logs) |
| 1 — Self-healing parser | `schema.py`, `healing.py` | Field indices as data; re-derived by value-search on drift | off (opt-in) |
| 2 — Model contact extraction | `contacts.py` | Model on the residual only, source-grounded | off (regex only) |
| 3 — Closed-loop control | `control.py` | AIMD rate + zoom-coupled adaptive quadtree | off (fixed grid) |
| 4 — Capture-and-replay identity | `identity.py` | Real cookies + real `pb=` replace fabricated ones | off (fabricated) |
| 5 — Model-swap readiness | `registry.py`, `evaluation.py` | Config-selected providers + eval/auto-promote | n/a (tooling) |

Related: `fetchers.py` (content-fetch provider chain — see `docs/content-fetching.md`)
is the `content_fetcher` boundary the Phase 5 registry references.

---

## Phase 0 — Drift safety net (`validation.py`)

Turns "Google changed its format → silent empty output" into a loud signal.

```python
from gmaps import GMapsClient

# Default: warn-only. Logs a warning on an unhealthy first page; output unchanged.
async with GMapsClient() as c:
    await c.search.places("cafe", latitude=30.27, longitude=-97.74)

# Strict: raise DriftError on drift (use for canaries / CI).
async with GMapsClient(validate="strict") as c:
    ...

# Off entirely:
GMapsClient(validate=False)
```

Canary probe (a known-dense query that should always come back healthy):

```python
from gmaps.validation import run_canary          # raises DriftError in strict mode
async with GMapsClient(validate="strict") as c:
    await run_canary(c)                           # or run_canary(c, DEFAULT_CANARIES[1])
```

## Phase 1 — Self-healing parser (`schema.py`, `healing.py`)

Field indices live in a `FieldSchema` (default mirrors the parser constants). On
drift, the schema is re-derived by **searching the response for known values** —
no LLM. The default parser still does all steady-state work.

```python
from gmaps.healing import SelfHealingParser, LabeledRepair

# A small maintained set of anchor businesses you already know.
known = [{"name": "Starbucks Reserve", "place_id": "ChIJ...",
          "latitude": 47.61, "longitude": -122.34}]

parser = SelfHealingParser(repair=LabeledRepair(known=known), cache_path="schema.json")
places = parser.parse_search(raw, query="coffee")   # heals + caches on drift; else default
```

Optional model hook for fields with no anchor (you supply the callable):

```python
from gmaps.healing import CallableRepair
parser = SelfHealingParser(repair=CallableRepair(fn=my_index_finder))
```

## Phase 2 — Model contact extraction (`contacts.py`)

The regex path stays the default; a model runs **only on residual sites** (where
regex found nothing) and every returned contact is **source-grounded** (must
appear on the page — blocks hallucination).

```python
from gmaps.contacts import ModelContactExtractor

def my_llm(page_text, url):            # returns {"emails": [...], "socials": {platform: url}}
    ...

async with GMapsClient() as c:
    r = await c.search.places("hvac", latitude=30.27, longitude=-97.74)
    await c.extract_contacts(r.places, model_extractor=ModelContactExtractor(fn=my_llm))

# Default (no model_extractor) = pure regex, unchanged behavior and zero model cost.
```

## Phase 3 — Closed-loop control (`control.py`)

AIMD rate control + a quadtree that subdivides saturated (≥120) cells at higher
zoom — recovering businesses a fixed grid silently drops. Standalone; your
`grid_search` is untouched.

```python
from gmaps.control import adaptive_grid_search
from gmaps.grid import BoundingBox

bbox = BoundingBox(40.55, -74.05, 40.90, -73.70)   # NYC
async with GMapsClient() as c:
    places, report = await adaptive_grid_search(c.search, bbox, "restaurants")
    print(report.summary())   # cells subdivided, saturated recovered, blocks, final delay
```

## Phase 4 — Capture-and-replay identity (`identity.py`)

Replace the fabricated SOCS cookie and hand-authored `pb=` flags with real
captured artifacts.

```python
# 1) Capture once (ops step; needs a browser).
from gmaps.identity import PlaywrightCapture, save_identity
ident = await PlaywrightCapture().capture()
save_identity(ident, "identity.json")

# ...or feed artifacts exported from your browser devtools (no browser needed):
from gmaps.identity import ManualCapture
ident = await ManualCapture(cookies={"NID": "...", "SOCS": "..."},
                            user_agent="Mozilla/5.0 ...").capture()

# 2) Replay.
async with GMapsClient(identity="identity.json") as c:
    ...

# Default (no identity) = existing fabricated-cookie behavior.
```

## Phase 5 — Model-swap readiness (`registry.py`, `evaluation.py`)

Pick each capability's implementation by config, and measure candidates before
trusting them.

```python
from gmaps.registry import build_registry, resolve_providers
from gmaps.contacts import ModelContactExtractor

reg = build_registry()
reg.register("contact_extractor", "model", ModelContactExtractor(fn=my_llm))

# Flip "regex" <-> "model" here; no other code changes.
providers = resolve_providers({"contact_extractor": "model"}, reg)
await client.extract_contacts(places, model_extractor=providers["contact_extractor"])
```

Score + auto-promote against a labeled corpus:

```python
from gmaps.evaluation import compare_extractors, ExtractionCase

cases = [ExtractionCase("email: sales [at] acme [dot] com", {"sales@acme.com"})]
winner, ranked = compare_extractors({"regex": regex_x, "model": model_x}, cases)
print(winner, [r.summary() for r in ranked])
```

Parse candidates are scored the same way against the golden corpus with
`compare_parsers(...)`; ranking is by score, ties broken by latency.

---

## Running the tests

```bash
pip install -e ".[dev]"     # installs the package + httpx/click/rich + pytest-asyncio
pytest tests/ -v
```

The suite uses a `src/` layout (`pythonpath = ["src"]` in `pyproject.toml`), so
the package imports without an editable install for the pure tests; `[dev]` is
still needed for runtime deps and the async tests.
