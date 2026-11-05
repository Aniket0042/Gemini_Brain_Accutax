"""confirm.py: the person clicked the card's button. Write the prepared action to Accutax, at most once, as them.

The payload comes from the pending store, never from the request. pending.claim() makes the write run once however
many times the button is pressed. What runs depends on the action's kind:
  sales_document         POST income/create?sync=1      a new draft invoice or quote
  sales_document_update  PUT income/update              a change to a draft saved earlier (after re-checking it)
  customer               POST contact/create            a new customer
  sales_document_finalise PUT income/finalise/{id}      posts a draft invoice to the books (finalise.py)

Outcomes:
  saved                 -> done, with the Accutax id (and number)
  Accutax refused it    -> back to pending with the reason (nothing was written; the person can fix and retry)
  outcome unknown       -> failed (a timeout or dropped connection after the request left): the card says to check
                           Accutax before trying again, so a second click cannot make a duplicate
  not applicable        -> failed with the reason, nothing written (the saved draft was finalised or removed)
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from gemini_brain.accutax_api.client import AccutaxApiError, AccutaxClient, AccutaxCredential, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.invoicing import blocks, customer, draft, existing, finalise, lookups

logger = logging.getLogger("gemini_brain.invoicing.confirm")

#: Refusals that leave nothing written: the draft goes back to pending so it can be saved after a fix.
RETRYABLE = frozenset({"auth", "permission", "validation", "conflict", "not_found", "unavailable"})
UNKNOWN_OUTCOME = ("Accutax did not confirm the save in time. Check Accutax before saving again, so it is not "
                   "created twice.")


def confirm(action_id: str, *, user_id: int, organization_id: int, credential: Optional[AccutaxCredential],
            expected_version: Optional[int] = None) -> pending.PendingAction:
    """Raises pending.ActionStateError when the action cannot be saved now (not found, expired, already saved...)."""
    action = pending.claim(action_id, user_id=user_id, organization_id=organization_id,
                           expected_version=expected_version)
    audit = dict(action_id=action.id, kind=action.kind, user_id=user_id, organization_id=organization_id,
                 session_id=action.session_id)
    action_audit.step("confirm_started", payload=action.payload, **audit)
    run = _RUNNERS.get(action.kind)
    if run is None:
        action_audit.step("failed", error_kind="error", retry=False, **audit)
        return pending.fail(action.id, kind="error", retry=False, message="This kind of draft cannot be saved here.")
    try:
        with client_for(credential, organization_id) as client:
            result = run(client, action)
    except existing.DocumentError as e:
        action_audit.step("failed", error_kind="not_applicable", retry=False, **audit)
        return pending.fail(action.id, kind="conflict", retry=False, message=str(e))
    except AccutaxApiError as e:
        retry = e.kind in RETRYABLE
        action_audit.step("failed", error_kind=e.kind, status=e.status, retry=retry, **audit)
        return pending.fail(action.id, kind=e.kind, retry=retry, message=e.user_message if retry else UNKNOWN_OUTCOME)
    except Exception as e:  # noqa: BLE001 - unknown outcome: never let the person save twice blindly
        logger.warning("confirm %s failed: %s", action.id, type(e).__name__, exc_info=True)
        action_audit.step("failed", error_kind="error", retry=False, **audit)
        return pending.fail(action.id, kind="error", retry=False,
                            message="Something went wrong while saving. Check Accutax before saving again.")
    action_audit.step("confirmed", record_id=result.get("record_id"), number=result.get("number"), **audit)
    return pending.finish(action.id, result=result)


def _create_document(client: AccutaxClient, action: pending.PendingAction) -> Dict[str, Any]:
    body = client.post("income/create", action.payload, params={"sync": "1"})
    return _result(lookups.unwrap(body), action.payload)


def _update_document(client: AccutaxClient, action: pending.PendingAction) -> Dict[str, Any]:
    """Re-read the saved draft first: it must still be in this organization and still a draft. Accutax's update
    does neither check, and a posted invoice must never be rewritten from chat."""
    income_id = int(action.payload["income_id"])
    doc = existing.load(client, income_id)
    if not doc.is_draft or doc.is_posted:
        raise existing.DocumentError(
            f"{doc.number or 'That document'} is no longer a draft in Accutax, so it cannot be changed from chat. "
            "Open it in Accutax to edit it.")
    lookups.unwrap(client.put("income/update", action.payload))
    return {"record_id": income_id, "number": action.payload.get("invoice_number") or doc.number,
            "url": blocks.record_url(income_id), "updated": True}


def _create_customer(client: AccutaxClient, action: pending.PendingAction) -> Dict[str, Any]:
    record = lookups.unwrap(client.post("contact/create", action.payload))
    record = record if isinstance(record, dict) else {}
    return {"customer_id": record.get("id"), "name": action.payload["base"]["name"]}


_RUNNERS: Dict[str, Callable[[AccutaxClient, pending.PendingAction], Dict[str, Any]]] = {
    draft.KIND: _create_document,
    draft.UPDATE_KIND: _update_document,
    customer.KIND: _create_customer,
    finalise.KIND: finalise.finalise_document,
}


def _result(record: Any, payload: Dict[str, Any]) -> Dict[str, Any]:
    record = record if isinstance(record, dict) else {}
    record_id = record.get("id")
    number = str(record.get("invoice_number") or payload.get("invoice_number") or "")
    out = {"record_id": record_id, "number": number, "url": blocks.record_url(record_id)}
    if record_id in (None, "") and record.get("correlation_id"):
        out["correlation_id"] = record.get("correlation_id")  # queued: Accutax finishes it shortly
    return out


def discard(action_id: str, *, user_id: int) -> pending.PendingAction:
    action = pending.discard(action_id, user_id=user_id)
    action_audit.step("discarded", action_id=action.id, kind=action.kind, user_id=user_id,
                      organization_id=action.organization_id, session_id=action.session_id, reason="person")
    return action
