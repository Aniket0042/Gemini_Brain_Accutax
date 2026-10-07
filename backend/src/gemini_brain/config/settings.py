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
        default="in.anthropic.claude-sonnet-5",
        description="Primary Bedrock model (Sonnet-class). India-region profile.",
    )
    bedrock_model_id_fast: str = Field(
        default="in.anthropic.claude-haiku-4-5-20251001-v1:0",
        description="Fast/cheap Bedrock model (Haiku-class). India-region profile.",
    )
    bedrock_max_tokens: int = Field(default=2000)
    bedrock_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    bedrock_read_timeout_seconds: float = Field(
        default=45.0,
        gt=0,
        description="Longest wait for a Bedrock response (or the next stream chunk). boto3's default is 60 s.",
    )
    bedrock_max_attempts: int = Field(
        default=2,
        ge=1,
        description="Total attempts per Bedrock call at the SDK level (boto3's legacy default is 5).",
    )
    multi_org_fetch_deadline_seconds: float = Field(
        default=40.0,
        gt=0,
        description=(
            "How long a multi-org answer waits for the organizations' data. Organizations still "
            "running are reported as not answered in time, and their queued work is cancelled."
        ),
    )

    # ── Accutax Backend API ────────────────────────────────────
    accutax_base_url: str = Field(default="http://13.127.157.108:8081")
    accutax_app_url: str = Field(
        default="https://accutax-bk-testing.netlify.app",
        description=(
            "Public URL of the Accutax web app (the frontend users click "
            "into), NOT the REST API host above. Used to build deep links "
            "from App Guidance answers into the live app — see "
            "knowledge/guide_loader.py SECTION_ROUTES. Override via "
            "ACCUTAX_APP_URL for production."
        ),
    )
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

    db_pool_enabled: bool = Field(
        default=True,
        description="Reuse database connections (sql_fallback/db_connection.py). False opens one per query.",
    )
    db_pool_max: int = Field(
        default=10,
        ge=1,
        description="Pooled connections per database. When all are busy, a caller gets a direct connection.",
    )
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
        default=[
            "http://localhost:3000",
            "http://localhost:5173",
            "http://localhost:5174",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
            "https://accutaxai.netlify.app",
            "https://accutax-bk-testing.netlify.app",
        ],
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
    max_orgs_per_query: int = Field(
        default=5,
        description="Most organizations one request may name. Bounds fan-out cost and latency.",
    )
    multi_org_enabled: bool = Field(
        default=False,
        description=(
            "Allow one query to name several organizations (MULTI_ORG_ENABLED). "
            "Off: a request naming more than one organization is refused."
        ),
    )
    multi_org_plan_log: str = Field(
        default="logs/multi_org_plans.jsonl",
        description=(
            "JSON-lines file recording how each multi-organization question was planned and laid "
            "out (redacted question, no IDs), relative to backend/. Empty turns it off."
        ),
    )
    seed_test_users: str = Field(
        default="",
        description=(
            "JSON list of test accounts to create at startup, e.g. "
            '[{"name": "Demo", "email": "demo@x.com", "password": "...", "org_ids": [27]}]. '
            "Empty means no seeding and no startup writes to the database."
        ),
    )
    show_sql_traces: bool = Field(
        default=True,
        description="Whether to capture and return executed SQL traces in query responses.",
    )
    show_api_traces: bool = Field(
        default=True,
        description="Whether to capture and return executed Accutax REST API call traces (endpoint, params, status) in query responses.",
    )
    show_llm_traces: bool = Field(
        default=True,
        description="Whether to capture and return per-call Bedrock/Claude LLM traces (model, purpose, tokens, duration) in query responses.",
    )
    report_narrative_mode: str = Field(
        default="llm",
        description=(
            "How report files and the canvas get their written summary: 'llm' (a report "
            "narrator writes from the verified fact brief, template on failure), 'template' "
            "(deterministic sentences only, no model call)."
        ),
    )
    report_narrative_model_id: str = Field(
        default="",
        description="Bedrock model for report narration. Empty = BEDROCK_MODEL_ID (the primary model).",
    )
    report_narrative_timeout_seconds: float = Field(
        default=20.0,
        ge=1.0,
        description="Longest a report waits for its narrative before using the template instead.",
    )
    artifact_dir: str = Field(
        default="",
        description="Directory for generated report files, their specs and the audit log. Empty = <system temp>/accutax-artifacts.",
    )
    artifact_spec_retention_days: int = Field(
        default=30,
        ge=0,
        description="How long a report's validated spec is kept after its file expires, so the file can be regenerated.",
    )
    artifact_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        description="Largest generated file the store accepts; a bigger render is refused rather than served.",
    )
    report_render_timeout_seconds: float = Field(
        default=45.0,
        ge=1.0,
        description="Longest one file format may take to render before the report goes out without it.",
    )
    artifact_ttl_seconds: int = Field(
        default=86_400,
        ge=60,
        description=(
            "How long a generated report file (PDF/XLSX/DOCX/...) stays downloadable. "
            "Download still requires the owning user and an allowed organization."
        ),
    )

    # ── UAE VAT knowledge base (docs/product/VAT_KNOWLEDGE_BASE_PLAN.md) ──
    vat_kb_enabled: bool = Field(
        default=False,
        description="Answer UAE VAT law questions from the FTA document knowledge base, with cited sources.",
    )
    vat_kb_shadow: bool = Field(
        default=False,
        description=(
            "Detect and search VAT law questions in the background and log what would be used, "
            "without changing any answer. Ignored when vat_kb_enabled is on."
        ),
    )
    vat_kb_dir: str = Field(
        default="",
        description="Knowledge-base directory (documents.csv, pdfs/, builds/, CURRENT). Empty = feature unavailable.",
    )
    vat_kb_model_id: str = Field(
        default="",
        description="Bedrock model for VAT law answers. Empty = BEDROCK_MODEL_ID (the primary Sonnet model).",
    )
    vat_kb_timeout_seconds: float = Field(
        default=3.0,
        ge=0.1,
        description="Longest the knowledge-base search may take before the answer falls back to the normal path.",
    )

    # ── Cube Core semantic layer (docs/CUBE_CORE_INTEGRATION_GUIDE_V2.md) ──
    cube_api_url: str = Field(
        default="http://127.0.0.1:4000",
        description="Cube API base URL. Cube listens on loopback only; never point this at a public host.",
    )
    cube_api_secret: str = Field(
        default="",
        description=(
            "HS256 secret shared with Cube (CUBEJS_API_SECRET), at least 32 characters. "
            "Deliberately no default: empty disables every Cube call."
        ),
    )
    cube_jwt_audience: str = Field(default="accutax-cube")
    cube_jwt_issuer: str = Field(default="gemini-brain")
    metrics_backend: str = Field(
        default="sql",
        description=(
            "Which backend answers multi-org metric questions: 'sql' (the rpt_ reports), "
            "'shadow' (SQL answers; Cube is compared off the request path and logged), "
            "or 'cube' (not wired yet; Phase 1b)."
        ),
    )
    metrics_shadow_log: str = Field(
        default="logs/metrics_shadow.jsonl",
        description=(
            "JSON-lines file of SQL-vs-Cube metric comparisons in shadow mode, relative to "
            "backend/. Holds organization IDs and figures, no user IDs. Empty turns it off."
        ),
    )
    report_timezone: str = Field(
        default="Asia/Dubai",
        description="Time zone that turns period presets such as 'ytd' into dates.",
    )

    # ── Tool-using agent (Phase 2, docs/ai-architecture-review-2026-10.md section 6.3) ──
    agent_mode: str = Field(
        default="off",
        description=(
            "'off', or 'shadow': the current path answers and the agent answers the same question "
            "off the request path; both answers are logged to AGENT_SHADOW_LOG. Users never see "
            "the agent's answer in shadow mode."
        ),
    )
    agent_model_id: str = Field(
        default="",
        description="Bedrock model the agent plans and writes with. Empty = the Sonnet 5 profile (SONNET5_ID).",
    )
    agent_answer_model_id: str = Field(
        default="",
        description=(
            "Bedrock model that writes the answer after the tools have run (the planner picks the tools). "
            "Empty = the planner model. A faster model here cuts most of the agent's time."
        ),
    )
    agent_preview_users: str = Field(
        default="",
        description=(
            "Comma-separated login emails or user ids that see 'Accutax Agent (preview)' in the model picker "
            "and can have their chats answered by the agent. Empty = nobody; everyone else keeps the current path."
        ),
    )
    agent_max_tool_calls: int = Field(default=6, ge=1, le=12)
    agent_deadline_seconds: float = Field(default=45.0, ge=5.0, le=120.0)
    agent_shadow_log: str = Field(
        default="logs/agent_shadow.jsonl",
        description=(
            "JSON-lines file of current-path vs agent answers in shadow mode, relative to backend/. "
            "Holds the redacted question, both answers and the tool calls, no user IDs. Empty turns it off."
        ),
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
