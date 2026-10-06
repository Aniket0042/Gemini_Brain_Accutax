"""
catalog.py — The views the agent may query, read from Cube's /v1/meta.

Cube hides raw cubes (public: false), so /v1/meta lists only the governed
views. Each view's meta says how time applies to it: "flow" needs a period,
"balance" an as-of date, "current" neither.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic import cube_client

_TTL_SECONDS = 600
#: Added to every query by code; the model never chooses or filters on them.
AUTO_DIMENSIONS = ("organization_id", "organization_name", "currency")
KINDS = ("flow", "balance", "current")


class ToolInputError(ValueError):
    """The model asked for something the catalog does not have. Sent back to the model."""


@dataclass(frozen=True)
class View:
    name: str
    description: str
    ai_context: str
    kind: str                       # "flow" | "balance" | "current"
    time_dimension: Optional[str]
    measures: Dict[str, str]        # member -> description
    dimensions: Dict[str, str]      # member -> description; excludes AUTO_DIMENSIONS
    always_group_by: Tuple[str, ...]


class Catalog:
    def __init__(self, views: List[View]):
        self.views = {v.name: v for v in views}

    def view(self, name: object) -> View:
        view = self.views.get(str(name))
        if view is None:
            raise ToolInputError(f"Unknown view {name!r}. Choose one of: {', '.join(sorted(self.views))}")
        return view

    def describe(self) -> str:
        """Catalog text for the tool description."""
        timing = {"flow": "give a period", "balance": "give as_of", "current": "no period"}
        lines = []
        for v in self.views.values():
            lines.append(f"View {v.name} ({timing[v.kind]}): {v.description} {v.ai_context}".strip())
            lines += [f"  measure {m}: {d}" for m, d in v.measures.items()]
            if v.dimensions:
                lines.append("  group_by/filter: " + ", ".join(v.dimensions))
        return "\n".join(lines)


def parse_meta(body: Dict[str, Any]) -> Catalog:
    """Build the catalog from a /v1/meta response. Views with a bad meta are skipped."""
    views = []
    for c in body.get("cubes") or []:
        if c.get("type") != "view":
            continue
        meta = c.get("meta") or {}
        kind = meta.get("kind", "flow")
        if kind not in KINDS or (kind in ("flow", "balance") and not meta.get("time_dimension")):
            continue
        name = c["name"]
        auto = {f"{name}.{d}" for d in AUTO_DIMENSIONS}
        views.append(View(
            name=name,
            description=c.get("description") or "",
            ai_context=meta.get("ai_context") or "",
            kind=kind,
            time_dimension=meta.get("time_dimension"),
            measures={m["name"]: m.get("description") or m.get("title") or "" for m in c.get("measures") or []},
            dimensions={d["name"]: d.get("description") or d.get("title") or ""
                        for d in c.get("dimensions") or [] if d["name"] not in auto},
            always_group_by=tuple(meta.get("always_group_by") or ()),
        ))
    return Catalog(views)


_cache: Tuple[float, Optional[Catalog]] = (0.0, None)
_lock = threading.Lock()


def get_catalog(organization_ids: Sequence[int], subject: str, deadline: float) -> Catalog:
    """The current catalog, cached for ten minutes. The catalog is the same for every tenant."""
    global _cache
    with _lock:
        loaded_at, catalog = _cache
        if catalog is not None and time.monotonic() - loaded_at < _TTL_SECONDS:
            return catalog
    catalog = parse_meta(cube_client.meta(organization_ids=organization_ids, subject=subject, deadline=deadline))
    with _lock:
        _cache = (time.monotonic(), catalog)
    return catalog


def clear_cache() -> None:
    global _cache
    with _lock:
        _cache = (0.0, None)
