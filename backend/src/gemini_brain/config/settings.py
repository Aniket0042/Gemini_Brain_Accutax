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

import logging
from pathlib import Path
from typing import Annotated, Optional
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, NoDecode
from pydantic import Field, field_validator

#: Repository root: settings.py → config → gemini_brain → src → <root>
_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"

# pydantic-settings' own `env_file` support (below) only loads .env values into
# this module's `GeminiBrainSettings` fields — it never touches the real process
# environment. Anything read via a bare `os.getenv()` elsewhere (boto3's default
# AWS credential chain, the agents/ pipeline's module-level env lookups) sees
# nothing from .env without this. `override=False` so already-set real env vars
# (a deployment platform's injected config) always win over the local file.
load_dotenv(_ENV_FILE, override=False)


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
    accutax_auth_token: str = Field(
        default="",
        description=(
            "Long-lived seed/fallback bearer token. Used only when no per-request "
            "user token is available and no service account is configured — it is a "
            "~24h JWT and goes stale. Prefer the service account fields below."
        ),
    )
    accutax_service_email: str = Field(
        default="",
        description=(
            "Service-account email. When set with accutax_service_password, "
            "unattended API calls re-authenticate before expiry instead of relying "
            "on the static accutax_auth_token. Never used in place of a real user "
            "token — see auth/service_token.py."
        ),
    )
    accutax_service_password: str = Field(
        default="",
        description="Service-account password. Supply via environment, never commit.",
    )
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
    db_name_allowlist: Optional[str] = Field(
        default="",
        description=(
            "Comma-separated extra databases a request may select via the "
            "db_name field. The configured db_name is always allowed."
        ),
    )
    #: Numeric grounding runs in shadow mode until this is on: the report is
    #: produced and logged either way, but only enforcement alters the answer.
    verify_enforce: bool = Field(default=False)

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
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:3000", "http://localhost:5173", "http://127.0.0.1:3000", "http://127.0.0.1:5173"],
        description="Allowed CORS origins for API requests.",
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value):
        if value is None or value == "":
            return []
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return value
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value
    jwt_secret: str = Field(
        default="",
        description="Secret key for signing and verifying JWT tokens. Must be supplied via environment variable JWT_SECRET.",
    )
    accutax_jwt_secret: str = Field(
        default="",
        description=(
            "Accutax Nest JWT secret (JWT_SECRET_KEY). When set, Gemini Brain "
            "accepts the same bearer the main app already holds so the in-app "
            "chat widget can call /query without a second login."
        ),
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
        # Anchored to the repository root rather than the process working
        # directory. A relative ".env" silently falls back to the defaults
        # below when the app is started from anywhere else — which points the
        # database at a host that does not exist, with no error until a query
        # runs and fails.
        "env_file": _ENV_FILE,
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
        "extra": "ignore",
    }


# Module-level singleton — import this wherever config is needed.
settings = GeminiBrainSettings()


if not _ENV_FILE.exists():
    logging.getLogger("gemini_brain.config").warning(
        "No .env found at %s — falling back to built-in defaults "
        "(db=%s@%s). Set DB_* environment variables explicitly if this is "
        "not what you intended.",
        _ENV_FILE, settings.db_name, settings.db_host,
    )
