"""
privacy_routes.py — The privacy page's API: before/after snapshots of PII masking (PII plan, phase 4).

Only PII_SNAPSHOT_ADMINS may read snapshots; everyone else gets 403 (and /access tells the app whether to show
the page at all). A snapshot's raw side holds personal data of any user, so each read is written to the audit log.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

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
