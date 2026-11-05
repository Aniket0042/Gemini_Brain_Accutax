"""finalise.py: the finalise_sales_invoice tool. It prepares the finalising of a draft invoice; it never writes.

Finalising posts the invoice to the books: Accutax flips it out of draft, sets it to pending and creates the journal
entry. That is the one step here that changes the ledger, so it has its own tool, its own card and its own click, and
the tool is off until AI_WRITE_TOOLS names it.

Two Accutax facts make it risky, and two guards answer them:
  * PUT income/finalise/{id} posts the journal entry in the organization of the person's token, whatever
    organization the request names. The tool is refused unless the token's organization is the one chosen.
  * Accutax flips the invoice out of draft before it creates the journal entry, and does not undo the flip when the
    entry fails. A failed finalise is therefore reported as "outcome unknown: check the invoice in Accutax".

The target is the invoice saved earlier in this chat, or a draft invoice named by its number. The record is read
first: it must be an invoice, a draft, not posted, and in the chosen organization.
"""
from __future__ import annotations

from typing import Any, Dict

from gemini_brain.accutax_api.client import AccutaxApiError, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.invoicing import draft, existing, lookups, totals
from gemini_brain.semantic.catalog import ToolInputError

KIND = "sales_document_finalise"
FINALISABLE = ("INVOICE", "CASH_INVOICE")

SPEC = {"toolSpec": {
    "name": "finalise_sales_invoice",
    "description": (
        "Prepare the finalising of a draft sales invoice: it posts the invoice to the books. Use it only when the "
        "person asks to finalise, post or confirm an invoice. Without invoice_number it takes the invoice saved "
        "earlier in this chat. Nothing is posted by this call; a card lets the person finalise with a click."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "invoice_number": {"type": "string", "description": "The number of a draft invoice, e.g. INV-1048."},
            "organization_id": {"type": "integer", "description": "Required when several organizations are selected."},
        },
    }},
}}

SAVE_NOTE = ("A card with this invoice and a Finalise and post button is shown under your answer. Nothing is posted "
             "to the books until the person clicks it: never say it is finalised, posted or sent.")


def _unavailable(note: str, status: str = "finalise_unavailable") -> Dict[str, Any]:
    return {"status": status, "note": f"{note} Nothing was prepared."}


def run(params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    org = draft._organization(params, ctx)
    if ctx.accutax.organization_id() != org:
        return _unavailable("Posting to the books from chat needs the person's Accutax sign-in to be on this "
                            "organization, and it is not. Tell them to finalise the invoice in Accutax.")
    number_text = str(params.get("invoice_number") or "").strip()

    with client_for(ctx.accutax, org) as client:
        try:
            if number_text:
                rows = existing.pick(existing.list_documents(client, ctx.user_id, "INVOICE", number=number_text),
                                     number_text)
                if not rows:
                    return _unavailable(f"No invoice of this organization has the number {number_text}.",
                                        "source_not_found")
                if len(rows) > 1:
                    return _unavailable("Several invoices have that number. Ask which one.", "choose_source")
                income_id = rows[0].id
            else:
                saved = pending.latest_in_chat(draft.DOCUMENT_KINDS, user_id=ctx.user_id, organization_id=org,
                                               session_id=ctx.session_id, statuses=(pending.DONE,))
                open_ = pending.latest_in_chat(draft.DOCUMENT_KINDS, user_id=ctx.user_id, organization_id=org,
                                               session_id=ctx.session_id, statuses=(pending.PENDING,))
                if open_ is not None and (saved is None or open_.created_at > saved.created_at):
                    return _unavailable("The newest draft in this chat is not saved yet. Tell the person to click "
                                        "Save as draft on its card first.", "save_first")
                if saved is None or saved.view.get("document_type") != "INVOICE":
                    return _unavailable("No invoice was saved in this chat. Ask which invoice number to finalise.",
                                        "nothing_to_finalise")
                income_id = (saved.result or {}).get("record_id")
                if income_id in (None, ""):
                    return _unavailable("Accutax has not finished saving that invoice. Try again in a moment.",
                                        "nothing_to_finalise")
            doc = existing.load(client, int(income_id))
            customers = {c.id: c for c in lookups.customers(client, ctx.user_id)}
        except existing.DocumentError as e:
            return _unavailable(str(e), "source_not_found")
        except AccutaxApiError as e:
            return draft._accutax_failed(e)

    if doc.income_type not in FINALISABLE:
        return _unavailable(f"{doc.number} is a {doc.income_type.lower().replace('_', ' ')}: only invoices can be "
                            "finalised.", "not_a_draft")
    if not doc.is_draft or doc.is_posted:
        return _unavailable(f"{doc.number} is already finalised in Accutax.", "not_a_draft")
    if not doc.lines:
        return _unavailable(f"{doc.number} has no lines that can be read, so it cannot be finalised from chat.")

    customer = customers.get(doc.contact_id)
    gross = totals.round2(sum(l["line_amount"] for l in doc.lines))
    vat = totals.round2(sum(l["tax_amount"] for l in doc.lines))
    view = {
        "kind": "finalise", "number": doc.number, "customer": customer.label if customer else "",
        "currency": (customer.currency if customer else "") or "AED", "date": doc.date, "due_date": doc.due_date,
        "lines": len(doc.lines), "subtotal": totals.money(totals.round2(gross - vat)), "vat": totals.money(vat),
        "total": totals.money(gross),
        "warnings": ["Finalising posts this invoice to your books. To undo it you must void it in Accutax."],
    }
    payload = {"income_id": int(doc.id), "invoice_number": doc.number}
    action = pending.create(KIND, user_id=ctx.user_id, organization_id=org, session_id=ctx.session_id,
                            payload=payload, view=view)
    action_audit.step("prepared", action_id=action.id, kind=KIND, user_id=ctx.user_id, organization_id=org,
                      session_id=ctx.session_id, payload=payload, income_id=doc.id)
    from gemini_brain.invoicing.blocks import card
    ctx.cards.append(card(action))
    return {"status": "finalise_ready", "draft_id": action.id, "invoice_number": doc.number,
            "customer": view["customer"], "currency": view["currency"], "total": view["total"], "vat": view["vat"],
            "warnings": view["warnings"], "note": SAVE_NOTE}


def finalise_document(client, action: pending.PendingAction) -> Dict[str, Any]:
    """The confirm step. Everything is checked again: the token's organization, and the record itself."""
    from gemini_brain.invoicing import blocks
    income_id = int(action.payload["income_id"])
    if client.token_organization_id != client.organization_id:
        raise existing.DocumentError("Your Accutax sign-in is no longer on this organization, so the invoice cannot "
                                     "be finalised from chat. Finalise it in Accutax.")
    doc = existing.load(client, income_id)
    if doc.income_type not in FINALISABLE or not doc.is_draft or doc.is_posted:
        raise existing.DocumentError(f"{doc.number or 'That invoice'} is no longer a draft in Accutax, so it cannot "
                                     "be finalised from chat.")
    lookups.unwrap(client.put(f"income/finalise/{income_id}"))
    return {"record_id": income_id, "number": doc.number, "url": blocks.record_url(income_id), "finalised": True}
