"""
models.py — Pydantic request and response schemas for the Gemini Brain REST API.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class UIContext(BaseModel):
    """Contextual metadata injected by the frontend UI."""
    active_year: Optional[str] = Field(default=None, description="The year currently selected or viewed in the UI.")
    contact_name: Optional[str] = Field(default=None, description="The customer or vendor currently viewed.")
    bank_account: Optional[str] = Field(default=None, description="The bank account currently selected.")
    current_page: Optional[str] = Field(default=None, description="The UI page or dashboard the user is currently on (e.g. /invoices, /reports/pl).")


class QueryRequest(BaseModel):
    """Payload for submitting a financial query to Gemini Brain."""

    query: str = Field(
        ...,
        description="The natural language financial query (e.g. 'Show total revenue for 2026').",
        examples=["What is our total revenue this year?"],
    )
    organization_id: Optional[int] = Field(
        default=None,
        description="Organization / Tenant ID. If omitted, Gemini Brain attempts to extract it dynamically from the query.",
        examples=[27],
    )
    user_id: Optional[int] = Field(
        default=None,
        description=(
            "User ID making the request. Ignored for authenticated calls — the "
            "caller's own id is taken from the access token."
        ),
        examples=[None],
    )
    db_name: str = Field(
        default="",
        description=(
            "Database override. Only databases named in DB_NAME_ALLOWLIST are "
            "accepted; anything else falls back to the configured database."
        ),
        examples=[""],
    )
    use_api: bool = Field(
        default=True,
        description="Whether to attempt live REST API retrieval before DB fallback.",
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Optional chat session UUID for conversation memory and project knowledge context.",
        examples=[None],
    )
    selected_model_key: Optional[str] = Field(
        default=None,
        description="Optional model override for model arena comparison.",
        examples=[None],
    )
    model: Optional[str] = Field(
        default="auto",
        description="Model key from GET /api/v1/models, or 'auto' to let the system choose.",
        examples=["auto"],
    )
    effort: Optional[str] = Field(
        default="exhaustive",
        description=(
            "Effort tier: quick | standard | thorough | exhaustive, or 'auto'. "
            "Defaults to 'exhaustive' — the highest tier each model supports is "
            "used automatically; there is no user-facing effort picker."
        ),
        examples=["exhaustive"],
    )
    ui_context: Optional[UIContext] = Field(
        default=None,
        description="Optional metadata from the frontend describing the user's current view.",
    )
    brief: bool = Field(
        default=False,
        description="When true, narration stays to a few short bullets instead of a full write-up.",
    )


class TokenUsageSchema(BaseModel):
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    llm_calls: int = Field(default=0)
    cost_usd: float = Field(default=0.0)
    elapsed_seconds: float = Field(default=0.0)


class RoutingInfoSchema(BaseModel):
    type: int = Field(..., description="Intent classification type ID (1-7).")
    type_label: str = Field(..., description="Human readable intent type label.")
    path: str = Field(..., description="Execution path: gemini_direct, api_then_anthropic, or db_fallback.")
    reason: Optional[str] = Field(default=None)
    api_endpoint: Optional[str] = Field(default=None)
    complexity: Optional[str] = Field(default=None)
    bedrock_model: Optional[str] = Field(default=None)


class NoticeSchema(BaseModel):
    kind: str
    code: str
    title: str
    message: str
    suggestions: List[str] = Field(default_factory=list)
    retryable: bool = False
    request_id: str = ""


class DataSourceSchema(BaseModel):
    tier: str = ""
    endpoint: Optional[str] = None
    row_count: int = 0
    truncated: bool = False
    as_of: Optional[str] = None


# ── Structured response blocks (UI rebuild, phase 1) ──────────────────────
# A response is a list of typed blocks instead of one narrated string, so the
# client can give a KPI figure and a data table their own chrome instead of
# both flattening into the same markdown container. Additive alongside
# `answer`/`table_markdown` — see docs/UI rebuild plan for the migration.
#
# Deliberately permissive (`Dict[str, Any]` with only `type` required) rather
# than a strict discriminated union: new block types (chart, image) land in
# later phases without a schema migration, and an old client that doesn't
# recognise a `type` still gets a well-formed dict it can ignore.
class ResponseBlock(BaseModel):
    type: str = Field(..., description="markdown | table | kpi_grid | code | chart | canvas | artifact | image")

    model_config = {"extra": "allow"}


class QueryResponse(BaseModel):
    """Complete response payload for a Gemini Brain query."""

    answer: str = Field(..., description="The generated natural language answer.")
    blocks: List[ResponseBlock] = Field(
        default_factory=list,
        description=(
            "Structured content blocks for the answer (markdown, table, kpi_grid, "
            "code, chart, canvas, artifact). Prefer this over answer/table_markdown "
            "when present; not every formatter is ported yet, so it always contains "
            "at least one markdown block as a fallback."
        ),
    )
    sql: Optional[str] = Field(default=None, description="SQL query executed (if DB fallback path).")
    results: List[Any] = Field(default_factory=list, description="Raw structured results list.")
    error: Optional[str] = Field(default=None, description="Error message if processing failed.")
    status: str = Field(default="ok", description="ok | empty | partial | degraded | failed")
    notice: Optional[NoticeSchema] = Field(default=None, description="Structured user-safe notice.")
    data_source: Optional[DataSourceSchema] = Field(default=None, description="Data provenance tier and endpoint.")
    table_markdown: Optional[str] = Field(default=None, description="Pre-rendered deterministic table.")
    request_id: str = Field(default="", description="Correlation request ID.")
    pii_redacted: bool = Field(default=False, description="Whether PII entities were detected and redacted from the query.")
    pii_redactions: Dict[str, int] = Field(default_factory=dict, description="Counts of redacted PII entity types.")
    token_usage: TokenUsageSchema = Field(..., description="Token and cost metrics.")
    agent_trace: List[Dict[str, Any]] = Field(default_factory=list, description="Step-by-step execution trace.")
    routing_info: Optional[RoutingInfoSchema] = Field(default=None, description="Routing classification metadata.")
    query_trace: Optional[Dict[str, Any]] = Field(default=None, description="Detailed per-stage latency trace metrics.")
    policy: Optional[PolicySchema] = Field(default=None, description="Model and effort tier used for this request.")
    verification: Optional[VerificationSchema] = Field(default=None, description="Numeric grounding check result.")


class MultiModelQueryResponse(BaseModel):
    """Response payload for POST /api/v1/query/all — one QueryResponse per
    available model, run at the same query and effort='exhaustive'. Dev-only
    comparison option; each entry self-describes which model answered via its
    own `policy.model_label`."""

    responses: List[QueryResponse] = Field(
        ..., description="One QueryResponse per model that was queried."
    )


class PolicySchema(BaseModel):
    """Which model answered, at what effort, and what verification ran."""

    model: str = Field(default="")
    model_label: str = Field(default="")
    effort_requested: str = Field(default="auto")
    effort_delivered: str = Field(default="standard")
    auto: bool = Field(default=True)
    auto_reason: str = Field(default="")
    degraded_reason: str = Field(default="")
    verification: Dict[str, bool] = Field(default_factory=dict)
    budgets: Dict[str, int] = Field(default_factory=dict)


class VerificationSchema(BaseModel):
    """Result of checking every figure in the answer against retrieved data."""

    checked: int = Field(default=0)
    matched: int = Field(default=0)
    unmatched: List[str] = Field(default_factory=list)
    grounding_rate: float = Field(default=1.0)
    grounded: bool = Field(default=True)
    enforced: bool = Field(default=False)


class ModelInfo(BaseModel):
    key: str
    label: str
    description: str
    provider: str = "bedrock"
    context_window: int = 0
    max_output: int = 0
    supports: List[str] = Field(default_factory=list)
    efforts: List[str] = Field(default_factory=list)
    cost_per_mtok: Dict[str, float] = Field(default_factory=dict)
    latency_class: str = "fast"
    deprecated: bool = False
    #: Shown in the picker's top-level list rather than under "More models".
    primary: bool = False
    #: Credential or capability the deployment must have for this model.
    requires: str = ""
    available: bool = True


class EffortInfo(BaseModel):
    name: str
    label: str
    description: str
    target_latency: str = ""
    adds: List[str] = Field(default_factory=list)
    max_retrievals: int = 0


class ModelCatalogResponse(BaseModel):
    """Everything the client needs to render the model and effort pickers."""

    models: List[ModelInfo] = Field(default_factory=list)
    efforts: List[EffortInfo] = Field(default_factory=list)
    default_model: str = "auto"
    default_effort: str = "auto"


class HealthResponse(BaseModel):
    status: str = Field(default="ok")
    version: str = Field(default="0.1.0")
    service: str = Field(default="gemini-brain-api")


class ModelDiagnosticRequest(BaseModel):
    test_prompt: str = Field(
        default="Respond with 'OK'",
        description="Prompt to send to AI models for diagnostic test.",
        examples=["Respond with 'OK'"],
    )


class ModelStatusSchema(BaseModel):
    name: str = Field(...)
    model_id: str = Field(...)
    provider: str = Field(...)
    status: str = Field(..., description="Status: ok or error")
    latency_ms: int = Field(...)
    sample_response: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)


class ServiceStatusSchema(BaseModel):
    service: str = Field(...)
    target: str = Field(...)
    status: str = Field(...)
    latency_ms: int = Field(...)
    http_code: Optional[int] = Field(default=None)
    error: Optional[str] = Field(default=None)


class ModelHealthResponse(BaseModel):
    overall_status: str = Field(..., description="Overall status: ok or degraded")
    summary: Dict[str, int] = Field(...)
    models: List[ModelStatusSchema] = Field(...)
    services: List[ServiceStatusSchema] = Field(...)


class LoginRequest(BaseModel):
    """Payload for login when calling JSON auth endpoint."""

    username: str = Field(..., description="Email address / username", examples=["user_single@example.com"])
    password: str = Field(..., description="Password", examples=["TestPass123!"])


class TokenResponse(BaseModel):
    """OAuth2 JWT access token response for Swagger and API clients."""

    access_token: str = Field(..., description="JWT bearer token.")
    token_type: str = Field(default="bearer", description="Token type.")
    expires_in: int = Field(default=3600, description="Token validity duration in seconds.")
    user_id: int = Field(..., description="Authenticated user ID.")
    email: str = Field(..., description="Authenticated user email.")
    allowed_org_ids: List[int] = Field(default_factory=list, description="Allowed tenant organization IDs.")
    tenants: List[Dict[str, Any]] = Field(default_factory=list, description="Metadata list of accessible tenant organizations.")


class TenantInfo(BaseModel):
    """Metadata describing a single selectable tenant organization in the UI."""

    id: int = Field(..., description="Organization ID.")
    name: str = Field(..., description="Full canonical organization name.")
    display_name: str = Field(..., description="User-friendly display name.")
    tag: str = Field(default="", description="Specialty / capability tag.")
    badge_color: str = Field(default="emerald", description="UI badge accent color.")
    industry: str = Field(default="", description="Company industry.")
    currency: str = Field(default="AED", description="Operating currency.")
    description: str = Field(default="", description="Summary of key data & strengths in database.")


class TenantListResponse(BaseModel):
    """Response returned when fetching accessible tenant organizations."""

    status: str = Field(default="success", description="Status code.")
    user_id: int = Field(..., description="Authenticated user ID.")
    email: str = Field(..., description="Authenticated user email.")
    tenants: List[TenantInfo] = Field(default_factory=list, description="List of accessible tenants.")


class CreateSessionRequest(BaseModel):
    organization_id: Optional[int] = Field(default=None, description="Tenant this thread belongs to.")
    session_id: Optional[str] = Field(default=None, description="Optional client-generated UUID.")


class ChatSessionSchema(BaseModel):
    id: str
    user_id: int
    organization_id: Optional[int] = None
    name: str = "New Chat"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0


class ChatSessionListResponse(BaseModel):
    sessions: List[ChatSessionSchema] = Field(default_factory=list)


class ChatMessageSchema(BaseModel):
    role: str
    content: str
    created_at: Optional[str] = None
    blocks: Optional[List[Dict[str, Any]]] = None


class ChatMessageListResponse(BaseModel):
    session_id: str
    messages: List[ChatMessageSchema] = Field(default_factory=list)




