"""
routes.py — FastAPI route definitions for Gemini Brain.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Generator, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm

from gemini_brain.api.auth import (
    CurrentUser,
    authenticate_with_accutax_api,
    authorize_org_scope,
    create_access_token,
    fetch_accutax_accessible_orgs,
    fetch_organizations_from_db,
    get_current_user,
    get_user_allowed_orgs,
    get_user_by_email,
    placeholder_tenants,
    verify_password,
)
from gemini_brain.api.models import (
    HealthResponse,
    ModelCatalogResponse,
    ModelInfo,
    LoginRequest,
    ModelDiagnosticRequest,
    ModelHealthResponse,
    QueryRequest,
    QueryResponse,
    TenantInfo,
    TenantListResponse,
    TokenResponse,
    CreateSessionRequest,
    ChatSessionSchema,
    ChatSessionListResponse,
    ChatMessageSchema,
    ChatMessageListResponse,
    UploadConfig,
    UploadInfo,
)
from gemini_brain.config.settings import settings
from gemini_brain.health.model_health_checker import check_all_models_and_services
from gemini_brain.agent import models as agent_models
from gemini_brain.agent import preview as agent_preview
from gemini_brain.resilience import (
    HTTP_FOR_CODE,
    classify_exception,
    notice_for,
    normalize_envelope,
    new_request_id,
)

logger = logging.getLogger("gemini_brain.api.routes")

router = APIRouter(prefix="/api/v1", tags=["Gemini Brain AI Engine"])


def _resolve_tenants_for_org_ids(org_ids: list[int]) -> list[dict[str, Any]]:
    if not org_ids:
        return []
    return fetch_organizations_from_db(org_ids) or placeholder_tenants(org_ids)


# ── Authentication Endpoints ──────────────────────────────────────────────────

@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="OAuth2 Password Flow Login (Form-Encoded)",
    description=(
        "Standard OAuth2 form-encoded login returning JWT access token with user claims. "
        "Compatible with Swagger UI's top-right **Authorize** button."
    ),
)
def login_form(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    """Authenticate user credentials via form data and issue JWT token."""
    # 1. Try upstream Accutax API login first
    upstream = authenticate_with_accutax_api(form_data.username, form_data.password)
    if upstream:
        live = fetch_accutax_accessible_orgs(upstream["access_token"], upstream["user_id"])
        tenants = live if live else _resolve_tenants_for_org_ids(upstream["allowed_org_ids"])
        # Issue our own signed token rather than passing the upstream one
        # through: ours is verifiable by any worker, and survives a restart.
        session_token = create_access_token(
            user_id=upstream["user_id"],
            email=upstream["email"],
            allowed_org_ids=upstream["allowed_org_ids"],
            accutax_token=upstream["access_token"],
        )
        return TokenResponse(
            access_token=session_token,
            token_type="bearer",
            expires_in=3600,
            user_id=upstream["user_id"],
            email=upstream["email"],
            allowed_org_ids=upstream["allowed_org_ids"],
            tenants=tenants,
        )

    # 2. Fallback to local DB / seed map login
    user = get_user_by_email(form_data.username)
    if not user or not verify_password(form_data.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed_org_ids = get_user_allowed_orgs(user["id"])
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
    )
    tenants = _resolve_tenants_for_org_ids(allowed_org_ids)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600,
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
        tenants=tenants,
    )


@router.post(
    "/auth/login-json",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="User Login (JSON Payload)",
    description="Authenticates user email and password via JSON payload and issues JWT token with accessible organizations.",
)
def login_json(payload: LoginRequest) -> TokenResponse:
    """Authenticate user credentials via JSON payload and issue JWT token."""
    # 1. Try upstream Accutax API login first
    upstream = authenticate_with_accutax_api(payload.username, payload.password)
    if upstream:
        live = fetch_accutax_accessible_orgs(upstream["access_token"], upstream["user_id"])
        tenants = live if live else _resolve_tenants_for_org_ids(upstream["allowed_org_ids"])
        # Issue our own signed token rather than passing the upstream one
        # through: ours is verifiable by any worker, and survives a restart.
        session_token = create_access_token(
            user_id=upstream["user_id"],
            email=upstream["email"],
            allowed_org_ids=upstream["allowed_org_ids"],
            accutax_token=upstream["access_token"],
        )
        return TokenResponse(
            access_token=session_token,
            token_type="bearer",
            expires_in=3600,
            user_id=upstream["user_id"],
            email=upstream["email"],
            allowed_org_ids=upstream["allowed_org_ids"],
            tenants=tenants,
        )

    # 2. Fallback to local DB / seed map login
    user = get_user_by_email(payload.username)
    if not user or not verify_password(payload.password, user["password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    allowed_org_ids = get_user_allowed_orgs(user["id"])
    access_token = create_access_token(
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
    )
    tenants = _resolve_tenants_for_org_ids(allowed_org_ids)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600,
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
        tenants=tenants,
    )


# ── Tenant Management Endpoints ───────────────────────────────────────────────

@router.get(
    "/models",
    response_model=ModelCatalogResponse,
    tags=["Models"],
    summary="List the Models This User Can Pick",
    description=(
        "The Claude models that can answer for this user (the same agent, tools and data with each). "
        "One model means the client hides the picker. Send the chosen key as `model` on /query."
    ),
)
def list_model_catalog(
    current_user: CurrentUser = Depends(get_current_user),
) -> ModelCatalogResponse:
    """The models the deployment offers (AGENT_MODELS) that this user may pick (AGENT_MODEL_USERS)."""
    choices = agent_models.allowed(current_user.user_id, current_user.email)
    return ModelCatalogResponse(models=[ModelInfo(**m.to_public()) for m in choices], efforts=[],
                                default_model=agent_models.DEFAULT_KEY, default_effort="exhaustive",
                                picker_hidden=len(choices) <= 1)


def _model_choice(payload: QueryRequest, current_user: CurrentUser) -> dict:
    """The model the request asks for and the keys this user may pick; agent_preview.answer resolves them."""
    return {"model_key": payload.model or payload.selected_model_key,
            "allowed_models": [m.key for m in agent_models.allowed(current_user.user_id, current_user.email)]}



@router.get(
    "/tenants",
    response_model=TenantListResponse,
    tags=["Tenant Management"],
    summary="List Accessible Tenant Organizations",
    description="Returns metadata, badges, and capability descriptions for all tenant organizations the authenticated user is authorized to query.",
)
def list_tenants(current_user: CurrentUser = Depends(get_current_user)) -> TenantListResponse:
    """List organizations this user owns or can access as a collaborator."""
    # The dropdown must never offer an org the query path would refuse, so the
    # Accutax list is filtered to the same allow-list get_current_user resolved.
    allowed = [int(o) for o in (current_user.allowed_org_ids or [])]
    allowed_set = set(allowed)
    live = [
        t
        for t in fetch_accutax_accessible_orgs(current_user.accutax_token, current_user.user_id)
        if int(t["id"]) in allowed_set
    ]
    if not live:
        live = _resolve_tenants_for_org_ids(allowed)

    return TenantListResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenants=[TenantInfo(**t) for t in live],
        multi_org_enabled=settings.multi_org_enabled,
        max_orgs_per_query=settings.max_orgs_per_query if settings.multi_org_enabled else 1,
    )


def _query_orgs(payload: QueryRequest, current_user: CurrentUser, *, action: str) -> list[int]:
    """Authorize every organization a query names and return them.

    [] means the request named no organization; the runner then uses the
    caller's first allowed one.
    """
    orgs = authorize_org_scope(payload.requested_org_ids(), current_user, action=action)
    orgs = _match_session_scope(payload.session_id, orgs, current_user, action=action)
    _require_multi_org_enabled(orgs)
    return orgs


def _agent_scope(current_user: CurrentUser, orgs: list[int]) -> list[int]:
    """The organizations a chat runs on: those it names, else the caller's first allowed one."""
    scope = orgs or [int(o) for o in (current_user.allowed_org_ids or [])[:1]]
    if not scope:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="No organization is available for this account.")
    return scope


def _attachments(payload: QueryRequest, current_user: CurrentUser) -> list:
    """The files a question carries, checked (owner, thread, read) before anything runs. [] when it has none."""
    if not payload.attachments:
        return []
    from gemini_brain.uploads import context as upload_context
    from gemini_brain.uploads import enabled_for
    from gemini_brain.agent.preview import usable_session_id

    if not enabled_for(current_user.user_id, current_user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="File upload is not available for this account.")
    try:
        return upload_context.resolve(payload.attachments, user_id=current_user.user_id,
                                      session_id=usable_session_id(payload.session_id))
    except upload_context.AttachmentError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


def _agent_events(payload: QueryRequest, current_user: CurrentUser, orgs: list[int],
                  documents: Optional[list] = None) -> Generator[str, None, None]:
    """SSE for an agent answer: a status line per tool call, then the final result."""
    import queue
    import threading

    events: "queue.Queue[Optional[str]]" = queue.Queue()
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = agent_preview.answer(
                payload.query, orgs, _agent_org_meta(orgs, current_user),
                session_id=payload.session_id, user_id=current_user.user_id, db_name=payload.db_name,
                progress=events.put, brief=bool(payload.brief), documents=documents,
                **_model_choice(payload, current_user),
            )
        except Exception as e:  # noqa: BLE001 - answer() never raises; this is belt and braces
            logger.warning("agent answer failed: %s", e, exc_info=True)
            box["error"] = e
        finally:
            events.put(None)

    threading.Thread(target=work, name="agent-answer", daemon=True).start()
    yield f"data: {json.dumps({'status': 'Reading your question…', 'type': 'agent'})}\n\n"
    while (message := events.get()) is not None:
        yield f"data: {json.dumps({'status': message, 'type': 'agent'})}\n\n"
    if "result" in box:
        yield f"data: {json.dumps({'final_result': normalize_envelope(box['result'])}, default=str)}\n\n"
        return
    rid = new_request_id()
    code = classify_exception(box.get("error") or RuntimeError("no answer"))
    err_notice = notice_for(code, request_id=rid)
    err_env = normalize_envelope({
        "answer": err_notice["message"],
        "error": code.value,
        "status": "degraded" if err_notice.get("retryable") else "failed",
        "notice": err_notice,
        "request_id": rid,
    })
    yield f"data: {json.dumps({'type': 'error', 'notice': err_notice})}\n\n"
    yield f"data: {json.dumps({'final_result': err_env})}\n\n"



def _require_multi_org_enabled(orgs: list[int]) -> None:
    if len(orgs) > 1 and not settings.multi_org_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Querying several organizations together is not enabled.",
        )


def _match_session_scope(
    session_id: Optional[str],
    orgs: list[int],
    current_user: CurrentUser,
    *,
    action: str,
) -> list[int]:
    """Hold a query to the organizations of the thread it continues.

    A thread's memory holds data from its organizations, and the runner loads
    that memory into the prompt. So the thread's organizations must all still
    be allowed, and the query must name exactly them. A query that names none
    continues the thread's own. A new selection needs a new thread.
    """
    from gemini_brain.memory.session_memory import get_session_record, is_valid_uuid, session_scope

    if not session_id or not is_valid_uuid(session_id):
        return orgs
    rec = get_session_record(session_id)
    if not rec:
        return orgs
    if int(rec["user_id"]) != int(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session does not belong to this user.")
    scope = authorize_org_scope(session_scope(rec), current_user, action=f"{action}.session")
    if not scope:
        return orgs
    if not orgs:
        return scope
    if sorted(orgs) != scope:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This chat belongs to a different organization selection. Start a new chat to change it.",
        )
    return orgs


def _agent_org_meta(orgs: list[int], current_user: CurrentUser) -> Callable[[], dict[int, dict[str, Any]]]:
    """Org names and currencies for the agent, from Cube (no Accutax endpoint). Looked up only when called."""
    from gemini_brain.semantic import org_directory
    return lambda: org_directory.lookup(orgs, subject=f"agent-org-meta:{current_user.user_id}")


@router.get(
    "/sessions",
    response_model=ChatSessionListResponse,
    tags=["Chat Sessions"],
    summary="List chat threads for the current user",
)
def list_chat_sessions(
    organization_id: Optional[int] = None,
    organization_ids: Optional[list[int]] = Query(default=None),
    current_user: CurrentUser = Depends(get_current_user),
) -> ChatSessionListResponse:
    """organization_ids lists threads for exactly that selection; organization_id
    lists single-org threads. Threads from organizations the user can no longer
    access are never listed."""
    from gemini_brain.memory.session_memory import list_sessions_for_user_org

    requested = organization_ids if organization_ids else [organization_id]
    authorize_org_scope(requested, current_user, action="sessions.list")
    rows = list_sessions_for_user_org(
        current_user.user_id,
        organization_id,
        organization_ids=organization_ids or None,
        allowed_org_ids=current_user.allowed_org_ids,
    )
    return ChatSessionListResponse(sessions=[ChatSessionSchema(**r) for r in rows])


@router.post(
    "/sessions",
    response_model=ChatSessionSchema,
    status_code=status.HTTP_201_CREATED,
    tags=["Chat Sessions"],
    summary="Create a chat thread",
)
def create_chat_session(
    payload: CreateSessionRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ChatSessionSchema:
    import uuid as uuid_lib
    from gemini_brain.memory.session_memory import ensure_session, get_session_record, is_valid_uuid

    orgs = authorize_org_scope(payload.requested_org_ids(), current_user, action="sessions.create")
    _require_multi_org_enabled(orgs)
    session_id = payload.session_id if payload.session_id and is_valid_uuid(payload.session_id) else str(uuid_lib.uuid4())
    ok = ensure_session(session_id, current_user.user_id, organization_ids=orgs or None)
    if not ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Could not create session.")
    rec = get_session_record(session_id) or {}
    return ChatSessionSchema(
        id=session_id,
        user_id=current_user.user_id,
        organization_id=orgs[0] if len(orgs) == 1 else rec.get("organization_id"),
        organization_ids=rec.get("organization_ids") or sorted(orgs),
        name=rec.get("name") or "New Chat",
        created_at=rec.get("created_at"),
        updated_at=rec.get("updated_at"),
        message_count=0,
    )


@router.get(
    "/sessions/{session_id}/messages",
    response_model=ChatMessageListResponse,
    tags=["Chat Sessions"],
    summary="Load a thread transcript",
)
def get_chat_session_messages(
    session_id: str,
    limit: int = 50,
    current_user: CurrentUser = Depends(get_current_user),
) -> ChatMessageListResponse:
    from gemini_brain.memory.session_memory import (
        get_transcript_by_session,
        verify_session_ownership,
        get_session_record,
        session_scope,
    )

    rec = get_session_record(session_id)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if not verify_session_ownership(session_id, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session does not belong to this user.")
    authorize_org_scope(session_scope(rec), current_user, action="sessions.read")
    from gemini_brain.memory.context_window import get_context_window_usage

    messages = get_transcript_by_session(session_id, limit=min(max(limit, 1), 200))
    return ChatMessageListResponse(
        session_id=session_id,
        messages=[ChatMessageSchema(**m) for m in messages],
        context_window=get_context_window_usage(session_id),
    )


@router.post(
    "/sessions/{session_id}/reset",
    response_model=ChatSessionSchema,
    tags=["Chat Sessions"],
    summary="Start a new thread (does not delete the previous one)",
)
def reset_chat_session(
    session_id: str,
    payload: CreateSessionRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> ChatSessionSchema:
    """Clients should mint a new UUID; this endpoint creates it. The previous id is left intact."""
    return create_chat_session(payload, current_user)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Chat Sessions"],
    summary="Permanently delete a chat thread",
)
def delete_chat_session(
    session_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> Response:
    from gemini_brain.memory.session_memory import delete_session, get_session_record, session_scope

    rec = get_session_record(session_id)
    if rec and int(rec["user_id"]) == int(current_user.user_id):
        authorize_org_scope(session_scope(rec), current_user, action="sessions.delete")
    if not delete_session(session_id, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    try:  # the thread is gone; its attached files go with it
        from gemini_brain.uploads.store import delete_for_session

        delete_for_session(session_id)
    except Exception as e:
        logger.warning("Thread %s deleted but its uploads were not: %s", session_id, e)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/artifacts/{artifact_id}",
    tags=["Artifacts"],
    summary="Download a short-lived generated file",
)
def download_artifact(
    artifact_id: str,
    current_user: CurrentUser = Depends(get_current_user),
) -> FileResponse:
    from gemini_brain.artifacts.store import get as get_artifact, get_spec, record_event, was_expired

    rec = get_artifact(artifact_id)
    if rec is None:
        if was_expired(artifact_id):
            can_regenerate = get_spec(artifact_id) is not None
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={
                    "message": "This file expired." + (" It can be regenerated." if can_regenerate
                                                       else " Ask again to regenerate it."),
                    "regenerate": can_regenerate,
                },
            )
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    _authorize_artifact(rec, current_user, action="download")
    record_event(rec.id, "downloaded", user_id=current_user.user_id, organization_id=rec.organization_id)
    return FileResponse(
        rec.path,
        media_type=rec.mime,
        filename=rec.filename,
        content_disposition_type="attachment",
    )


def _authorize_artifact(rec: Any, current_user: CurrentUser, *, action: str) -> None:
    """Owner and organization check; every refusal is written to the audit log."""
    from gemini_brain.artifacts.store import record_event

    if int(rec.user_id) != int(current_user.user_id):
        record_event(rec.id, "denied", user_id=current_user.user_id, organization_id=rec.organization_id,
                     detail={"action": action, "reason": "not_owner"})
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This file does not belong to this user.")
    try:
        authorize_org_scope([rec.organization_id], current_user, action=f"artifacts.{action}")
    except HTTPException:
        record_event(rec.id, "denied", user_id=current_user.user_id, organization_id=rec.organization_id,
                     detail={"action": action, "reason": "organization"})
        raise


@router.post(
    "/artifacts/{artifact_id}/regenerate",
    tags=["Artifacts"],
    summary="Rebuild an expired file from the report it was generated from",
)
def regenerate_artifact(
    artifact_id: str,
    format: Optional[str] = None,  # noqa: A002 - query parameter name
    current_user: CurrentUser = Depends(get_current_user),
) -> JSONResponse:
    """Renders the stored, already-validated report spec again — no new data
    retrieval, so the regenerated file shows exactly the figures of the
    original. Owner and organization are checked as for a download."""
    from gemini_brain.artifacts.generate import RENDERERS, generate_and_store
    from gemini_brain.artifacts.store import get_spec

    found = get_spec(artifact_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail="This report is no longer kept. Ask again to rebuild it.")
    rec, spec = found
    _authorize_artifact(rec, current_user, action="regenerate")
    fmt = (format or rec.fmt or "").lower()
    if fmt not in RENDERERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown format {fmt!r}.")
    block = generate_and_store(
        spec, fmt, user_id=rec.user_id, organization_id=rec.organization_id,
        session_id=rec.session_id, regenerated_from=rec.id,
    )
    if block is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="The file could not be rebuilt.")
    return JSONResponse(block)


# ── Uploads (files attached in chat) ──────────────────────────────────────────

def _upload_info(rec: Any) -> UploadInfo:
    from gemini_brain.uploads.store import problems_of as upload_problems

    return UploadInfo(id=rec.id, name=rec.filename, size=rec.size_bytes, kind=rec.kind, pages=rec.pages,
                      status=rec.status, failure_reason=rec.failure_reason, summary=rec.summary,
                      document_type=rec.document_type,
                      checks_failed=rec.checks_failed, problems=upload_problems(rec),
                      attached=rec.attached_at is not None)


def _require_uploads(current_user: CurrentUser) -> None:
    from gemini_brain.uploads import enabled_for

    if not enabled_for(current_user.user_id, current_user.email):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                            detail="File upload is not available for this account.")


@router.get(
    "/uploads/config",
    response_model=UploadConfig,
    tags=["Uploads"],
    summary="Whether this user can attach files, and the limits",
)
def upload_config(current_user: CurrentUser = Depends(get_current_user)) -> UploadConfig:
    from gemini_brain.uploads import enabled_for
    from gemini_brain.uploads.store import pages_used_today
    from gemini_brain.uploads.validate import accepted_types

    if not enabled_for(current_user.user_id, current_user.email):
        return UploadConfig(enabled=False)
    return UploadConfig(
        enabled=True,
        max_bytes=settings.upload_max_bytes,
        max_pages=settings.upload_max_pages,
        max_files=settings.upload_max_files,
        daily_pages=settings.upload_daily_pages,
        pages_used_today=pages_used_today(current_user.user_id),
        accept=sorted(accepted_types()),
    )


@router.post(
    "/uploads",
    response_model=UploadInfo,
    status_code=status.HTTP_201_CREATED,
    tags=["Uploads"],
    summary="Attach a file to a chat thread",
)
def create_upload(
    request: Request,
    file: UploadFile = File(...),
    session_id: str = Form(...),
    organization_ids: str = Form(default=""),
    current_user: CurrentUser = Depends(get_current_user),
) -> UploadInfo:
    """Checks the file on its bytes (type, size, pages, macros, bombs), stores it for the thread and returns
    its id. The question that uses it sends `attachments: [id]`.

    A plain (not async) endpoint on purpose: it asks the database about the thread and reads the whole file, and a call
    that waits must not run on the event loop, where it would hold up every other request."""
    from gemini_brain.memory.session_memory import is_valid_uuid, verify_session_ownership
    from gemini_brain.uploads import store as upload_store
    from gemini_brain.uploads.validate import UploadRejected, inspect

    _require_uploads(current_user)
    limit = settings.upload_max_bytes
    too_large = f"This file is too large. The limit is {limit // 1_048_576} MB."
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit + 64 * 1024:  # the body carries form overhead
        raise HTTPException(status_code=413, detail=too_large)
    if not is_valid_uuid(session_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id must be a UUID.")
    if not verify_session_ownership(session_id, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This thread does not belong to this user.")
    try:
        orgs = [int(o) for o in organization_ids.split(",") if o.strip()]
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="organization_ids must be numbers.")
    if orgs:
        orgs = authorize_org_scope(orgs, current_user, action="uploads.create")

    chunks, total = [], 0
    while True:  # read in pieces and stop at the limit instead of holding an oversized body
        chunk = file.file.read(1_048_576)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=413, detail=too_large)
        chunks.append(chunk)
    content = b"".join(chunks)

    try:
        inspected = inspect(content, file.filename or "")
        rec = upload_store.put(content, filename=file.filename or "", inspected=inspected,
                               user_id=current_user.user_id, session_id=session_id, organization_ids=orgs)
    except UploadRejected as e:
        upload_store.record_event("-", "rejected", user_id=current_user.user_id,
                                  detail={"code": e.code, "size_bytes": len(content), "session_id": session_id})
        raise HTTPException(status_code=e.http_status, detail={"code": e.code, "message": e.message})
    except upload_store.DailyLimitReached as e:
        upload_store.record_event("-", "rejected", user_id=current_user.user_id,
                                  detail={"code": "daily_limit", "session_id": session_id})
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "daily_limit",
                    "message": f"You have uploaded {e.used} of {e.limit} pages today. Try again tomorrow."})
    from gemini_brain.uploads import reader

    return _upload_info(reader.start(rec))  # reading continues in the background; the chip shows its status


def _owned_upload(upload_id: str, current_user: CurrentUser, *, action: str) -> Any:
    from gemini_brain.uploads.store import get_for_user

    rec = get_for_user(upload_id, current_user.user_id, action=action)
    if rec is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found.")
    return rec


@router.get("/uploads/{upload_id}", response_model=UploadInfo, tags=["Uploads"], summary="One attached file's state")
def get_upload(upload_id: str, current_user: CurrentUser = Depends(get_current_user)) -> UploadInfo:
    from gemini_brain.uploads.store import fail_if_stale

    _require_uploads(current_user)
    return _upload_info(fail_if_stale(_owned_upload(upload_id, current_user, action="read")))


@router.get("/uploads/{upload_id}/file", tags=["Uploads"], summary="The attached file itself, for its owner")
def download_upload(upload_id: str, current_user: CurrentUser = Depends(get_current_user)) -> FileResponse:
    from gemini_brain.uploads.store import record_event
    from gemini_brain.uploads.validate import INLINE_KINDS

    _require_uploads(current_user)
    rec = _owned_upload(upload_id, current_user, action="download")
    record_event(rec.id, "downloaded", user_id=current_user.user_id)
    return FileResponse(
        rec.path,
        media_type=rec.mime,  # from the checked content, never from what the browser claimed
        filename=rec.filename,
        content_disposition_type="inline" if rec.kind in INLINE_KINDS else "attachment",
        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                 "Cache-Control": "private, no-store"},
    )


@router.delete(
    "/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["Uploads"],
    summary="Remove a file before it is sent with a question",
)
def delete_upload(upload_id: str, current_user: CurrentUser = Depends(get_current_user)) -> Response:
    from gemini_brain.uploads.store import delete

    _require_uploads(current_user)
    rec = _owned_upload(upload_id, current_user, action="delete")
    if rec.attached_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail="This file was sent with a question. It is removed when the thread is deleted.")
    delete(rec.id, user_id=current_user.user_id, refund_pages=True, reason="removed_by_user")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Health & Diagnostic Endpoints ─────────────────────────────────────────────

@router.get("/health", response_model=HealthResponse, tags=["Health"])
def health_check() -> HealthResponse:
    """Check API service health status."""
    return HealthResponse()


@router.get(
    "/health/models",
    response_model=ModelHealthResponse,
    tags=["Health & Diagnostics"],
    summary="Check All AI Models & Services Health",
    description=(
        "Pings the agent's AWS Bedrock model(s), the Cube semantic layer and PostgreSQL. "
        "Measures latency and returns diagnostic status & sample responses."
    ),
)
def check_models_get() -> ModelHealthResponse:
    """Run diagnostics on all AI models and backend services."""
    res = check_all_models_and_services(test_prompt="Respond with 'OK'")
    return ModelHealthResponse(**res)


@router.post(
    "/health/models",
    response_model=ModelHealthResponse,
    tags=["Health & Diagnostics"],
    summary="Check AI Models with Custom Test Prompt",
    description="Runs model diagnostics using a custom test prompt.",
)
def check_models_post(payload: ModelDiagnosticRequest) -> ModelHealthResponse:
    """Run diagnostics on all AI models using a custom test prompt."""
    res = check_all_models_and_services(test_prompt=payload.test_prompt)
    return ModelHealthResponse(**res)


# ── Protected AI Engine Query Endpoints ───────────────────────────────────────

@router.post(
    "/query",
    response_model=QueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Financial Query (Authenticated)",
    description=(
        "Answers a natural language financial question with the agent (figures from Cube, "
        "VAT law from the knowledge base), enforcing tenant isolation."
    ),
)
async def run_query(
    payload: QueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> QueryResponse:
    """Answer one question with the agent, with the model the user picked (`model`). effort and use_api are ignored."""
    import asyncio
    orgs = _agent_scope(current_user, _query_orgs(payload, current_user, action="query"))
    documents = _attachments(payload, current_user)
    try:
        result = await asyncio.to_thread(
            agent_preview.answer, payload.query, orgs, _agent_org_meta(orgs, current_user),
            session_id=payload.session_id, user_id=current_user.user_id, db_name=payload.db_name,
            brief=bool(payload.brief), documents=documents, **_model_choice(payload, current_user),
        )
        return QueryResponse(**normalize_envelope(result))
    except Exception as e:
        logger.error("Unhandled exception processing query: %s", e, exc_info=True)
        code = classify_exception(e)
        notice_obj = notice_for(code, request_id=new_request_id())
        envelope = normalize_envelope({
            "answer": notice_obj["message"],
            "error": code.value,
            "status": "degraded" if notice_obj.get("retryable") else "failed",
            "notice": notice_obj,
        })
        return JSONResponse(status_code=HTTP_FOR_CODE.get(code, 500), content=envelope)


@router.post(
    "/query/stream",
    summary="Stream Financial Query Progress (SSE) (Authenticated)",
    description=(
        "Streams status updates and final result of a financial query using "
        "Server-Sent Events (SSE) `text/event-stream` format with tenant isolation enforcement."
    ),
)
def stream_query(
    payload: QueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> StreamingResponse:
    """Stream the agent's tool steps and its answer via Server-Sent Events (SSE)."""
    # Checked before the stream opens, so a refused org is a plain 403 and no
    # event is ever emitted for it.
    orgs = _agent_scope(current_user, _query_orgs(payload, current_user, action="query.stream"))
    documents = _attachments(payload, current_user)  # a refused attachment is a plain error before the stream opens
    return StreamingResponse(
        _agent_events(payload, current_user, orgs, documents),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
