"""
models.py — Pydantic request and response schemas for the Gemini Brain REST API.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


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
    user_id: int = Field(
        default=18,
        description="User ID making the request.",
        examples=[18],
    )
    db_name: str = Field(
        default="accutax_bk",
        description="Database name override.",
        examples=["accutax_bk"],
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
    narrate: bool = Field(
        default=True,
        description="Whether to generate natural language narration with Claude or return raw JSON immediately.",
        examples=[True],
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


class QueryResponse(BaseModel):
    """Complete response payload for a Gemini Brain query."""

    answer: str = Field(..., description="The generated natural language answer.")
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



