"""
settings.py — Environment-driven configuration via Pydantic BaseSettings.

All environment variables the Gemini Brain subsystem reads are declared here.
Values are loaded from the process environment (and optionally from a .env file
via python-dotenv) at import time.

Original locations:
  - api_agent.py             (ACCUTAX_BASE_URL)
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

    db_pool_enabled: bool = Field(
        default=True,
        description="Reuse database connections (db/connection.py). False opens one per query.",
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

    # ── Document upload in chat (docs/DOCUMENT_UPLOAD_PLAN.md) ──
    uploads_enabled: bool = Field(
        default=False,
        description="Turns file upload in chat on. Off by default: the paperclip is hidden and /uploads refuses.",
    )
    upload_users: str = Field(
        default="",
        description="Comma-separated emails or user ids who may upload while UPLOADS_ENABLED is on. Empty = everyone.",
    )
    upload_dir: str = Field(
        default="",
        description="Directory for uploaded files, their metadata and the audit log. Empty = <system temp>/accutax-uploads.",
    )
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, description="Largest file accepted.")
    upload_max_pages: int = Field(default=20, ge=1, description="Most pages in one PDF.")
    upload_max_files: int = Field(default=3, ge=1, description="Most files attached to one question.")
    upload_daily_pages: int = Field(
        default=100,
        ge=1,
        description="Pages of PDF and image files one user may upload per day (UTC). Text files do not count.",
    )
    upload_extractor: str = Field(
        default="claude",
        description=(
            "Who reads scanned pages, photos and bills: 'claude' (Bedrock) or 'none' (only text files are read). "
            "The existing OCR service becomes a third value in phase 7."
        ),
    )
    upload_extract_model: str = Field(
        default="in.anthropic.claude-sonnet-5",
        description="Bedrock model that reads documents. India-region profile. Haiku 4.5 misread tax IDs in phase 0.",
    )
    upload_read_timeout_seconds: float = Field(
        default=180.0, gt=0, description="Longest wait for the model to read one document.")
    upload_read_workers: int = Field(default=4, ge=1, le=32, description="Documents read at the same time.")
    upload_read_max_tokens: int = Field(default=12000, ge=1000, le=32000, description="Longest record of one model call.")
    upload_chunk_pages: int = Field(
        default=5, ge=1, le=20,
        description=(
            "A PDF the model reads is cut into parts of this many pages, read at the same time and put back together: "
            "a 20-page statement does not fit one answer (phase 5: 240 lines gave no lines at all)."
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
    report_timezone: str = Field(
        default="Asia/Dubai",
        description="Time zone that turns period presets such as 'ytd' into dates.",
    )

    # ── Tool-using agent (docs/ai-architecture-review-2026-10.md section 6.3) ──
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
    agent_models: str = Field(
        default="sonnet-5",
        description=(
            "Comma-separated model keys users can pick (agent/models.py): sonnet-5, haiku-4.5, haiku-3. "
            "The default (sonnet-5) is always offered. Switch a model on only after it passes the real and "
            "golden question sets. One model means no picker."
        ),
    )
    agent_model_users: str = Field(
        default="",
        description=(
            "Comma-separated emails or user ids who may pick models other than the default. "
            "Empty = everyone who can use the agent."
        ),
    )
    agent_max_tool_calls: int = Field(default=6, ge=1, le=12)
    agent_deadline_seconds: float = Field(default=45.0, ge=5.0, le=120.0)
    aed_rates_file: str = Field(
        default="",
        description=(
            "JSON file of rates to AED kept by finance (see config/aed_rates.json). Empty uses the file that ships with the "
            "code, which holds only the official fixed USD rate."
        ),
    )
    agent_audit_log: str = Field(
        default="logs/agent_audit.jsonl",
        description=(
            "JSON-lines file of what the assistant flagged and what its guards did (VAT reviews, order-like text found in "
            "files or in the books, claims about the books that were not checked, a leaked prompt marker), relative to "
            "backend/. Ids, counts and document numbers only. Empty turns it off."
        ),
    )
    agent_answer_log: str = Field(
        default="",
        description=(
            "JSON-lines file with one line per agent answer (primary mode and preview), relative to backend/, "
            "for example logs/agent_answers.jsonl: masked question, user and organization ids, route, tool calls, "
            "figure check, time, tokens, cost, status and answer. Rotates at 50 MB, keeping 5 files. "
            "Empty (the default) turns it off."
        ),
    )
    usage_ledger: str = Field(
        default="",
        description=(
            "JSON-lines file with one line per piece of AI spend (an agent answer, a file read, a model call made "
            "outside both), relative to backend/, for example logs/ai_usage.jsonl: user, organizations, their owners "
            "and the owner charged, models, tokens, cost and credits (1 credit = $0.001). Records only; nothing is "
            "limited. Rotates at 50 MB, keeping 5 files. Empty (the default) turns it off."
        ),
    )

    # ── Privacy (PII plan) ─────────────────────────────────────
    pii_mode: str = Field(
        default="off",
        description=(
            "off | shadow | on. on: the agent sends the model placeholders ([SUPPLIER_3], [ORG_1], [TRN_2]) instead "
            "of names, contact details and identifiers, and puts the real values back in its tool calls and answer. "
            "shadow: builds the masked messages and lets PII_SENTINEL report them, but sends the originals. "
            "off: as before."
        ),
    )
    pii_sentinel: str = Field(
        default="off",
        description=(
            "off | report | strict. report: scan every Bedrock call just before it is sent and log the personal "
            "data it carries (kinds, counts, where; masked previews only) to PII_SENTINEL_LOG. strict (with "
            "PII_MODE=on): also hide anything it finds behind the request's placeholders before the call goes, so "
            "the answer still shows real values; logged as strict_hid. Neither blocks a call. Summarise with "
            "scripts/pii_leak_report.py."
        ),
    )
    pii_snapshots: str = Field(
        default="off",
        description=(
            "off | on. on: keep a before/after snapshot of every masked request (PII_MODE shadow or on) for the "
            "privacy page: what was sent to the model (plain) and what the request held (encrypted with "
            "PII_SNAPSHOT_KEY). See privacy/snapshots.py."
        ),
    )
    pii_badge: str = Field(
        default="on",
        description=(
            "on | off. on (with PII_MODE=on): every answer carries a privacy badge: how many names and IDs were "
            "hidden from the model, which ones in this question and answer, and what the model saw."
        ),
    )
    pii_card_guard: str = Field(
        default="on",
        description=(
            "on | off. on: a card number typed into the chat is removed from the question before the model, the "
            "saved chat or any log sees it, never put back, and the user is warned on screen (privacy/sensitive.py)."
        ),
    )
    pii_demo_orgs: str = Field(
        default="",
        description=(
            "Comma-separated organization ids the privacy page's live before/after demo may run on (admins in "
            "PII_SNAPSHOT_ADMINS only). Empty: any organization the admin can open."
        ),
    )
    pii_snapshot_users: str = Field(
        default="", description="Comma-separated user ids: keep snapshots only of their requests. Empty: everyone.")
    pii_snapshot_sample_pct: int = Field(default=100, ge=0, le=100,
                                         description="Share of selected requests that get a snapshot, in percent.")
    pii_snapshot_admins: str = Field(
        default="", description="Comma-separated emails or user ids that may open the privacy page. Empty: nobody.")
    pii_snapshot_key: str = Field(
        default="",
        description="Fernet key (cryptography.fernet.Fernet.generate_key()) for the raw side. Empty: raw not kept.",
    )
    pii_snapshot_dir: str = Field(default="logs/pii_snapshots",
                                  description="Directory for snapshots, relative to backend/.")
    pii_snapshot_raw_days: int = Field(default=7, ge=0, description="Days the encrypted raw side is kept.")
    pii_snapshot_days: int = Field(default=30, ge=0, description="Days the sent side is kept.")
    pii_sentinel_log: str = Field(
        default="logs/pii_sentinel.jsonl",
        description="JSON-lines file for PII_SENTINEL, relative to backend/. Rotates at 50 MB, keeping 5 files.",
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
