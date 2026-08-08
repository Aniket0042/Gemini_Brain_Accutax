"""
routes.py — FastAPI route definitions for Gemini Brain.
"""
from __future__ import annotations

import json
import logging
from typing import Generator

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from fastapi.security import OAuth2PasswordRequestForm

from gemini_brain.api.auth import (
    CurrentUser,
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
    TokenResponse,
)
from gemini_brain.health.model_health_checker import check_all_models_and_services
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner

logger = logging.getLogger("gemini_brain.api.routes")

router = APIRouter(prefix="/api/v1", tags=["Gemini Brain AI Engine"])


# ── Authentication Endpoints ──────────────────────────────────────────────────

@router.post(
    "/auth/login",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="User Login (Swagger Form & OAuth2)",
    description=(
        "Authenticates user email and password, returning a JWT token with embedded tenant allow-list (`allowed_org_ids`). "
        "Compatible with Swagger UI's top-right **Authorize** button."
    ),
)
def login_form(form_data: OAuth2PasswordRequestForm = Depends()) -> TokenResponse:
    """Authenticate user credentials via form data and issue JWT token."""
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

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600,
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
    )


@router.post(
    "/auth/login-json",
    response_model=TokenResponse,
    tags=["Authentication"],
    summary="User Login (JSON Payload)",
    description="Authenticates user email and password via JSON payload and issues JWT token.",
)
def login_json(payload: LoginRequest) -> TokenResponse:
    """Authenticate user credentials via JSON payload and issue JWT token."""
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

    return TokenResponse(
        access_token=access_token,
        token_type="bearer",
        expires_in=3600,
        user_id=user["id"],
        email=user["email"],
        allowed_org_ids=allowed_org_ids,
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
        )
        return QueryResponse(**result)
    except ValueError as ve:
        logger.warning("Tenant or validation error processing query: %s", ve)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve),
        ) from ve
    except Exception as e:
        logger.error("Unhandled exception processing query: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Query execution failed: {str(e)}",
        ) from e


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
            ):
                yield f"data: {json.dumps(chunk, default=str)}\n\n"
        except ValueError as ve:
            err_chunk = {"status": str(ve), "type": "error"}
            yield f"data: {json.dumps(err_chunk)}\n\n"
        except Exception as e:
            err_chunk = {"status": f"Stream error: {str(e)}", "type": "error"}
            yield f"data: {json.dumps(err_chunk)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

