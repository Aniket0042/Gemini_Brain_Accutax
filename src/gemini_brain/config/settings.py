"""
settings.py — Environment-driven configuration via Pydantic BaseSettings.

All environment variables the Gemini Brain subsystem reads are declared here.
Values are loaded from the process environment (and optionally from a .env file
via python-dotenv) at import time.

Original locations:
  - gemini_brain_adapter.py  (GEMINI_API_KEY, ACCUTAX_USER_ID)
  - api_agent.py             (ACCUTAX_BASE_URL, ACCUTAX_AUTH_TOKEN, ACCUTAX_USER_ID)
  - executor.py              (DB_HOST … DB_PASSWORD)
  - bedrock_client.py        (BEDROCK_REGION, BEDROCK_MODEL_ID, etc.)
"""
from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field


class GeminiBrainSettings(BaseSettings):
    """Centralised, validated configuration for the Gemini Brain package."""

    # ── Google Gemini ──────────────────────────────────────────
    gemini_api_key: str = Field(
        default="",
        description="Google Gemini API key.",
    )

    # ── AWS Bedrock ────────────────────────────────────────────
    bedrock_region: str = Field(default="ap-south-1")
    bedrock_model_id: str = Field(
        default="apac.anthropic.claude-3-5-sonnet-20241022-v2:0",
        description="Primary Bedrock model (Sonnet-class).",
    )
    bedrock_model_id_fast: str = Field(
        default="anthropic.claude-3-haiku-20240307-v1:0",
        description="Fast/cheap Bedrock model (Haiku-class).",
    )
    bedrock_max_tokens: int = Field(default=2000)

    # ── Accutax Backend API ────────────────────────────────────
    accutax_base_url: str = Field(default="http://13.127.157.108:8081")
    accutax_auth_token: str = Field(default="")
    accutax_user_id: str = Field(
        default="18",
        description=(
            "Default user ID for Accutax API calls.  Kept as a string because "
            "some endpoints require userId as a quoted string value."
        ),
    )

    # ── PostgreSQL Database ────────────────────────────────────
    db_host: str = Field(default="127.0.0.1")
    db_port: int = Field(default=5432)
    db_name: str = Field(default="accutax_llm")
    db_user: str = Field(default="accutax_llm_user")
    db_password: Optional[str] = Field(
        default=None,
        description="PostgreSQL password. Must be supplied via environment.",
    )

    # ── Defaults ───────────────────────────────────────────────
    accutax_org_id: Optional[int] = Field(
        default=None,
        description="Organization/tenant ID. Must be passed explicitly or resolved dynamically.",
    )

    # ── API Server & Security Settings ─────────────────────────
    api_host: str = Field(default="0.0.0.0", description="API server host.")
    api_port: int = Field(default=8000, description="API server port.")
    cors_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
        description="Allowed CORS origins for API requests.",
    )
    jwt_secret: str = Field(
        default="",
        description="Secret key for signing and verifying JWT tokens. Must be supplied via environment variable JWT_SECRET.",
    )
    jwt_algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm.",
    )
    jwt_expiration_minutes: int = Field(
        default=60,
        description="JWT token validity in minutes.",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Module-level singleton — import this wherever config is needed.
settings = GeminiBrainSettings()
