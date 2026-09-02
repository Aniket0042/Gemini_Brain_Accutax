"""messages.py — Curated, user-safe copy. NOTHING else may write user-facing error text."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .errors import ErrorCode, RETRYABLE

NOTICES: Dict[str, Dict[str, Any]] = {
    "NO_ROWS": {
        "kind": "empty",
        "title": "Nothing recorded for this request",
        "message": (
            "I checked {subject} and there are no matching records yet. "
            "That is a confirmed result from your books."
        ),
        "suggestions": [
            "Widen the date range and ask again",
            "Confirm the entries were posted to this organization",
        ],
    },
    "WIDENED_WINDOW": {
        "kind": "partial",
        "title": "Showing a longer period",
        "message": (
            "Nothing was recorded for the period you asked about, so this covers "
            "{subject} over a longer window instead."
        ),
        "suggestions": [
            "Ask for a specific period if you need it scoped differently",
        ],
    },
    "PARTIAL_DATA": {
        "kind": "partial",
        "title": "Showing a partial view",
        "message": (
            "This covers the first {shown} of {total} records. "
            "Totals below reflect only the rows shown."
        ),
        "suggestions": [
            "Narrow the date range for a complete view",
        ],
    },
    "UPSTREAM_TIMEOUT": {
        "kind": "degraded",
        "title": "The finance service took too long",
        "message": (
            "I could not retrieve live figures for {subject} in time, so I have not "
            "shown any numbers rather than showing you something unverified."
        ),
        "suggestions": [
            "Try again in a moment",
            "Narrow the date range to reduce the load",
        ],
    },
    "UPSTREAM_UNAVAILABLE": {
        "kind": "degraded",
        "title": "The finance service is unreachable",
        "message": (
            "Your accounting service did not respond. I have not produced any figures, "
            "because anything I showed would be a guess."
        ),
        "suggestions": [
            "Try again shortly",
            "Check the service status page",
        ],
    },
    "MODEL_UNAVAILABLE": {
        "kind": "degraded",
        "title": "The analysis engine is unavailable",
        "message": (
            "I retrieved your data but could not generate the written summary. "
            "The figures below are complete and correct."
        ),
        "suggestions": [
            "Read the table below",
            "Retry for the written summary",
        ],
    },
    "MODEL_RATE_LIMITED": {
        "kind": "degraded",
        "title": "High demand right now",
        "message": (
            "The analysis engine is at capacity. Your data was retrieved successfully — "
            "only the written summary is missing."
        ),
        "suggestions": [
            "Retry in about a minute",
        ],
    },
    "DB_UNAVAILABLE": {
        "kind": "degraded",
        "title": "The database is unreachable",
        "message": (
            "I could not reach the reporting database, so no figures were produced for this question."
        ),
        "suggestions": [
            "Try again shortly",
        ],
    },
    "QUERY_FAILED": {
        "kind": "degraded",
        "title": "I could not build a reliable query",
        "message": (
            "I understood the question but could not turn it into a query I trust. "
            "Rather than show a number that might be wrong, I have shown nothing."
        ),
        "suggestions": [
            "Rephrase more specifically",
            "Name the report you want",
        ],
    },
    "SQL_FALLBACK_FAILED": {
        "kind": "degraded",
        "title": "Database fallback query failed",
        "message": (
            "The direct query against your reporting database encountered an issue. "
            "No figures were produced for {subject}."
        ),
        "suggestions": [
            "Try rephrasing your question",
            "Try again in a few moments",
        ],
    },
    "TENANT_REQUIRED": {
        "kind": "denied",
        "title": "Organization required",
        "message": (
            "Please choose an organization before asking questions about your books."
        ),
        "suggestions": [
            "Choose an organization from the workspace switcher",
        ],
    },
    "TENANT_FORBIDDEN": {
        "kind": "denied",
        "title": "That organization is outside your access",
        "message": (
            "Your account does not have access to the organization in this request. "
            "Nothing was retrieved."
        ),
        "suggestions": [
            "Switch to an organization you have access to",
            "Contact your administrator",
        ],
    },
    "TENANT_AMBIGUOUS": {
        "kind": "denied",
        "title": "Which organization?",
        "message": (
            "You have access to more than one organization and this question did not name one."
        ),
        "suggestions": [
            "Pick an organization from the switcher",
            "Name the organization in your question",
        ],
    },
    "AUTH_REQUIRED": {
        "kind": "denied",
        "title": "Please sign in",
        "message": (
            "Your session is not active. Sign in to continue."
        ),
        "suggestions": [
            "Sign in",
        ],
    },
    "AUTH_EXPIRED": {
        "kind": "denied",
        "title": "Your session has expired",
        "message": (
            "For security, sessions end after a period of inactivity. "
            "Sign in again to continue — your conversation is preserved."
        ),
        "suggestions": [
            "Sign in again",
        ],
    },
    "NOT_FOUND": {
        "kind": "failed",
        "title": "That page doesn't exist",
        "message": (
            "The address you requested is not part of this service."
        ),
        "suggestions": [
            "Return to the main screen",
        ],
    },
    "VALIDATION_FAILED": {
        "kind": "failed",
        "title": "I couldn't read that request",
        "message": (
            "Part of the request was not in the expected format."
        ),
        "suggestions": [
            "Try rephrasing the question",
        ],
    },
    "INTERNAL_ERROR": {
        "kind": "failed",
        "title": "Something went wrong on our side",
        "message": (
            "An unexpected problem stopped this request. Nothing was changed in your books. "
            "Reference {request_id}."
        ),
        "suggestions": [
            "Try again",
            "Share the reference with support",
        ],
    },
}

_FALLBACK = {
    "kind": "failed",
    "title": "I could not complete that request",
    "message": "Something went wrong on our side. No figures were produced for this question.",
    "suggestions": ["Try again", "Rephrase the question"],
}


def notice_for(
    code: "ErrorCode | str",
    *,
    subject: str = "your records",
    request_id: str = "",
    suggestions: Optional[List[str]] = None,
    shown: Any = "",
    total: Any = "",
) -> Dict[str, Any]:
    """Build a user-safe notice object. Never raises, never leaks exception text."""
    key = code.value if hasattr(code, "value") else str(code)
    tpl = NOTICES.get(key, _FALLBACK)
    try:
        message = tpl["message"].format(
            subject=subject,
            request_id=request_id or "N/A",
            shown=shown,
            total=total,
        )
    except Exception:
        message = tpl["message"]
    return {
        "kind": tpl["kind"],
        "code": key,
        "title": tpl["title"],
        "message": message,
        "suggestions": suggestions if suggestions is not None else list(tpl.get("suggestions", [])),
        "retryable": key in {c.value for c in RETRYABLE},
        "request_id": request_id,
    }
