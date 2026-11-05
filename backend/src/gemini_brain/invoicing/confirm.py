"""confirm.py: the person clicked Save as draft. Write the prepared draft to Accutax, at most once, as that person.

The payload comes from the pending store, never from the request. pending.claim() makes the save run once however
many times the button is pressed. The create runs synchronously (?sync=1) so the card can show the saved number.

Outcomes:
  saved                 -> done, with the Accutax id and number
  Accutax refused it    -> back to pending with the reason (nothing was written; the person can fix and retry)
  outcome unknown       -> failed (a timeout or dropped connection after the request left): the card says to check
                           Accutax's drafts before trying again, so a second click cannot make a duplicate
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from gemini_brain.accutax_api.client import AccutaxApiError, AccutaxCredential, client_for
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.invoicing import blocks, lookups
from gemini_brain.invoicing.draft import KIND

logger = logging.getLogger("gemini_brain.invoicing.confirm")

#: Refusals that leave nothing written: the draft goes back to pending so it can be saved after a fix.
RETRYABLE = frozenset({"auth", "permission", "validation", "conflict", "not_found", "unavailable"})


def confirm(action_id: str, *, user_id: int, organization_id: int, credential: Optional[AccutaxCredential],
            expected_version: Optional[int] = None) -> pending.PendingAction:
    """Raises pending.ActionStateError when the draft cannot be saved now (not found, expired, already saved...)."""
    action = pending.claim(action_id, user_id=user_id, organization_id=organization_id,
                           expected_version=expected_version)
    audit = dict(action_id=action.id, kind=KIND, user_id=user_id, organization_id=organization_id,
                 session_id=action.session_id)
    action_audit.step("confirm_started", payload=action.payload, **audit)
    try:
        with client_for(credential, organization_id) as client:
            body = client.post("income/create", action.payload, params={"sync": "1"})
        record = lookups.unwrap(body)
    except AccutaxApiError as e:
        retry = e.kind in RETRYABLE
        action_audit.step("failed", error_kind=e.kind, status=e.status, retry=retry, **audit)
        message = e.user_message if retry else (
            "Accutax did not confirm the save in time. Check your drafts in Accutax before saving again, so it is "
            "not created twice.")
        return pending.fail(action.id, kind=e.kind, message=message, retry=retry)
    except Exception as e:  # noqa: BLE001 - unknown outcome: never let the person save twice blindly
        logger.warning("confirm %s failed: %s", action.id, type(e).__name__, exc_info=True)
        action_audit.step("failed", error_kind="error", retry=False, **audit)
        return pending.fail(action.id, kind="error", retry=False,
                            message="Something went wrong while saving. Check your drafts in Accutax before saving again.")
    result = _result(record, action.payload)
    action_audit.step("confirmed", record_id=result.get("record_id"), number=result.get("number"), **audit)
    return pending.finish(action.id, result=result)


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
    action_audit.step("discarded", action_id=action.id, kind=KIND, user_id=user_id,
                      organization_id=action.organization_id, session_id=action.session_id, reason="person")
    return action
