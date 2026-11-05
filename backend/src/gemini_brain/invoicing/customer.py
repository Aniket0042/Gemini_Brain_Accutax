"""customer.py: the create_customer tool. It prepares a new customer; it never writes to Accutax.

A sales draft needs a customer that exists. When the person names a new one, this tool prepares the customer with
the few details Accutax requires (name, email) and an optional TRN and phone. A card lets the person create it with
a click (confirm.py, POST contact/create as them). Then they ask for the invoice again.

Accutax fills the address, country and bank fields of a new contact with placeholders; the card says so.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from gemini_brain.accutax_api.client import AccutaxApiError, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.invoicing import draft, lookups
from gemini_brain.semantic.catalog import ToolInputError

KIND = "customer"
CUSTOMER_TYPE_ID = lookups.CUSTOMER_TYPE_ID
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SPEC = {"toolSpec": {
    "name": "create_customer",
    "description": (
        "Prepare a new customer for the organization, when a sales draft names a customer that does not exist. "
        "Accutax needs the name and an email. Ask the person for the email if they did not give it. Nothing is "
        "saved by this call; a card lets the person create the customer."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The customer's name."},
            "email": {"type": "string"},
            "trn": {"type": "string", "description": "The customer's 15-digit UAE tax registration number, if given."},
            "phone": {"type": "string"},
            "organization_id": {"type": "integer", "description": "Required when several organizations are selected."},
            "create_anyway": {"type": "boolean",
                              "description": "True only after the person confirmed a customer with a similar name "
                                             "is a different one."},
        },
        "required": ["name"],
    }},
}}

SAVE_NOTE = ("A card with this customer and a Create customer button is shown under your answer. The customer "
             "does not exist until the person clicks it: never say it is created.")


def run(params: Dict[str, Any], ctx: Any) -> Dict[str, Any]:
    name = " ".join(str(params.get("name") or "").split())[:200]
    if not name:
        raise ToolInputError("name is required")
    email = str(params.get("email") or "").strip()
    if not email:
        return {"status": "email_needed", "note": "Accutax needs an email for a new customer. Ask the person for it. "
                                                    "Nothing was prepared."}
    if not _EMAIL.match(email) or len(email) > 200:
        raise ToolInputError("email is not a valid email address")
    trn = "".join(str(params.get("trn") or "").split())
    if trn and not (trn.isdigit() and len(trn) == 15):
        raise ToolInputError("trn must be 15 digits")
    phone = str(params.get("phone") or "").strip()[:40]
    org = draft._organization(params, ctx)

    with client_for(ctx.accutax, org) as client:
        try:
            pool = lookups.customers(client, ctx.user_id)
        except AccutaxApiError as e:
            return draft._accutax_failed(e)
    match = lookups.match_customer(name, pool)
    if match.found is not None:
        return {"status": "customer_exists", "customer": match.found.label,
                "note": ("A customer with that name already exists. Tell the person and use it for the draft. "
                         "Nothing was prepared.")}
    if match.candidates and params.get("create_anyway") is not True:
        return {"status": "similar_customers", "candidates": [c.label for c in match.candidates],
                "note": ("Customers with similar names exist. List them and ask whether the person means one of "
                         "them, or a new customer (then call again with create_anyway true). Nothing was prepared.")}

    # Accutax's contacts table has NOT NULL columns that its create code fills with a placeholder only when the
    # block that holds them is missing: image_url, last_name and phone_number (base), trn_number and website
    # (personal). Sending the personal block without them makes the insert fail with a 500, so they are sent here.
    payload = {
        "base": {"user_id": int(ctx.user_id), "organization_id": int(org), "organization_name": name, "salutation": "",
                 "name": name, "last_name": "", "email": email, "image_url": "", "phone_number": phone,
                 "contact_type_id": CUSTOMER_TYPE_ID},
        "personal": {"currency": "AED", "portal_access": False, "trn_number": trn or "N/A", "website": "N/A"},
    }
    view = {"kind": "customer", "name": name, "email": email, "trn": trn, "phone": phone, "currency": "AED",
            "warnings": ["The address, country and bank details are left empty; add them in Accutax."]
            + ([] if trn else ["No TRN was given; add it in Accutax if the customer has one."])}
    action = pending.create(KIND, user_id=ctx.user_id, organization_id=org, session_id=ctx.session_id,
                            payload=payload, view=view)
    action_audit.step("prepared", action_id=action.id, kind=KIND, user_id=ctx.user_id, organization_id=org,
                      session_id=ctx.session_id, payload=payload)
    from gemini_brain.invoicing.blocks import card
    ctx.cards.append(card(action))
    return {"status": "customer_ready", "draft_id": action.id, "name": name, "has_trn": bool(trn),
            "warnings": view["warnings"], "note": SAVE_NOTE}
