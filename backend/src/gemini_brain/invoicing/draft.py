"""draft.py: the draft_sales_document tool. It prepares a sales invoice or quote; it never writes to Accutax.

The model gives what the person asked for in words (customer, lines, dates). Code then:
  1. picks the one organization (a write needs exactly one),
  2. finds the customer among the organization's customers (or returns the close names to ask about),
  3. finds each line's catalog item by exact name (its price, revenue account and tax rate), else keeps a free-text
     line,
  4. picks each line's tax rate (the item's, the treatment asked, or the organization's default sales rate),
  5. computes the totals the way the Accutax form does (totals.py),
  6. stores the Accutax payload as a pending action (actions/pending.py) and builds the card (blocks.py).

The person saves it with the card's button (confirm.py). The model gets a short summary: names and totals, never
the customer's TRN, email or phone, and never the payload.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.accutax_api.client import AccutaxApiError, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.config.settings import settings
from gemini_brain.invoicing import lookups, totals
from gemini_brain.semantic.catalog import ToolInputError

KIND = "sales_document"
TYPES = {"invoice": "INVOICE", "quote": "QUOTE"}
LABELS = {"INVOICE": "Invoice", "QUOTE": "Quote"}
TREATMENTS = ("standard", "zero_rated", "exempt", "out_of_scope")
MAX_LINES = 50
DEFAULT_DUE_DAYS = 30
SAVE_NOTE = ("A card with this draft and a Save as draft button is shown under your answer. Nothing is saved in "
             "Accutax until the person clicks it: never say it is saved, sent or posted.")

SPEC = {"toolSpec": {
    "name": "draft_sales_document",
    "description": (
        "Prepare a sales invoice or a quote for one customer, from what the person asked for. Code finds the customer "
        "and items in Accutax, picks the VAT rate and computes the totals; a card lets the person save it as a draft. "
        "Nothing is saved by this call. To change the draft of this chat (quantity, price, lines, dates, customer), "
        "call it again with every line as it should be now and changes_earlier_draft true."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "document_type": {"type": "string", "enum": list(TYPES)},
            "organization_id": {"type": "integer",
                                "description": "The organization it is for. Required when several are selected."},
            "customer": {"type": "string", "description": "The customer's name as the person said it."},
            "customer_id": {"type": "integer",
                            "description": "Only an id this tool returned as a candidate, once the person picked it."},
            "lines": {
                "type": "array", "minItems": 1, "maxItems": MAX_LINES,
                "items": {"type": "object", "properties": {
                    "description": {"type": "string", "description": "What is sold, e.g. 'Consulting'."},
                    "quantity": {"type": "number"},
                    "unit_price": {"type": "number",
                                   "description": "Price per unit before VAT. Omit to use the catalog item's price."},
                    "discount_percent": {"type": "number"},
                    "discount_amount": {"type": "number"},
                    "vat": {"type": "string", "enum": list(TREATMENTS),
                            "description": "Only when the person says so; omit for the usual rate."},
                }, "required": ["description", "quantity"]},
            },
            "invoice_date": {"type": "string", "description": "YYYY-MM-DD; omit for today."},
            "due_date": {"type": "string",
                         "description": "YYYY-MM-DD (for a quote: valid until). Omit to use due_in_days or the default."},
            "due_in_days": {"type": "integer"},
            "notes": {"type": "string", "description": "Notes to print on the document, only if the person gave some."},
            "changes_earlier_draft": {"type": "boolean",
                                      "description": "True when this corrects the draft prepared earlier in this chat."},
        },
        "required": ["document_type", "lines"],
    }},
}}


def _date(value: Any, field: str) -> Optional[dt.date]:
    if value in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ToolInputError(f"{field} must be a date as YYYY-MM-DD") from None


def _number(value: Any, field: str, default: Optional[float] = None) -> Optional[float]:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ToolInputError(f"{field} must be a number") from None


def _organization(params: Dict[str, Any], ctx: Any) -> int:
    orgs = list(ctx.organization_ids)
    wanted = params.get("organization_id")
    if wanted not in (None, ""):
        try:
            wanted = int(wanted)
        except (TypeError, ValueError):
            raise ToolInputError("organization_id must be an organization id") from None
        if wanted not in orgs:
            raise ToolInputError(f"Organization {wanted} is not selected in this chat")
        return wanted
    if len(orgs) != 1:
        raise ToolInputError("Several organizations are selected: ask the person which one this is for, then pass "
                             "organization_id")
    return orgs[0]


def _today() -> dt.date:
    from gemini_brain.semantic import periods
    return periods.today_in(settings.report_timezone)


def run(params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    income_type = TYPES.get(str(params.get("document_type") or "").lower())
    if income_type is None:
        raise ToolInputError("document_type must be invoice or quote")
    raw_lines = params.get("lines") or []
    if not isinstance(raw_lines, list) or not raw_lines:
        raise ToolInputError("lines needs at least one line")
    if len(raw_lines) > MAX_LINES:
        raise ToolInputError(f"At most {MAX_LINES} lines")
    org = _organization(params, ctx)
    replaces = None
    if params.get("changes_earlier_draft") is True:
        earlier = pending.latest_open(KIND, user_id=ctx.user_id, organization_id=org, session_id=ctx.session_id)
        replaces = earlier.id if earlier is not None else None

    with client_for(ctx.accutax, org) as client:
        try:
            pool = lookups.customers(client, ctx.user_id)
        except AccutaxApiError as e:
            return _accutax_failed(e)
        customer_id = params.get("customer_id")
        try:
            customer_id = int(customer_id) if customer_id not in (None, "") else None
        except (TypeError, ValueError):
            raise ToolInputError("customer_id must be an id") from None
        match = lookups.match_customer(str(params.get("customer") or ""), pool, customer_id)
        if match.found is None:
            return _no_customer(params, match, customer_id)
        customer = match.found
        try:
            catalog = lookups.items(client, ctx.user_id)
            rates = lookups.tax_rates(client, ctx.user_id)
            numbers = lookups.numbering(client, ctx.user_id, income_type)
        except AccutaxApiError as e:
            return _accutax_failed(e)

    lines, warnings = _lines(raw_lines, catalog, rates)
    per_line, doc = totals.all_totals([l["calc"] for l in lines])
    invoice_date = _date(params.get("invoice_date"), "invoice_date") or _today()
    due_date = _date(params.get("due_date"), "due_date")
    if due_date is None:
        days = params.get("due_in_days")
        if days in (None, ""):
            days = numbers.due_days
        if days in (None, ""):
            days = DEFAULT_DUE_DAYS
            warnings.append(f"The {'due' if income_type == 'INVOICE' else 'valid-until'} date is set {DEFAULT_DUE_DAYS} "
                            "days after the date; change it if needed.")
        due_date = invoice_date + dt.timedelta(days=int(days))
    if due_date < invoice_date:
        raise ToolInputError("due_date cannot be before invoice_date")
    if numbers.has_custom_columns:
        warnings.append("Your invoice settings add custom columns to lines; this draft leaves them empty.")
    if not numbers.suggested:
        warnings.append("Accutax suggested no number; set one when you open the draft.")

    notes = str(params.get("notes") or "").strip()[:2000]
    payload = _payload(income_type, ctx.user_id, org, customer.id, invoice_date, due_date, numbers.suggested, notes,
                       lines, per_line)
    view = _view(income_type, customer, invoice_date, due_date, numbers.suggested, notes, lines, per_line, doc,
                 warnings)
    action = pending.create(KIND, user_id=ctx.user_id, organization_id=org, session_id=ctx.session_id,
                            payload=payload, view=view)
    action_audit.step("prepared", action_id=action.id, kind=KIND, user_id=ctx.user_id, organization_id=org,
                      session_id=ctx.session_id, payload=payload, document_type=income_type,
                      lines=len(lines), replaces=replaces)
    if replaces:
        _discard_replaced(replaces, ctx, org)
    from gemini_brain.invoicing.blocks import card
    ctx.cards.append(card(action))
    return {
        "status": "draft_ready",
        "draft_id": action.id,
        "document_type": LABELS[income_type],
        "customer": customer.label,
        "currency": view["currency"],
        "lines": [{"description": l["description"], "quantity": l["calc"].quantity,
                   "unit_price": totals.money(l["calc"].unit_price), "vat": l["vat_label"],
                   "line_total": totals.money(t.line_total)} for l, t in zip(lines, per_line)],
        "subtotal": totals.money(doc.subtotal), "vat": totals.money(doc.vat), "total": totals.money(doc.total),
        "date": invoice_date.isoformat(), "due_date": due_date.isoformat(),
        "suggested_number": numbers.suggested or None,
        "warnings": warnings,
        "note": SAVE_NOTE,
    }


def _lines(raw_lines: List[Any], catalog: List[lookups.Item],
           rates: List[lookups.TaxRate]) -> Tuple[List[Dict[str, Any]], List[str]]:
    out, warnings = [], []
    for n, raw in enumerate(raw_lines, start=1):
        if not isinstance(raw, dict):
            raise ToolInputError(f"line {n} must be an object")
        description = str(raw.get("description") or "").strip()[:500]
        if not description:
            raise ToolInputError(f"line {n} needs a description")
        item = lookups.match_item(description, catalog)
        price = _number(raw.get("unit_price"), f"line {n} unit_price")
        if price is None:
            if item is None or item.unit_price is None:
                raise ToolInputError(f"line {n} ({description}) has no price and no catalog item with a price: ask "
                                     "the person for the price")
            price = item.unit_price
        treatment = str(raw.get("vat") or "").strip().lower() or None
        if treatment and treatment not in TREATMENTS:
            raise ToolInputError(f"line {n} vat must be one of {', '.join(TREATMENTS)}")
        rate = lookups.pick_rate(rates, treatment, None if treatment else (item.revenue_tax_rate_id if item else None))
        if rate is None:
            warnings.append(f"Line {n} ({description}) has no VAT rate: no matching tax rate is set up in Accutax. "
                            "Check it before saving.")
        if treatment and treatment != "standard" and rate is not None:
            warnings.append(f"Line {n} ({description}) is {rate.label.lower()}: make sure the sale qualifies.")
        calc = totals.Line(quantity=_number(raw.get("quantity"), f"line {n} quantity"), unit_price=price,
                           discount_percent=_number(raw.get("discount_percent"), f"line {n} discount_percent", 0.0),
                           discount_amount=_number(raw.get("discount_amount"), f"line {n} discount_amount", 0.0),
                           tax_rate=rate.rate if rate else None)
        try:
            totals.check(calc)
        except totals.TotalsError as e:
            raise ToolInputError(f"line {n}: {e}") from None
        out.append({"description": description, "item": item, "rate": rate, "calc": calc,
                    "vat_label": rate.label if rate else "No VAT rate"})
    return out, warnings


def _iso(day: dt.date) -> str:
    return dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", ".000Z")


def _payload(income_type: str, user_id: int, org: int, contact_id: int, invoice_date: dt.date, due_date: dt.date,
             number: str, notes: str, lines: List[Dict[str, Any]], per_line: List[totals.LineTotals]) -> Dict[str, Any]:
    """POST income/create, as the form builds it for a draft (CreateNew.jsx handleSubmit)."""
    return {
        "income_type": income_type,
        "user_id": int(user_id),
        "organization_id": int(org),
        "contact_id": int(contact_id),
        "status_type_id": 1,
        "payment_type_id": 1,
        "is_recurring": False,
        "is_draft": True,
        "invoice_date": _iso(invoice_date),
        "due_date": _iso(due_date),
        "invoice_number": number,
        "additional_notes": notes,
        "terms_and_conditions": "",
        "attachments": [],
        "line_items": [{
            "items_id": l["item"].id if l["item"] else None,
            "description": l["description"],
            "account_id": l["item"].revenue_account_id if l["item"] else None,
            "quantity": l["calc"].quantity,
            "unit_price": l["calc"].unit_price,
            "discount_percent": l["calc"].discount_percent,
            "discount_amount": l["calc"].discount_amount,
            "tax_rate_id": l["rate"].id if l["rate"] else None,
            "tax_amount": t.tax,
            "line_amount": t.line_total,
            "cost_center_id": None,
        } for l, t in zip(lines, per_line)],
    }


def _view(income_type: str, customer: lookups.Customer, invoice_date: dt.date, due_date: dt.date, number: str,
          notes: str, lines: List[Dict[str, Any]], per_line: List[totals.LineTotals], doc: totals.DocumentTotals,
          warnings: List[str]) -> Dict[str, Any]:
    """What the card shows. Built in code; the model never writes it."""
    return {
        "document_type": income_type,
        "document_label": LABELS[income_type],
        "customer": {"id": customer.id, "name": customer.label, "trn": customer.trn, "email": customer.email},
        "currency": customer.currency or "AED",
        "number": number,
        "date": invoice_date.isoformat(),
        "due_date": due_date.isoformat(),
        "due_label": "Due" if income_type == "INVOICE" else "Valid until",
        "notes": notes,
        "lines": [{
            "description": l["description"],
            "item": l["item"].name if l["item"] else None,
            "quantity": l["calc"].quantity,
            "unit_price": totals.money(l["calc"].unit_price),
            "discount": (f"{l['calc'].discount_percent:g}%" if l["calc"].discount_percent else "")
                        + (" + " if l["calc"].discount_percent and l["calc"].discount_amount else "")
                        + (totals.money(l["calc"].discount_amount) if l["calc"].discount_amount else ""),
            "vat": l["vat_label"],
            "vat_amount": totals.money(t.tax),
            "line_total": totals.money(t.line_total),
        } for l, t in zip(lines, per_line)],
        "subtotal": totals.money(doc.subtotal),
        "vat": totals.money(doc.vat),
        "total": totals.money(doc.total),
        "warnings": warnings,
    }


def _no_customer(params: Dict[str, Any], match: lookups.CustomerMatch, customer_id: Optional[int]) -> Dict[str, Any]:
    if customer_id is not None:
        raise ToolInputError("customer_id is not a customer of this organization")
    asked = str(params.get("customer") or "").strip()
    if not asked:
        return {"status": "customer_needed", "note": "Ask the person who the customer is. Nothing was prepared."}
    if match.candidates:
        return {"status": "choose_customer", "asked": asked,
                "candidates": [{"customer_id": c.id, "name": c.label} for c in match.candidates],
                "note": ("Several customers could be meant. List these names and ask which one; then call again with "
                         "its customer_id. Never pick one yourself. Nothing was prepared.")}
    return {"status": "customer_not_found", "asked": asked,
            "note": ("No customer of this organization has that name. Ask the person to check the name, or to add "
                     "the customer in Accutax first. Nothing was prepared.")}


def _accutax_failed(e: AccutaxApiError) -> Dict[str, Any]:
    return {"status": "accutax_error", "error_kind": e.kind,
            "note": f"{e.user_message} Tell the person this in one sentence. Nothing was prepared."}


def _discard_replaced(action_id: str, ctx: Any, org: int) -> None:
    try:
        old = pending.discard(action_id, user_id=ctx.user_id)
        action_audit.step("discarded", action_id=old.id, kind=KIND, user_id=ctx.user_id, organization_id=org,
                          session_id=ctx.session_id, reason="replaced")
    except pending.ActionStateError:
        pass  # already saved or expired: the old card shows its own state
