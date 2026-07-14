"""Self-healing parser (Phase 1) — regenerate the field schema on drift.

When Phase 0's validator reports that a parse is structurally unhealthy (Google
moved its array layout), this module re-derives the field→index map instead of
requiring a human to hand-patch constants.

The primary strategy is deterministic **search**, not an LLM: given a few known
"anchor" entities that should appear in the response (name, place_id, hex_id,
coordinates we already know), we search the drifted structure for where those
known values now live and rebuild the schema from the discovered paths. This is
free, deterministic, testable, and — per Sutton — leverages search rather than
hand-coded knowledge.

An optional pluggable repair hook (`CallableRepair`) lets you supply a model for
the harder cases (e.g., a field with no known anchor value), but no model is
required and none is wired to any specific API here.

Steady state is untouched: the default hardcoded parser does all the work and
this path only engages when validation fails.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Protocol

from .schema import DEFAULT_SCHEMA, FieldSchema, iter_place_data, parse_core, save_schema
from .validation import ParseHealth, assess_search

logger = logging.getLogger(__name__)

# A label is a dict of known field values for one entity expected in a response,
# e.g. {"name": "Golden Coffee Co", "place_id": "ChIJ...", "latitude": 30.27}.
Label = dict[str, Any]


def _match(value: Any, target: Any, tol: float = 1e-6) -> bool:
    if isinstance(target, bool) or isinstance(value, bool):
        return value is target
    if isinstance(target, (int, float)) and isinstance(value, (int, float)):
        return abs(value - target) <= tol
    return value == target


def find_paths(data: Any, target: Any) -> list[list[int]]:
    """All index paths in a nested-list structure whose leaf equals ``target``."""
    found: list[list[int]] = []

    def walk(node: Any, path: list[int]) -> None:
        if _match(node, target):
            found.append(list(path))
        if isinstance(node, list):
            for i, child in enumerate(node):
                walk(child, path + [i])

    walk(data, [])
    return found


def _anchor_entry(place_data_list: list[Any], label: Label) -> Any:
    """Find the place_data whose contents match one of the label's anchors."""
    anchors = [label.get(k) for k in ("place_id", "hex_id", "ftid", "name")]
    anchors = [a for a in anchors if a]
    for pd in place_data_list:
        if any(find_paths(pd, a) for a in anchors):
            return pd
    return None


def rederive_schema(
    raw: Any,
    labels: list[Label],
    base: FieldSchema = DEFAULT_SCHEMA,
) -> FieldSchema | None:
    """Deterministically rebuild a schema by locating known values in ``raw``.

    For each labeled entity, find its entry, then search that entry for the
    index path of each known field value. A field's new path is the one that is
    consistent (most common) across all matched labels. Fields with no anchor in
    any label keep their base path. Returns None if no label could be matched.
    """
    entries = list(iter_place_data(raw, base))
    if not entries:
        return None

    candidates: dict[str, list[tuple[int, ...]]] = {}
    matched = 0
    for label in labels:
        pd = _anchor_entry(entries, label)
        if pd is None:
            continue
        matched += 1
        for fieldname, value in label.items():
            if fieldname not in base.paths:
                continue
            paths = find_paths(pd, value)
            if paths:
                candidates.setdefault(fieldname, []).append(tuple(paths[0]))

    if matched == 0:
        return None

    new_paths = {k: list(v) for k, v in base.paths.items()}
    for fieldname, plist in candidates.items():
        best = Counter(plist).most_common(1)[0][0]
        new_paths[fieldname] = list(best)

    return FieldSchema(
        paths=new_paths,
        results_path=list(base.results_path),
        entry_index=base.entry_index,
        skip_first=base.skip_first,
        version=base.version,
        note=f"healed by value-search from {matched} anchor(s)",
    )


# ── Repair strategies ──


class SchemaRepair(Protocol):
    """Something that can produce a corrected schema from a drifted response."""

    def repair(self, raw: Any, labels: list[Label] | None) -> FieldSchema | None: ...


@dataclass
class LabeledRepair:
    """Deterministic repair: re-derive indices by searching for known values."""

    known: list[Label]
    base: FieldSchema = field(default_factory=FieldSchema)

    def repair(self, raw: Any, labels: list[Label] | None = None) -> FieldSchema | None:
        return rederive_schema(raw, labels or self.known, self.base)


@dataclass
class CallableRepair:
    """Optional pluggable repair (e.g. LLM-backed). No model wired by default.

    ``fn(raw, labels, base) -> dict[field, path] | None``. The user supplies the
    callable; this adapter wraps its output into a FieldSchema. Use only for
    fields a deterministic anchor search cannot resolve.
    """

    fn: Callable[[Any, list[Label] | None, FieldSchema], dict[str, list[int]] | None]
    base: FieldSchema = field(default_factory=FieldSchema)

    def repair(self, raw: Any, labels: list[Label] | None = None) -> FieldSchema | None:
        paths = self.fn(raw, labels, self.base)
        if not paths:
            return None
        merged = {k: list(v) for k, v in self.base.paths.items()}
        merged.update({k: list(v) for k, v in paths.items()})
        return FieldSchema(
            paths=merged,
            results_path=list(self.base.results_path),
            entry_index=self.base.entry_index,
            skip_first=self.base.skip_first,
            note="healed by callable/LLM repair",
        )


def _health_of(core_places: list[dict[str, Any]], min_results: int = 1) -> ParseHealth:
    """Health of schema-extracted core dicts (wraps them for the assessor)."""
    return assess_search([SimpleNamespace(**p) for p in core_places], min_results=min_results)


@dataclass
class SelfHealingParser:
    """Schema-driven parser that regenerates its schema when output drifts.

    Flow per response:
      1. Parse with the active schema (default = current parser indices).
      2. Validate structural health.
      3. If healthy → return (steady state; deterministic; no repair).
      4. If unhealthy and a repair is configured → repair to a new schema,
         re-parse, and if that is healthy, adopt + cache it.
      5. If still unhealthy → raise DriftError (strict) or return best-effort.

    This is opt-in machinery; the default scraper path is unaffected.
    """

    schema: FieldSchema = field(default_factory=lambda: DEFAULT_SCHEMA)
    repair: SchemaRepair | None = None
    cache_path: str | None = None
    strict: bool = False
    min_results: int = 1

    def parse_search(
        self, raw: Any, *, query: str = "", labels: list[Label] | None = None
    ) -> list[dict[str, Any]]:
        places = parse_core(raw, self.schema)
        health = _health_of(places, self.min_results)
        if health.is_healthy:
            return places

        logger.warning(
            "self-heal: drift detected%s (%s)",
            f" for '{query}'" if query else "",
            "; ".join(health.problems),
        )

        if self.repair is not None:
            new_schema = self.repair.repair(raw, labels)
            if new_schema is not None:
                healed = parse_core(raw, new_schema)
                if _health_of(healed, self.min_results).is_healthy:
                    self.schema = new_schema
                    if self.cache_path:
                        save_schema(new_schema, self.cache_path)
                    logger.info("self-heal: recovered via %s; schema updated", new_schema.note)
                    return healed
                logger.warning("self-heal: repair produced a schema that is still unhealthy")

        from .exceptions import DriftError

        if self.strict:
            raise DriftError(
                f"parse unhealthy and unrecoverable{f' for {query!r}' if query else ''}: "
                + "; ".join(health.problems),
                health=health,
            )
        return places  # best-effort (already logged)
