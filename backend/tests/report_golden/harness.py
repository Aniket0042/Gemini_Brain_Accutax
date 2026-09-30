"""Run a golden fixture through the real report pipeline and normalise the result.

`build(name)` returns the canvas spec exactly as a user would get it (offline:
the report narrator uses its template). `snapshot(spec)` keeps what defines
the report's content — figures, forms, colors, takeaways, warnings, summary —
and drops what legitimately changes run to run (timestamps, artifact ids).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from tests.report_golden.fixtures import FIXTURES

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"
#: Stable timestamp for anything rendered from a fixture (PDF metadata, headers).
FIXED_GENERATED_AT = "2026-01-15T09:00:00+00:00"


def _offline_attach(query: str, data: Any) -> Optional[Dict[str, Any]]:
    """The canvas spec for a request, with the narrator's model switched off."""
    from gemini_brain.artifacts import narrator
    from gemini_brain.artifacts.attach import attach_delivery

    original = narrator._model_call

    def _offline(system: str, user: str) -> str:
        raise RuntimeError("golden fixtures never call the model")

    narrator._model_call = _offline
    try:
        out = attach_delivery(query, data, [], user_id=1, organization_id=1)
    finally:
        narrator._model_call = original
    canvas = next((b for b in out if b.get("type") == "canvas"), None)
    return canvas["spec"] if canvas else None


def build(name: str) -> Optional[Dict[str, Any]]:
    """The canvas spec for a fixture, or None when the pipeline shows no report."""
    fixture = FIXTURES[name]
    spec = _offline_attach(fixture["query"], fixture["data"])
    if spec is not None:
        spec["generated_at"] = FIXED_GENERATED_AT
    return spec


def _round(v: Any) -> Any:
    return round(v, 4) if isinstance(v, float) else v


def snapshot(spec: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if spec is None:
        return {"report": None}
    charts = []
    for c in spec.get("charts") or []:
        charts.append({
            "type": c.get("chart_type"),
            "title": c.get("title"),
            "categories": c.get("categories"),
            "series": [{"name": s.get("name"), "data": [_round(v) for v in s.get("data") or []]} for s in c.get("series") or []],
            "series_colors": c.get("series_colors"),
            "point_colors": c.get("point_colors"),
            "takeaway": c.get("takeaway"),
            "requested_type": c.get("requested_type"),
            "downgrade_reason": c.get("downgrade_reason"),
            "total_indices": c.get("total_indices"),
        })
    tables = []
    for t in spec.get("tables") or []:
        tables.append({
            "title": t.get("title"),
            "columns": [c.get("label") for c in t.get("columns") or []],
            "first_rows": (t.get("rows") or [])[:5],
            "total_rows": t.get("total_rows"),
        })
    methodology: List[str] = [line for line in spec.get("methodology") or [] if not line.startswith("Source:")]
    return {
        "title": spec.get("title"),
        "period": spec.get("period"),
        "currency": spec.get("currency"),
        "kpis": [(k.get("label"), k.get("formatted")) for k in spec.get("kpis") or []],
        "charts": charts,
        "tables": tables,
        "callouts": spec.get("callouts"),
        "narrative": spec.get("narrative_parts"),
        "integrity": spec.get("integrity"),
        "methodology": methodology,
    }


def snapshot_path(name: str) -> Path:
    return SNAPSHOT_DIR / f"{name}.json"


def load_snapshot(name: str) -> Optional[Dict[str, Any]]:
    path = snapshot_path(name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_snapshot(name: str, data: Dict[str, Any]) -> None:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_path(name).write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def normalised(data: Dict[str, Any]) -> Dict[str, Any]:
    """Round-trip through JSON so tuples/lists compare equal to stored snapshots."""
    return json.loads(json.dumps(data, ensure_ascii=False))
