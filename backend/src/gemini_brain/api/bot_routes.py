"""bot_routes.py — FastAPI routes for the VAT bot (Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md).
"""
from __future__ import annotations

import datetime as dt
import hmac
import logging
import time
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import JSONResponse

from gemini_brain.api.auth import (
    CurrentUser,
    authorize_org_scope,
    fetch_organizations_from_db,
    get_current_user,
    placeholder_tenants,
)
from gemini_brain.api.models import (
    BotApprovalItem,
    BotEventRequest,
    BotEventResponse,
    BotFindingItem,
    BotInboxResponse,
    BotListResponse,
    BotMemoryItem,
    BotTemplatesResponse,
    BotTickResponse,
    BotWatchesResponse,
    CheckDraftRequest,
    CreateBotRequest,
    CreateMemoryRequest,
    DismissFindingRequest,
    DraftResponse,
    EditApprovalRequest,
    FromChatRequest,
    FromSentenceRequest,
    FromTemplateRequest,
    NextRunInfo,
    TemplateItem,
    TryDraftRequest,
    UpdateBotRequest,
    WatchItem,
)
from gemini_brain.bots import cost, draft as bot_draft, monitor, preview, schedule, store, templates, validate, watches
from gemini_brain.bots.run import run_bot
from gemini_brain.bots.schema import BotDraft, BotIssue
from gemini_brain.config.settings import settings
from gemini_brain.usage import ledger, limits
from gemini_brain.vat_return import tool as vat_return_tool

logger = logging.getLogger("gemini_brain.api.bot_routes")

router = APIRouter(prefix="/api/v1/bots", tags=["Bots"])

#: One tick runs at most this many bots and stops starting new ones after this many seconds.
TICK_MAX_BOTS = 20
TICK_BUDGET_SECONDS = 45.0


def _user_org_dict(current_user: CurrentUser) -> Dict[int, str]:
    org_ids = current_user.allowed_org_ids or []
    if not org_ids:
        return {}
    tenants = fetch_organizations_from_db(org_ids) or placeholder_tenants(org_ids)
    return {int(t["id"]): str(t.get("name") or f"Organization {t['id']}") for t in tenants}


def _enrich_draft(draft: BotDraft, issues: List[BotIssue], user_id: int) -> DraftResponse:
    subject = f"bot-calc:{user_id}"
    deadline = time.monotonic() + 5.0
    freqs = vat_return_tool.filing_frequencies(draft.org_ids, subject, deadline) if draft.org_ids else {}
    tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
    now = dt.datetime.now(tz)
    runs = schedule.next_runs(draft, now, freqs, count=3)
    next_runs_list = [NextRunInfo(at=r.at.isoformat(), reason=r.reason) for r in runs]
    credits_est = cost.monthly_credits(draft, freqs)
    return DraftResponse(
        draft=draft,
        issues=issues,
        next_runs=next_runs_list,
        monthly_credits=credits_est,
    )


@router.get(
    "/templates",
    response_model=BotTemplatesResponse,
    summary="List ready-made bot templates",
)
def get_templates(
    agent: str = Query(default="vat"),
    current_user: CurrentUser = Depends(get_current_user),
) -> BotTemplatesResponse:
    tmpl_list = templates.all_templates(agent=agent)
    return BotTemplatesResponse(
        templates=[
            TemplateItem(id=t.id, title=t.title, summary=t.summary, draft=t.draft)
            for t in tmpl_list
        ]
    )


@router.get(
    "/watches",
    response_model=BotWatchesResponse,
    summary="List VAT checks (watches) available for a bot",
)
def get_watches(
    agent: str = Query(default="vat"),
    current_user: CurrentUser = Depends(get_current_user),
) -> BotWatchesResponse:
    if agent != "vat":
        return BotWatchesResponse(watches=[])
    all_w = watches.all_watches()
    return BotWatchesResponse(
        watches=[
            WatchItem(id=w.id, label=w.label, side=w.side, default_on=w.default_on)
            for w in all_w
        ]
    )


@router.post(
    "/draft/from-template",
    response_model=DraftResponse,
    summary="Create a draft from a template",
)
def draft_from_template(
    payload: FromTemplateRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> DraftResponse:
    try:
        tmpl = templates.get(payload.template_id)
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template {payload.template_id!r} not found",
        )

    if payload.org_ids:
        authorize_org_scope(payload.org_ids, current_user, action="bots.from_template")

    draft = tmpl.draft.model_copy(deep=True)
    draft.org_ids = [int(o) for o in payload.org_ids]

    current_orgs = payload.org_ids if payload.org_ids else (current_user.allowed_org_ids or [])[:1]
    validated_draft, issues = validate.validate(
        draft,
        allowed_org_ids=current_user.allowed_org_ids or [],
        current_org_ids=current_orgs,
    )

    return _enrich_draft(validated_draft, issues, current_user.user_id)


@router.post(
    "/draft/from-sentence",
    response_model=DraftResponse,
    summary="Create a draft from one natural language sentence",
)
def draft_from_sentence(
    payload: FromSentenceRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> DraftResponse:
    if payload.org_ids:
        authorize_org_scope(payload.org_ids, current_user, action="bots.from_sentence")

    current_orgs = payload.org_ids if payload.org_ids else (current_user.allowed_org_ids or [])[:1]

    # Usage quota check
    decision = limits.check(current_user.user_id, current_orgs)
    if decision.action == limits.BLOCK:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": decision.code, "message": decision.message},
        )

    allowed_orgs = _user_org_dict(current_user)

    with ledger.scope("bot_draft", user_id=current_user.user_id, organization_ids=current_orgs):
        try:
            validated_draft, issues = bot_draft.from_sentence(
                payload.text,
                allowed_orgs=allowed_orgs,
                current_org_ids=current_orgs,
            )
        except ValueError as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    return _enrich_draft(validated_draft, issues, current_user.user_id)


@router.post(
    "/draft/from-chat",
    response_model=DraftResponse,
    summary="Create a draft from a chat question",
)
def draft_from_chat(
    payload: FromChatRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> DraftResponse:
    if payload.agent != "vat":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agent must be 'vat'",
        )
    sentence = "A bot that regularly does this: " + payload.question.strip()
    sub_payload = FromSentenceRequest(text=sentence, org_ids=payload.org_ids)
    return draft_from_sentence(sub_payload, current_user)


@router.post(
    "/draft/check",
    response_model=DraftResponse,
    summary="Validate and check a draft as the user edits",
)
def draft_check(
    payload: CheckDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> DraftResponse:
    current_orgs = (current_user.allowed_org_ids or [])[:1]
    validated_draft, issues = validate.validate(
        payload.draft,
        allowed_org_ids=current_user.allowed_org_ids or [],
        current_org_ids=current_orgs,
    )
    return _enrich_draft(validated_draft, issues, current_user.user_id)


@router.post(
    "/try",
    summary="Try a bot draft on a past period (last_month or this_quarter)",
)
def try_draft(
    payload: TryDraftRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    if payload.preset not in ("last_month", "this_quarter"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="preset must be 'last_month' or 'this_quarter'",
        )

    validated_draft, _ = validate.validate(
        payload.draft,
        allowed_org_ids=current_user.allowed_org_ids or [],
        current_org_ids=(current_user.allowed_org_ids or [])[:1],
    )

    if not validated_draft.org_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No accessible organization selected",
        )

    # Usage quota check
    decision = limits.check(current_user.user_id, validated_draft.org_ids)
    if decision.action == limits.BLOCK:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": decision.code, "message": decision.message},
        )

    with ledger.scope("bot_try", user_id=current_user.user_id, organization_ids=validated_draft.org_ids):
        res = preview.try_period(
            validated_draft,
            preset=payload.preset,
            subject=f"bot-try:{current_user.user_id}",
        )

    return JSONResponse(content=res)


@router.post("", summary="Save a bot to the user's account", status_code=status.HTTP_201_CREATED)
def create_bot(
    payload: CreateBotRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    allowed = current_user.allowed_org_ids or []
    validated_draft, _ = validate.validate(
        payload.draft,
        allowed_org_ids=allowed,
        current_org_ids=allowed[:1],
    )
    if not validated_draft.org_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No accessible organization in bot draft",
        )

    subject = f"bot-init:{current_user.user_id}"
    deadline = time.monotonic() + 5.0
    freqs = vat_return_tool.filing_frequencies(validated_draft.org_ids, subject, deadline) if validated_draft.org_ids else {}
    tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
    runs = schedule.next_runs(validated_draft, dt.datetime.now(tz), freqs, count=1)
    next_run_at = runs[0].at if runs else None

    bot_record = store.create(
        owner_user_id=current_user.user_id,
        draft=validated_draft,
        status=payload.status or "active",
        next_run_at=next_run_at,
    )
    return JSONResponse(status_code=status.HTTP_201_CREATED, content=bot_record)


@router.get("", summary="List all bots belonging to the current user")
def list_bots(
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    bots = store.list_for_user(owner_user_id=current_user.user_id)
    return JSONResponse(content={"bots": bots})


def _verify_service_token(
    x_bot_service_token: Optional[str] = Header(None, alias="x-bot-service-token"),
    authorization: Optional[str] = Header(None),
) -> bool:
    # No token set: the service routes are off. Never fall back to a known secret.
    expected = settings.bot_service_token
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Bot service routes are not configured",
        )
    token = x_bot_service_token
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token or not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing service token",
        )
    return True


@router.post("/tick", response_model=BotTickResponse, summary="Tick scheduler for due bots")
def tick_scheduler(
    authorized: bool = Depends(_verify_service_token),
) -> BotTickResponse:
    tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
    now = dt.datetime.now(tz)
    started = time.monotonic()
    due = store.due_bots(now=now, limit=TICK_MAX_BOTS)

    results: List[Dict[str, Any]] = []
    for bot in due:
        # Bots left over wait for the next tick (every 5 minutes), so one call never runs past the proxy timeout.
        if time.monotonic() - started > TICK_BUDGET_SECONDS:
            break
        bot_id = bot["id"]
        try:
            run_res = run_bot(bot_id, trigger="schedule")
        except Exception:
            logger.exception("Bot %s run failed in tick", bot_id)
            run_res = {"status": "failed"}

        # Always move next_run_at on, even after a failure; otherwise the same bot is due again on every tick.
        try:
            draft = BotDraft(**bot["draft"])
            subject = f"bot-sched:{bot_id}"
            deadline = time.monotonic() + 5.0
            freqs = vat_return_tool.filing_frequencies(draft.org_ids, subject, deadline) if draft.org_ids else {}
            runs = schedule.next_runs(draft, dt.datetime.now(tz), freqs, count=1)
            next_run_at = runs[0].at if runs else None
        except Exception:
            logger.exception("Could not work out the next run of bot %s", bot_id)
            next_run_at = None
        try:
            store.set_next_run(bot_id, next_run_at)
        except Exception:
            logger.exception("Could not save the next run of bot %s", bot_id)
        results.append({
            "bot_id": bot_id,
            "name": bot["name"],
            "status": run_res.get("status", "unknown"),
            "summary": run_res.get("summary"),
        })

    return BotTickResponse(
        timestamp=now.isoformat(),
        ran=len(results),
        results=results,
    )


@router.post("/event", response_model=BotEventResponse, summary="Process document lifecycle event hook (Phase 3)")
def post_bot_event(
    payload: BotEventRequest,
    authorized: bool = Depends(_verify_service_token),
) -> BotEventResponse:
    if payload.event not in ("bill.saved", "invoice.saved"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported event type: {payload.event!r}. Expected 'bill.saved' or 'invoice.saved'",
        )
    doc_ids = list(payload.document_ids)
    if payload.document_id is not None and payload.document_id not in doc_ids:
        doc_ids.append(payload.document_id)

    try:
        res = monitor.handle_document_event(
            event=payload.event,
            org_id=payload.org_id,
            document_ids=doc_ids,
        )
        return BotEventResponse(**res)
    except Exception:
        # The details go to the log, not to the caller.
        logger.exception("Bot event %s for org %s failed", payload.event, payload.org_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error processing bot event",
        )


@router.get("/inbox", response_model=BotInboxResponse, summary="Get inbox of latest runs and findings")
def get_inbox(
    current_user: CurrentUser = Depends(get_current_user),
) -> BotInboxResponse:
    inbox_data = store.get_inbox(owner_user_id=current_user.user_id)
    return BotInboxResponse(**inbox_data)


@router.post("/inbox/mark-read", summary="Mark all new findings in inbox as seen")
def mark_inbox_read(
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    updated = store.mark_inbox_seen(owner_user_id=current_user.user_id)
    return JSONResponse(content={"marked_seen": updated})



# Fixed paths go before /{bot_id}, or FastAPI reads "approvals" as a bot id.
@router.get("/approvals", response_model=List[BotApprovalItem], summary="List pending bot action approvals")
def list_approvals(
    current_user: CurrentUser = Depends(get_current_user),
) -> List[BotApprovalItem]:
    approvals = store.get_approvals_for_user(owner_user_id=current_user.user_id)
    return [BotApprovalItem(**a) for a in approvals]


@router.get("/{bot_id}", summary="Get a bot by ID")
def get_bot(
    bot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    b = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not b:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return JSONResponse(content=b)


@router.patch("/{bot_id}", summary="Update a bot")
def update_bot(
    bot_id: str,
    payload: UpdateBotRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")

    patch: Dict[str, Any] = {}
    if payload.name is not None:
        patch["name"] = payload.name
    if payload.status is not None:
        patch["status"] = payload.status
        if payload.status == "active" and payload.draft is None:
            # Turning a bot back on: work out its next run, as /resume does, so it is not left without one.
            try:
                d = BotDraft(**existing["draft"])
                freqs = vat_return_tool.filing_frequencies(d.org_ids, f"bot-calc:{current_user.user_id}",
                                                           time.monotonic() + 5.0) if d.org_ids else {}
                tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
                runs = schedule.next_runs(d, dt.datetime.now(tz), freqs, count=1)
                patch["next_run_at"] = runs[0].at if runs else None
            except Exception:
                logger.exception("Could not work out the next run of bot %s", bot_id)
    if payload.status_note is not None:
        patch["status_note"] = payload.status_note

    if payload.draft is not None:
        allowed = current_user.allowed_org_ids or []
        validated_draft, _ = validate.validate(
            payload.draft,
            allowed_org_ids=allowed,
            current_org_ids=allowed[:1],
        )
        if not validated_draft.org_ids:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No accessible organization in bot draft",
            )
        patch["draft"] = validated_draft
        patch["name"] = validated_draft.name
        patch["org_ids"] = validated_draft.org_ids

        subject = f"bot-calc:{current_user.user_id}"
        deadline = time.monotonic() + 5.0
        freqs = vat_return_tool.filing_frequencies(validated_draft.org_ids, subject, deadline) if validated_draft.org_ids else {}
        tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
        runs = schedule.next_runs(validated_draft, dt.datetime.now(tz), freqs, count=1)
        patch["next_run_at"] = runs[0].at if runs else None

    updated = store.update(bot_id=bot_id, owner_user_id=current_user.user_id, patch=patch)
    return JSONResponse(content=updated)


@router.delete("/{bot_id}", summary="Delete a bot")
def delete_bot(
    bot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    deleted = store.delete(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return JSONResponse(content={"deleted": True, "id": bot_id})


@router.post("/{bot_id}/pause", summary="Pause a bot")
def pause_bot(
    bot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    updated = store.set_status(bot_id=bot_id, owner_user_id=current_user.user_id, status="paused", status_note="Paused by user")
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    return JSONResponse(content=updated)


@router.post("/{bot_id}/resume", summary="Resume a bot")
def resume_bot(
    bot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")

    try:
        d = BotDraft(**existing["draft"])
        subject = f"bot-calc:{current_user.user_id}"
        deadline = time.monotonic() + 5.0
        freqs = vat_return_tool.filing_frequencies(d.org_ids, subject, deadline) if d.org_ids else {}
        tz = ZoneInfo(settings.report_timezone) if settings.report_timezone else dt.timezone.utc
        runs = schedule.next_runs(d, dt.datetime.now(tz), freqs, count=1)
        next_run_at = runs[0].at if runs else None
    except Exception:
        next_run_at = None

    updated = store.update(
        bot_id=bot_id,
        owner_user_id=current_user.user_id,
        patch={"status": "active", "status_note": None, "next_run_at": next_run_at},
    )
    return JSONResponse(content=updated)


@router.post("/{bot_id}/findings/{key}/dismiss", summary="Dismiss a bot finding")
def dismiss_finding(
    bot_id: str,
    key: str,
    payload: Optional[DismissFindingRequest] = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    note = payload.note if payload else None
    supplier = payload.supplier if payload else None
    kwargs = {}
    if supplier is not None:
        kwargs["supplier"] = supplier
    ok = store.dismiss_finding(
        bot_id=bot_id,
        owner_user_id=current_user.user_id,
        key=key,
        note=note,
        **kwargs,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding or bot not found")
    return JSONResponse(content={"success": True, "key": key, "state": "dismissed"})


@router.post("/{bot_id}/findings/{key}/undismiss", summary="Un-dismiss a bot finding")
def undismiss_finding(
    bot_id: str,
    key: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    ok = store.undismiss_finding(
        bot_id=bot_id,
        owner_user_id=current_user.user_id,
        key=key,
    )
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Finding or bot not found")
    return JSONResponse(content={"success": True, "key": key, "state": "seen"})


# =========================================================================
# Phase 4: Memory Endpoints
# =========================================================================

@router.get("/{bot_id}/memory", response_model=List[BotMemoryItem], summary="List bot memory entries")
def get_bot_memory(
    bot_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> List[BotMemoryItem]:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    items = store.list_memory(bot_id=bot_id)
    return [BotMemoryItem(**m) for m in items]


@router.post("/{bot_id}/memory", response_model=BotMemoryItem, summary="Add custom memory note to bot")
def add_bot_memory(
    bot_id: str,
    payload: CreateMemoryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> BotMemoryItem:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    mem_id = store.add_memory(bot_id=bot_id, text=payload.text, source=payload.source)
    return BotMemoryItem(id=mem_id, bot_id=bot_id, text=payload.text, source=payload.source)


@router.delete("/{bot_id}/memory/{memory_id}", summary="Delete a bot memory entry")
def delete_bot_memory(
    bot_id: str,
    memory_id: int,
    current_user: CurrentUser = Depends(get_current_user),
) -> Any:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    ok = store.delete_memory(bot_id=bot_id, memory_id=memory_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory entry not found")
    return JSONResponse(content={"deleted": True, "id": memory_id})


# =========================================================================
# Phase 4: Approvals Endpoints
# =========================================================================

@router.post("/{bot_id}/approvals/{approval_id}/approve", response_model=BotApprovalItem, summary="Approve an action")
def approve_action(
    bot_id: str,
    approval_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> BotApprovalItem:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    res = store.update_approval_status(bot_id=bot_id, approval_id=approval_id, new_status="approved")
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return BotApprovalItem(**res)


@router.post("/{bot_id}/approvals/{approval_id}/reject", response_model=BotApprovalItem, summary="Reject an action")
def reject_action(
    bot_id: str,
    approval_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> BotApprovalItem:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    res = store.update_approval_status(bot_id=bot_id, approval_id=approval_id, new_status="rejected")
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return BotApprovalItem(**res)


@router.post("/{bot_id}/approvals/{approval_id}/edit", response_model=BotApprovalItem, summary="Edit and approve action")
def edit_and_approve_action(
    bot_id: str,
    approval_id: str,
    payload: EditApprovalRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> BotApprovalItem:
    existing = store.get(bot_id=bot_id, owner_user_id=current_user.user_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot not found")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    res = store.update_approval_status(bot_id=bot_id, approval_id=approval_id, new_status="approved", updates=updates)
    if not res:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval not found")
    return BotApprovalItem(**res)


