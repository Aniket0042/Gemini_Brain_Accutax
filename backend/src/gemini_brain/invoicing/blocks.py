"""blocks.py: the sales_document_draft card, built from the pending action only (never from model text).

The chat frontend (blocks/SalesDocumentDraftBlock.jsx) renders it and refreshes its state from
GET /api/v1/actions/{id} when a saved chat is reopened, so an old card never offers Save for a draft that was
already saved, discarded or expired.
"""
from __future__ import annotations

from typing import Any, Dict

from gemini_brain.actions.pending import PendingAction
from gemini_brain.config.settings import settings

#: The Accutax page of a saved sales document (frontend App.jsx), as agent/links.py uses it.
DETAILS_PATH = "/income/details/{id}"


def record_url(record_id: Any) -> str:
    base = (settings.accutax_app_url or "").rstrip("/")
    try:
        return f"{base}{DETAILS_PATH.format(id=int(record_id))}" if base and record_id not in (None, "") else ""
    except (TypeError, ValueError):
        return ""


#: The block type of each kind of pending action.
BLOCK_TYPES = {"sales_document": "sales_document_draft", "sales_document_update": "sales_document_draft",
               "customer": "customer_draft"}


def card(action: PendingAction) -> Dict[str, Any]:
    public = action.public()
    return {"type": BLOCK_TYPES.get(action.kind, "sales_document_draft"), "action_id": action.id, "status": public["status"],
            "version": action.version, "organization_id": action.organization_id, "expires_at": action.expires_at,
            "view": action.view, "result": action.result, "error": action.error,
            "text": _text(action)}


def _text(action: PendingAction) -> str:
    """The card as one line, for a client that does not render it (and the saved chat's plain text)."""
    v = action.view or {}
    if v.get("kind") == "customer":
        return f"New customer {v.get('name', '')}".strip()
    who = (v.get("customer") or {}).get("name") or "the customer"
    return f"{v.get('document_label', 'Draft')} for {who}: total {v.get('currency', '')} {v.get('total', '')}".strip()
