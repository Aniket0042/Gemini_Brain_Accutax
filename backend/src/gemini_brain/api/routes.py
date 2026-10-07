"""
routes.py — FastAPI route definitions for Gemini Brain.
"""
from __future__ import annotations

import json
from collections import Counter
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Generator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    EffortInfo,
    HealthResponse,
    ModelCatalogResponse,
    ModelInfo,
    LoginRequest,
    ModelDiagnosticRequest,
    ModelHealthResponse,
    MultiModelQueryResponse,
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
)
from gemini_brain.api_client.accutax_client import active_auth_token
from gemini_brain.config.settings import settings
from gemini_brain.health.model_health_checker import check_all_models_and_services
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.orchestrator.multi_org import run_multi_org, run_multi_org_stream
from gemini_brain.agent import preview as agent_preview
from gemini_brain.agent import shadow as agent_shadow
from gemini_brain.orchestrator.multi_org_plan import plan_query
from gemini_brain.policy import choose_policy, list_models
from gemini_brain.policy.effort import DEFAULT_EFFORT, EFFORT_ORDER, EFFORT_TIERS
from gemini_brain.resilience import (
    ErrorCode,
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
    summary="List Selectable Models and Effort Tiers",
    description=(
        "Returns the models this user may choose between and the effort tiers each supports, "
        "so the client never hardcodes model identifiers."
    ),
)
def list_model_catalog(
    current_user: CurrentUser = Depends(get_current_user),
) -> ModelCatalogResponse:
    """Serve the model registry and effort ladder for the picker UI."""
    if agent_preview.primary():
        # Every chat is answered by the agent: one model, no picker.
        return ModelCatalogResponse(models=[ModelInfo(**agent_preview.catalog_entry())], efforts=[],
                                    default_model=agent_preview.MODEL_KEY, default_effort=DEFAULT_EFFORT,
                                    picker_hidden=True)
    models = [ModelInfo(**m) for m in list_models()]
    if agent_preview.allowed(current_user):
        models.insert(0, ModelInfo(**agent_preview.catalog_entry()))
    return ModelCatalogResponse(
        models=models,
        efforts=[
            EffortInfo(
                name=tier.name,
                label=tier.label,
                description=tier.description,
                target_latency=tier.target_latency,
                adds=tier.adds(),
                max_retrievals=tier.max_retrievals,
            )
            for name in EFFORT_ORDER
            for tier in [EFFORT_TIERS[name]]
        ],
        default_model="auto",
        default_effort=DEFAULT_EFFORT,
    )


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


def _preview_scope(payload: QueryRequest, current_user: CurrentUser, orgs: list[int]) -> Optional[list[int]]:
    """The organizations an agent-preview request runs on, or None when it is not one.

    The picker shows the preview only to allowed users; a stale selection from anyone
    else falls back to the normal path. With AGENT_MODE=primary every request is one.
    """
    if not agent_preview.primary() and (
            not agent_preview.requested(payload.model) or not agent_preview.allowed(current_user)):
        return None
    scope = orgs or [int(o) for o in (current_user.allowed_org_ids or [])[:1]]
    return scope or None


def _preview_events(payload: QueryRequest, current_user: CurrentUser, orgs: list[int]) -> Generator[str, None, None]:
    """SSE for an agent-preview answer: a status line per tool call, then the final result."""
    import queue
    import threading

    events: "queue.Queue[Optional[str]]" = queue.Queue()
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["result"] = agent_preview.answer(
                payload.query, orgs, _agent_org_meta(orgs, current_user),
                session_id=payload.session_id, user_id=current_user.user_id, db_name=payload.db_name,
                progress=events.put,
            )
        except Exception as e:  # noqa: BLE001 - answer() never raises; this is belt and braces
            logger.warning("agent preview failed: %s", e, exc_info=True)
        finally:
            events.put(None)

    threading.Thread(target=work, name="agent-preview", daemon=True).start()
    yield f"data: {json.dumps({'status': 'Reading your question…', 'type': 'agent'})}\n\n"
    while (message := events.get()) is not None:
        yield f"data: {json.dumps({'status': message, 'type': 'agent'})}\n\n"
    result = box.get("result") or {"answer": "", "status": "failed"}
    yield f"data: {json.dumps({'final_result': normalize_envelope(result)}, default=str)}\n\n"


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


def _org_meta(current_user: CurrentUser, orgs: list[int]) -> dict[int, dict[str, Any]]:
    """Display name and currency for each authorized org, for labelling a comparison."""
    known = {
        int(t["id"]): t
        for t in fetch_accutax_accessible_orgs(current_user.accutax_token, current_user.user_id)
    }
    # Names from every org the user can reach, not only the selected ones, so
    # a name shared with an unselected org is still told apart.
    missing = [o for o in {*orgs, *(current_user.allowed_org_ids or [])} if o not in known]
    if missing:
        known.update({int(t["id"]): t for t in fetch_organizations_from_db(missing)})

    def base_name(oid: int) -> str:
        return (known.get(oid) or {}).get("name") or f"Organization {oid}"

    name_counts = Counter(base_name(oid) for oid in known)
    # An unknown currency stays empty, so the comparison never labels an
    # amount with a currency it does not have. A name two orgs share gets the
    # org ID, so chips, headings and the comparison never mix them up.
    return {
        o: {"name": base_name(o) + (f" (ID {o})" if name_counts[base_name(o)] > 1 else ""),
            "currency": (known.get(o) or {}).get("currency") or ""}
        for o in orgs
    }


def _multi_org_run_kwargs(payload: QueryRequest, current_user: CurrentUser) -> dict[str, Any]:
    """Per-org runner.run arguments; organization_id and session_id are set per org."""
    return {
        "db_name": payload.db_name,
        "use_api": payload.use_api,
        "user_id": current_user.user_id,
        "selected_model_key": payload.selected_model_key,
        "allowed_org_ids": current_user.allowed_org_ids,
        "auth_token": current_user.raw_token,
        "model": payload.model,
        "effort": payload.effort,
        "ui_context": payload.ui_context.model_dump() if payload.ui_context else None,
        "brief": bool(payload.brief),
        # Used once for the whole org set; each per-org run gets none.
        "session_id": payload.session_id,
    }


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
    messages = get_transcript_by_session(session_id, limit=min(max(limit, 1), 200))
    return ChatMessageListResponse(
        session_id=session_id,
        messages=[ChatMessageSchema(**m) for m in messages],
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
        "Pings Google Gemini 2.5 Flash, the two configured AWS Bedrock Claude models, "
        "Accutax REST API, and PostgreSQL DB. Measures latency and returns diagnostic status & sample responses."
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
        "Routes a natural language financial query through Google Gemini (classification) "
        "and Anthropic Claude on AWS Bedrock (data reasoning), enforcing tenant isolation."
    ),
)
async def run_query(
    payload: QueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> QueryResponse:
    """Execute a synchronous financial query through Gemini Brain with tenant isolation enforcement."""
    import asyncio
    orgs = _query_orgs(payload, current_user, action="query")
    preview_orgs = _preview_scope(payload, current_user, orgs)
    if preview_orgs is not None:
        result = await asyncio.to_thread(
            agent_preview.answer, payload.query, preview_orgs, _agent_org_meta(preview_orgs, current_user),
            session_id=payload.session_id, user_id=current_user.user_id, db_name=payload.db_name,
        )
        return QueryResponse(**normalize_envelope(result))
    if agent_preview.requested(payload.model):
        payload = payload.model_copy(update={"model": "auto"})
    organization_id = orgs[0] if orgs else None
    # Set request-scoped bearer token for any downstream Accutax REST calls
    token_reset = active_auth_token.set(current_user.raw_token)
    try:
        runner = GeminiBrainRunner()
        
        def _run():
            from gemini_brain.observability.sql_tracer import start_sql_trace, get_sql_traces, clear_sql_trace
            from gemini_brain.observability.api_tracer import start_api_trace, get_api_traces, clear_api_trace
            from gemini_brain.observability.llm_tracer import start_llm_trace, get_llm_traces, clear_llm_trace
            rid = new_request_id()
            start_sql_trace(trace_id=rid)
            start_api_trace(trace_id=rid)
            start_llm_trace(trace_id=rid)
            history = agent_shadow.thread_history(payload.session_id, payload.db_name)
            try:
                if len(orgs) > 1:
                    res = run_multi_org(
                        payload.query, orgs, _org_meta(current_user, orgs),
                        runner_factory=GeminiBrainRunner,
                        run_kwargs=_multi_org_run_kwargs(payload, current_user),
                        planner=plan_query,
                    )
                else:
                    res = runner.run(
                        query=payload.query,
                        organization_id=organization_id,
                        db_name=payload.db_name,
                        use_api=payload.use_api,
                        user_id=current_user.user_id,
                        session_id=payload.session_id,
                        selected_model_key=payload.selected_model_key,
                        allowed_org_ids=current_user.allowed_org_ids,
                        auth_token=current_user.raw_token,
                        model=payload.model,
                        effort=payload.effort,
                        ui_context=payload.ui_context.model_dump() if payload.ui_context else None,
                        brief=bool(payload.brief),
                    )
                traces = get_sql_traces(trace_id=rid)
                if traces and isinstance(res, dict):
                    res["sql_traces"] = traces
                    if not res.get("sql"):
                        res["sql"] = traces[0]["sql"]
                api_traces = get_api_traces(trace_id=rid)
                if api_traces and isinstance(res, dict):
                    res["api_traces"] = api_traces
                llm_traces = get_llm_traces(trace_id=rid)
                if llm_traces and isinstance(res, dict):
                    res["llm_traces"] = llm_traces
                agent_shadow.start(payload.query, orgs, _agent_org_meta(orgs, current_user), res, history=history)
                return res
            finally:
                clear_sql_trace(trace_id=rid)
                clear_api_trace(trace_id=rid)
                clear_llm_trace(trace_id=rid)
            
        result = await asyncio.to_thread(_run)
        return QueryResponse(**normalize_envelope(result))
    except ValueError as ve:
        logger.warning("Tenant or validation error processing query: %s", ve)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        ) from ve
    except Exception as e:
        logger.error("Unhandled exception processing query: %s", e, exc_info=True)
        code = classify_exception(e)
        status_code = HTTP_FOR_CODE.get(code, 500)
        notice_obj = notice_for(code, request_id=new_request_id())
        envelope = normalize_envelope({
            "answer": notice_obj["message"],
            "error": code.value,
            "status": "degraded" if notice_obj.get("retryable") else "failed",
            "notice": notice_obj,
        })
        return JSONResponse(status_code=status_code, content=envelope)
    finally:
        try:
            active_auth_token.reset(token_reset)
        except ValueError:
            active_auth_token.set("")


@router.post(
    "/query/all",
    response_model=MultiModelQueryResponse,
    status_code=status.HTTP_200_OK,
    summary="Execute Financial Query Against Every Available Model (Dev)",
    description=(
        "Dev-only comparison option: runs the same query against every model "
        "this deployment has credentials for, each at effort='exhaustive', "
        "and returns every answer. Synchronous only — no streaming variant."
    ),
    tags=["Gemini Brain AI Engine", "Dev"],
)
async def run_query_all_models(
    payload: QueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> MultiModelQueryResponse:
    """Fan the same query out to every available model and collect every answer."""
    import asyncio
    orgs = _query_orgs(payload, current_user, action="query.all_models")
    if len(orgs) > 1:
        # Models x organizations multiplies cost; this dev route stays single-org.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Model comparison runs against one organization at a time.",
        )
    organization_id = orgs[0] if orgs else None
    available_keys = [m["key"] for m in list_models() if m["available"]]
    auth_token = current_user.raw_token

    def _run_one(model_key: str) -> dict:
        # ContextVars are per-OS-thread unless explicitly propagated, so each
        # worker thread sets its own copy from the captured token rather than
        # relying on it being inherited from the request thread.
        token_reset = active_auth_token.set(auth_token)
        try:
            runner = GeminiBrainRunner()
            result = runner.run(
                query=payload.query,
                organization_id=organization_id,
                db_name=payload.db_name,
                use_api=payload.use_api,
                user_id=current_user.user_id,
                session_id=payload.session_id,
                allowed_org_ids=current_user.allowed_org_ids,
                auth_token=auth_token,
                model=model_key,
                effort="exhaustive",
                ui_context=payload.ui_context.model_dump() if payload.ui_context else None,
                brief=bool(payload.brief),
            )
            return normalize_envelope(result)
        except Exception as e:
            logger.warning("query/all: model %s failed: %s", model_key, e)
            code = classify_exception(e)
            notice_obj = notice_for(code, request_id=new_request_id())
            return normalize_envelope({
                "answer": notice_obj["message"],
                "error": code.value,
                "status": "failed",
                "notice": notice_obj,
                "policy": {"model": model_key, "model_label": model_key},
            })
        finally:
            try:
                active_auth_token.reset(token_reset)
            except ValueError:
                active_auth_token.set("")

    if not available_keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No models are currently configured/available.",
        )

    # Use asyncio.gather to run all blocking operations in the default thread pool
    # concurrently without blocking the main FastAPI event loop.
    tasks = [asyncio.to_thread(_run_one, key) for key in available_keys]
    results = await asyncio.gather(*tasks)

    return MultiModelQueryResponse(responses=[QueryResponse(**r) for r in results])


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
    """Stream query execution status chunks via Server-Sent Events (SSE)."""
    # Checked before the stream opens, so a refused org is a plain 403 and no
    # event is ever emitted for it.
    orgs = _query_orgs(payload, current_user, action="query.stream")
    preview_orgs = _preview_scope(payload, current_user, orgs)
    if preview_orgs is not None:
        return StreamingResponse(
            _preview_events(payload, current_user, preview_orgs),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
        )
    if agent_preview.requested(payload.model):
        payload = payload.model_copy(update={"model": "auto"})
    organization_id = orgs[0] if orgs else None

    def event_generator() -> Generator[str, None, None]:
        from gemini_brain.observability.sql_tracer import start_sql_trace, get_sql_traces, clear_sql_trace
        from gemini_brain.observability.api_tracer import start_api_trace, get_api_traces, clear_api_trace
        from gemini_brain.observability.llm_tracer import start_llm_trace, get_llm_traces, clear_llm_trace
        rid = new_request_id()
        token_reset = active_auth_token.set(current_user.raw_token)
        start_sql_trace(trace_id=rid)
        start_api_trace(trace_id=rid)
        start_llm_trace(trace_id=rid)
        chunks = None
        try:
            history = agent_shadow.thread_history(payload.session_id, payload.db_name)
            if len(orgs) > 1:
                chunks = run_multi_org_stream(
                    payload.query, orgs, _org_meta(current_user, orgs),
                    runner_factory=GeminiBrainRunner,
                    run_kwargs=_multi_org_run_kwargs(payload, current_user),
                    planner=plan_query,
                )
            else:
                chunks = GeminiBrainRunner().run_stream(
                    query=payload.query,
                    organization_id=organization_id,
                    db_name=payload.db_name,
                    use_api=payload.use_api,
                    user_id=current_user.user_id,
                    session_id=payload.session_id,
                    selected_model_key=payload.selected_model_key,
                    allowed_org_ids=current_user.allowed_org_ids,
                    auth_token=current_user.raw_token,
                    model=payload.model,
                    effort=payload.effort,
                    ui_context=payload.ui_context.model_dump() if payload.ui_context else None,
                    brief=bool(payload.brief),
                )
            for chunk in chunks:
                if isinstance(chunk, dict) and "final_result" in chunk:
                    traces = get_sql_traces(trace_id=rid)
                    if traces and isinstance(chunk["final_result"], dict):
                        chunk["final_result"]["sql_traces"] = traces
                        if not chunk["final_result"].get("sql"):
                            chunk["final_result"]["sql"] = traces[0]["sql"]
                    api_traces = get_api_traces(trace_id=rid)
                    if api_traces and isinstance(chunk["final_result"], dict):
                        chunk["final_result"]["api_traces"] = api_traces
                    llm_traces = get_llm_traces(trace_id=rid)
                    if llm_traces and isinstance(chunk["final_result"], dict):
                        chunk["final_result"]["llm_traces"] = llm_traces
                    chunk["final_result"] = normalize_envelope(chunk["final_result"])
                    agent_shadow.start(payload.query, orgs, _agent_org_meta(orgs, current_user),
                                       chunk["final_result"], history=history)
                yield f"data: {json.dumps(chunk, default=str)}\n\n"
        except ValueError as ve:
            code = ErrorCode.TENANT_FORBIDDEN if "tenant" in str(ve).lower() else ErrorCode.VALIDATION_FAILED
            err_notice = notice_for(code, request_id=rid)
            err_env = normalize_envelope({
                "answer": err_notice["message"],
                "error": code.value,
                "status": "failed",
                "notice": err_notice,
                "request_id": rid,
            })
            yield f"data: {json.dumps({'type': 'error', 'notice': err_notice})}\n\n"
            yield f"data: {json.dumps({'final_result': err_env})}\n\n"
        except Exception as e:
            code = classify_exception(e)
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
        finally:
            # Stop or a dropped connection closes this generator; close the
            # pipeline's too, so its cleanup (cancelling queued per-org work)
            # runs now rather than whenever it is garbage-collected.
            close = getattr(chunks, "close", None)
            if close is not None:
                try:
                    close()
                except Exception as e:
                    logger.debug("stream pipeline close failed: %s", e)
            clear_sql_trace(trace_id=rid)
            clear_api_trace(trace_id=rid)
            clear_llm_trace(trace_id=rid)
            try:
                active_auth_token.reset(token_reset)
            except ValueError:
                active_auth_token.set("")

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


