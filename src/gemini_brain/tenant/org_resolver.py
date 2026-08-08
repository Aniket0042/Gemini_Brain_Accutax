"""
org_resolver.py — Dynamic organization/tenant resolution from user queries.

Extracted from ``gemini_brain_adapter.py`` lines 443-487 (``_resolve_organization``).

Uses Gemini to detect if the query mentions an org name or ID, then looks it up
in the ``organizations`` table.  Both the Gemini extraction prompt and the DB
lookup logic (numeric vs. fuzzy ILIKE match) are preserved verbatim.
"""
from __future__ import annotations

import logging
from typing import Optional, Callable, Tuple

logger = logging.getLogger("gemini_brain.tenant.org_resolver")

# ── Extraction system prompt (verbatim from original) ────────────────────────
_ORG_EXTRACTION_SYSTEM: str = (
    "Analyze the user query. Identify if the user is asking about a specific organization, "
    "company, or tenant, either by name or by database ID (e.g. 'Zero-Config', 'ID 69', 'organization 27').\n"
    'If yes, return a JSON object: {"mentioned": true, "value": "extracted name or ID"}.\n'
    'If no, return: {"mentioned": false}.\n'
    "Return ONLY raw JSON, no markdown, no explanation."
)


def resolve_organization(
    query: str,
    call_gemini: Callable[[str, str, int], Tuple[str, int, int]],
    parse_json: Callable[[str], Optional[dict]],
    get_connection: Callable,
    db_name: str = "",
) -> Optional[int]:
    """Attempt to extract and resolve an organization ID from the query.

    Parameters
    ----------
    query : str
        The user's natural-language question.
    call_gemini : callable
        ``(system, user_text, max_tokens) -> (text, input_tokens, output_tokens)``
    parse_json : callable
        ``(text) -> dict | None``
    get_connection : callable
        ``(db_name) -> psycopg2.connection`` — the executor ``get_connection`` function.
    db_name : str
        Database name override (passed through to ``get_connection``).

    Returns
    -------
    int or None
        The resolved organization ID, or ``None`` if not mentioned / not found.
    """
    try:
        resp_text, _, _ = call_gemini(_ORG_EXTRACTION_SYSTEM, query, 100)
        res = parse_json(resp_text)
        if not res or not res.get("mentioned"):
            return None

        val = str(res.get("value", "")).strip()
        if not val:
            return None

        conn = get_connection(db_name=db_name)
        cur = conn.cursor()
        try:
            # Check if numeric
            if val.isdigit():
                cur.execute("SELECT id FROM organizations WHERE id = %s;", (int(val),))
                row = cur.fetchone()
                if row:
                    return row[0]

            # Check fuzzy match
            cur.execute(
                "SELECT id FROM organizations WHERE name ILIKE %s LIMIT 1;",
                (f"%{val}%",),
            )
            row = cur.fetchone()
            if row:
                return row[0]
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.warning("Failed to resolve dynamic organization: %s", e)

    return None
