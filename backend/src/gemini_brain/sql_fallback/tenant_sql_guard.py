"""
tenant_sql_guard.py — Shared regex-based tenant-isolation hardening for
LLM-generated SQL.

Consolidates what used to be two independently-maintained, divergent copies
of the same logic: `sql_fallback/sql_engine.py::enforce_tenant_isolation_sql`
(14 hardcoded tables) and `agents/finance_agent.py::_enforce_tenant_isolation_sql`
(26 different hardcoded tables). Neither matched the real table set, and they
disagreed with each other. This is a defense-in-depth safety net on top of
the structured query builders (which already inject organization_id safely)
and on top of the verified org_id being hard-overwritten into every tool call
before dispatch (see sql_fallback/sql_engine.py's tool loop) — not the primary
isolation mechanism. The primary, hard-to-bypass mechanism is Postgres Row
Level Security (see sql/functions/005_rls_reader_role.sql); this module keeps
mattering even after RLS is enabled, since Gemini Brain's own DB role may
still be the table owner in some environments and bypass RLS.
"""
from __future__ import annotations

import logging
import re
import threading
from typing import Optional

logger = logging.getLogger("gemini_brain.sql_fallback.tenant_sql_guard")

# ──────────────────────────────────────────────
# Tenant table discovery
# ──────────────────────────────────────────────
# Every table with its own organization_id column, discovered dynamically
# from the connected database so this list can't silently drift out of sync
# with the schema the way two separate hardcoded lists did. Mirrors the
# discovery query in sql/functions/005_rls_reader_role.sql Part B1.
_DISCOVERY_SQL = """
    SELECT DISTINCT c.relname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND a.attname = 'organization_id'
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
"""

#: Used only if live discovery fails (e.g. DB unreachable at first call) —
#: the union of the two previous hardcoded lists, plus "organizations"
#: itself (keyed on `id`, handled separately below). Kept as a safety net,
#: not the source of truth.
_FALLBACK_TABLES = frozenset({
    "audit_logs", "audit_trails", "bank_accounts", "bank_transaction_rules",
    "bank_transactions", "branches", "chart_of_accounts", "contacts",
    "cost_centers", "customer_overdue_summary", "customer_payment",
    "delivery_notes", "expense", "income", "inventory_adjustments",
    "inventory_fifo_layers", "inventory_ledger", "inventory_movements",
    "inventory_quantities", "inventory_transfers", "items", "journal_entries",
    "overdue_invoices", "projects", "reconciliations", "sub_contacts",
    "supplier_payments", "tax_adjustments", "warehouses",
})

_lock = threading.Lock()
_tenant_tables_cache: Optional[frozenset] = None


def _discover_tenant_tables() -> frozenset:
    from gemini_brain.sql_fallback.db_connection import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(_DISCOVERY_SQL)
            tables = frozenset(row[0] for row in cur.fetchall())
        if not tables:
            raise ValueError("Discovery query returned zero tenant tables")
        return tables
    finally:
        conn.close()


def get_tenant_tables() -> frozenset:
    """Every table with its own organization_id column, cached after first call."""
    global _tenant_tables_cache
    if _tenant_tables_cache is not None:
        return _tenant_tables_cache
    with _lock:
        if _tenant_tables_cache is None:
            try:
                _tenant_tables_cache = _discover_tenant_tables()
            except Exception as e:
                logger.warning(
                    "Tenant-table discovery failed (%s); using hardcoded fallback list.", e
                )
                _tenant_tables_cache = _FALLBACK_TABLES
    return _tenant_tables_cache


def reset_cache() -> None:
    """Clear the cached tenant-table set. For tests, or after a schema change."""
    global _tenant_tables_cache
    with _lock:
        _tenant_tables_cache = None


# ──────────────────────────────────────────────
# The rewriter
# ──────────────────────────────────────────────

def enforce_tenant_isolation_sql(sql: str, org_id: Optional[int]) -> str:
    """Best-effort regex hardening of LLM-generated SQL against the verified org_id.

    Not the primary isolation mechanism (see module docstring) — a defense-in-
    depth net that (1) forces any organization_id/org_id literal already in
    the SQL to match the verified value, regardless of what the model wrote,
    and (2) injects a WHERE filter if a known tenant table is queried with no
    org filter at all.
    """
    if not sql or not isinstance(sql, str) or org_id is None:
        return sql
    try:
        org_id = int(org_id)
    except (TypeError, ValueError):
        return sql

    original = sql
    cleaned = sql.strip()

    # 1. Force-correct any organization_id/org_id literal comparison.
    cleaned = re.sub(
        r'(\b(?:\w+\.)?(?:"?organization_id"?|"?org_id"?)\s*=\s*)\d+',
        rf'\g<1>{org_id}',
        cleaned,
        flags=re.IGNORECASE,
    )

    # 2. Force-correct any organization_id/org_id IN (...) clause.
    cleaned = re.sub(
        r'(\b(?:\w+\.)?(?:"?organization_id"?|"?org_id"?)\s+IN\s*\()[^)]+(\))',
        rf'\g<1>{org_id}\g<2>',
        cleaned,
        flags=re.IGNORECASE,
    )

    # 3. The `organizations` table keys on `id`, not `organization_id` —
    #    force-correct `organizations.id`/`org.id`/`o.id` comparisons too.
    cleaned = re.sub(
        r'(\b(?:organizations|org|o)\.id\s*=\s*)\d+',
        rf'\g<1>{org_id}',
        cleaned,
        flags=re.IGNORECASE,
    )
    if re.search(r'\bFROM\s+organizations\b', cleaned, re.IGNORECASE) and not re.search(
        r'\b(?:organizations|org|o)\.id\b', cleaned, re.IGNORECASE
    ):
        cleaned = re.sub(
            r'(\bWHERE\s+id\s*=\s*)\d+',
            rf'\g<1>{org_id}',
            cleaned,
            flags=re.IGNORECASE,
        )

    # 4. Safety net: a known tenant table referenced with no org filter at
    #    all gets one injected.
    has_org_filter = re.search(r'\b(?:organization_id|org_id)\b', cleaned, re.IGNORECASE)
    is_orgs_table_only = re.search(
        r'\bFROM\s+organizations\b', cleaned, re.IGNORECASE
    ) and not re.search(r'\bJOIN\b', cleaned, re.IGNORECASE)

    if not has_org_filter and not is_orgs_table_only:
        for tbl in get_tenant_tables():
            if not re.search(rf'\b(?:FROM|JOIN)\s+{re.escape(tbl)}\b', cleaned, re.IGNORECASE):
                continue
            if re.search(r'\bWHERE\b', cleaned, re.IGNORECASE):
                cleaned = re.sub(
                    r'(\bWHERE\b\s+)',
                    rf'\1organization_id = {org_id} AND ',
                    cleaned,
                    count=1,
                    flags=re.IGNORECASE,
                )
            else:
                match = re.search(
                    r'(\b(?:GROUP\s+BY|ORDER\s+BY|HAVING|LIMIT|OFFSET)\b)', cleaned, re.IGNORECASE
                )
                if match:
                    pos = match.start()
                    cleaned = cleaned[:pos] + f"WHERE organization_id = {org_id} " + cleaned[pos:]
                else:
                    cleaned = f"{cleaned} WHERE organization_id = {org_id}"
            logger.warning("tenant_sql_guard: injected missing organization_id filter (org=%s, table=%s)", org_id, tbl)
            break

    if cleaned != original:
        logger.info("tenant_sql_guard: rewrite applied (org=%s)", org_id)
    return cleaned
