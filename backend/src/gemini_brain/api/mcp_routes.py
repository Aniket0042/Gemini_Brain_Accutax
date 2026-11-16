"""
mcp_routes.py — The MCP connector's data tools: the agent's own tools, run directly, without our model.

ask_accutax runs our agent (our model, our tokens). These endpoints let the host model (ChatGPT, Claude) reason
for itself over plain facts: one tool call, no model call on our side, so no credits are charged. The tools are
the ones the agent already uses (agent/tools.py), so the figures are the same as in an Accutax answer.

Every call goes through the same gates as a question: authorize_org_scope (one organization, the caller's own;
written to the org-access audit log), and the external view of privacy/external.py (names and ID numbers as
placeholders unless the organization is in MCP_FULL_DETAIL_ORGS; withheld when masking fails). Order-like text in
the data is flagged, never removed (policy/injection.py).
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from gemini_brain.api.auth import CurrentUser, authorize_org_scope, get_current_user

logger = logging.getLogger("gemini_brain.api.mcp")

router = APIRouter(prefix="/api/v1/mcp", tags=["MCP connector"])

#: Connector tool -> the agent tool that answers it. search_vat_law needs no organization.
TOOLS = {"search_vat_law": "search_vat_kb", "vat_summary": "vat_return", "review_vat": "review_vat",
         "list_documents": "list_documents"}
#: Longest a data tool may run (the agent's own per-call limits apply inside it).
DEADLINE_SECONDS = 45.0
WITHHELD = "Accutax could not prepare this data for sharing outside Accutax. Try again, or ask in the Accutax app."


class DataRequest(BaseModel):
    organization_id: Optional[int] = Field(default=None, description="Omit for the caller's first organization.")
    params: Dict[str, Any] = Field(default_factory=dict, description="The agent tool's own input.")


def _scope(user: CurrentUser, organization_id: Optional[int], tool: str) -> list[int]:
    orgs = authorize_org_scope([organization_id] if organization_id is not None else [], user, action=f"mcp.{tool}")
    scope = orgs or [int(o) for o in (user.allowed_org_ids or [])[:1]]
    if not scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization is available for this account.")
    return scope


@router.post("/data/{tool}", summary="Run one MCP data tool (no model call)")
def run_data_tool(
    tool: Literal["search_vat_law", "vat_summary", "review_vat", "list_documents"],
    payload: DataRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    from gemini_brain.agent import tools
    from gemini_brain.policy import injection
    from gemini_brain.privacy import external

    name = TOOLS[tool]
    orgs = [] if tool == "search_vat_law" else _scope(current_user, payload.organization_id, tool)
    subject = f"mcp:{current_user.user_id}"
    ctx = tools.ToolContext(organization_ids=orgs, subject=subject, deadline=time.monotonic() + DEADLINE_SECONDS,
                            user_id=current_user.user_id,
                            authorize=(lambda: current_user.ensure_current_scope(orgs)) if orgs else None)
    params = {k: v for k, v in (payload.params or {}).items() if k != "organization_ids"}
    result, ok = tools.execute(name, params, ctx)
    if not ok:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=str(result.get("error") or "That could not be answered."))
    if tool == "search_vat_law":
        # Published FTA documents: nothing of the customer's to hide.
        return {"tool": tool, "result": {**result, "sources": ctx.fta_sources}, "masked": False, "placeholders": 0}
    flags = injection.scan_value(result)
    try:
        shared, masked, hidden = external.data(result, orgs, subject=subject)
    except external.Withheld:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=WITHHELD) from None
    if flags:
        from gemini_brain.agent import audit
        audit.event("injection_in_results", user_id=current_user.user_id, organization_ids=orgs, tool=f"mcp.{tool}",
                    patterns=flags)
    return {"tool": tool, "organization_id": orgs[0], "result": shared, "masked": masked, "placeholders": hidden,
            "injection": flags}
