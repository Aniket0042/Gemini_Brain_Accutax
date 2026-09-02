"""
routes.py — FastAPI route definitions for Gemini Brain.
"""
from __future__ import annotations

import json
import logging
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm

from gemini_brain.api.auth import (
    ORGANIZATION_DIRECTORY,
    CurrentUser,
    authenticate_with_accutax_api,
    create_access_token,
    get_current_user,
    get_user_allowed_orgs,
    get_user_by_email,
    verify_password,
)
from gemini_brain.api.models import (
    HealthResponse,
    LoginRequest,
    ModelDiagnosticRequest,
    ModelHealthResponse,
    QueryRequest,
    QueryResponse,
    TenantInfo,
    TenantListResponse,
    TokenResponse,
)
from gemini_brain.api_client.accutax_client import active_auth_token
from gemini_brain.health.model_health_checker import check_all_models_and_services
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
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
        return TokenResponse(
            access_token=upstream["access_token"],
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
        return TokenResponse(
            access_token=upstream["access_token"],
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
    "/tenants",
    response_model=TenantListResponse,
    tags=["Tenant Management"],
    summary="List Accessible Tenant Organizations",
    description="Returns metadata, badges, and capability descriptions for all tenant organizations the authenticated user is authorized to query.",
)
def list_tenants(current_user: CurrentUser = Depends(get_current_user)) -> TenantListResponse:
    """List accessible tenant organizations for the current authenticated user."""
    accessible = [
        t for t in ORGANIZATION_DIRECTORY
        if t["id"] in current_user.allowed_org_ids
    ]
    if not accessible and len(current_user.allowed_org_ids) == 0:
        accessible = ORGANIZATION_DIRECTORY

    return TenantListResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenants=[TenantInfo(**t) for t in accessible],
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
def run_query(
    payload: QueryRequest,
    current_user: CurrentUser = Depends(get_current_user),
) -> QueryResponse:
    """Execute a synchronous financial query through Gemini Brain with tenant isolation enforcement."""
    # Set request-scoped bearer token for any downstream Accutax REST calls
    token_reset = active_auth_token.set(current_user.raw_token)
    try:
        runner = GeminiBrainRunner()
        result = runner.run(
            query=payload.query,
            organization_id=payload.organization_id,
            db_name=payload.db_name,
            use_api=payload.use_api,
            user_id=current_user.user_id,
            session_id=payload.session_id,
            selected_model_key=payload.selected_model_key,
            allowed_org_ids=current_user.allowed_org_ids,
            auth_token=current_user.raw_token,
        )
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


