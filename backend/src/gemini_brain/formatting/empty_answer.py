"""
empty_answer.py — Deterministic natural language generators for 0-row confirmed answers.

Eliminates LLM latency and hallucination risks when queries return confirmed zero rows.
"""
from __future__ import annotations

import re
from typing import Any, Optional


def subject_for(endpoint: Optional[str], tool_spec: Any = None, query: str = "") -> str:
    """Infer a clean, natural English subject noun phrase."""
    if tool_spec is not None and getattr(tool_spec, "name", ""):
        name = str(tool_spec.name).replace("_", " ").lower()
        name = re.sub(r"^(get|fetch|list|show|view|find)\s+", "", name)
        return name

    if endpoint:
        ep = endpoint.strip("/").replace("fn_", "").replace("/", " ").replace("-", " ").replace("_", " ").lower()
        ep = re.sub(r"^(get|fetch|list|show|view|find)\s+", "", ep)
        return ep

    q = (query or "").lower()
    if "invoice" in q:
        return "invoices"
    if "bill" in q:
        return "bills"
    if "bank" in q or "account" in q:
        return "bank accounts"
    if "contact" in q or "customer" in q or "vendor" in q:
        return "contacts"
    if "tax" in q or "vat" in q:
        return "tax records"
    if "expense" in q:
        return "expenses"
    if "revenue" in q or "income" in q:
        return "revenue records"
    if "item" in q or "product" in q or "inventory" in q:
        return "inventory items"

    return "your records"


def build_empty_answer(query: str, subject: str, retrieved: Any = None) -> str:
    """Build clear, definitive, non-blaming markdown answer for confirmed zero-row datasets."""
    subj = subject or "your records"
    return (
        f"I checked your **{subj}** and found no matching records.\n\n"
        f"This is a confirmed result from your books.\n\n"
        "**Suggestions:**\n"
        "- Try expanding your date range or removing specific filters.\n"
        "- Check if transactions might be categorized under a different status (e.g., Draft vs Approved).\n"
        "- Verify spelling if searching by customer, vendor, or reference name."
    )
