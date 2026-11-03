"""The Accutax MCP server: its tools, and how an answer is worded for the host model.

Tools return markdown text, the one form every MCP host shows well. Traces, SQL, token counts and cost never
leave: the host model sees the answer, its tables and its FTA sources, nothing else.
"""
from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from gemini_brain.config.settings import settings
from gemini_brain.mcp_server.upstream import Upstream, UpstreamError
from gemini_brain.mcp_server.verifier import SCOPE, AccutaxTokenVerifier

logger = logging.getLogger("gemini_brain.mcp_server")

INSTRUCTIONS = (
    "Accutax is UAE accounting and VAT software. Use these tools for questions about the user's own books "
    "(revenue, expenses, invoices, bills, VAT returns, cash, receivables, payables) and about UAE VAT law. "
    "Call list_organizations first when the user has not said which company they mean, then pass its id. "
    "Quote figures and FTA citations exactly as returned; do not recompute or round them. "
    "These tools only read data: they never file, post, pay or change anything."
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

AgentKey = Literal["accutax", "vat", "ap", "cash"]


# ── Wording an answer for the host model ────────────────────────────────────

def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_table(block: dict[str, Any], max_rows: int) -> str:
    """A table block as a markdown table, at most max_rows rows, saying so when rows were left out."""
    columns = block.get("columns") or []
    keys = [c.get("key") if isinstance(c, dict) else c for c in columns]
    labels = [(c.get("label") or c.get("key")) if isinstance(c, dict) else c for c in columns]
    rows = [r for r in (block.get("rows") or []) if isinstance(r, dict)]
    if not keys and rows:
        keys = labels = [k for k in rows[0] if not str(k).startswith("_")]
    if not keys:
        return ""
    lines = []
    if block.get("title"):
        lines.append(f"**{_cell(block['title'])}**\n")
    lines.append("| " + " | ".join(_cell(label) for label in labels) + " |")
    lines.append("|" + " --- |" * len(keys))
    for row in rows[:max_rows]:
        lines.append("| " + " | ".join(_cell(row.get(k)) for k in keys) + " |")
    total = block.get("total_rows") or len(rows)
    shown = min(len(rows), max_rows)
    if total > shown:
        lines.append(f"\n_{shown} of {total} rows shown. Ask a narrower question for the rest._")
    return "\n".join(lines)


def render_answer(envelope: dict[str, Any], max_rows: int | None = None) -> str:
    """The agent's answer, its tables and its FTA sources, as one markdown text."""
    max_rows = max_rows or settings.mcp_max_table_rows
    parts = [str(envelope.get("answer") or "").strip()]
    for block in envelope.get("blocks") or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "table":
            table = render_table(block, max_rows)
            if table:
                parts.append(table)
        elif kind == "fta_sources" and block.get("text"):
            parts.append(str(block["text"]))
    notice = envelope.get("notice") or {}
    if envelope.get("status") in ("degraded", "failed") and notice.get("message"):
        parts.append(f"_Note: {notice['message']}_")
    agent = (envelope.get("agent") or {}).get("name")
    footer = "Answered by Accutax AI" + (f" ({agent})" if agent else "")
    if envelope.get("request_id"):
        footer += f" · request {envelope['request_id']}"
    parts.append(f"_{footer}_")
    return "\n\n".join(p for p in parts if p)


def render_organizations(tenants: list[dict[str, Any]]) -> str:
    if not tenants:
        return "This Accutax account has no organizations it can read."
    lines = ["| id | organization | currency |", "| --- | --- | --- |"]
    for t in tenants:
        name = t.get("display_name") or t.get("name") or ""
        lines.append(f"| {t.get('id')} | {_cell(name)} | {_cell(t.get('currency') or '')} |")
    return "\n".join(lines)


# ── The server ──────────────────────────────────────────────────────────────

def _bearer() -> str:
    access = get_access_token()
    if access is None or not access.token:
        raise ToolError("Not signed in to Accutax. Reconnect the Accutax connector.")
    return access.token


def _issuer_url() -> str:
    if settings.mcp_issuer_url:
        return settings.mcp_issuer_url
    parts = urlsplit(settings.mcp_public_url)
    return f"{parts.scheme}://{parts.netloc}"


def transport_security() -> TransportSecuritySettings:
    """Host-header guard against DNS rebinding: localhost always, plus MCP_ALLOWED_HOSTS (the public domain)."""
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    hosts += [h.strip() for h in settings.mcp_allowed_hosts.split(",") if h.strip()]
    return TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=hosts)


def build_server(upstream: Upstream | None = None) -> MCPServer:
    api = upstream or Upstream()
    server = MCPServer(
        name="accutax",
        title="Accutax",
        instructions=INSTRUCTIONS,
        website_url="https://accutax.ai",
        token_verifier=AccutaxTokenVerifier(),
        auth=AuthSettings(
            issuer_url=_issuer_url(),
            resource_server_url=settings.mcp_public_url,
            required_scopes=[SCOPE],
            # The verifier checks the token itself; Accutax tokens carry no RFC 8707 resource yet.
            validate_token_resource=False,
        ),
    )

    @server.tool(title="List organizations", annotations=READ_ONLY)
    async def list_organizations() -> str:
        """List the Accutax organizations (companies) this user can read, with their ids and currencies."""
        try:
            return render_organizations(await api.organizations(_bearer()))
        except UpstreamError as e:
            raise ToolError(str(e)) from None

    @server.tool(title="Ask Accutax", annotations=READ_ONLY)
    async def ask_accutax(
        question: Annotated[str, Field(min_length=3, max_length=4000,
                                       description="The question, in the user's words, with any period or "
                                                   "company they named.")],
        organization_id: Annotated[Optional[int], Field(
            description="Organization id from list_organizations. Omit to use the user's default organization.")] = None,
        agent: Annotated[Optional[AgentKey], Field(
            description="Specialist to answer: vat (VAT returns, reviews, VAT law), ap (bills and payables), "
                        "cash (cash and bank), accutax (general). Omit to let Accutax choose.")] = None,
    ) -> str:
        """Answer a question about the user's books or UAE VAT law with the Accutax AI agent.

        Figures come from the organization's live Accutax data; VAT law answers cite FTA documents. Use it
        for revenue, expenses, profit, invoices, bills, customers, suppliers, cash, VAT returns, VAT review
        findings and VAT rules. One question per call. It can take up to a few minutes.
        """
        try:
            envelope = await api.ask(_bearer(), question, organization_id=organization_id, agent=agent)
        except UpstreamError as e:
            raise ToolError(str(e)) from None
        return render_answer(envelope)

    return server
