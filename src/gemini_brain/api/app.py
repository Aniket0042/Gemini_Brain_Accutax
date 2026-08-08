"""
app.py — FastAPI application factory with Swagger UI OpenAPI documentation and CORS support.
"""
from __future__ import annotations

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from gemini_brain.api.routes import router
from gemini_brain.config.settings import settings

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


from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager to auto-initialize DB tables on startup."""
    try:
        from gemini_brain.api.auth import init_auth_db
        init_auth_db()
    except Exception as e:
        logger.warning("Auth DB auto-initialization skipped or failed on startup: %s", e)
    yield


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

    # ── CORS Middleware Configuration ──────────────────────────────
    # Configured origins from settings.cors_origins (no wildcard allow_origins with credentials)
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
