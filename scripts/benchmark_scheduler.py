#!/usr/bin/env python3
"""Fixed-geometry benchmark harness for the discovery scheduler.

Runs several scheduler *policies* over the **same** city geometry and query so
their recall and cost are directly comparable (past runs used different cell
sizes and were not). Drives the real production path (``CollectionRunner``) with
discovery only — no enrichment / contacts.

It is LIVE: each policy issues real Google Maps searches and takes minutes.
Geometry is resolved once and cached, so every policy and every re-run shares
identical geometry (and Nominatim is hit at most once).

Metrics per policy:

* retained, recall vs the within-benchmark union floor, relevant recall
* discovery requests, unique-per-request (the 2x gate lives here)
* duplicates, outside-fence, outside-footprint (waste breakdown)
* footprint recall leak — in-fence places dropped by the footprint filter that
  NO cell recovered (measured via the on_footprint_drop hook)
* cells_saturated (unrecoverable) and honest `complete`

Usage::

    # dry run — print the plan, hit nothing
    python scripts/benchmark_scheduler.py --location "Nashville, Tennessee" \
        --query chiropractors --dry-run

    # live benchmark of the default policy set
    python scripts/benchmark_scheduler.py --location "Nashville, Tennessee" \
        --query chiropractors --out-dir bench/nashville

    # pick specific policies
    python scripts/benchmark_scheduler.py -l "Atlanta, Georgia" -q chiropractors \
        --policies P0,P2,P4 --out-dir bench/atlanta
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gmaps.client import GMapsClient
from gmaps.collection import (
    CollectionRunner,
    CollectionState,
    CollectionStore,
    _matches_query,
    _place_key,
    choose_cell_size,
)
from gmaps.geocoding import NominatimResolver
from gmaps.grid import BoundingBox


@dataclass(frozen=True)
class Policy:
    """A named scheduler configuration."""

    name: str
    description: str
    runner_kwargs: dict[str, Any] = field(default_factory=dict)


# The catalogue from context/discovery-scheduler-plan.md Section 3.
POLICIES: dict[str, Policy] = {
    "P0": Policy("P0", "minimap only (current default)", {}),
    "P1a": Policy("P1a", "footprint buffer 1.0", {"footprint_buffer": 1.0}),
    "P1c": Policy("P1c", "footprint buffer 2.0", {"footprint_buffer": 2.0}),
    "P2": Policy("P2", "minimap + neighborhood/ZIP diversity",
                 {"enable_diversity_pass": True}),
    "P3": Policy("P3", "minimap + gap-fill", {"enable_gap_fill": True}),
    "P4": Policy("P4", "minimap + diversity + gap-fill",
                 {"enable_diversity_pass": True, "enable_gap_fill": True}),
    "P5": Policy("P5", "deep pagination (6 pages/cell)", {"minimap_max_pages": 6}),
    "P6": Policy("P6", "deep pages + deeper split (6 pages, depth 2)",
                 {"minimap_max_pages": 6, "minimap_max_depth": 2}),
    "P7": Policy("P7", "deep pages + tight buffer (6 pages, buffer 1.0)",
                 {"minimap_max_pages": 6, "footprint_buffer": 1.0}),
}
DEFAULT_POLICIES = ["P0", "P1a", "P1c", "P2", "P3", "P4"]


async def _resolve_geometry(location: str, cache: Path, lang: str) -> dict[str, Any]:
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    resolved = await NominatimResolver().resolve(location, language=lang)
    payload = resolved.to_dict()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _state_for(
    policy: Policy, query: str, location: str, resolved: dict[str, Any],
    cell_size: float, max_results: int,
) -> CollectionState:
    box = resolved["bbox"]
    return CollectionState(
        query=query,
        location=location,
        bbox={k: float(box[k]) for k in ("min_lat", "min_lon", "max_lat", "max_lon")},
        cell_size_km=cell_size,
        max_results=max_results,
        resolved_location=resolved,
        enrich=False,
        contacts=False,
    )


async def _run_policy(
    policy: Policy, *, query: str, location: str, resolved: dict[str, Any],
    cell_size: float, max_results: int, out_dir: Path, lang: str, timeout: float,
) -> dict[str, Any]:
    prefix = out_dir / f"{policy.name}.json"
    # Fresh output each policy (no --resume): remove stale artifacts.
    for suffix in (".json", ".jsonl", ".checkpoint.json", ".manifest.json"):
        stale = prefix.with_suffix(suffix)
        if stale.exists():
            stale.unlink()

    store = CollectionStore(prefix)
    state = _state_for(policy, query, location, resolved, cell_size, max_results)

    dropped: set[str] = set()

    def on_footprint_drop(place: Any) -> None:
        dropped.add(_place_key(place))

    client = GMapsClient(enrich=False, timeout=timeout, language=lang)
    async with client:
        runner = CollectionRunner(
            client=client,
            store=store,
            state=state,
            on_footprint_drop=on_footprint_drop,
            **policy.runner_kwargs,
        )
        places, manifest = await runner.run()

    retained_keys = {_place_key(p) for p in places}
    leak = dropped - retained_keys
    res = manifest["results"]
    return {
        "policy": policy.name,
        "description": policy.description,
        "retained": len(places),
        "retained_keys": sorted(retained_keys),
        "relevant_keys": sorted(_place_key(p) for p in places if _matches_query(p, query)),
        "discovery_requests": res.get("discovery_requests"),
        "duplicates": res.get("duplicates"),
        "outside_boundary": res.get("outside_boundary"),
        "outside_footprint": res.get("outside_footprint"),
        "cells_saturated": manifest["coverage"].get("cells_saturated"),
        "complete": manifest.get("complete"),
        "footprint_leak": len(leak),
        "elapsed_seconds": manifest.get("elapsed_seconds"),
    }


def _summarize(rows: list[dict[str, Any]], query: str) -> dict[str, Any]:
    floor: set[str] = set()
    rel_floor: set[str] = set()
    for r in rows:
        floor |= set(r["retained_keys"])
        rel_floor |= set(r["relevant_keys"])
    base_upr = None
    for r in rows:
        if r["policy"] == "P0" and r["discovery_requests"]:
            base_upr = r["retained"] / r["discovery_requests"]
    summary = {
        "query": query,
        "union_floor": len(floor),
        "relevant_floor": len(rel_floor),
        "baseline_unique_per_request": round(base_upr, 3) if base_upr else None,
        "policies": [],
    }
    for r in rows:
        reqs = r["discovery_requests"] or 0
        upr = round(r["retained"] / reqs, 2) if reqs else None
        summary["policies"].append({
            "policy": r["policy"],
            "description": r["description"],
            "retained": r["retained"],
            "recall": round(len(set(r["retained_keys"]) & floor) / len(floor), 3) if floor else 0.0,
            "relevant_recall": (
                round(len(set(r["relevant_keys"]) & rel_floor) / len(rel_floor), 3)
                if rel_floor else 0.0
            ),
            "requests": reqs,
            "unique_per_request": upr,
            "vs_baseline_x": (
                round(upr / base_upr, 2) if upr and base_upr else None
            ),
            "duplicates": r["duplicates"],
            "outside_boundary": r["outside_boundary"],
            "outside_footprint": r["outside_footprint"],
            "footprint_leak": r["footprint_leak"],
            "cells_saturated": r["cells_saturated"],
            "complete": r["complete"],
            "elapsed_seconds": r["elapsed_seconds"],
        })
    return summary


def _print(summary: dict[str, Any]) -> None:
    print(f"\nunion floor: {summary['union_floor']}  "
          f"| relevant floor: {summary['relevant_floor']}  "
          f"| P0 baseline u/req: {summary['baseline_unique_per_request']}")
    print("(gate: >=2x baseline u/req AND recall not below floor AND no false complete)\n")
    cols = ("retain", "recall", "rel_rec", "reqs", "u/req", "x", "dup",
            "out_b", "out_f", "leak", "sat", "done")
    keys = ("retained", "recall", "relevant_recall", "requests", "unique_per_request",
            "vs_baseline_x", "duplicates", "outside_boundary", "outside_footprint",
            "footprint_leak", "cells_saturated", "complete")
    namew = max(len("policy"), *(len(p["policy"]) for p in summary["policies"]))
    hdr = "policy".ljust(namew) + "  " + "  ".join(c.rjust(7) for c in cols)
    print(hdr)
    print("-" * len(hdr))
    for p in summary["policies"]:
        print(p["policy"].ljust(namew) + "  "
              + "  ".join(str(p.get(k)).rjust(7) for k in keys))
    print()


async def _main_async(args: argparse.Namespace) -> None:
    out_dir = Path(args.out_dir)
    cache = out_dir / "geometry.json"
    selected = [POLICIES[name] for name in args.policies]

    if args.dry_run:
        print(f"Location : {args.location}")
        print(f"Query    : {args.query}")
        print(f"Out dir  : {out_dir}")
        print(f"Policies : {', '.join(p.name for p in selected)}")
        for p in selected:
            print(f"  {p.name:4s} {p.description}  {p.runner_kwargs}")
        print("\n(dry run — resolve geometry + run each policy live when --dry-run is dropped)")
        return

    resolved = await _resolve_geometry(args.location, cache, args.lang)
    box = BoundingBox(**{k: float(resolved["bbox"][k])
                         for k in ("min_lat", "min_lon", "max_lat", "max_lon")})
    cell_size = args.cell_size or choose_cell_size(box)
    print(f"Geometry: {resolved.get('display_name')} | cell {cell_size:g} km "
          f"| max_results {args.max_results}")

    rows: list[dict[str, Any]] = []
    for policy in selected:
        print(f"\n=== {policy.name}: {policy.description} ===")
        row = await _run_policy(
            policy, query=args.query, location=args.location, resolved=resolved,
            cell_size=cell_size, max_results=args.max_results, out_dir=out_dir,
            lang=args.lang, timeout=args.timeout,
        )
        rows.append(row)
        print(f"  retained={row['retained']} requests={row['discovery_requests']} "
              f"leak={row['footprint_leak']} complete={row['complete']}")

    summary = _summarize(rows, args.query)
    (out_dir / "benchmark.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _print(summary)
    print(f"Report: {out_dir / 'benchmark.json'}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-l", "--location", required=True, help='e.g. "Nashville, Tennessee"')
    ap.add_argument("-q", "--query", required=True, help="e.g. chiropractors")
    ap.add_argument("--out-dir", default="bench", help="Output directory (default: bench).")
    ap.add_argument("--policies", default=",".join(DEFAULT_POLICIES),
                    help=f"Comma list from {list(POLICIES)} (default: all).")
    ap.add_argument("--cell-size", type=float, default=None,
                    help="Override cell size km (default: choose_cell_size).")
    ap.add_argument("--max-results", type=int, default=5000,
                    help="Cap (keep high for a full-city floor; default 5000).")
    ap.add_argument("--lang", default="en")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan and exit without any network calls.")
    args = ap.parse_args()

    unknown = [p for p in args.policies.split(",") if p not in POLICIES]
    if unknown:
        ap.error(f"unknown policies {unknown}; choose from {list(POLICIES)}")
    args.policies = [p for p in args.policies.split(",") if p]

    asyncio.run(_main_async(args))


if __name__ == "__main__":
    main()
