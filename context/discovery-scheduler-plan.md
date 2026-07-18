# Discovery Scheduler — Diagnosis & Plan

Status: **open problem.** Written 2026-07-17 on branch
`codex/fix-current-maps-pagination`. This is the plan for the one part of the
project that is not production-solid: the `collect` command's discovery
scheduler (how a city is tiled into cells and each cell paginated).

Everything else — parser, transport, enrichment, contacts, CLI, MCP — is stable
(283 tests pass). Do not touch those to "fix discovery."

---

## 1. What is actually broken

### 1a. Regression: the honesty invariant was silently disabled

The uncommitted change to `src/gmaps/stats.py` deleted the `saturated_cells`
incompleteness reason:

```python
-        if self.cells_saturated:
-            reasons.append("saturated_cells")
+        # Saturated leaves are normal when we intentionally stop splitting;
+        # only treat them as incomplete if work remains unprocessed.
```

Consequences:

- **2 tests fail** — `test_saturated_cell_prevents_false_complete_claim`,
  `test_minimap_saturated_leaf_marks_incomplete_when_cannot_split`.
- It **contradicts the project's core invariant**: `PROJECT_LOG.md` line ~248
  ("Any saturated terminal leaf must keep `complete: false`") and lines ~366-369
  (explicit warning against false-completeness claims).

Why the change is wrong, not just test-breaking: both `grid_search` and
`minimap_grid_search` only increment `cells_saturated` when the cell is
saturated **and cannot be recovered** (`saturated and not can_split`, see
`_search.py:652`; `grid_search` has no split path at all). So the counter
already means "unrecoverable data-loss frontier." A run with `cells_saturated >
0` genuinely dropped businesses it could not reach. Reporting `complete: true`
in that state is dishonest.

The likely *motivation* for the change is real, though: on any dense real city,
some downtown leaf saturates even at the finest allowed grain, so `complete`
never goes green. That is annoying but honest. The answer is not to lie — it is
(a) restore the honest signal and (b) make genuine finest-grain saturation rare
via a better scheduler (Section 3), and document that Google's ~120/area cap
imposes a residual data-loss frontier that no pure-HTTP scheduler can fully
remove.

**Fix (do first):** revert the `stats.py` hunk — restore
`if self.cells_saturated: reasons.append("saturated_cells")`. Keep the new
`outside_footprint` counter and its summary line; those are fine. Re-run
`pytest tests/` → green.

### 1b. The real unsolved problem: discovery is dominated by waste

Google Maps `/search?tbm=map` returns a **metro-wide ranking**; the viewport
(`!1d`) and zoom (`!4f`) fields do **not** hard-constrain which businesses come
back (established in `PROJECT_LOG.md` lines ~254-276). So most raw results are
thrown away by the polygon fence, and the rest overlap heavily across cells.

Measured across the historical run manifests in the repo root:

| run | cell km | retained | raw | outside fence | duplicates |
|---|---|---|---|---|---|
| nashville-full | 5 | 232 | 9057 | 8214 (91%) | 611 |
| nashville-chiro-policy | 5 | 224 | 3696 | 3127 (85%) | 345 |
| austin-chiro | 5 | 493 | 3899 | 2387 (61%) | 1019 |
| atlanta-minimap-full | 3 | 142 | 4419 | 2247 (51%) | 2030 |

Two separate wastes: **outside-fence** (ranking pulls in suburbs) and
**duplicates** (overlapping cells re-surface the same businesses). The
`cell_accept_radius_meters` footprint filter (`_search.py:67`) trades one for
the other — tighter = fewer dups but risks recall; looser = more recall but more
waste.

---

## 2. Fix the measurement before chasing the number

Past runs are **not comparable**: Nashville alone was run at cell sizes 5, 8,
10, and 20 km, so retained counts (80…232) reflect coverage geometry, not
policy quality. Any real progress needs a fixed benchmark protocol.

### 2a. Empirical recall floor via run-union (we already have the data)

The union of retained `place_id`s across *all* historical runs for a city is a
strong lower bound on true recall. Computed from the repo's `.jsonl` files:

| city | best single run | **union floor** | gap |
|---|---|---|---|
| atlanta | 277 | **301** | 24 |
| austin | 493 | **493** | 0 (one run only) |
| nashville | 232 | **249** | 17 |

The Nashville and Atlanta gaps are the headline finding: **no single policy
found everything the policies collectively found.** Different schedulers surface
different businesses → the multi-pass / diversity direction is empirically
justified, not speculative.

Action: add `scripts/recall_floor.py` (offline, no network) that recomputes the
union floor per city from `*.jsonl`, so the floor updates as new runs land.

### 2b. Benchmark protocol (fixed harness)

- Same city, same resolved geometry, same query, **vary only the policy**.
- Cell size chosen by `choose_cell_size` (do not hand-pick per run).
- No `--max-results` cap (a cap stops after a few saturated cells and hides the
  full-city duplicate pattern — see `PROJECT_LOG.md` line ~369).
- Report per policy: `retained`, `recall = retained∩floor / floor`,
  `unique_per_request`, `duplicate_share`, `outside_share`,
  `cells_saturated_unrecoverable`, `complete`.

### 2c. Recall-leak probe (does the footprint filter cost recall?)

Offline check on existing runs: for places dropped by the footprint filter
(`outside_footprint`) whose coords are **inside** the fence, how many **never**
appear in any cell's retained set? That count = recall lost purely to the
footprint buffer. If ~0, the buffer is safe to tighten for fewer dups; if
material, the buffer is too aggressive. This decides the Section 3 buffer sweep.

---

## 3. Scheduler experiment plan

Acceptance gate (from `PROJECT_LOG.md` line ~250): **≥2× unique relevant places
per discovery request vs the one-page-per-cell baseline, without dropping below
the recall floor, and with no false completeness claim.**

Candidate policies, each run through the 2b harness on Atlanta + Nashville
(both have a union floor and the historic 305 Atlanta category baseline):

- **P0 — baseline:** current `minimap_grid_search` as-is (post-1a fix).
- **P1 — footprint buffer sweep:** `cell_accept_radius_meters` buffer ∈
  {1.0, 1.5, 2.0}. Find the recall/dup knee using the 2c probe result.
- **P2 — geo + text diversity:** P0 + `diversity_subarea_search` (neighborhood /
  ZIP name queries from `resolve_subareas`). Hypothesis, backed by 2a gap:
  text-varied queries change Google's *ranking*, surfacing businesses pure geo
  re-ranking cannot. This is the most promising lever.
- **P3 — gap-fill:** P0 + `gap_fill_search` on uncovered hex centers only.
- **P4 — P2 + P3 combined**, global `place_id` dedupe across all passes.

Decision rule: pick the lowest-request policy that (a) meets or beats the union
floor and the 305 Atlanta category baseline and (b) clears the 2× unique/request
gate. If none clear 2× without losing recall, that is a **real finding** — record
it honestly (as prior rejected experiments were, `PROJECT_LOG.md` lines
~254-397) rather than shipping a false-completeness fix.

### Known dead ends (do not repeat — already rejected in PROJECT_LOG)

- Strict per-mini-map footprint filtering (kept 28/900 — kills recall).
- One-page mini-map subdivision alone (122 retained vs 305 floor).
- Two-strike / four-page-ceiling pagination (below floor or worse dup cost).

---

## 4. Execution order

1. **[DONE 2026-07-18] Revert `stats.py` hunk** (1a) → 285 tests green, invariant
   restored. `cells_saturated` is honestly incompleteness-triggering again.
2. **[DONE] `scripts/recall_floor.py`** (2a) — offline union recall floor + per-run
   recall/waste table. Recall-**leak** probe (2c) required a live hook (dropped
   places aren't persisted): added `on_footprint_drop` to `minimap_grid_search`
   and threaded it through `CollectionRunner`; the harness measures leak.
3. **[DONE] `scripts/benchmark_scheduler.py`** (2b) — fixed-geometry harness that
   drives the real `CollectionRunner` path across policies P0…P4, resolving
   geometry once (cached) so runs are comparable. Also exposed `footprint_buffer`
   as a first-class knob (P1) and added `outside_footprint` to the manifest.
4. **[DONE — Nashville]** Live benchmark found the lever is **pagination depth**,
   not footprint/diversity. `max_pages=2` → 0.745 recall / 24 saturated;
   `max_pages=6` → **0.941 recall / 2 saturated at equal request count**. Diversity
   flat; depth-2 no gain; buffer 1.0 = −80% dup but −4% relevant recall. Table in
   PROJECT_LOG (2026-07-18). **Atlanta confirmed** (2026-07-18): max_pages=6 positive
   again (0.876→0.901); buffer 1.0 there was strictly better (recall 0.912, dup
   266→128, only `complete: True` run). buffer default stays 1.5 pending a 3rd city.
5. **[DONE for the pagination lever]** Wired winner: `CollectionRunner` mini-map
   default `max_pages` 2 → 6. `footprint_buffer` stays 1.5 (buffer 1.0 is an opt-in
   knob pending Atlanta). `enable_diversity_pass` / `enable_gap_fill` stay off.
6. **[DONE]** `PROJECT_LOG.md` + `progress-tracker.md` updated with measured results.

Steps 1-3 landed with no live-Google dependency. Steps 4-5 need live runs (each
policy is minutes and uses the local IP budget) and are where the genuinely-open
question resolves — run them when ready.

### Current empirical baseline (from `recall_floor.py` on existing runs)

Not comparable across cities (different geometry/cell sizes) but a useful floor:

| city | union floor | relevant floor | best single run (recall / relevant / out%) |
|---|---|---|---|
| atlanta | 301 | 221 | atlanta-minimap-v2 — 0.92 / 0.95 / 0.41 |
| austin | 493 | 349 | austin-chiro — 1.00 / 1.00 / 0.61 (only run) |
| nashville | 249 | 152 | nashville-chiro-policy — 0.90 / 0.96 / **0.85 waste** |

Read: the current minimap path (atlanta-minimap-v2) already reaches 0.92 recall
at 0.41 outside-share — the strongest existing result. High-recall runs pay in
waste (nashville-chiro-policy hits 0.96 relevant recall but 85% outside-fence).
The benchmark (step 4) exists to find a policy that holds recall while cutting
that waste — or to prove honestly that overlapping mini-maps can't.

---

## 5. Key code references

- `src/gmaps/stats.py:73` — `incomplete_reasons` / `complete` (the 1a fix).
- `src/gmaps/_search.py:403` — `minimap_grid_search` (Strategy B, Phase 1).
- `src/gmaps/_search.py:512` — `absorb()` fence + footprint filter + dedupe.
- `src/gmaps/_search.py:631-653` — saturation / split decision + counter.
- `src/gmaps/_search.py:67` — `cell_accept_radius_meters` (buffer sweep target).
- `src/gmaps/_search.py:708` — `diversity_subarea_search` (Phase 2).
- `src/gmaps/_search.py:861` — `gap_fill_search` (Phase 3).
- `src/gmaps/geocoding.py:resolve_subareas` — neighborhood/ZIP discovery.
- `src/gmaps/collection.py` — 3-phase orchestration + `choose_cell_size`.
</content>
</invoke>
