"""
tiles.py — KPI tiles for an agent answer, built in code from the tool result.

A question answered by one headline query (one organization, no breakdown, no
trend: "P&L this year", "cash position", "receivables today") gets its figures
as tiles above the answer, as the current path shows them. Values come from the
Cube row, never from the model's text. Anything else (a comparison, a list, a
trend) gets no tiles: its table is in the answer.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence

#: Words kept upper case in tile labels.
_ACRONYMS = {"vat": "VAT", "ar": "AR", "ap": "AP"}
_PERIOD = re.compile(r"Period: (\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})")
_AS_OF = re.compile(r"(?:as of|on) (\d{4}-\d{2}-\d{2})")


def kpi_tiles(data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[kpi_grid block] for a single headline query; [] otherwise."""
    figures = [d for d in data or [] if d.get("tool") in ("query_metrics", "list_documents", "cash_forecast")]
    if len(figures) != 1 or figures[0].get("tool") != "query_metrics":
        return []
    params, result = figures[0].get("input") or {}, figures[0].get("result") or {}
    rows = result.get("rows") or []
    measures = [m for m in params.get("measures") or [] if isinstance(m, str)]
    if len(rows) != 1 or not measures or params.get("group_by") or params.get("granularity"):
        return []
    row, view = rows[0], str(result.get("view") or measures[0].split(".", 1)[0])
    currency = row.get(f"{view}.currency") or ""
    items = []
    for member in measures:
        value = _number(row.get(member))
        if value is None:
            continue
        items.append({"label": _label(member), "value": _display(member, value, currency),
                      "raw_value": float(value), "numeric": True, "section": None})
    if not items:
        return []
    block: Dict[str, Any] = {"type": "kpi_grid", "items": items}
    period = _period(result.get("notes") or [])
    if period:
        block["period"] = period
    return [block]


def _number(value: Any) -> Optional[Decimal]:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _label(member: str) -> str:
    words = member.split(".")[-1].replace("_pct", "").split("_")
    words = [_ACRONYMS.get(w, w) for w in words]
    text = " ".join(words)
    text = text[:1].upper() + text[1:]
    return text + (" %" if member.endswith("_pct") else "")


def _display(member: str, value: Decimal, currency: str) -> str:
    if member.endswith("_pct"):
        return f"{value:,.1f}%"
    if member.endswith("_ratio"):
        return f"{value:,.2f}"
    name = member.split(".")[-1]
    if name.endswith(("_count", "_transactions")) or name.startswith("quantity") or name.endswith("_level"):
        return f"{value:,.0f}" if value == value.to_integral_value() else f"{value:,.2f}"
    return f"{currency} {value:,.2f}".strip()


def _period(notes: Sequence[Any]) -> Optional[str]:
    for note in notes:
        text = str(note or "")
        m = _PERIOD.search(text)
        if m:
            return f"{m.group(1)} to {m.group(2)}"
        m = _AS_OF.search(text)
        if m:
            return f"As of {m.group(1)}"
    return None
