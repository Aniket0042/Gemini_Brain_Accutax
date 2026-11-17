"""
mcp_data.py — The MCP connector's composite data tools: named reports, and search / fetch over documents.

Each is a fixed recipe on the agent's own tools (agent/tools.py: query_metrics, list_documents), run without a
model, so its figures are the ones an Accutax answer would give. api/mcp_routes.py scopes, masks and returns them.

- run_report: the reports people ask for by name (profit and loss, balance sheet, aging, top customers ...).
  The recipe picks the view, measures and grouping; the caller only picks the report and the period.
- search / fetch: the pair ChatGPT's deep research and company knowledge look for. search finds sales invoices
  and bills by number or by customer / supplier name across the user's organizations; fetch returns one of them
  in full (header, lines, what is still open) by the id search gave.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gemini_brain.semantic.catalog import ToolInputError

#: report -> (view, measures, group_by, extra query_metrics params). Members are the view's own names.
REPORTS: Dict[str, Tuple[str, List[str], List[str], Dict[str, Any]]] = {
    "profit_and_loss": ("pnl", ["revenue", "other_income", "cost_of_sales", "operating_expenses", "gross_profit",
                                "net_profit"], [], {}),
    "profit_and_loss_by_account": ("pnl", ["revenue", "other_income", "cost_of_sales", "operating_expenses"],
                                   ["account_type", "account_name"], {"limit": 100}),
    "monthly_profit_and_loss": ("pnl", ["revenue", "total_expenses", "net_profit", "net_margin_pct"], [],
                                {"granularity": "month"}),
    "balance_sheet": ("balance_sheet", ["assets", "current_assets", "liabilities", "current_liabilities",
                                        "total_equity", "cash_and_bank"], [], {}),
    "receivables_aging": ("receivables", ["outstanding", "open_count"], ["aging_bucket"], {}),
    "payables_aging": ("payables", ["outstanding", "open_count"], ["aging_bucket"], {}),
    "top_customers": ("sales", ["net_sales", "invoice_count"], ["customer_name"], {"order_by": "net_sales"}),
    "top_suppliers": ("purchases", ["net_purchases", "bill_count"], ["vendor_name"], {"order_by": "net_purchases"}),
    "sales_by_month": ("sales", ["net_sales", "output_vat", "invoice_count"], [], {"granularity": "month"}),
    "purchases_by_month": ("purchases", ["net_purchases", "input_vat", "bill_count"], [], {"granularity": "month"}),
    "expenses_by_category": ("purchases", ["net_purchases", "bill_count"], ["expense_category"],
                             {"order_by": "net_purchases"}),
}
#: Reports on a "current" view: today's state only, no period.
CURRENT_ONLY = {"receivables_aging", "payables_aging"}
MAX_TOP = 25

#: search / fetch: id prefix -> (list_documents type, view, counterparty member, number member, kind label)
KINDS = {"inv": ("sales_invoices", "sales", "customer_name", "Invoice"),
         "bill": ("bills", "purchases", "vendor_name", "Bill")}
OPEN_TYPE = {"inv": "open_receivables", "bill": "open_payables"}
SEARCH_PER_QUERY = 5
_ID = re.compile(r"^(inv|bill):(\d+):(\d+)$")

Run = Callable[[str, Dict[str, Any], Sequence[int]], Dict[str, Any]]


def report(params: Dict[str, Any], run: Run, orgs: Sequence[int]) -> Dict[str, Any]:
    """One named report. `params`: report, and period ({preset} or {start, end}) or as_of, and limit for a top list."""
    name = params.get("report")
    if name not in REPORTS:
        raise ToolInputError(f"report must be one of {sorted(REPORTS)}")
    view, measures, group_by, extra = REPORTS[name]
    q: Dict[str, Any] = {"view": view, "measures": [f"{view}.{m}" for m in measures],
                         "group_by": [f"{view}.{d}" for d in group_by]}
    if extra.get("granularity"):
        q["granularity"] = extra["granularity"]
    if extra.get("limit"):
        q["limit"] = extra["limit"]
    if extra.get("order_by"):
        q["order"] = {"member": f"{view}.{extra['order_by']}", "direction": "desc"}
        q["limit"] = max(1, min(int(params.get("limit") or 10), MAX_TOP))
    if name in CURRENT_ONLY:
        if params.get("period") or params.get("as_of"):
            raise ToolInputError(f"{name} is as of today only; ask without a period")
    elif view == "balance_sheet":
        if params.get("as_of"):
            q["as_of"] = params["as_of"]
    elif params.get("period"):
        q["period"] = params["period"]
    result = run("query_metrics", q, orgs)
    return {"report": name, **result}


def _doc_title(kind: str, row: Dict[str, Any]) -> str:
    _, _, party, label = KINDS[kind]
    parts = [f"{label} {row.get('document_number') or ''}".strip(), str(row.get(party) or "")]
    amount = row.get("net_sales") if kind == "inv" else row.get("net_purchases")
    if amount not in (None, ""):
        parts.append(f"{row.get('currency') or ''} {amount}".strip())
    if row.get("document_date"):
        parts.append(str(row["document_date"])[:10])
    if row.get("organization_name"):
        parts.append(str(row["organization_name"]))
    return " · ".join(p for p in parts if p)


def _url(kind: str, document_id: Any) -> str:
    from gemini_brain.agent import links
    base = links._app_url()
    route = links.INVOICE if kind == "inv" else links.BILL
    return f"{base}{route.format(id=document_id)}" if base and document_id else ""


def search(params: Dict[str, Any], run: Run, orgs: Sequence[int]) -> Dict[str, Any]:
    """Sales invoices and bills whose number or customer / supplier name contains the query, newest first."""
    query = str(params.get("query") or "").strip()
    if len(query) < 2:
        raise ToolInputError("query needs at least 2 characters")
    results: List[Dict[str, Any]] = []
    seen = set()
    for kind, (doc_type, view, party, _) in KINDS.items():
        for member in (f"{view}.document_number", f"{view}.{party}"):
            out = run("list_documents", {"type": doc_type, "limit": SEARCH_PER_QUERY,
                                         "order": {"by": "date", "direction": "desc"},
                                         "period": {"start": "2000-01-01", "end": "2100-12-31"},
                                         "filters": [{"member": member, "operator": "contains", "values": [query]}]},
                      orgs)
            for row in out.get("rows") or []:
                rid = f"{kind}:{row.get('organization_id')}:{row.get('document_id')}"
                if rid in seen or not row.get("document_id"):
                    continue
                seen.add(rid)
                results.append({"id": rid, "title": _doc_title(kind, row), "url": _url(kind, row.get("document_id")),
                                "organization_id": row.get("organization_id")})
    return {"query": query, "results": results}


def parse_id(document: str) -> Tuple[str, int, int]:
    m = _ID.match(str(document or "").strip())
    if not m:
        raise ToolInputError("id must be one that search returned, such as inv:27:22539")
    return m.group(1), int(m.group(2)), int(m.group(3))


def fetch(params: Dict[str, Any], run: Run, orgs: Sequence[int]) -> Dict[str, Any]:
    """One sales invoice or bill in full: header, lines (items, quantity, net, VAT) and what is still open today."""
    kind, org, document_id = parse_id(params.get("id"))
    doc_type, view, party, label = KINDS[kind]
    by_id = [{"member": f"{view}.document_id", "operator": "equals", "values": [str(document_id)]}]
    head = run("list_documents", {"type": doc_type, "limit": 1, "filters": by_id,
                                  "period": {"start": "2000-01-01", "end": "2100-12-31"}}, orgs)
    rows = head.get("rows") or []
    if not rows:
        raise ToolInputError("No such document in this organization (it may be a draft, voided or cancelled)")
    header = rows[0]
    amount = "net_sales" if kind == "inv" else "net_purchases"
    vat = "output_vat" if kind == "inv" else "input_vat"
    qty = "quantity_sold" if kind == "inv" else "quantity_purchased"
    lines = run("query_metrics", {"view": view, "measures": [f"{view}.{qty}", f"{view}.{amount}", f"{view}.{vat}"],
                                  "group_by": [f"{view}.item_name"], "filters": by_id, "limit": 100,
                                  "period": {"start": "2000-01-01", "end": "2100-12-31"}}, orgs)
    open_rows = run("list_documents", {"type": OPEN_TYPE[kind], "limit": 1, "filters": [
        {"member": f"{'receivables' if kind == 'inv' else 'payables'}.document_id", "operator": "equals",
         "values": [str(document_id)]}]}, orgs).get("rows") or []
    item_rows = [{"item": r.get(f"{view}.item_name"), "quantity": r.get(f"{view}.{qty}"),
                  "net": r.get(f"{view}.{amount}"), "vat": r.get(f"{view}.{vat}")} for r in lines.get("rows") or []]
    status = open_rows[0] if open_rows else None
    return {
        "id": f"{kind}:{org}:{document_id}", "title": _doc_title(kind, header), "url": _url(kind, document_id),
        "document": {"type": label, "number": header.get("document_number"), "date": header.get("document_date"),
                     party: header.get(party), "status": header.get("status"), "net": header.get(amount),
                     "vat": header.get(vat), "currency": header.get("currency"),
                     "organization_id": header.get("organization_id"),
                     "organization_name": header.get("organization_name")},
        "lines": item_rows,
        "open": ({"outstanding": status.get("outstanding"), "due_date": status.get("due_date"),
                  "days_overdue": status.get("days_overdue"), "due_date_basis": status.get("due_date_basis")}
                 if status else {"outstanding": "0", "note": "Not open today: paid, or not yet due to be counted."}),
    }


COMPOSITE = {"run_report": report, "search": search, "fetch": fetch}
