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


#: review_vat's result for an outside host: the summary first, then the details. `rows` (the same findings again,
#: flat, for the app's table) is left out, and so is the glossary of rules that found nothing.
_REVIEW_ORDER = ("mode", "side", "period", "bills_checked", "groups_found", "findings_found", "amount_counted_twice",
                 "groups_by_rule", "findings_by_rule", "findings_by_severity", "notes", "organizations", "groups",
                 "findings", "patterns", "rules", "law_questions")
_REVIEW_DROP = {"rows", "row_count"}


def _review_for_host(result: Dict[str, Any]) -> Dict[str, Any]:
    fired = {r for key in ("groups_by_rule", "findings_by_rule") for r, n in (result.get(key) or {}).items() if n}
    out: Dict[str, Any] = {}
    for key in _REVIEW_ORDER + tuple(k for k in result if k not in _REVIEW_ORDER):
        if key not in result or key in _REVIEW_DROP or key in out:
            continue
        value = result[key]
        if key in ("groups_by_rule", "findings_by_rule") and isinstance(value, dict):
            value = {r: n for r, n in value.items() if n}
        elif key == "rules" and isinstance(value, dict):
            value = {r: text for r, text in value.items() if r in fired}
        elif key == "dismissed" and not value:
            continue
        out[key] = value
    return out


def _scope(user: CurrentUser, organization_id: Optional[int], tool: str) -> list[int]:
    orgs = authorize_org_scope([organization_id] if organization_id is not None else [], user, action=f"mcp.{tool}")
    scope = orgs or [int(o) for o in (user.allowed_org_ids or [])[:1]]
    if not scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization is available for this account.")
    return scope


def _search_scope(user: CurrentUser) -> list[int]:
    """Every organization the caller may read, up to the per-query limit: search has no organization argument."""
    from gemini_brain.config.settings import settings
    allowed = [int(o) for o in (user.allowed_org_ids or [])][:max(1, settings.max_orgs_per_query)]
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No organization is available for this account.")
    return authorize_org_scope(allowed, user, action="mcp.search")


@router.post("/data/{tool}", summary="Run one MCP data tool (no model call)")
def run_data_tool(
    tool: Literal["search_vat_law", "vat_summary", "review_vat", "list_documents", "run_report", "search", "fetch"],
    payload: DataRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Dict[str, Any]:
    from gemini_brain.agent import tools
    from gemini_brain.api import mcp_data
    from gemini_brain.policy import injection
    from gemini_brain.privacy import external
    from gemini_brain.semantic.catalog import ToolInputError

    params = {k: v for k, v in (payload.params or {}).items() if k != "organization_ids"}
    if tool == "search_vat_law":
        orgs: list[int] = []
    elif tool == "search":
        orgs = _search_scope(current_user)
    elif tool == "fetch":
        try:
            _, org, _ = mcp_data.parse_id(params.get("id"))
        except ToolInputError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
        orgs = _scope(current_user, org, tool)
    else:
        orgs = _scope(current_user, payload.organization_id, tool)
    subject = f"mcp:{current_user.user_id}"
    ctx = tools.ToolContext(organization_ids=orgs, subject=subject, deadline=time.monotonic() + DEADLINE_SECONDS,
                            user_id=current_user.user_id, compact_law=True,
                            authorize=(lambda: current_user.ensure_current_scope(orgs)) if orgs else None)

    def run(name: str, args: Dict[str, Any], scope: Any) -> Dict[str, Any]:
        result, ok = tools.execute(name, {**args, "organization_ids": list(scope)}, ctx)
        if not ok:
            raise ToolInputError(str(result.get("error") or "That could not be answered."))
        return result

    if tool in mcp_data.COMPOSITE:
        try:
            result = mcp_data.COMPOSITE[tool](params, run, orgs)
        except ToolInputError as e:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)) from None
    else:
        result, ok = tools.execute(TOOLS[tool], params, ctx)
        if not ok:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                                detail=str(result.get("error") or "That could not be answered."))
    if tool == "search_vat_law":
        # Published FTA documents: nothing of the customer's to hide. Only the sources whose passages were sent.
        text = str(result.get("sources_and_rules") or "")
        sources = [{k: s.get(k) for k in ("n", "title", "url", "issued")} for s in ctx.fta_sources
                   if f"[{s.get('n')}] " in text]
        return {"tool": tool, "result": {**result, "sources": sources}, "masked": False, "placeholders": 0}
    if tool == "review_vat":
        result = _review_for_host(result)
    flags = injection.scan_value(result)
    try:
        shared, masked, hidden = external.data(result, orgs, subject=subject)
    except external.Withheld:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=WITHHELD) from None
    if flags:
        from gemini_brain.agent import audit
        audit.event("injection_in_results", user_id=current_user.user_id, organization_ids=orgs, tool=f"mcp.{tool}",
                    patterns=flags)
    return {"tool": tool, "organization_id": orgs[0] if len(orgs) == 1 else None, "result": shared, "masked": masked,
            "placeholders": hidden, "injection": flags}
