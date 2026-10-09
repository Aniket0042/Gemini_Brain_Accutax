"""
links.py — Links from an agent answer into the Accutax app.

Built in code from tool results, never written by the model, so a link can
neither be invented nor point at the wrong record:

- document links: each invoice, bill, journal or payment number in the answer
  that a list_documents result returned with its record id becomes a Markdown
  link to that record's page in Accutax;
- guide buttons: an app_guide result that matched a guide section with a known
  route adds an "Open in Accutax" button, as the current path does.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from gemini_brain.config.settings import settings

#: Routes of the record pages in Accutax (frontend App.jsx).
INVOICE, BILL, JOURNAL = "/income/details/{id}", "/expenses/edit-expense/{id}", "/journal-entries/{id}"
CUSTOMER_PAYMENT, SUPPLIER_PAYMENT = "/edit-customer-payment/{id}", "/edit-supplier-payment/{id}"


def _by_direction(received: str, paid: str):
    return lambda row: paid if str(row.get("direction") or "").lower() == "paid" else received


#: list_documents type -> [(number column, id column, route, or a function of the row giving it)]
DOCUMENT_ROUTES = {
    "sales_invoices": [("document_number", "document_id", INVOICE)],
    "open_receivables": [("document_number", "document_id", INVOICE)],
    "bills": [("document_number", "document_id", BILL)],
    "open_payables": [("document_number", "document_id", BILL)],
    "journal_lines": [("journal_number", "journal_id", JOURNAL)],
    "payments": [("payment_number", "payment_id", _by_direction(CUSTOMER_PAYMENT, SUPPLIER_PAYMENT))],
    "payment_settlements": [("payment_number", "payment_id", _by_direction(CUSTOMER_PAYMENT, SUPPLIER_PAYMENT)),
                            ("document_number", "document_id", _by_direction(INVOICE, BILL))],
}
#: Existing Markdown links and inline code, which are left as they are.
_PROTECTED = re.compile(r"\[[^\]\n]*\]\([^)\s]*\)|`[^`\n]*`")


def _app_url() -> str:
    return (settings.accutax_app_url or "").rstrip("/")


def document_links(data: Sequence[Dict[str, Any]]) -> Dict[str, str]:
    """{document or journal number: app URL}. A number that maps to two different records
    (the same number in two organizations) gets no link."""
    base = _app_url()
    if not base:
        return {}
    links: Dict[str, str] = {}
    clashes = set()
    for item in data or []:
        if item.get("tool") != "list_documents":
            continue
        result = item.get("result") or {}
        for number_key, id_key, route in DOCUMENT_ROUTES.get(result.get("type"), []):
            for row in result.get("rows") or []:
                number, record_id = str(row.get(number_key) or "").strip(), row.get(id_key)
                if len(number) < 3 or record_id in (None, ""):
                    continue
                path = route(row) if callable(route) else route
                url = base + path.format(id=int(float(record_id)))
                if links.get(number, url) != url:
                    clashes.add(number)
                links[number] = url
    return {n: u for n, u in links.items() if n not in clashes}


def link_documents(answer: str, links: Dict[str, str]) -> str:
    """Turn each listed document number in the answer into a link to its record."""
    if not answer or not links:
        return answer
    numbers = sorted(links, key=len, reverse=True)
    pattern = re.compile(r"(?<![\w\-/\[])(" + "|".join(re.escape(n) for n in numbers) + r")(?![\w\-\]])")

    def link_plain(text: str) -> str:
        return pattern.sub(lambda m: f"[{m.group(1)}]({links[m.group(1)]})", text)

    out, last = [], 0
    for m in _PROTECTED.finditer(answer):
        out.append(link_plain(answer[last:m.start()]))
        out.append(m.group(0))
        last = m.end()
    out.append(link_plain(answer[last:]))
    return "".join(out)


def guide_buttons(data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """"Open in Accutax" buttons for the guide sections the answer used, once each."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in data or []:
        if item.get("tool") != "app_guide":
            continue
        url = (item.get("result") or {}).get("app_url")
        if url and url not in seen:
            seen.add(url)
            out.append({"type": "action_button", "label": "Open in Accutax", "url": url})
    return out
