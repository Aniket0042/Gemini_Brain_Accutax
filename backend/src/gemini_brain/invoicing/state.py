"""state.py: what happened to the cards of this chat, as a short list for the model's instructions.

The model only sees the text of earlier turns. It does not see that the person clicked Save as draft, discarded a
card or that a save failed, so it said a saved quote "has not been saved yet". The list below is built in code from
the pending store and tells it the truth. It holds no names and no amounts: the privacy layer hides names from the
model, and amounts the model repeats must come from a tool result.
"""
from __future__ import annotations

from typing import Any, List

from gemini_brain.actions import pending
from gemini_brain.invoicing import draft

MAX_LINES = 8
_WHAT = {draft.KIND: "{type}", draft.UPDATE_KIND: "Change to a saved {type}", "customer": "New customer",
         "sales_document_finalise": "Finalising of an invoice"}
_DONE = {draft.KIND: "saved in Accutax as a draft", draft.UPDATE_KIND: "saved in Accutax",
         "customer": "created in Accutax", "sales_document_finalise": "done: the invoice is posted to the books"}


def describe(ctx: Any) -> str:
    """The chat's cards and their states, newest last, or "" when there are none."""
    if not getattr(ctx, "session_id", None) or not getattr(ctx, "user_id", None):
        return ""
    rows: List[tuple] = []
    for org in ctx.organization_ids:
        for a in pending.list_in_chat(tuple(_WHAT), user_id=ctx.user_id, organization_id=org,
                                      session_id=ctx.session_id, statuses=tuple(pending.STATUSES)):
            rows.append((a.created_at, org, a))
    rows.sort(key=lambda r: r[0])
    lines = []
    for _, org, a in rows[-MAX_LINES:]:
        kind_type = draft.LABELS.get(str(a.view.get("document_type") or ""), "Document")
        what = _WHAT[a.kind].format(type=kind_type)
        if a.expired():
            state = "expired (it was never clicked)"
        elif a.status == pending.PENDING:
            state = "prepared, waiting for the person to click its button"
        elif a.status == pending.DONE:
            state = _DONE[a.kind]
        elif a.status == pending.DISCARDED:
            state = "withdrawn (replaced or discarded)"
        elif a.status == pending.CONFIRMING:
            state = "being saved right now"
        else:
            state = "outcome unknown: the person must check Accutax"
        extra = f", number {a.result.get('number')}" if a.result.get("number") else ""
        lines.append(f"- {what} (organization {org}): {state}{extra}")
    if not lines:
        return ""
    return "\nCards of this chat (what really happened; the person clicks the buttons, you never do):\n" + "\n".join(lines) + "\n"
