"""
cash_card.py — The cash forecast card under an answer, built in code from the cash_forecast result.

Tiles for the key figures (one organization) and a line chart of the weekly closing
cash (one line per organization, at most MAX_SERIES). Values come from the tool
result, never from the model's text, as the KPI tiles do (tiles.py).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.agent import tiles

MAX_SERIES = 5
_TILES = (
    ("opening_cash", "Cash today"),
    ("lowest_closing_cash", "Lowest week"),
    ("closing_cash", "Closing cash"),
    ("overdue_receivables", "Overdue to collect"),
    ("overdue_payables", "Overdue to pay"),
)


def cash_blocks(data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[kpi_grid, chart] for the last cash_forecast result of this turn; [] when there is none or it holds no weeks."""
    result = next((d.get("result") or {} for d in reversed(list(data or [])) if d.get("tool") == "cash_forecast"), None)
    orgs = [o for o in (result or {}).get("organizations") or [] if o.get("weeks")]
    if not orgs:
        return []
    blocks: List[Dict[str, Any]] = []
    if len(orgs) == 1:
        grid = _tiles(orgs[0], result.get("period"))
        if grid:
            blocks.append(grid)
    shown = orgs[:MAX_SERIES]
    weeks = shown[0]["weeks"]
    currencies = {o.get("currency") or "" for o in shown}
    blocks.append({
        "type": "chart",
        "chart_type": "line",
        "title": "Weekly closing cash",
        "caption": _caption(result, currencies, len(orgs) - len(shown)),
        "x_label": "Week starting",
        "y_label": next(iter(currencies)) if len(currencies) == 1 else "",
        "categories": [_week(w.get("week_start")) for w in weeks],
        "series": [{"name": o.get("organization_name") or f"Organization {o.get('organization_id')}",
                    "data": [_float(w.get("closing_cash")) for w in o["weeks"][:len(weeks)]]} for o in shown],
    })
    return blocks


def _tiles(org: Dict[str, Any], period: Optional[str]) -> Optional[Dict[str, Any]]:
    currency = org.get("currency") or ""
    items = []
    for key, label in _TILES:
        value = _decimal(org.get(key))
        if value is None:
            continue
        items.append({"label": label, "value": tiles._display(key, value, currency), "raw_value": float(value),
                      "numeric": True, "section": None})
    if not items:
        return None
    block: Dict[str, Any] = {"type": "kpi_grid", "items": items}
    if period:
        block["period"] = str(period).split(" (")[0]
    return block


def _caption(result: Dict[str, Any], currencies: set, hidden: int) -> str:
    parts = ["Open invoices and bills counted in the week they fall due."]
    if len(currencies) > 1:
        parts.append("Each line is in its organization's own currency.")
    if hidden > 0:
        parts.append(f"{hidden} more organization(s) not drawn.")
    return " ".join(parts)


def _week(value: Any) -> str:
    try:
        return dt.date.fromisoformat(str(value)[:10]).strftime("%d %b")
    except ValueError:
        return str(value)


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _float(value: Any) -> Optional[float]:
    number = _decimal(value)
    return float(number) if number is not None else None
