"""copy.py: the copy_sales_document tool. It prepares a new invoice or quote from one that already exists. It never
writes to Accutax.

Two uses, one tool:
  convert   "turn quote QUO-0012 into an invoice": the new invoice links to the quote (reference_id), so Accutax
            marks the quote as converted. Only a quote can become an invoice.
  repeat    "same as the last invoice to Al Noor": a new draft with the same customer, lines and notes, dated today.

The source is read as the person. Its lines keep the amounts stored with them: nothing is recomputed, so the copy
equals the source to the fils. The copy is refused when the lines cannot all be read (existing.whole_copy). The
result is a pending action like draft_sales_document's, saved with the same card and button.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from gemini_brain.accutax_api.client import AccutaxApiError, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.invoicing import draft, existing, lookups, totals
from gemini_brain.semantic.catalog import ToolInputError

SPEC = {"toolSpec": {
    "name": "copy_sales_document",
    "description": (
        "Prepare a new sales invoice or quote as a copy of an existing one: turn a quote into an invoice, or repeat "
        "the last invoice or quote of a customer. Name the source by its number, or by its customer to take the "
        "newest. Code reads it and keeps its lines and amounts. Nothing is saved by this call; a card lets the "
        "person save the copy as a draft."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "target_type": {"type": "string", "enum": list(draft.TYPES), "description": "What to create."},
            "source_type": {"type": "string", "enum": list(draft.TYPES),
                            "description": "What to copy from. Omit to use target_type."},
            "source_number": {"type": "string", "description": "The number of the document to copy, e.g. QUO-0012."},
            "customer": {"type": "string", "description": "Copy this customer's newest document of source_type."},
            "customer_id": {"type": "integer", "description": "Only an id this tool or draft_sales_document returned."},
            "link_to_source": {"type": "boolean",
                               "description": "Quote to invoice only: mark the quote as converted. Default true."},
            "organization_id": {"type": "integer", "description": "Required when several organizations are selected."},
            "invoice_date": {"type": "string", "description": "YYYY-MM-DD; omit for today."},
            "due_in_days": {"type": "integer"},
        },
        "required": ["target_type"],
    }},
}}

SAVE_NOTE = ("A card with this copy and a Save as draft button is shown under your answer. Nothing is saved in "
             "Accutax until the person clicks it: never say it is created, converted or sent.")


def run(params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    target = draft.TYPES.get(str(params.get("target_type") or "").lower())
    source = draft.TYPES.get(str(params.get("source_type") or params.get("target_type") or "").lower())
    if target is None or source is None:
        raise ToolInputError("target_type and source_type must be invoice or quote")
    convert = source == "QUOTE" and target == "INVOICE"
    if target == "QUOTE" and source == "INVOICE":
        raise ToolInputError("An invoice cannot be turned into a quote; copy it to a new invoice instead")
    link = convert and params.get("link_to_source") is not False
    number_text = str(params.get("source_number") or "").strip()
    customer_text = str(params.get("customer") or "").strip()
    customer_id = params.get("customer_id")
    org = draft._organization(params, ctx)
    invoice_date = draft._date(params.get("invoice_date"), "invoice_date") or draft._today()

    with client_for(ctx.accutax, org) as client:
        try:
            pool = lookups.customers(client, ctx.user_id)
            found = _from_this_chat(client, ctx, org, source, number_text, customer_text, customer_id, pool)
            if found is None:
                found = _find_source(client, ctx, source, number_text, customer_text, customer_id, pool)
            if isinstance(found, dict):
                return found
            doc, converted_to, complete = found
            if link and converted_to:
                return {"status": "already_converted", "source_number": doc.number,
                        "converted_to": converted_to,
                        "note": ("That quote was already turned into an invoice. Tell the person the invoice "
                                 "number. Ask whether they want another copy without linking (link_to_source false). "
                                 "Nothing was prepared.")}
            if not doc.lines or not complete:
                return {"status": "cannot_copy", "source_number": doc.number,
                        "note": ("Some lines of that document cannot be read here, so a copy could lose one. Tell "
                                 "the person to copy it in Accutax. Nothing was prepared.")}
            customer = next((c for c in pool if c.id == doc.contact_id), None)
            if customer is None:
                return {"status": "cannot_copy", "source_number": doc.number,
                        "note": ("The customer of that document is not among this organization's customers. Tell "
                                 "the person. Nothing was prepared.")}
            rates = lookups.tax_rates(client, ctx.user_id)
            numbers = lookups.numbering(client, ctx.user_id, target)
        except existing.DocumentError as e:
            return {"status": "source_not_found", "note": f"{e} Nothing was prepared."}
        except AccutaxApiError as e:
            if e.kind in ("unavailable", "error") and (number_text or customer_text or customer_id not in (None, "")):
                return {"status": "list_unavailable",
                        "note": ("Accutax's list of documents did not answer in time for this organization. Only "
                                 "documents saved in this chat can be copied right now. Tell the person this, and "
                                 "that they can copy it in Accutax. Nothing was prepared.")}
            return draft._accutax_failed(e)

    warnings: List[str] = []
    days = params.get("due_in_days")
    if days in (None, ""):
        days = numbers.due_days
    if days in (None, ""):
        days = draft.DEFAULT_DUE_DAYS
        warnings.append(f"The {'due' if target == 'INVOICE' else 'valid-until'} date is set {days} days after the "
                        "date; change it if needed.")
    due_date = invoice_date + dt.timedelta(days=int(days))
    if numbers.has_custom_columns:
        warnings.append("Your invoice settings add custom columns to lines; this copy leaves them empty.")
    if numbers.other_organization:
        warnings.append("Your Accutax sign-in is on another organization, so the number of this one could not be "
                        "read. The number is left empty: set it when you open the draft.")
    elif not numbers.suggested:
        warnings.append("Accutax suggested no number; set one when you open the draft.")
    if doc.income_type == "QUOTE" and doc.is_draft and convert:
        warnings.append(f"Quote {doc.number} is still a draft.")

    # Lines saved by Accutax's own form hold the net plus VAT in line_amount; lines saved by the assistant hold the
    # net. Either way the copy takes the net from quantity, price and discounts (totals.py), and keeps the stored VAT.
    for l in doc.lines:
        l["net"] = _net(l)
    lines = [_line(l, rates) for l in doc.lines]
    net_lines = [totals.LineTotals(net=l["net"], tax=l["tax_amount"], line_total=totals.round2(l["net"] + l["tax_amount"]))
                 for l in doc.lines]
    docs = totals.document_totals(net_lines)
    payload = _payload(target, ctx.user_id, org, customer.id, invoice_date, due_date, numbers.suggested, doc,
                       link)
    view = draft._view_from(target, customer, invoice_date, due_date, numbers.suggested, doc.notes, lines, docs,
                            warnings)
    view["copied_from"] = {"number": doc.number, "type": draft.LABELS.get(doc.income_type, doc.income_type),
                           "linked": link}
    action = pending.create(draft.KIND, user_id=ctx.user_id, organization_id=org, session_id=ctx.session_id,
                            payload=payload, view=view)
    action_audit.step("prepared", action_id=action.id, kind=action.kind, user_id=ctx.user_id, organization_id=org,
                      session_id=ctx.session_id, payload=payload, document_type=target, lines=len(lines),
                      copied_from=doc.id, linked=link)
    from gemini_brain.invoicing.blocks import card
    ctx.cards.append(card(action))
    return {
        "status": "draft_ready", "draft_id": action.id, "document_type": draft.LABELS[target],
        "copied_from": doc.number, "linked_to_source": link, "customer": customer.label, "currency": view["currency"],
        "lines": [{"description": l["description"], "quantity": l["quantity"], "unit_price": l["unit_price"],
                   "line_total": l["line_total"]} for l in lines],
        "subtotal": totals.money(docs.subtotal), "vat": totals.money(docs.vat), "total": totals.money(docs.total),
        "date": invoice_date.isoformat(), "due_date": due_date.isoformat(),
        "suggested_number": numbers.suggested or None, "warnings": warnings, "note": SAVE_NOTE,
    }


def _from_this_chat(client, ctx, org: int, source: str, number_text: str, customer_text: str, customer_id: Any,
                    pool: List[lookups.Customer]):
    """(document, converted_to, complete) of a document saved in this chat, or None to look in the organization.

    Accutax's list of documents (income/list_filter) can take longer than any request may, on big organizations, and
    the number of a draft made here may be empty. A document this chat saved is known by its Accutax id, so it is
    read directly (income/find, which the module checks against the organization). Completeness is checked against the
    lines this chat sent: the saved document must have as many.

    With no number and no customer the newest document of that type saved here is meant. A number or customer that
    matches nothing made here returns None, so the organization's list is tried."""
    saved = pending.list_in_chat(draft.DOCUMENT_KINDS, user_id=ctx.user_id, organization_id=org,
                                 session_id=ctx.session_id, statuses=(pending.DONE,))
    seen, candidates = set(), []
    for a in saved:                                   # newest change first: one entry per Accutax record
        rid = (a.result or {}).get("record_id")
        if rid in (None, "") or rid in seen or a.view.get("document_type") != source:
            continue
        seen.add(rid)
        candidates.append(a)
    if number_text:
        want = "".join(number_text.split()).casefold()
        candidates = [a for a in candidates
                      if "".join(str((a.result or {}).get("number") or "").split()).casefold() == want]
    elif customer_text or customer_id not in (None, ""):
        try:
            cid = int(customer_id) if customer_id not in (None, "") else None
        except (TypeError, ValueError):
            raise ToolInputError("customer_id must be an id") from None
        match = lookups.match_customer(customer_text, pool, cid)
        if match.found is None:
            return None
        candidates = [a for a in candidates if (a.view.get("customer") or {}).get("id") == match.found.id]
    if not candidates:
        if not number_text and not customer_text and customer_id in (None, ""):
            raise ToolInputError(f"No {draft.LABELS[source].lower()} was saved in this chat. Give source_number, or "
                                 "the customer whose newest document to copy")
        return None
    chosen = candidates[0]
    doc = existing.load(client, int(chosen.result["record_id"]))
    if doc.income_type != source:
        raise existing.DocumentError("That document is not a " + draft.LABELS[source].lower() + ".")
    sent = len(chosen.payload.get("line_items") or [])
    return doc, doc.converted_to_number, len(doc.lines) == sent


def _find_source(client, ctx, source: str, number_text: str, customer_text: str, customer_id: Any,
                 pool: List[lookups.Customer]):
    """(document, converted_to, complete) of the one source in the organization, or a status dict for the model."""
    if not number_text and not customer_text and customer_id in (None, ""):
        raise ToolInputError("Give source_number, or the customer whose newest document to copy")
    if number_text:
        rows = existing.pick(existing.list_documents(client, ctx.user_id, source, number=number_text), number_text)
        label = draft.LABELS[source].lower()
        if not rows:
            return {"status": "source_not_found", "asked": number_text,
                    "note": f"No {label} of this organization has that number. Ask the person to check it. "
                            "Nothing was prepared."}
        if len(rows) > 1:
            return {"status": "choose_source", "candidates": [
                {"number": r.number, "customer": r.contact_name, "date": r.date} for r in rows[:8]],
                "note": "Several documents have that number. List them and ask which. Nothing was prepared."}
        row = rows[0]
    else:
        try:
            cid = int(customer_id) if customer_id not in (None, "") else None
        except (TypeError, ValueError):
            raise ToolInputError("customer_id must be an id") from None
        match = lookups.match_customer(customer_text, pool, cid)
        if match.found is None:
            return draft._no_customer({"customer": customer_text}, match, cid)
        name = match.found.label
        rows = [r for r in existing.list_documents(client, ctx.user_id, source, contact_name=name)
                if r.contact_name.casefold() == name.casefold()]
        if not rows:
            return {"status": "source_not_found", "asked": name,
                    "note": f"{name} has no {draft.LABELS[source].lower()} yet. Tell the person. Nothing was prepared."}
        row = rows[0]  # newest first
    doc = existing.load(client, row.id)
    if doc.income_type != source:
        raise existing.DocumentError("That document is not a " + draft.LABELS[source].lower() + ".")
    return doc, row.converted_to_number, existing.whole_copy(doc, row)


def _line(l: Dict[str, Any], rates: List[lookups.TaxRate]) -> Dict[str, Any]:
    rate = next((r for r in rates if r.id == l.get("tax_rate_id")), None)
    pct = f"{l['discount_percent']:g}%" if l["discount_percent"] else ""
    amt = totals.money(l["discount_amount"]) if l["discount_amount"] else ""
    return {"description": l["description"], "item": None, "quantity": l["quantity"],
            "unit_price": totals.money(l["unit_price"]), "discount": " + ".join(x for x in (pct, amt) if x),
            "vat": rate.label if rate else ("VAT" if l["tax_amount"] else "No VAT"),
            "vat_name": rate.name if rate else "",
            "vat_amount": totals.money(l["tax_amount"]), "line_total": totals.money(totals.round2(l["net"] + l["tax_amount"])),
            "line_amount": l["net"], "tax_amount": l["tax_amount"], "_raw": l}


def _net(l: Dict[str, Any]) -> float:
    """The net of a stored line. From quantity, price and discounts when they are there; else line_amount as stored
    (Accutax's backend reads it as the net)."""
    gross = l["quantity"] * l["unit_price"]
    if gross > 0:
        return totals.round2(gross - gross * (l["discount_percent"] / 100) - l["discount_amount"])
    return totals.round2(l["line_amount"])


def _payload(target: str, user_id: int, org: int, contact_id: int, invoice_date: dt.date, due_date: dt.date,
             number: str, doc: existing.Document, link: bool) -> Dict[str, Any]:
    """POST income/create for the copy, with the stored line amounts. reference_id links a converted quote."""
    body = {
        "income_type": target, "user_id": int(user_id), "organization_id": int(org), "contact_id": int(contact_id),
        "status_type_id": 1, "payment_type_id": 1, "is_recurring": False, "is_draft": True,
        "invoice_date": draft._iso(invoice_date), "due_date": draft._iso(due_date), "invoice_number": number,
        "additional_notes": doc.notes, "terms_and_conditions": doc.terms, "attachments": [],
        "line_items": [{**{k: l[k] for k in ("items_id", "description", "account_id", "quantity", "unit_price",
                                             "discount_percent", "discount_amount", "tax_rate_id", "tax_amount",
                                             "cost_center_id")}, "line_amount": l["net"]} for l in doc.lines],
    }
    if link:
        body["reference_id"] = int(doc.id)
    return body
