"""
Schema Agent — DB introspection and entity resolution.

Responsibilities:
- Introspect active database (accutax_bk or accutax_llm)
- Provide table/column mapping for any entity
- Resolve schema drift between databases
- Normalize entity names to actual table/column names
- Confirm whether a field exists before other agents use it

This agent NEVER executes analytics SQL.
It only runs information_schema queries and returns metadata.
"""

import json
import logging
from typing import Dict, Any, Optional, List

from gemini_brain.agents.executor import execute_sql, get_connection

logger = logging.getLogger("agents.schema")

# ──────────────────────────────────────────────
# Cached schema per database
# ──────────────────────────────────────────────
_schema_cache: Dict[str, Dict] = {}


def _introspect_db() -> Dict[str, Any]:
    """Introspect the current active database and return full schema metadata."""
    # Get all tables
    cols, rows = execute_sql("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    tables = {}
    for row in rows:
        table_name = row[0]
        # Get columns for each table
        c_cols, c_rows = execute_sql(f"""
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = '{table_name}'
            ORDER BY ordinal_position
        """)
        columns = []
        for cr in c_rows:
            columns.append({
                "name": cr[0],
                "type": cr[1],
                "nullable": cr[2] == "YES",
                "default": cr[3],
            })
        tables[table_name] = {
            "columns": columns,
            "column_names": [c["name"] for c in columns],
            "has_is_deleted": any(c["name"] == "is_deleted" for c in columns),
            "has_organization_id": any("organization" in c["name"] for c in columns),
        }
    return tables


def get_schema() -> Dict[str, Any]:
    """Return schema for the active database (cached after first call per DB)."""
    # Determine active DB by checking connection
    conn = get_connection()
    db_name = conn.info.dbname
    conn.close()

    if db_name not in _schema_cache:
        logger.info(f"Introspecting schema for database: {db_name}")
        _schema_cache[db_name] = _introspect_db()
    return _schema_cache[db_name]


def clear_cache():
    """Clear all cached schemas."""
    _schema_cache.clear()


# ──────────────────────────────────────────────
# Public API — called by Coordinator as tool
# ──────────────────────────────────────────────

def handle(task: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Schema Agent entry point.  Called by Coordinator.

    Supported tasks:
      - get_tables              → list all table names
      - get_table_schema        → columns for one table {table: "income"}
      - resolve_entity          → map entity name → table + key columns
      - check_field_exists      → {table, column} → bool
      - get_join_path           → join between two tables
      - get_sample_values       → top N distinct values for a column
    """
    params = params or {}

    if task == "get_tables":
        return _task_get_tables()
    elif task == "get_table_schema":
        return _task_get_table_schema(params.get("table", ""))
    elif task == "resolve_entity":
        return _task_resolve_entity(params.get("entity", ""))
    elif task == "check_field_exists":
        return _task_check_field(params.get("table", ""), params.get("column", ""))
    elif task == "get_join_path":
        return _task_get_join_path(params.get("from_table", ""), params.get("to_table", ""))
    elif task == "get_sample_values":
        return _task_get_sample_values(params.get("table", ""), params.get("column", ""), params.get("limit", 20))
    else:
        return {"error": f"Unknown schema_agent task: {task}"}


# ──────────────────────────────────────────────
# Task implementations
# ──────────────────────────────────────────────

def _task_get_tables() -> Dict:
    schema = get_schema()
    return {
        "tables": list(schema.keys()),
        "count": len(schema),
    }


def _task_get_table_schema(table: str) -> Dict:
    schema = get_schema()
    table = table.lower().strip()
    if table not in schema:
        # Try fuzzy match
        candidates = [t for t in schema if table in t or t in table]
        if candidates:
            return {"error": f"Table '{table}' not found. Did you mean: {candidates}?"}
        return {"error": f"Table '{table}' does not exist in this database."}
    info = schema[table]
    return {
        "table": table,
        "columns": info["columns"],
        "has_soft_delete": info["has_is_deleted"],
        "has_organization_filter": info["has_organization_id"],
    }


def _task_resolve_entity(entity: str) -> Dict:
    """Map a user-facing entity name to actual table, key columns, and relationships."""
    entity = entity.lower().strip()

    COST_EXPR = "NULLIF(REGEXP_REPLACE(i.cost, '[^0-9\\\\.]', '', 'g'), '')::NUMERIC"

    # ─── Comprehensive entity → table mapping (all 58 tables) ───
    ENTITY_MAP = {
        # ════════════ Income / Invoices ════════════
        "invoice": {"table": "income", "alias": "inc",
                     "key_columns": ["invoice_number", "invoice_date", "due_date", "status_type_id", "contact_id"],
                     "amount_path": "income → income_items → items.cost",
                     "amount_sql": f"SUM({COST_EXPR})",
                     "joins": ["income_items ON income_items.income_id = income.id", "items ON items.id = income_items.items_id"]},
        "income": {"table": "income", "alias": "inc",
                    "key_columns": ["invoice_number", "invoice_date", "due_date", "status_type_id", "contact_id"],
                    "amount_path": "income → income_items → items.cost",
                    "amount_sql": f"SUM({COST_EXPR})",
                    "joins": ["income_items ON income_items.income_id = income.id", "items ON items.id = income_items.items_id"]},
        "revenue": {"table": "income", "alias": "inc",
                     "key_columns": ["invoice_number", "invoice_date", "contact_id"],
                     "amount_path": "income → income_items → items.cost",
                     "amount_sql": f"SUM({COST_EXPR})",
                     "joins": ["income_items ON income_items.income_id = income.id", "items ON items.id = income_items.items_id"]},
        "sales": {"table": "income", "alias": "inc",
                   "key_columns": ["invoice_number", "invoice_date", "contact_id"],
                   "amount_path": "income → income_items → items.cost",
                   "amount_sql": f"SUM({COST_EXPR})",
                   "joins": ["income_items ON income_items.income_id = income.id", "items ON items.id = income_items.items_id"]},
        "receivable": {"table": "income", "alias": "inc",
                        "key_columns": ["invoice_number", "invoice_date", "due_date", "status_type_id", "contact_id"],
                        "filter": "status_type_id IN (SELECT id FROM status_type WHERE value IN ('PENDING','PARTIALLY_PAID'))"},
        "income_item": {"table": "income_items", "alias": "ii",
                         "key_columns": ["income_id", "items_id"]},
        "recurring_invoice": {"table": "income", "alias": "inc",
                               "key_columns": ["invoice_number", "invoice_date", "is_recurring", "repeat_frequency_type_id", "start_date", "end_date"],
                               "note": "Recurring invoices are rows in income with is_recurring=true"},

        # ════════════ Expenses / Bills ════════════
        "expense": {"table": "expense", "alias": "e",
                     "key_columns": ["reception_date", "expense_type", "receipt_number", "contact_id"],
                     "amount_path": "expense → expense_items → items.cost",
                     "amount_sql": f"SUM({COST_EXPR})",
                     "joins": ["expense_items ON expense_items.expense_id = expense.id", "items ON items.id = expense_items.items_id"]},
        "bill": {"table": "expense", "alias": "e",
                  "key_columns": ["reception_date", "expense_type", "receipt_number", "contact_id"],
                  "amount_path": "expense → expense_items → items.cost",
                  "amount_sql": f"SUM({COST_EXPR})",
                  "joins": ["expense_items ON expense_items.expense_id = expense.id", "items ON items.id = expense_items.items_id"]},
        "payable": {"table": "expense", "alias": "e",
                     "key_columns": ["reception_date", "expense_type", "receipt_number", "contact_id"],
                     "filter": "status_type_id IN (SELECT id FROM status_type WHERE value IN ('PENDING','PARTIALLY_PAID'))"},
        "expense_item": {"table": "expense_items", "alias": "ei",
                          "key_columns": ["expense_id", "items_id"]},
        "expense_category": {"table": "expense_category_type", "alias": "ect",
                              "key_columns": ["id", "value"]},
        "recurring_expense": {"table": "expense", "alias": "e",
                               "key_columns": ["receipt_number", "reception_date", "is_draft", "expense_type"],
                               "note": "Recurring expenses tracked in expense table"},

        # ════════════ Contacts ════════════
        "customer": {"table": "contacts", "alias": "c",
                      "key_columns": ["name", "email", "phone_number", "contact_type_id", "organization_name"],
                      "filter": "contact_type_id = 4"},
        "client": {"table": "contacts", "alias": "c",
                    "key_columns": ["name", "email", "phone_number", "contact_type_id", "organization_name"],
                    "filter": "contact_type_id = 4"},
        "vendor": {"table": "contacts", "alias": "c",
                    "key_columns": ["name", "email", "phone_number", "contact_type_id", "organization_name"],
                    "filter": "contact_type_id IN (1, 2, 3)"},
        "supplier": {"table": "contacts", "alias": "c",
                      "key_columns": ["name", "email", "phone_number", "contact_type_id", "organization_name"],
                      "filter": "contact_type_id IN (1, 2, 3)"},
        "contact": {"table": "contacts", "alias": "c",
                     "key_columns": ["name", "email", "phone_number", "contact_type_id", "organization_name"]},
        "contact_type": {"table": "contact_type", "alias": "ct",
                          "key_columns": ["id", "value"]},

        # ════════════ Items / Products / Services ════════════
        "item": {"table": "items", "alias": "itm",
                  "key_columns": ["name", "number", "description", "cost", "item_type_id"]},
        "product": {"table": "items", "alias": "itm",
                     "key_columns": ["name", "number", "description", "cost", "item_type_id"],
                     "filter": "item_type_id = (SELECT id FROM item_type WHERE value = 'Product')"},
        "service": {"table": "items", "alias": "itm",
                     "key_columns": ["name", "number", "description", "cost", "item_type_id"],
                     "filter": "item_type_id = (SELECT id FROM item_type WHERE value = 'Service')"},
        "item_type": {"table": "item_type", "alias": "it",
                       "key_columns": ["id", "value"]},

        # ════════════ Accounting Core (GL) ════════════
        "journal_entry": {"table": "journal_entries", "alias": "je",
                           "key_columns": ["journal_number", "reference_number", "transaction_date", "description", "total_debit", "total_credit", "is_posted"],
                           "note": "Source of truth for all financial transactions. 85K+ entries."},
        "journal": {"table": "journal_entries", "alias": "je",
                     "key_columns": ["journal_number", "reference_number", "transaction_date", "description", "total_debit", "total_credit", "is_posted"]},
        "journal_entry_line": {"table": "journal_entry_lines", "alias": "jel",
                                "key_columns": ["journal_entry_id", "account_id", "account_code", "account_name", "debit_amount", "credit_amount", "description"],
                                "note": "Double-entry detail. 321K rows. Every line has debit XOR credit. account_code/account_name are denormalized."},
        "journal_line": {"table": "journal_entry_lines", "alias": "jel",
                          "key_columns": ["journal_entry_id", "account_id", "account_code", "account_name", "debit_amount", "credit_amount", "description"]},
        "chart_of_account": {"table": "chart_of_accounts", "alias": "coa",
                              "key_columns": ["account_name", "account_code", "account_type", "account_sub_type", "parent_account_id", "balance"],
                              "note": "130 accounts. account_type is VARCHAR (Asset/Liability/Equity/Revenue/Expense)."},
        "account": {"table": "chart_of_accounts", "alias": "coa",
                     "key_columns": ["account_name", "account_code", "account_type", "account_sub_type", "parent_account_id", "balance"]},
        "ledger": {"table": "chart_of_accounts", "alias": "coa",
                    "key_columns": ["account_name", "account_code", "account_type", "account_sub_type", "parent_account_id", "balance"]},
        "account_type": {"table": "account_type", "alias": "at",
                          "key_columns": ["id", "value"],
                          "note": "3 types: Asset, Liability, Equity"},

        # ════════════ Banking ════════════
        "bank_account": {"table": "bank_accounts", "alias": "ba",
                          "key_columns": ["account_name", "account_number", "bank_name", "opening_bank_balance", "currency_code"]},
        "bank_transaction": {"table": "bank_transactions", "alias": "bt",
                              "key_columns": ["date", "description", "amount", "category", "account_id", "account_name", "debit_or_credit"]},
        "bank_reconciliation": {"table": "reconciliations", "alias": "rec",
                                 "key_columns": ["account_id", "start_date", "end_date", "status", "closing_balances"]},
        "reconciliation": {"table": "reconciliations", "alias": "rec",
                            "key_columns": ["account_id", "start_date", "end_date", "date", "status", "status_formatted", "closing_balances", "opening_balances"]},
        "bank_rule": {"table": "bank_transaction_rules", "alias": "btr",
                       "key_columns": ["rule_name", "apply_to", "criteria", "account_id"]},

        # ════════════ Inventory ════════════
        "inventory": {"table": "inventory_quantities", "alias": "iq",
                       "key_columns": ["item_id", "warehouse_id", "quantity_available", "quantity_reserved", "quantity_on_hold"],
                       "note": "Current stock levels per item per warehouse."},
        "stock": {"table": "inventory_quantities", "alias": "iq",
                   "key_columns": ["item_id", "warehouse_id", "quantity_available"]},
        "inventory_quantity": {"table": "inventory_quantities", "alias": "iq",
                                "key_columns": ["item_id", "warehouse_id", "quantity_available", "quantity_reserved"]},
        "inventory_movement": {"table": "inventory_movements", "alias": "im",
                                "key_columns": ["item_id", "warehouse_id", "quantity", "movement_type", "created_at", "reference_number"],
                                "note": "Stock in/out events. Date column is created_at."},
        "inventory_adjustment": {"table": "inventory_adjustments", "alias": "ia",
                                  "key_columns": ["adjustment_number", "adjustment_date", "adjustment_type", "warehouse_id", "status"]},
        "inventory_ledger": {"table": "inventory_ledger", "alias": "il",
                              "key_columns": ["item_id", "warehouse_id", "transaction_date", "quantity_in", "quantity_out", "balance_quantity", "unit_cost"]},
        "warehouse": {"table": "warehouses", "alias": "wh",
                       "key_columns": ["warehouse_name", "warehouse_code", "location", "is_active", "valuation_method"]},

        # ════════════ Audit ════════════
        "audit_trail": {"table": "audit_trails", "alias": "atr",
                         "key_columns": ["transaction_type", "transaction_id", "transaction_number", "action_type", "user_name", "old_values", "new_values", "changed_fields", "created_at"],
                         "note": "Who changed what, when. 95K rows. old_values/new_values/changed_fields are JSON."},
        "audit_log": {"table": "audit_logs", "alias": "al",
                       "key_columns": ["http_method", "url", "user_id", "user_name", "entity_type", "action_type", "ip_address", "created_at"],
                       "note": "Technical API activity log. 191K rows."},
        "audit": {"table": "audit_trails", "alias": "atr",
                   "key_columns": ["transaction_type", "transaction_id", "transaction_number", "action_type", "user_name", "old_values", "new_values", "changed_fields", "created_at"]},

        # ════════════ Organization & Structure ════════════
        "organization": {"table": "organizations", "alias": "org",
                          "key_columns": ["name", "vat_registeration_number", "trn_registeration_number", "industry_type_id", "country_id", "currency"]},
        "company": {"table": "organizations", "alias": "org",
                     "key_columns": ["name", "vat_registeration_number", "industry_type_id", "country_id", "currency"]},
        "branch": {"table": "branches", "alias": "br",
                    "key_columns": ["branch_name", "display_name", "city", "district", "is_active", "organization_id"]},
        "project": {"table": "projects", "alias": "prj",
                     "key_columns": ["project_name", "is_active", "organization_id"],
                     "note": "32 projects."},
        "cost_center": {"table": "cost_centers", "alias": "cc",
                         "key_columns": ["costcenter_name", "is_active", "organization_id"],
                         "note": "11 cost centers."},
        "collaborator": {"table": "collaborators", "alias": "collab",
                          "key_columns": ["user_id", "organization_id", "role_id"],
                          "note": "38 collaborators. Who has access to what org."},

        # ════════════ Payments ════════════
        "customer_payment": {"table": "customer_payment", "alias": "cp",
                              "key_columns": ["payment_number", "date", "customer_id", "invoice_ids", "amount", "status", "bank_account_id"]},
        "supplier_payment": {"table": "supplier_payments", "alias": "sp",
                              "key_columns": ["payment_number", "date", "supplier_id", "bill_ids", "amount", "status", "bank_account_id"]},
        "payment": {"table": "customer_payment", "alias": "cp",
                     "key_columns": ["payment_number", "date", "customer_id", "amount", "status"]},
        "receipt": {"table": "customer_payment", "alias": "cp",
                     "key_columns": ["payment_number", "date", "customer_id", "amount", "status"]},
        "payment_type": {"table": "payment_type", "alias": "pt",
                          "key_columns": ["id", "value"],
                          "note": "3 types: Cash, Bank Transfer, Cheque"},

        # ════════════ Documents ════════════
        "document": {"table": "documents", "alias": "doc",
                      "key_columns": ["name", "type", "folder", "user_id", "created_date"]},

        # ════════════ Reference / Lookup Tables ════════════
        "status": {"table": "status_type", "alias": "st",
                    "key_columns": ["id", "value"],
                    "note": "7 statuses: PENDING, PAID, CANCELLED, VOIDED, PARTIALLY_PAID, OVERDUE, DRAFT"},
        "status_type": {"table": "status_type", "alias": "st",
                         "key_columns": ["id", "value"]},
        "currency": {"table": "currency_type", "alias": "cur",
                      "key_columns": ["id", "value"],
                      "note": "2 currencies: AED, USD"},
        "country": {"table": "country_type", "alias": "cty",
                     "key_columns": ["id", "value"],
                     "note": "3 countries: UAE, Saudi Arabia, UK"},
        "industry": {"table": "industry_type", "alias": "ind",
                      "key_columns": ["id", "value"],
                      "note": "17 industry types"},
        "business_type": {"table": "business_type", "alias": "bt",
                           "key_columns": ["id", "value"]},

        # ════════════ Subscriptions & Users ════════════
        "user": {"table": "users", "alias": "u",
                  "key_columns": ["name", "email", "role_id", "is_deleted"]},
        "collaborator": {"table": "collaborators", "alias": "collab",
                          "key_columns": ["user_id", "organization_id", "account_type_id"],
                          "note": "38 collaborators. Who has access to what org."},
    }

    # Normalize plurals
    singular = entity.rstrip("s") if entity.endswith("s") and entity not in ENTITY_MAP else entity
    if singular in ENTITY_MAP:
        result = ENTITY_MAP[singular].copy()
        result["resolved_from"] = entity
        return result

    if entity in ENTITY_MAP:
        result = ENTITY_MAP[entity].copy()
        result["resolved_from"] = entity
        return result

    # Fallback: check if entity matches a real table name
    schema = get_schema()
    if entity in schema:
        return {
            "table": entity,
            "key_columns": schema[entity]["column_names"][:6],
            "resolved_from": entity,
            "note": "Direct table match — no semantic mapping available",
        }

    return {"error": f"Cannot resolve entity '{entity}'. Known entities: {list(ENTITY_MAP.keys())}"}


def _task_check_field(table: str, column: str) -> Dict:
    schema = get_schema()
    table = table.lower().strip()
    column = column.lower().strip()
    if table not in schema:
        return {"exists": False, "reason": f"Table '{table}' does not exist."}
    exists = column in schema[table]["column_names"]
    return {
        "exists": exists,
        "table": table,
        "column": column,
        "available_columns": schema[table]["column_names"] if not exists else None,
    }


# ──────────────────────────────────────────────
# Known safe join paths (comprehensive)
# ──────────────────────────────────────────────
KNOWN_JOINS = {
    # ── Income chain ──
    ("income", "contacts"):             "LEFT JOIN contacts c ON c.id = inc.contact_id",
    ("income", "income_items"):         "JOIN income_items ii ON ii.income_id = inc.id AND ii.is_deleted = false",
    ("income_items", "items"):          "JOIN items i ON i.id = ii.items_id",
    ("income", "status_type"):          "LEFT JOIN status_type st ON st.id = inc.status_type_id",
    ("income", "projects"):             "LEFT JOIN projects prj ON prj.id = inc.project_id",

    # ── Expense chain ──
    ("expense", "contacts"):            "LEFT JOIN contacts c ON c.id = e.contact_id",
    ("expense", "expense_items"):       "JOIN expense_items ei ON ei.expense_id = e.id AND ei.is_deleted = false",
    ("expense_items", "items"):         "JOIN items i ON i.id = ei.items_id",
    ("expense", "status_type"):         "LEFT JOIN status_type st ON st.id = e.status_type_id",
    ("expense", "expense_category_type"): "LEFT JOIN expense_category_type ect ON ect.id = e.expense_category_type_id",
    ("expense", "projects"):            "LEFT JOIN projects prj ON prj.id = e.project_id",
    ("expense", "supplier_payments"):   "LEFT JOIN supplier_payments sp ON sp.supplier_id = e.contact_id",
    ("income", "customer_payment"):     "LEFT JOIN customer_payment cp ON cp.customer_id = inc.contact_id",
    ("income", "projects"):             "LEFT JOIN projects prj ON prj.id = inc.project_id",

    # ── Contact chain ──
    ("contacts", "contact_type"):       "LEFT JOIN contact_type ct ON ct.id = c.contact_type_id",
    ("contacts", "income"):             "LEFT JOIN income inc ON inc.contact_id = c.id AND inc.is_deleted = false",
    ("contacts", "expense"):            "LEFT JOIN expense e ON e.contact_id = c.id AND e.is_deleted = false",

    # ── Item chain ──
    ("items", "item_type"):             "LEFT JOIN item_type it ON it.id = itm.item_type_id",
    ("items", "inventory_quantities"):  "LEFT JOIN inventory_quantities iq ON iq.item_id = itm.id",
    ("items", "inventory_movements"):   "LEFT JOIN inventory_movements im ON im.item_id = itm.id",

    # ── Journal Entry chain (GL / Accounting Core) ──
    ("journal_entries", "journal_entry_lines"): "JOIN journal_entry_lines jel ON jel.journal_entry_id = je.id",
    ("journal_entry_lines", "chart_of_accounts"): "LEFT JOIN chart_of_accounts coa ON coa.id = jel.account_id",
    ("chart_of_accounts", "account_type"): "-- account_type is a VARCHAR column on chart_of_accounts, no join needed",
    ("journal_entries", "contacts"):    "-- journal_entries has no contact_id FK",
    ("journal_entries", "projects"):    "-- journal_entries has no project_id FK",

    # ── Banking chain ──
    ("bank_transactions", "bank_accounts"): "-- bank_transactions.account_id is TEXT not integer FK; use account_name for lookups",
    ("reconciliations", "bank_accounts"): "LEFT JOIN bank_accounts ba ON CAST(ba.id AS TEXT) = rec.account_id",

    # ── Inventory chain ──
    ("inventory_quantities", "items"):      "JOIN items itm ON itm.id = iq.item_id",
    ("inventory_quantities", "warehouses"): "JOIN warehouses wh ON wh.id = iq.warehouse_id",
    ("inventory_movements", "items"):       "JOIN items itm ON itm.id = im.item_id",
    ("inventory_movements", "warehouses"):  "JOIN warehouses wh ON wh.id = im.warehouse_id",
    ("inventory_adjustments", "items"):     "JOIN items itm ON itm.id = ia.item_id",
    ("inventory_adjustments", "warehouses"): "JOIN warehouses wh ON wh.id = ia.warehouse_id",

    # ── Organization chain ──
    ("organizations", "country_type"):      "LEFT JOIN country_type cty ON cty.id = org.country_type_id",
    ("organizations", "industry_type"):     "LEFT JOIN industry_type ind ON ind.id = org.industry_type_id",
    ("organizations", "branches"):          "LEFT JOIN branches br ON br.organization_id = org.id",
    ("collaborators", "organizations"):     "JOIN organizations org ON org.id = collab.organization_id",
    ("collaborators", "users"):             "JOIN users u ON u.id = collab.user_id",
    ("collaborators", "roles"):             "JOIN roles rl ON rl.id = collab.role_id",

    # ── Audit chain ──
    ("audit_trails", "users"):              "-- audit_trails.user_name is denormalized, no FK join needed",
    ("audit_logs", "users"):                "-- audit_logs.user_name is denormalized, no FK join needed",

    # ── Payment chain ──
    ("customer_payment", "contacts"):       "LEFT JOIN contacts c ON c.id = cp.customer_id",
    ("customer_payment", "payment_type"):   "LEFT JOIN payment_type pt ON pt.id = cp.payment_type_id",
    ("customer_payment", "customer_payment_items"): "LEFT JOIN customer_payment_items cpi ON cpi.payment_id = cp.id",
    ("supplier_payments", "contacts"):      "LEFT JOIN contacts c ON c.id = sp.supplier_id",
    ("supplier_payments", "payment_type"):  "LEFT JOIN payment_type pt ON pt.id = sp.payment_type_id",
    ("supplier_payments", "supplier_payment_items"): "LEFT JOIN supplier_payment_items spi ON spi.payment_id = sp.id",
    ("income_items", "cost_centers"):       "-- income_items.cost_center_id FK available",
}


def _task_get_join_path(from_table: str, to_table: str) -> Dict:
    from_table = from_table.lower().strip()
    to_table = to_table.lower().strip()
    key = (from_table, to_table)
    if key in KNOWN_JOINS:
        return {"join_sql": KNOWN_JOINS[key], "from": from_table, "to": to_table}
    key_rev = (to_table, from_table)
    if key_rev in KNOWN_JOINS:
        return {"join_sql": KNOWN_JOINS[key_rev], "from": to_table, "to": from_table, "note": "reversed"}
    return {"error": f"No known join path between '{from_table}' and '{to_table}'."}


def _task_get_sample_values(table: str, column: str, limit: int = 20) -> Dict:
    table = table.lower().strip()
    column = column.lower().strip()
    schema = get_schema()
    if table not in schema:
        return {"error": f"Table '{table}' not found."}
    if column not in schema[table]["column_names"]:
        return {"error": f"Column '{column}' not found in '{table}'."}
    try:
        soft_delete = " WHERE is_deleted = false" if schema[table]["has_is_deleted"] else ""
        cols, rows = execute_sql(
            f"SELECT DISTINCT {column} FROM {table}{soft_delete} ORDER BY {column} LIMIT {int(limit)}"
        )
        return {"table": table, "column": column, "values": [r[0] for r in rows]}
    except Exception as e:
        return {"error": str(e)}
