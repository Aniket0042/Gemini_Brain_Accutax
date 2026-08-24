"""
app.py — FastAPI application factory with Swagger UI OpenAPI documentation, CORS support,
request correlation tracking, and global resilience exception handlers.
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from gemini_brain.api.routes import router
from gemini_brain.config.settings import settings
from gemini_brain.resilience import (
    AppError,
    ErrorCode,
    HTTP_FOR_CODE,
    classify_exception,
    build_notice,
    normalize_envelope,
    new_request_id,
    notice_for,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("gemini_brain.api.app")

DESCRIPTION = """
### 🧠 Gemini Brain — Hybrid AI Orchestration Engine API

**Gemini Brain** routes financial questions between **Google Gemini 2.5 Flash** (routing, classification, endpoint selection, direct answers) and **Anthropic Claude on AWS Bedrock** (data-driven financial reasoning over live REST API data).

#### Key Features:
- **7-Type Intent Router**: Direct Gemini Flash answers for FAQs, app guidance, accounting concepts, strategic advice.
- **API-First Reasoning**: Automatic REST endpoint parameter building + Claude reasoning over live financial data.
- **SQL Fallback Engine**: PostgreSQL NL-to-SQL execution engine when REST endpoints are missing.
- **Session Memory**: Persistent chat history and hybrid state tracking.

---
#### Interactive Swagger Documentation
- **Swagger UI**: `/docs`
- **ReDoc**: `/redoc`
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to auto-initialize DB tables on startup and cleanup clients."""
    try:
        from gemini_brain.api.auth import init_auth_db
        init_auth_db()
    except Exception as e:
        logger.warning("Auth DB auto-initialization skipped or failed on startup: %s", e)

    # Phase F: Proactively inspect Accutax REST API auth token health on startup
    try:
        from gemini_brain.auth.token_monitor import inspect_jwt_token
        inspect_jwt_token()
    except Exception as e:
        logger.warning("JWT token health inspection skipped on startup: %s", e)

    yield
    try:
        from gemini_brain.api_client.accutax_client import close_client_async
        await close_client_async()
    except Exception:
        pass


def create_app() -> FastAPI:
    """Create and configure the FastAPI web application instance."""
    app = FastAPI(
        title="Gemini Brain AI Orchestration Engine",
        description=DESCRIPTION,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # ── Request Correlation Middleware ──────────────────────────────
    @app.middleware("http")
    async def correlation_id_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID") or new_request_id()
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response

    # ── Global Resilience Exception Handlers ────────────────────────
    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        req_id = getattr(request.state, "request_id", None) or new_request_id()
        status_code = HTTP_FOR_CODE.get(exc.code, 500)
        logger.warning("[%s] AppError (%s): %s", req_id, exc.code.value, exc.message)
        notice = build_notice(exc.code, subject="your records", request_id=req_id)
        envelope = normalize_envelope({
            "answer": notice["message"],
            "error": exc.code.value,
            "status": "failed",
            "notice": notice,
            "request_id": req_id,
        })
        return JSONResponse(status_code=status_code, content=envelope)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        req_id = getattr(request.state, "request_id", None) or new_request_id()
        logger.warning("[%s] RequestValidationError: %s", req_id, exc)
        notice = build_notice(ErrorCode.VALIDATION_FAILED, subject="your request", request_id=req_id)
        envelope = normalize_envelope({
            "answer": notice["message"],
            "error": ErrorCode.VALIDATION_FAILED.value,
            "status": "failed",
            "notice": notice,
            "request_id": req_id,
        })
        return JSONResponse(status_code=422, content=envelope)

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        req_id = getattr(request.state, "request_id", None) or new_request_id()
        code = classify_exception(exc)
        logger.exception("[%s] Unhandled exception (%s): %s", req_id, code.value, exc)
        status_code = HTTP_FOR_CODE.get(code, 500)
        notice = build_notice(code, subject="your records", request_id=req_id)
        envelope = normalize_envelope({
            "answer": notice["message"],
            "error": code.value,
            "status": "failed",
            "notice": notice,
            "request_id": req_id,
        })
        return JSONResponse(status_code=status_code, content=envelope)

    # ── CORS Middleware Configuration ──────────────────────────────
    origins = settings.cors_origins or ["http://localhost:3000", "http://localhost:5173"]
    logger.info("Configuring CORS with origins: %s", origins)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # ── Include API Routers ─────────────────────────────────────────
    app.include_router(router)

    return app


# Singleton FastAPI app instance for Uvicorn ASGI server
app = create_app()
