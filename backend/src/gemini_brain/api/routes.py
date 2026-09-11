"""
routes.py — FastAPI route definitions for Gemini Brain.
"""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Generator, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm

from gemini_brain.api.auth import (
    ORGANIZATION_DIRECTORY,
    CurrentUser,
    authenticate_with_accutax_api,
    create_access_token,
    fetch_accutax_accessible_orgs,
    get_current_user,
    get_user_allowed_orgs,
    get_user_by_email,
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
from gemini_brain.health.model_health_checker import check_all_models_and_services
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
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


# ── Authentication Endpoints ──────────────────────────────────────────────────

@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="User Login (Swagger Form & OAuth2)",
    description=(
        "Authenticates user email and password against live Accutax Backend API (with local fallback), "
        "returning an authentic JWT token with accessible organization tenants. "
        "Compatible with Swagger UI's top-right **Authorize** button."
    ),
)
def login_form(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    """Authenticate user credentials via form data and issue JWT token."""
    # 1. Try upstream Accutax API login first
    upstream = authenticate_with_accutax_api(form_data.username, form_data.password)
    if upstream:
        tenants = [t for t in ORGANIZATION_DIRECTORY if t["id"] in upstream["allowed_org_ids"]]
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
    tenants = [t for t in ORGANIZATION_DIRECTORY if t["id"] in allowed_org_ids]

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
        tenants = [t for t in ORGANIZATION_DIRECTORY if t["id"] in upstream["allowed_org_ids"]]
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
    tenants = [t for t in ORGANIZATION_DIRECTORY if t["id"] in allowed_org_ids]

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
    return ModelCatalogResponse(
        models=[ModelInfo(**m) for m in list_models()],
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
    live = fetch_accutax_accessible_orgs(current_user.raw_token, current_user.user_id)
    if live:
        tenants = live
    else:
        allowed = {int(o) for o in (current_user.allowed_org_ids or [])}
        tenants = [t for t in ORGANIZATION_DIRECTORY if t["id"] in allowed]

    return TenantListResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenants=[TenantInfo(**t) for t in tenants],
    )


def _assert_org_allowed(organization_id: Optional[int], current_user: CurrentUser) -> None:
    if organization_id is None:
        return
    allowed = current_user.allowed_org_ids or []
    if allowed and int(organization_id) not in {int(o) for o in allowed}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization is not in the caller's allowed tenant list.",
        )


@router.get(
    "/sessions",
    response_model=ChatSessionListResponse,
    tags=["Chat Sessions"],
    summary="List chat threads for the current user",
)
def list_chat_sessions(
    organization_id: Optional[int] = None,
    current_user: CurrentUser = Depends(get_current_user),
) -> ChatSessionListResponse:
    from gemini_brain.memory.session_memory import list_sessions_for_user_org

    _assert_org_allowed(organization_id, current_user)
    rows = list_sessions_for_user_org(current_user.user_id, organization_id)
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

    _assert_org_allowed(payload.organization_id, current_user)
    session_id = payload.session_id if payload.session_id and is_valid_uuid(payload.session_id) else str(uuid_lib.uuid4())
    ok = ensure_session(session_id, current_user.user_id, payload.organization_id)
    if not ok:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Could not create session.")
    rec = get_session_record(session_id) or {}
    return ChatSessionSchema(
        id=session_id,
        user_id=current_user.user_id,
        organization_id=payload.organization_id if payload.organization_id is not None else rec.get("organization_id"),
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
    )

    rec = get_session_record(session_id)
    if not rec:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found.")
    if not verify_session_ownership(session_id, current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Session does not belong to this user.")
    _assert_org_allowed(rec.get("organization_id"), current_user)
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
    from gemini_brain.memory.session_memory import delete_session, get_session_record

    rec = get_session_record(session_id)
    if rec:
        _assert_org_allowed(rec.get("organization_id"), current_user)
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
    from gemini_brain.artifacts.store import get as get_artifact, was_expired

    rec = get_artifact(artifact_id)
    if rec is None:
        if was_expired(artifact_id):
            raise HTTPException(status_code=status.HTTP_410_GONE, detail="This file expired. Ask again to regenerate it.")
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
    if int(rec.user_id) != int(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This file does not belong to this user.")
    _assert_org_allowed(rec.organization_id, current_user)
    return FileResponse(
        rec.path,
        media_type=rec.mime,
        filename=rec.filename,
        content_disposition_type="attachment",
    )


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
        "Pings Google Gemini 2.5 Flash, AWS Bedrock Claude 3.5 Sonnet, AWS Bedrock Claude 3 Haiku, "
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
    # Set request-scoped bearer token for any downstream Accutax REST calls
    token_reset = active_auth_token.set(current_user.raw_token)
    try:
        runner = GeminiBrainRunner()
        
        def _run():
            return runner.run(
                query=payload.query,
                organization_id=payload.organization_id,
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
                organization_id=payload.organization_id,
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

    def event_generator() -> Generator[str, None, None]:
        rid = new_request_id()
        token_reset = active_auth_token.set(current_user.raw_token)
        try:
            runner = GeminiBrainRunner()
            for chunk in runner.run_stream(
                query=payload.query,
                organization_id=payload.organization_id,
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
            ):
                if isinstance(chunk, dict) and "final_result" in chunk:
                    chunk["final_result"] = normalize_envelope(chunk["final_result"])
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


