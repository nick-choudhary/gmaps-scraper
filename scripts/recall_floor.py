#!/usr/bin/env python3
"""Offline recall-floor analysis for the discovery scheduler.

No network. Reads the ``*.jsonl`` collect outputs (and matching
``*.manifest.json`` if present) in a directory and reports, per city:

* the **union recall floor** — the set of unique businesses that *all* runs for
  that city collectively found (a strong empirical lower bound on true recall);
* per-run recall against that floor;
* the same for the *relevant* subset (category matches the query), which is the
  honest recall floor to gate a scheduler against (mirrors the historic "305
  explicitly chiropractic-category Atlanta baseline").

It also folds in manifest cost counters (discovery requests, duplicates,
outside-fence) so ``unique-per-request`` and waste shares sit next to recall in
one table.

Dedup key mirrors ``gmaps._search._place_dedup_key`` exactly so counts match
production.

Usage::

    python scripts/recall_floor.py                     # cwd, text table
    python scripts/recall_floor.py --dir path/to/runs
    python scripts/recall_floor.py --relevant chiropract
    python scripts/recall_floor.py --json              # machine-readable
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _dedup_key(rec: dict[str, Any]) -> str:
    """Match gmaps._search._place_dedup_key on a serialized record."""
    for k in ("place_id", "hex_id", "cid"):
        v = rec.get(k)
        if v:
            return str(v)
    name = str(rec.get("name") or "")
    addr = str((rec.get("address") or {}).get("full") or "")
    loc = rec.get("location") or {}
    lat, lon = loc.get("latitude"), loc.get("longitude")
    return f"{name.casefold()}|{addr.casefold()}|{lat}|{lon}"


def _categories(rec: dict[str, Any]) -> list[str]:
    return [str(c) for c in (rec.get("business") or {}).get("categories") or []]


def _city_of(path: Path) -> str:
    """City = first '-'-delimited segment of the file stem."""
    return path.stem.split(".")[0].split("-")[0]


@dataclass
class RunStats:
    file: str
    retained: int = 0
    relevant: int = 0
    ids: set[str] = field(default_factory=set)
    relevant_ids: set[str] = field(default_factory=set)
    # from manifest, if available
    requests: int | None = None
    duplicates: int | None = None
    outside: int | None = None
    raw: int | None = None


def _load_run(jsonl: Path, relevant_substr: str) -> RunStats:
    run = RunStats(file=jsonl.name)
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = _dedup_key(rec)
            run.ids.add(key)
            if relevant_substr and any(relevant_substr in c.casefold() for c in _categories(rec)):
                run.relevant_ids.add(key)
    run.retained = len(run.ids)
    run.relevant = len(run.relevant_ids)

    manifest = jsonl.with_name(jsonl.stem + ".manifest.json")
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text(encoding="utf-8")).get("results", {})
            run.requests = m.get("discovery_requests")
            run.duplicates = m.get("duplicates")
            run.outside = m.get("outside_boundary")
            run.raw = m.get("raw_occurrences")
        except (json.JSONDecodeError, OSError):
            pass
    return run


def analyze(directory: Path, relevant_substr: str) -> dict[str, Any]:
    runs_by_city: dict[str, list[RunStats]] = {}
    for jsonl in sorted(directory.glob("*.jsonl")):
        run = _load_run(jsonl, relevant_substr)
        runs_by_city.setdefault(_city_of(jsonl), []).append(run)

    report: dict[str, Any] = {"relevant_substr": relevant_substr, "cities": {}}
    for city, runs in sorted(runs_by_city.items()):
        floor: set[str] = set()
        rel_floor: set[str] = set()
        for r in runs:
            floor |= r.ids
            rel_floor |= r.relevant_ids
        report["cities"][city] = {
            "union_floor": len(floor),
            "relevant_floor": len(rel_floor),
            "runs": [
                {
                    "file": r.file,
                    "retained": r.retained,
                    "recall": round(len(r.ids & floor) / len(floor), 3) if floor else 0.0,
                    "relevant": r.relevant,
                    "relevant_recall": (
                        round(len(r.relevant_ids & rel_floor) / len(rel_floor), 3)
                        if rel_floor
                        else 0.0
                    ),
                    "requests": r.requests,
                    "unique_per_request": (
                        round(r.retained / r.requests, 2) if r.requests else None
                    ),
                    "duplicates": r.duplicates,
                    "outside": r.outside,
                    "outside_share": (
                        round(r.outside / r.raw, 3) if r.raw and r.outside is not None else None
                    ),
                }
                for r in runs
            ],
        }
    return report


def _print_text(report: dict[str, Any]) -> None:
    print(f"Relevant category substring: {report['relevant_substr']!r}\n")
    for city, data in report["cities"].items():
        print(
            f"== {city} ==  union floor: {data['union_floor']}  "
            f"| relevant floor: {data['relevant_floor']}"
        )
        cols = (
            "retained",
            "recall",
            "relevant",
            "rel_rec",
            "reqs",
            "u/req",
            "dup",
            "outside",
            "out%",
        )
        rows = []
        for r in data["runs"]:
            rows.append(
                (
                    r["file"].replace(".jsonl", ""),
                    r["retained"],
                    r["recall"],
                    r["relevant"],
                    r["relevant_recall"],
                    r["requests"] if r["requests"] is not None else "-",
                    r["unique_per_request"] if r["unique_per_request"] is not None else "-",
                    r["duplicates"] if r["duplicates"] is not None else "-",
                    r["outside"] if r["outside"] is not None else "-",
                    r["outside_share"] if r["outside_share"] is not None else "-",
                )
            )
        namew = max([len("run")] + [len(str(row[0])) for row in rows])
        hdr = "run".ljust(namew) + "  " + "  ".join(c.rjust(7) for c in cols)
        print(hdr)
        print("-" * len(hdr))
        for row in rows:
            print(str(row[0]).ljust(namew) + "  " + "  ".join(str(x).rjust(7) for x in row[1:]))
        print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dir",
        type=Path,
        default=Path("."),
        help="Directory of *.jsonl collect outputs (default: cwd).",
    )
    ap.add_argument(
        "--relevant",
        default="chiropract",
        help="Case-insensitive category substring for the relevant "
        "recall floor (default: 'chiropract').",
    )
    ap.add_argument(
        "--json", action="store_true", help="Emit machine-readable JSON instead of a table."
    )
    args = ap.parse_args()

    report = analyze(args.dir, args.relevant.casefold())
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text(report)


if __name__ == "__main__":
    main()
