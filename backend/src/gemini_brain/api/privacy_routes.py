"""
privacy_routes.py — The privacy page's API: before/after snapshots of PII masking (PII plan, phase 4).

Only PII_SNAPSHOT_ADMINS may read snapshots; everyone else gets 403 (and /access tells the app whether to show
the page at all). A snapshot's raw side holds personal data of any user, so each read is written to the audit log.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.config.settings import settings
from gemini_brain.privacy import snapshot_html, snapshots

logger = logging.getLogger("gemini_brain.api.privacy")

router = APIRouter(prefix="/api/v1/privacy", tags=["Privacy"])


def _admin(user: CurrentUser) -> None:
    if not snapshots.is_admin(user.user_id, user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to view privacy snapshots")


def _audit(user: CurrentUser, action: str, snapshot_id: str = "") -> None:
    try:
        from gemini_brain.agent import audit
        audit.event("privacy_snapshot_read", user_id=user.user_id, action=action, snapshot=snapshot_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("privacy audit not written: %s", e)


@router.get("/access", summary="Whether this user may open the privacy page")
def access(current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    admin = snapshots.is_admin(current_user.user_id, current_user.email)
    out: Dict[str, Any] = {"admin": admin}
    if admin:
        out.update(mode=(settings.pii_mode or "off").lower(), snapshots=snapshots.enabled(),
                   raw_kept=bool((settings.pii_snapshot_key or "").strip()),
                   raw_days=settings.pii_snapshot_raw_days, days=settings.pii_snapshot_days)
    return out


@router.get("/snapshots", summary="The newest privacy snapshots")
def list_snapshots(limit: int = Query(50, ge=1, le=500), user_id: int | None = Query(None),
                   current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    _admin(current_user)
    _audit(current_user, "list")
    return {"snapshots": snapshots.recent(limit=limit, user_id=user_id)}


@router.get("/snapshots/{snapshot_id}", summary="One snapshot, both sides")
def get_snapshot(snapshot_id: str, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    _admin(current_user)
    snap = snapshots.load(snapshot_id)
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    _audit(current_user, "view", snapshot_id)
    return snap


@router.get("/snapshots/{snapshot_id}/export", response_class=HTMLResponse, summary="One snapshot as an HTML page")
def export_snapshot(snapshot_id: str, current_user: CurrentUser = Depends(get_current_user)) -> HTMLResponse:
    _admin(current_user)
    snap = snapshots.load(snapshot_id)
    if snap is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot not found")
    _audit(current_user, "export", snapshot_id)
    return HTMLResponse(snapshot_html.render(snap), headers={
        "Content-Disposition": f'attachment; filename="privacy-snapshot-{snapshot_id}.html"',
        "Cache-Control": "no-store"})


class DemoRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    organization_ids: List[int] = Field(default_factory=list)


def _demo_orgs(user: CurrentUser, requested: List[int]) -> List[int]:
    allowed = set(int(o) for o in user.allowed_org_ids or [])
    demo = {int(o) for o in (settings.pii_demo_orgs or "").replace(" ", "").split(",") if o.strip().isdigit()}
    wanted = [int(o) for o in requested or []] or sorted(allowed)
    return [o for o in wanted if o in allowed and (not demo or o in demo)][:10]


@router.post("/demo", summary="Live before/after: ask one question with masking on and see both sides")
def demo(body: DemoRequest, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    """Runs the question through the agent with PII masking on, whatever PII_MODE says, and returns the request's
    calls as the privacy page shows a snapshot: what the request held, what the model got, what it wrote and what
    the user saw. Admins only, on PII_DEMO_ORGS. Costs a normal answer's model calls."""
    _admin(current_user)
    orgs = _demo_orgs(current_user, body.organization_ids)
    if not orgs:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Pick an organization you can open that is allowed for the demo (PII_DEMO_ORGS)")
    from gemini_brain.agent.loop import run_agent
    from gemini_brain.semantic import org_directory
    try:
        meta = org_directory.lookup(orgs, subject=f"privacy-demo:{current_user.user_id}")
    except Exception as e:  # noqa: BLE001 - names only label the answer
        logger.warning("privacy demo: organization names unavailable: %s", e)
        meta = {o: {"name": f"Organization {o}"} for o in orgs}
    result = run_agent(body.question, orgs, meta, subject=f"privacy-demo:{current_user.user_id}",
                       user_id=current_user.user_id, pii_mode="on", capture=True)
    capture = result.capture
    _audit(current_user, "demo", getattr(capture, "id", ""))
    vault = getattr(getattr(capture, "vault", None), "_value_of", {}) or {}
    return {
        "id": getattr(capture, "id", None), "mode": "on", "demo": True, "organization_ids": orgs,
        "answer": result.answer, "status": result.status, "stats": result.privacy, "raw_available": True,
        "vault": dict(vault), "calls": list(getattr(capture, "calls", None) or []),
    }
