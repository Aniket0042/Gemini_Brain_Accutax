"""
list_documents.py — Document-level rows (invoices, bills, open items) for the agent.

query_metrics answers with totals per organization; this answers "which
invoices", "top 10 bills", "unpaid invoices older than 180 days". It reads the
same governed views, grouped by document, so a document list and a total for
the same question can never disagree. Tenant scope comes from the request.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List, Sequence

from gemini_brain.semantic import cube_client
from gemini_brain.semantic.catalog import Catalog, ToolInputError, get_catalog
from gemini_brain.semantic.query_metrics import (
    CURRENCY_NOTE,
    MAX_FILTER_VALUES,
    MAX_FILTERS,
    OPERATORS,
    _apply_time,
    _filters,
    _limit,
)

MAX_LIMIT = 100

#: type -> (view, columns returned for each document, amount measures, member the "amount" order uses)
DOCUMENT_TYPES: Dict[str, tuple] = {
    "sales_invoices": ("sales", ("document_number", "document_date", "document_weekday", "document_type",
                                 "status", "customer_name"), ("net_sales", "output_vat"), "net_sales"),
    "bills": ("purchases", ("document_number", "document_date", "document_weekday", "document_type",
                            "status", "vendor_name"), ("net_purchases", "input_vat"), "net_purchases"),
    "open_receivables": ("receivables", ("document_number", "document_date", "due_date", "days_overdue",
                                         "aging_bucket", "status", "customer_name"), ("outstanding",), "outstanding"),
    "open_payables": ("payables", ("document_number", "document_date", "due_date", "days_overdue",
                                   "aging_bucket", "status", "vendor_name"), ("outstanding",), "outstanding"),
}
ORDER_BY = ("amount", "date", "days_overdue")


def run(params: Dict[str, Any], *, organization_ids: Sequence[int], subject: str, deadline: float) -> Dict[str, Any]:
    catalog = get_catalog(organization_ids, subject, deadline)
    query, period_note = build_query(params, catalog)
    result = cube_client.load(query, organization_ids=organization_ids, subject=subject, deadline=deadline)
    rows = [{k.split(".", 1)[1]: str(v) if isinstance(v, Decimal) else v for k, v in r.items()} for r in result.rows]
    notes = [period_note, CURRENCY_NOTE]
    if len(rows) >= query["limit"]:
        notes.append(f"Only the first {query['limit']} documents are listed; there may be more.")
    return {"type": params.get("type"), "rows": rows, "row_count": len(rows),
            "data_as_of": result.last_refresh_time, "notes": notes}


def build_query(params: Dict[str, Any], catalog: Catalog) -> tuple[Dict[str, Any], str]:
    """The Cube query for a document list and the period it covers. Raises ToolInputError."""
    if not isinstance(params, dict):
        raise ToolInputError("Tool input must be an object")
    kind = params.get("type")
    if kind not in DOCUMENT_TYPES:
        raise ToolInputError(f"type must be one of {sorted(DOCUMENT_TYPES)}")
    name, columns, amounts, amount_member = DOCUMENT_TYPES[kind]
    view = catalog.view(name)
    dims = [f"{name}.organization_id", f"{name}.organization_name", f"{name}.currency"]
    dims += [f"{name}.{c}" for c in columns if f"{name}.{c}" in view.dimensions]
    query: Dict[str, Any] = {
        "measures": [f"{name}.{m}" for m in amounts if f"{name}.{m}" in view.measures],
        "dimensions": dims,
        "filters": _filters(params.get("filters") or [], view),
        "limit": _limit(params.get("limit") or 25, MAX_LIMIT),
    }
    if not query["measures"]:
        raise ToolInputError(f"View {name} has no amount for {kind}")
    period_note = _apply_time(query, view, {k: v for k, v in params.items() if k != "granularity"})

    order = params.get("order") or {}
    by = order.get("by", "amount") if isinstance(order, dict) else "amount"
    if by not in ORDER_BY:
        raise ToolInputError(f"order.by must be one of {list(ORDER_BY)}")
    member = {"amount": f"{name}.{amount_member}", "date": f"{name}.document_date",
              "days_overdue": f"{name}.days_overdue"}[by]
    if member not in dims and member not in query["measures"]:
        raise ToolInputError(f"{kind} cannot be ordered by {by}")
    query["order"] = {member: "asc" if order.get("direction") == "asc" else "desc"}
    return query, period_note


def tool_spec(catalog: Catalog) -> Dict[str, Any]:
    """Bedrock Converse toolSpec for list_documents."""
    filterable = []
    for kind, (name, *_rest) in DOCUMENT_TYPES.items():
        if name in catalog.views:
            v = catalog.views[name]
            filterable.append(f"{kind} ({name}): " + ", ".join(sorted([*v.dimensions, *v.measures])))
    return {"toolSpec": {
        "name": "list_documents",
        "description": (
            "Individual documents for the organizations selected in this chat: issued sales invoices, bills, "
            "or the invoices and bills still open today. Use for 'which', 'list', 'largest', 'top N documents', "
            "'older than N days', weekday questions. Each row has the document number, date, counterparty, status "
            "and amount, and its organization. For totals use query_metrics.\n\nFilter members per type:\n"
            + "\n".join(filterable)
        ),
        "inputSchema": {"json": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": sorted(DOCUMENT_TYPES)},
                "filters": {"type": "array", "maxItems": MAX_FILTERS, "items": {
                    "type": "object",
                    "properties": {
                        "member": {"type": "string"},
                        "operator": {"type": "string", "enum": list(OPERATORS)},
                        "values": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_FILTER_VALUES},
                    },
                    "required": ["member", "operator"],
                }},
                "period": {"type": "object", "description": "sales_invoices and bills only; same shape as query_metrics",
                           "properties": {"preset": {"type": "string"}, "start": {"type": "string"},
                                          "end": {"type": "string"}}},
                "order": {"type": "object", "properties": {
                    "by": {"type": "string", "enum": list(ORDER_BY)},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                }},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["type"],
        }},
    }}
