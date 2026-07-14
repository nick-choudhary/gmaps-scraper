# Bitter Lesson Engineering — Audit & Upgrade Plan

*A study of Richard Sutton's "The Bitter Lesson," applied to the gmaps-scraper harness: where we over-engineer with hand-coded human knowledge, and how to re-architect so the system gets better on its own as the models we use improve.*

---

## Executive summary

Sutton's bitter lesson, in one line (his words): **"the only thing that matters in the long run is the leveraging of computation."** General methods that scale with compute — *search* and *learning* — beat hand-coded human knowledge every time, because compute keeps getting cheaper and human insight does not compound.

Applied to this harness, the finding is blunt: **gmaps-scraper is a zero-model system that is maximally dense in hand-coded human knowledge.** It contains no search and no learning. Every core behavior — how it reads Google's response, how it extracts contacts, how it evades detection, how it tunes itself — is a frozen human theory expressed as constants, regexes, index maps, and step functions. Three independent layer audits put the "substitute scaffolding" share at roughly **75–80% of the parsing layer, a majority of the transport/anti-detection layer, and nearly all of the contact-extraction layer.**

None of it improves when we drop in a stronger model. All of it breaks when Google, the web, or a browser changes. That is precisely the ceiling the bitter lesson warns about.

The good news: the harness already has the *observability scaffold* (`stats.py`) and a working feedback primitive (the adaptive timeout in the contact extractor), so the path to a control loop is short — though, to be accurate, the specific signals a controller needs (per-identity, windowed block-rate and latency) must be **added**, not merely wired up (see the correction in Phase 3). And there is a single architectural pattern — the **self-healing learned adapter** — that resolves the cost tension *for the parser* and, with a strong fast-path caveat, for extraction, converting the system from "hand-coded and brittle" toward "model-leveraged and self-repairing." This document lays out the framework, the full violation inventory, a six-phase plan, and — critically — the new risks the plan itself introduces.

---

## Part I — The Bitter Lesson & "Bitter Lesson Engineering"

### The thesis

Across 70 years of AI, researchers repeatedly encoded what they knew about a domain (chess heuristics, speech phonetics, computer-vision features). It helped in the short term, felt satisfying, and then plateaued — beaten by general methods that simply threw more computation at the problem via search and learning. Sutton's point is economic, not stylistic: exponentially cheaper compute means any approach that scales with compute eventually overtakes any approach bounded by human knowledge. His deeper, epistemic point: the contents of intelligence are irredeemably complex, so we should build methods that *discover* complexity rather than pre-loading our own conclusions.

### Bitter Lesson Engineering (BLE)

Applied to what we build *around* models — harnesses, agents, scrapers, pipelines — the lesson becomes a design bias:

> Every place a system hard-codes human domain knowledge is a bet against future compute. If that knowledge substitutes for something a stronger model could do, the bet loses on the next model release.

Over-engineering, in BLE terms, is not "too much code." It is code that installs a **human-knowledge ceiling** where a scaling curve belongs. The strongest architecture is one where dropping in a 10× model improves the whole system *with zero code change*, because the system **routes** capability instead of **replacing** it.

### Leverage vs. Substitute scaffolding — the load-bearing distinction

Not all engineering is a violation. The test is directional:

- **Leverage scaffolding (KEEP).** Structure that raises what the model's own capability can reach: tools/actions, context and retrieved evidence, feedback loops (execute → observe → retry), verification and grading, sampling, memory, orchestration, transport, sandboxing. *This improves as the model improves.*
- **Substitute scaffolding (THE LIABILITY).** Hand-coded logic placed *in the model's stead*: regexes, hardcoded field/index maps, hand-tuned thresholds, rule tables enumerating domain cases, keyword classifiers. *This caps at the human knowledge poured into it and never moves when compute does* — and it often actively obstructs the more general solution.

Hand-code the **rails** (transport, verification, safety, determinism). Never hand-code the **reasoning** a model could learn to do.

### The audit rubric (reusable)

Score each component; a "yes" to any of 1–6 flags a probable violation.

1. **Ceiling test** — Would this get automatically better with a 10× model, or is it a fixed ceiling?
2. **Brittleness test** — Does it encode knowledge that breaks when the upstream format/schema/domain changes?
3. **Substitute test** — Is this hand-tuned constant/rule standing in for search or learning that could find it?
4. **Re-engineering test** — Must a human re-tune or rewrite it every time the environment or model changes?
5. **Obstruction test** — Does it sit *between* the model and the task, blocking a more general method?
6. **Knowledge-encoding test** — Does it hardcode the engineer's theory of the domain (field maps, ontologies, case lists)?
7. **Leverage confirmation (inverse)** — Does it instead *extend* the model (tools, context, feedback, verification)? If so, keep it.

**Severity = brittleness × capability-cap.** *Critical*: breaks on routine upstream change AND hard-caps a core capability. *High*: stable today but caps a central capability and blocks upgrades from helping. *Medium*: bounded, peripheral, degrades gracefully. *Low*: legitimate rails, or hand-coding justified below.

---

## Part II — Harness audit: where we violate it

### Master inventory (severity-ranked)

| # | Location | Violation | Sev | Why it's a BLE liability |
|---|----------|-----------|-----|--------------------------|
| P1 | `rpc/parser.py:125-158` | Hardcoded field-index block (`F_NAME=11`, `F_ADDRESS=18`, `PD_*`) | **Critical** | One inserted element in Google's array shifts every index; silent mis-map; zero model benefit |
| P2 | `rpc/parser.py:206-381` | Dozens of inline magic ordinals (`_safe_deep_str(pd,178,0,0)`) | **Critical** | Field map scattered & undocumented; matches position not meaning |
| P3 | `rpc/parser.py:489-553` + `182` | `_safe_*` swallow all errors → empty output, never raises | **Critical** | Worst failure mode: silent degradation, undetectable without golden tests |
| P4 | `rpc/parser.py:585-596` | Positional `zip` of address components onto `pd[183][1]` | **Critical** | Any reorder scrambles address silently |
| X1 | `website.py:57-94` | 9 hardcoded `SOCIAL_PATTERNS` + reject substrings | **Critical** | Capped at platforms the engineer knew; rots on URL scheme change (the `twitter\|x.com` scar) |
| X2 | `website.py:43-52,140-151` | Email junk blocklists + `len(local)>=24` hex heuristic | **Critical** | Hand-curated denylist needs an edit per new case; can't see obfuscated/JS emails |
| T1 | `_search.py:97-121,435-452` | Hand-authored `pb=` templates (dozens of `!Nb1` flags) | **Critical** | Reverse-engineered by hand; any protobuf reshuffle breaks it silently |
| T2 | `_auth/session.py:260-275` | Fabricated SOCS cookie (`base64("CAI"+ts)[:20]`) | **Critical** | Cargo-cult fake; doesn't match Google's real format; instantly flaggable |
| T3 | `stats.py` ↔ `_search.py`/`transport.py` | Open loop: strategy hardcoded, feedback not used to adapt | **High** | *Missing capability, not breakage* → High by our own rubric. Note `stats.py` has only a **global** `rate_limited` counter and **no latency series**; a controller needs per-identity, windowed metrics that must be **added** |
| P5 | `rpc/parser.py:599-684` | Dual old/new format branches; deep media paths; index 34 = hours *and* status | High | Direct evidence of chasing Google's changes by hand; collisions |
| P6 | `rpc/decoder.py:322-350` | English-literal block/CAPTCHA/login detection + `>=2` count | High | Locale-specific; fails silently in other languages |
| X3 | `website.py:97-100` | `_CONTACT_HINTS` 4-language keyword list | High | Misses locales/synonyms; unlisted language → zero contact pages |
| X4 | `website.py:304-347` | `auto_params()` step-function + `_effective_timeout` constants | High | "Automatic" but a hand-authored control *policy*; batch size ≠ real signal |
| H1 | `transport.py:28-41,173,225` | 6 pinned UA strings, rotated **per request** | High | Version-pinned UAs rot; per-request rotation within a session is an anomaly real browsers never show |
| H2 | `_auth/session.py:118-195,26` | Fixed consent-URL chain + fixed `REQUIRED_COOKIES` | High | Hand-modeled UI flow Google reshapes often |
| H3 | `grid.py:75` + `_search.py:358` | Fixed `cell_size_km=1.0`; magic `if new_in_cell < 5` | High | Dense cells silently hit the 120 cap and lose data; guessed threshold; `generate_zoom_level_cells` is dead capability |
| M1–M3 | `_search.py`, `transport.py`, `session.py` | Viewport/zoom/radius math; 900s staleness; `http2=False`; 6h cookie TTL; UA mismatch | Medium | Unfounded constants; clock- not signal-driven; `http2=False` is itself a fingerprint tell |
| X5 | `cli.py:70,83-100` | Hardcoded CSV/text column lists | Medium | Silently drops fields `to_dict()` emits (owner, hours, menu) |
| X6 | `website.py:107,284` | Pinned `Chrome/131`; 2 MB HTML truncation | Medium | Ages; can cut a footer contact block |
| L* | `types.py:22`, `mcp_server.py:184-216`, `pyproject.toml:8` | Unverified endpoint paths; hand-rolled JSON-RPC fallback; placeholder author | Low | Hygiene / dead-weight |

### Layer verdicts

- **Parsing & protocol (~75–80% substitute).** The heart of the harness is a hand-reverse-engineered map of Google's undocumented array layout, wrapped in accessors that swallow every error. A Google reshape produces empty output with no signal. Genuinely necessary structure — anti-XSSI stripping, JSON/HTML detection, urlencode/pb *mechanics* — is thin.
- **Transport, anti-detection & grid (majority substitute).** `pb=` templates, the fabricated cookie, the pinned UA pool, the consent chain, and the fixed grid/zoom/radius constants are hand-modeled theories of Google's behavior. The real leverage — httpx transport, retry/backoff *mechanics*, proxy capability, geodesy in `grid.py`, and the observability *scaffold* in `stats.py` — is sound. **The hooks to close the loop exist, but the controller's actual inputs (per-identity block-rate, latency quantiles) are not yet collected and must be added** — `stats.py` today has only a global block counter and is passed as an optional `None` argument in the live grid path.
- **Extraction, interfaces & config (core value is nearly all substitute).** `website.py`'s contact/social extraction — regex + growing blocklists + a 9-row platform table + a 4-language keyword list — is a frozen human snapshot of the web. The CLI/MCP surface, batching, and `to_dict` output are legitimate leverage. The newly added `auto_params` is a real UX win but, honestly, still hand-coded policy wearing an "automatic" label.

---

## Part III — The upgrade plan

### North star

Re-architect so that **capability lives behind swappable boundaries, and a stronger model improves the system with no code change.** Two of Sutton's pillars map directly onto this harness:

- **Learning** → replace hand-coded *reading* (parsing, contact extraction) with model-derived extraction that self-heals.
- **Search** → replace hand-coded *strategy* (rate, identity, grid) with closed-loop control over the feedback the harness already collects.

### The pattern that resolves the cost tension: the self-healing learned adapter

The obvious objection to "use a model to parse" is cost/latency at 5,000-place scale. The resolution is **distillation, not replacement**:

1. A cheap deterministic path (the current index map / regex) runs at scale — but it is treated as a **cache of a learned rule**, not as hand-authored truth.
2. Every result is checked against a **structural validator** (types, ranges, required fields, canary counts).
3. On validation failure or confidence drop, a **model re-derives the rule** (which index holds the phone? which anchor is the contact page?) from a handful of labeled examples, rewrites the cache, and the cheap path resumes.

The model engages only on drift, so steady-state cost stays near zero — but the rule is now *learned and self-repairing* instead of hand-coded and brittle. This single pattern cleanly retires the Critical **parser** findings (P1–P4), where **one schema is shared across every response and drift is rare**, so the model runs seldom.

**Honest caveat — the pattern does *not* transfer cleanly to contact extraction (X1–X2).** For open-web extraction there is no single reusable rule to cache: every site differs, and the "cache miss" (regex finds nothing) is the *common* case (JS/form-gated emails), not a rare drift event. So the model would run often, and steady-state cost is real, not near-zero. Phase 2 is still worth doing for coverage/generalization, but it must be costed honestly (see Phase 2), not sold as free.

### Phased roadmap

**Phase 0 — Safety net & instrumentation (prerequisite, ~1 wk).**
Build a **golden corpus** (captured real responses + human-verified expected parses) and **canary queries** with known expected counts. Replace the silent `_safe_*` empties (P3) with a validator that **fails loud on structural drift**. *Nothing downstream is safe to change until drift is detectable.* This alone converts the worst failure mode (silent empty output) into an alert. **Ownership caveat:** this corpus is not a one-time artifact — it must be re-labeled at drift time, in production, by a human. Self-healing (Phase 1) *relocates* human knowledge from source-code constants to labeled examples; it does not eliminate it. Budget a standing owner for the golden set; that is the true, honest cost of "for free" model upgrades later.

**Phase 1 — Self-healing parser adapter (the core bet).** *Retires P1, P2, P4, P5.*
Introduce a `SchemaAdapter` interface. Demote the index constants from source code to a **cached learned schema**. Add shape-based structural extraction (a 0x-hex string is a data_id; a lat/lng pair is coords) as the first-line general method. On validator failure, an LLM re-derives field→index mappings from golden examples and rewrites the cache. Parser becomes a swappable, regenerating adapter.

**Phase 2 — Model-native contact extraction.** *Addresses X1, X2, X3 — for coverage, not for free.*
Add a **structured model pass** over page text returning `{emails, socials, contact_page_urls}` that generalizes to unseen platforms (Bluesky, Mastodon, Threads…), any language, and form/JS-gated addresses no regex can see. **Cost honesty (per the red-team):** keep the fast regex/table path as the **default first pass**, and invoke the model only on the *residual* — sites where the cheap path returns nothing or low-confidence — rather than on all 5,000. Even so, because cache-misses are common (not rare drift), budget for the model running on a meaningful fraction of sites, plus a headless render only where needed. Note the existing code already decodes Cloudflare `cfemail`, so the model's marginal win is JS/rendered/human-obfuscated cases and new platforms — real, but narrower than "replaces all extraction." This is a coverage upgrade with a real per-site cost, not a distillation freebie.

**Phase 3 — Close the control loop (search over the live environment).** *Retires T3, H1, H3, M-series.*
**First add the missing instrumentation** — `stats.py` currently has only a global block counter and no latency series, so step one is per-identity, windowed block-rate + latency metrics (and make `stats` non-optional in the grid path). *Then* add an adaptive controller on top:
- **Rate/identity:** AIMD delay; rotate proxy/UA on observed block clusters (not every request); refresh session on block-rate, not a 900s clock. One coherent identity across consent + scraping.
- **Grid:** adaptive quadtree — subdivide any cell returning ≥120 (saturated ⇒ incomplete), stop when marginal new density falls off. Replaces the fixed `cell_size_km` and the guessed `< 5` rule; revives the dead `generate_zoom_level_cells` idea as real behavior.

**Phase 4 — Capture-and-replay identity.** *Retires T1, T2, H2.*
Drive consent and a periodic real-request capture through a headless browser; **replay genuine `pb=` templates and cookies** instead of fabricating them. Auto-detect flag/format breakage via the Phase 0 canary. This removes the two most instantly-flaggable tells (fake SOCS cookie, `http2=False`) and the hand-maintained flag soup.

**Phase 5 — Model-swap readiness (the thing you explicitly asked for).**
Formalize the capability boundaries that *warrant* a model — **parse** and **extract** — as **pluggable interfaces** selected by config, so a stronger hosted model (or a cheap local one) drops in without code change. Add a **model eval harness** that scores any candidate model against the golden corpus (parse accuracy, contact recall, cost, latency) and **promotes automatically** when it wins. From here, model progress on those boundaries is a config bump, not an engineering project.
*Deliberately excluded (per red-team):* **block detection is NOT a good model boundary** — it sits in the hot path of every request, has no per-request learning signal, and is 99% not-blocked, so an LLM there adds latency/cost to fix a locale bug that a language-neutral **HTTP 429/403 status check + a multilingual keyword set** already solves. Fix P6 with cheap code, not a model.

### Crosscutting principles

- **Hybrid economics everywhere:** fast deterministic path + model fallback + learned cache. You pay for the model only on drift.
- **Adapters, not rewrites:** each phase swaps one boundary behind an interface; the harness keeps running throughout.
- **Measure to promote:** the golden corpus + eval harness is what makes "future model improvements" automatic rather than aspirational.

---

## Part IV — What to *not* touch (the counter-lesson)

BLE is a bias, not an absolute. Keep hand-coding where there is no scaling curve to ride:

- **Rails & mechanics:** httpx transport, retry/backoff *mechanics*, urlencode/pb *encoding*, the CLI/MCP surface, `to_dict` serialization, semaphore batching, `grid.py` geodesy (`KM_PER_DEGREE_LAT`). These are leverage — they carry and check computation and *improve* as the model improves.
- **Determinism & compliance:** anything that must return the same answer every time and be explainable.
- **Security & safety boundaries:** permission checks, sandboxing, input validation — never delegate a trust boundary to a probabilistic model.
- **Protocol contracts:** the review `sort_map` and page-size caps map to Google's discrete protobuf codes — a protocol requirement, not encoded theory.

**Honest caveat on the recent work.** The `auto_params` auto-tuning we just shipped is a real UX win, but it is itself hand-coded policy (human-chosen thresholds and step functions), not learning. It belongs in Phase 3's controller eventually — flagged here rather than hidden.

---

## Part V — Risks this plan itself introduces

Moving capability into models is not free of new liabilities. Four must be designed for from day one, or the cure is worse than the disease. (These were surfaced by an adversarial review of this document and are kept in rather than smoothed over.)

- **Prompt injection — a trust boundary we'd be creating.** Phases 1–2 feed untrusted Google responses and third-party website HTML into an LLM whose structured output is then trusted downstream. A malicious page can embed text instructing the extractor to forge, omit, or redirect contacts. This is exactly what Part IV forbids: *never make a probabilistic model a trust boundary.* Contain it — treat model output as untrusted; **source-ground every field** (an extracted email/URL must literally appear in the fetched bytes, else discard); never let extracted content trigger an action; sandbox the extractor and strip it of any tool access.
- **Non-determinism & hallucination — a correctness/legal hazard.** A model may emit different, or invented, emails/phones run to run. For a lead-generation tool, a *hallucinated* contact is real-world harm, not a cosmetic bug — and the Phase 0 validator checks **shape, not truth**. Contain it — temperature 0, mandatory source-grounding on every critical field, and a deterministic verifier wrapping the model so downstream consumers (CSV columns, `place_id` dedup, MCP schema) still see **schema-stable** output even when the model is not.
- **The golden set needs a standing owner.** Restated because it is load-bearing: self-healing relocates human knowledge to labeling. "Automatic / for free" refers to *marginal model upgrades*, never to the labeling program that keeps them safe. Name an owner and a cadence.
- **Legal / ToS exposure is orthogonal to the Bitter Lesson.** Phase 4 optimizes *evasion* (retiring detectable tells), which is a compliance and risk decision, not an engineering one — and it sits in direct tension with the "compliance" value listed as a KEEP in Part IV. Do not let a Bitter-Lesson framing smuggle in an evasion posture the business hasn't explicitly chosen. Flag Phase 4 for a human/legal owner before implementing.

---

## Appendix — one-glance scorecard

| Layer | Substitute share | Highest-leverage move |
|-------|------------------|------------------------|
| Parsing/protocol | ~75–80% | Self-healing learned schema adapter (P1–P4) |
| Transport/anti-detection/grid | Majority | Close the control loop; adaptive quadtree (T3, H3) |
| Contact extraction | Nearly all | One model pass replaces regex+blocklists+table (X1–X3) |
| Interfaces/config | Low (mostly leverage) | Derive outputs from `to_dict`; fix hygiene |

**The one sentence:** *Stop hand-coding what the model can read and what the environment can teach — cache the learned rule, validate it, and let the model re-derive it when it breaks — so every future model upgrade makes the whole harness better for free.*
