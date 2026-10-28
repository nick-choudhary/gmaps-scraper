"""Field schema — the parser's index map as swappable data (Phase 1).

The parser reads Google's undocumented response by fixed array indices baked
into source code (``F_NAME = 11`` …). That is the brittle core the audit flags:
when Google shifts the layout, every field silently mis-maps.

This module lifts the *core* field indices out of code into a `FieldSchema`
value that can be inspected, cached to disk, and — critically — regenerated
when the layout drifts (see `healing.py`). The default schema mirrors the
current parser constants exactly, so nothing changes in the happy path; the
schema-driven extractor here is a parallel, data-driven path used for
self-healing recovery when the hardcoded parser drifts.

Deterministic, dependency-free. No models, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Core fields that drive structural health and identity. Values are index paths
# into an entry's place_data list. These mirror the constants in rpc/parser.py.
DEFAULT_PATHS: dict[str, list[int]] = {
    "name": [11],  # F_NAME
    "place_id": [78],  # F_PLACE_ID
    "hex_id": [10],  # F_HEX_ID
    "ftid": [89],  # F_FTID
    "data_id": [0],
    "latitude": [9, 2],  # F_COORDS[2]
    "longitude": [9, 3],  # F_COORDS[3]
    "address": [18],  # F_ADDRESS
    "phone": [178, 0, 0],
    "website": [7, 0],
}

# String-valued fields (coerced to str/""); the rest are numeric/None.
_STRING_FIELDS = frozenset(
    {"name", "place_id", "hex_id", "ftid", "data_id", "address", "phone", "website"}
)
_FLOAT_FIELDS = frozenset({"latitude", "longitude"})

SCHEMA_VERSION = 1


@dataclass
class FieldSchema:
    """The core field→index map plus response navigation, as data."""

    paths: dict[str, list[int]] = field(
        default_factory=lambda: {k: list(v) for k, v in DEFAULT_PATHS.items()}
    )
    results_path: list[int] = field(default_factory=lambda: [0, 1])  # data[0][1] → results array
    entry_index: int = 14  # each result entry's place_data at entry[14]
    # results[0] is search metadata, except that a query naming one place exactly
    # gets that place alone in this slot: with skip_first it is kept only when it
    # has a name and an address.
    skip_first: bool = True
    version: int = SCHEMA_VERSION
    note: str = "default (mirrors rpc/parser.py constants)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "paths": {k: list(v) for k, v in self.paths.items()},
            "results_path": list(self.results_path),
            "entry_index": self.entry_index,
            "skip_first": self.skip_first,
            "version": self.version,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FieldSchema:
        return cls(
            paths={k: list(v) for k, v in d.get("paths", DEFAULT_PATHS).items()},
            results_path=list(d.get("results_path", [0, 1])),
            entry_index=int(d.get("entry_index", 14)),
            skip_first=bool(d.get("skip_first", True)),
            version=int(d.get("version", SCHEMA_VERSION)),
            note=str(d.get("note", "")),
        )


DEFAULT_SCHEMA = FieldSchema()


def traverse(data: Any, path: list[int]) -> Any:
    """Safely follow an index path through nested lists; None if any step fails."""
    cur = data
    for idx in path:
        if not isinstance(cur, list) or idx < 0 or idx >= len(cur):
            return None
        cur = cur[idx]
    return cur


def extract_core(place_data: Any, schema: FieldSchema = DEFAULT_SCHEMA) -> dict[str, Any]:
    """Extract the core fields from one entry's place_data using the schema."""
    out: dict[str, Any] = {}
    for fieldname, path in schema.paths.items():
        val = traverse(place_data, path)
        if fieldname in _FLOAT_FIELDS:
            out[fieldname] = (
                val if isinstance(val, (int, float)) and not isinstance(val, bool) else None
            )
        else:  # string fields
            out[fieldname] = str(val) if isinstance(val, str) and val else ""
    return out


def _is_business(place_data: list[Any], schema: FieldSchema) -> bool:
    """Whether ``place_data`` has a name and an address at the schema's paths."""
    return all(
        key in schema.paths and isinstance(traverse(place_data, schema.paths[key]), str)
        for key in ("name", "address")
    )


def iter_place_data(raw: Any, schema: FieldSchema = DEFAULT_SCHEMA):
    """Yield each business entry's place_data list from a raw search response."""
    results = traverse(raw, schema.results_path)
    if not isinstance(results, list):
        return
    for index, entry in enumerate(results):
        if not isinstance(entry, list) or len(entry) <= schema.entry_index:
            continue
        pd = entry[schema.entry_index]
        if not isinstance(pd, list):
            continue
        if index == 0 and schema.skip_first and not _is_business(pd, schema):
            continue
        yield pd


def parse_core(raw: Any, schema: FieldSchema = DEFAULT_SCHEMA) -> list[dict[str, Any]]:
    """Schema-driven core-field parse of a raw search response."""
    return [extract_core(pd, schema) for pd in iter_place_data(raw, schema)]


def save_schema(schema: FieldSchema, path: str | Path) -> None:
    """Persist a (typically healed) schema to a JSON cache file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(schema.to_dict(), indent=2), encoding="utf-8")


def load_schema(path: str | Path) -> FieldSchema | None:
    """Load a cached schema, or None if the file is absent/invalid."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return FieldSchema.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValueError, KeyError):
        return None
