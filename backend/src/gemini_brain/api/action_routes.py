"""action_routes.py: the buttons of a draft card. Read its state, save it as a draft in Accutax, or discard it.

    GET  /api/v1/actions/{id}           the card as it is now (a reopened chat refreshes old cards with this)
    POST /api/v1/actions/{id}/confirm   save it in Accutax as the signed-in person; body {"version": n}
    POST /api/v1/actions/{id}/discard   withdraw it

Every route checks that the draft belongs to the caller, that they still have its organization, and (for confirm)
that write actions are on for them and the request carries their own Accutax bearer. A draft of someone else is
"not found", never "forbidden", so an id reveals nothing.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gemini_brain import actions
from gemini_brain.accutax_api.client import AccutaxCredential
from gemini_brain.actions import pending
from gemini_brain.api.auth import CurrentUser, get_current_user

logger = logging.getLogger("gemini_brain.api.actions")

router = APIRouter(prefix="/api/v1/actions", tags=["Write actions"])

_STATE_HTTP = {"not_found": 404, "expired": 410, "stale": 409, "busy": 409, "confirming": 409, "done": 409,
               "failed": 409, "discarded": 409}


class ConfirmRequest(BaseModel):
    version: Optional[int] = Field(default=None, ge=1, description="The draft version the card shows.")


def _owned(action_id: str, user: CurrentUser) -> pending.PendingAction:
    action = pending.get_for(action_id, user_id=user.user_id) if pending.valid_id(action_id) else None
    if action is None or action.organization_id not in (user.allowed_org_ids or []):
        raise HTTPException(404, "That draft was not found.")
    try:
        user.ensure_current_scope([action.organization_id])
    except HTTPException:
        raise HTTPException(404, "That draft was not found.") from None
    return action


def _card(action: pending.PendingAction) -> Dict[str, Any]:
    from gemini_brain.invoicing.blocks import card
    return {"block": card(action)}


def _state_error(e: pending.ActionStateError) -> HTTPException:
    return HTTPException(_STATE_HTTP.get(e.status, 409), str(e))


@router.get("/{action_id}")
def get_action(action_id: str, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    return _card(_owned(action_id, current_user))


@router.post("/{action_id}/confirm")
async def confirm_action(action_id: str, payload: ConfirmRequest,
                         current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    action = _owned(action_id, current_user)
    if not actions.enabled_for(current_user.user_id, current_user.email):
        raise HTTPException(403, "Saving drafts from chat is not switched on for you.")
    credential = AccutaxCredential.of(current_user.accutax_token)
    if credential is None:
        raise HTTPException(401, "Sign in to Accutax again, then save the draft.")
    from gemini_brain.invoicing import confirm
    try:
        saved = await asyncio.to_thread(confirm.confirm, action.id, user_id=current_user.user_id,
                                        organization_id=action.organization_id, credential=credential,
                                        expected_version=payload.version)
    except pending.ActionStateError as e:
        raise _state_error(e) from None
    return _card(saved)


@router.post("/{action_id}/discard")
def discard_action(action_id: str, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    action = _owned(action_id, current_user)
    from gemini_brain.invoicing import confirm
    try:
        return _card(confirm.discard(action.id, user_id=current_user.user_id))
    except pending.ActionStateError as e:
        raise _state_error(e) from None
