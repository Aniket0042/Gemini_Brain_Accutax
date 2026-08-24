"""
Coordinator Agent — the central orchestrator for the multi-agent system.

3-tier model routing for cost efficiency:
  - Tier 1 (Haiku, ~0.3s, ~$0.0002): Query classifier — simple/medium/complex
  - Tier 2 (Haiku, ~3-8s, ~$0.001): Handles simple/medium queries end-to-end
  - Tier 3 (Sonnet, ~15-40s, ~$0.03): Only complex multi-step reasoning queries
"""

import json
import logging
import os
import time
import datetime
import re as _re
from decimal import Decimal
from typing import Dict, Any, Optional, List

from gemini_brain.agents.bedrock_client import (
    converse,
    converse_with_tools,
    extract_tool_calls,
    extract_text,
    reset_token_usage,
    get_token_usage,
    MODEL_ID,
    MODEL_ID_FAST,
)
from gemini_brain.agents import schema_agent, finance_agent, tax_agent, reasoning_agent
from gemini_brain.agents.schema_loader import SCHEMA_BLOCK
from gemini_brain.agents import api_agent

logger = logging.getLogger("agents.coordinator")

MAX_ITERATIONS = 5
TIME_BUDGET_SECONDS = 90

# ──────────────────────────────────────────────────
# Dynamic date helper for system prompt
# ──────────────────────────────────────────────────
def _get_current_date_str() -> str:
    """Return today's date as YYYY-MM-DD for use in system prompt."""
    return datetime.date.today().isoformat()

# ──────────────────────────────────────────────────
# SYSTEM PROMPT (template — formatted dynamically with current date)
# ──────────────────────────────────────────────────

_SYSTEM_PROMPT_TEMPLATE = """You are the Coordinator of a multi-agent accounting intelligence system for a company using PostgreSQL with 10 years of production data. Currency: AED (Arab Emirates Dirham).

## TODAY: <<TODAY>> (<<MONTH_NAME>>). Use CURRENT_DATE in SQL.

## RULES
- ONE tool call when possible. After results → answer IMMEDIATELY. Retry only on SQL error (simpler query). EXCEPTION: multi-year P&L requires one call per year (see P&L section).
- Single execute_sql with JOINs/CTEs for multi-data-point questions. NEVER call schema_agent or reasoning_agent.
- NEVER output raw SQL/JSON in answers. Currency: AED X,XXX.XX. Bullet points for lists.
- NEVER refuse. Always answer. If ambiguous: assume most likely interpretation, state it, answer.
- 0 rows this month → show last 30/60/90 days. "AED X"→10000, "X days"→30, "X%"→10.

## EXECUTE_SQL — MANDATORY FORMAT (CRITICAL)
When calling finance_agent with task "execute_sql", you MUST include the complete SQL query in params.sql:
  finance_agent(task="execute_sql", params={"sql": "SELECT ... FROM ... WHERE ...", "organization_id": {org_id}})
NEVER call execute_sql without a sql string. NEVER put the SQL only in your text — it MUST be in params.sql.
After receiving results, give a FINAL answer immediately. Do NOT say "Let me try" or "Let me query" — just present the data.

## DATE RULES — CRITICAL
income.invoice_date, income.due_date, expense.reception_date are VARCHAR ('2021-01-01T12:16:11.000Z').
ALWAYS CAST: WHERE CAST(inc.invoice_date AS DATE) >= '2026-01-01'
EXCEPTION: journal_entries.transaction_date and bank_transactions.date are real DATE — no CAST needed.

## SCHEMA RULES (org_id={org_id})

### KEY QUERY NOTES
**income** amounts: SUM(ii.line_amount) JOIN income_items ii ON ii.income_id=inc.id — Total incl VAT: amount_paid + amount_due
**expense** amounts: SUM(ei.line_amount) from expense_items — NO amount_due on expense table!
**expense** category: JOIN expense_category_type ect ON ect.id=e.expense_category_type_id → use ect.value (NOT ect.name)
**status_type** values: PAID, PARTIALLY_PAID, PENDING, CANCELLED, VOIDED, ACCEPTED, RECEIVED — NEVER use PARTIAL_PAID/OVERDUE/SUBMITTED/APPROVED
Overdue = CAST(due_date AS DATE) < CURRENT_DATE AND st.value IN ('PENDING','PARTIALLY_PAID')
**contacts**: contact_type_id 4=customer, 1/2/3=vendor
**bank_accounts** total cash: SELECT SUM(balance) FROM bank_accounts WHERE organization_id={org_id}
**journal_entries**: transaction_date is real DATE (no CAST needed). NO notes/status/is_deleted columns.
**customer_payment** status values: COMPLETED/PENDING/CANCELLED/REFUNDED
**items** cost is VARCHAR: NULLIF(REGEXP_REPLACE(i.cost,'[^0-9.]','','g'),'')::NUMERIC
**VAT/Tax**: income_items.tax_rate_id and tax_amount contain 5% UAE VAT. Total with VAT = SUM(ii.line_amount + ii.tax_amount).
**Discounts**: income_items.discount_percent and discount_amount — ~20% of items have discounts (5-15%).
**Cost Centers**: income_items.cost_center_id → JOIN cost_centers cc ON cc.id=ii.cost_center_id (10 departments: Sales, Marketing, Engineering, etc.)
**Projects**: income.project_id → JOIN projects p ON p.id=inc.project_id (15 projects)
**Warehouses**: income.warehouse_id, expense.warehouse_id → JOIN warehouses w ON w.id=inc.warehouse_id (4 warehouses)
**Inventory**: inventory_quantities (current stock per item/warehouse), inventory_movements (GRN/GOODS_ISSUE/etc.)
**Payment Terms**: contacts.payment_terms — values: 'Net 30', 'Net 60', 'Net 90' (varies by customer/vendor)
**Other: bank_reconciliation(0 rows), purchase_orders(0 rows), quotations(0 rows)**
ALWAYS filter: AND e.organization_id={org_id} / AND inc.organization_id={org_id}

<<SCHEMA_BLOCK>>

## P&L / ACCOUNTING
- P&L: use profit_and_loss task (NOT trial_balance — always nets to zero)
- Multi-year P&L ("2024 vs 2025", "compare years", "last 2 years", "YoY"): call profit_and_loss ONCE PER YEAR — first with filters:{year:2024}, then separately with filters:{year:2025}. NEVER pass year as a list [2024,2025] — always individual year calls.
- P&L answer format: Show each year as its own section with Revenue, Gross Profit, Total Expenses (broken down by category if available), Net Profit, and Profit Margin %. Add year-over-year change (AED and %). NEVER collapse two years into a single number.
- ROUND() in PostgreSQL needs explicit cast: CAST(val AS NUMERIC(10,2)) not ROUND(float, 2)
- No EXTRACT(DAY FROM interval) — use: CAST(date1 AS DATE) - CAST(date2 AS DATE) → integer days

## PERFORMANCE
- NEVER cartesian join. ALWAYS LIMIT on >100 row queries. COUNT(DISTINCT inc.id) not COUNT(DISTINCT id).

## TASK ROUTING — CRITICAL
Use the BEST pre-built task first. Only fall back to execute_sql when no pre-built task fits.

### COLLECTIONS & RECEIVABLES
- "top N defaulters" / "who owes most" / "which companies have unpaid invoices" → **customer_overdue_summary**
- "overdue invoices" / "oldest unpaid invoice" / "list overdue" → **overdue_invoices**
- "AR aging" / "receivables by bucket" / "0-30, 31-60 aging" → **ar_aging**
- "total receivables outstanding" / "total AR" → **ar_aging**
- "last N receipts" / "recent payments received" / "customer payments today" → **customer_payments** with limit
- "unallocated receipts" / "receipts not allocated" → **unallocated_payments**

### PAYABLES & VENDORS
- "vendor bills due" / "upcoming bills" / "overdue bills" → **overdue_bills** or **ap_aging**
- "AP aging" / "payables by bucket" → **ap_aging**
- "vendor payments made" / "last N vendor payments" → **vendor_payments** with limit
- "top N expenses" / "expenditures" / "outflows" → **expense_by_category** or **vendor_payments**

### CASH & BANKING
- "cash balance" / "bank balance" / "how much cash" → **bank_balances**
- "bank transactions" / "unmatched bank items" / "bank movements" → **bank_transactions** with filters
- "cash flow" / "cash in vs cash out" → **cash_flow_summary**
- "payment forecast" / "upcoming payments" / "7/15/30 day cash" → **payment_forecast** with {days: N}

### INVOICES & BILLING
- "draft invoices" / "invoices not sent" / "invoice status" → **invoice_status_summary**
- "last N invoices" / "recent invoices" / "invoices created today" → **list_invoices** with limit/filters
- "last N expenditures" / "recent expenses" → **list_expenses** with limit
- "bill status" / "draft bills" / "bills pending approval" → **bill_status_summary**

### REPORTS
- "P&L" / "profit" / "net income" → **profit_and_loss** (NEVER trial_balance)
- "balance sheet" → **balance_sheet**
- "expenses by category" / "top expense categories" → **expense_by_category**
- "VAT" / "tax payable" → **vat_summary**
- "project profitability" / "which project is most profitable" → **project_profitability**

### INVENTORY
- "stock" / "inventory" / "low stock" / "reorder" → **inventory_status**
- "stock movements" / "GRN" / "goods issued" → **inventory_movements**

### JOURNAL & AUDIT
- "journal entries" / "manual journals" / "backdated entries" → **journal_entry_search**
- "audit trail" / "who changed" / "activity log" → **audit_activity**
- "recent transactions" / "last N transactions" → **recent_transactions** with limit

NEVER use overdue_invoices for defaulters — it returns per-invoice rows, not per-company totals.
"""


def _build_system_prompt(org_id: int = 199) -> str:
    """Build full system prompt with current date, org_id, and real DB schema injected."""
    today = _get_current_date_str()
    d = datetime.date.today()
    return _SYSTEM_PROMPT_TEMPLATE.replace(
        "<<TODAY>>", today
    ).replace(
        "<<YEAR>>", str(d.year)
    ).replace(
        "<<MONTH_NAME>>", d.strftime("%B %Y")
    ).replace(
        "{org_id}", str(org_id)
    ).replace(
        "<<SCHEMA_BLOCK>>", SCHEMA_BLOCK
    )


# ──────────────────────────────────────────────────
# COMPACT SYSTEM PROMPT — SIMPLE (Haiku) tier only
# Omits the ~3 000-token SCHEMA_BLOCK and complex SQL rules.
# The finance_agent builds all SQL internally in Python, so the
# coordinator only needs routing rules + date/filter knowledge.
# Saves ~3 500 input tokens × 2 Haiku calls per SIMPLE query.
# ──────────────────────────────────────────────────

_SIMPLE_SYSTEM_PROMPT_TEMPLATE = """You are the Coordinator of a multi-agent accounting intelligence system. Currency: AED.

## TODAY: <<TODAY>> (<<MONTH_NAME>>). Use CURRENT_DATE in SQL.

## RULES
- ONE tool call. After results → answer IMMEDIATELY. NEVER say "Let me try" or "Let me query" — just present data.
- NEVER output raw SQL/JSON in answers. Format currency as AED X,XXX.XX. Bullet points for lists.
- NEVER refuse. Assume most likely interpretation and answer.
- If 0 rows this month → try last 30/60/90 days instead.

## EXECUTE_SQL FORMAT (CRITICAL)
When calling finance_agent with task "execute_sql", you MUST include complete SQL in params.sql:
  finance_agent(task="execute_sql", params={"sql": "SELECT ... FROM ... WHERE ...", "organization_id": {org_id}})
NEVER call execute_sql without a sql string in params. After receiving results, give a FINAL formatted answer.

## DATE RULES — CRITICAL
income.invoice_date, income.due_date, expense.reception_date are VARCHAR ('2021-01-01T12:16:11.000Z').
ALWAYS CAST: WHERE CAST(inc.invoice_date AS DATE) >= '2026-01-01'
EXCEPTION: journal_entries.transaction_date and bank_transactions.date are real DATE — no CAST needed.

## KEY RULES (org_id={org_id})
- income amounts: SUM(ii.line_amount) JOIN income_items ii ON ii.income_id=inc.id
- expense amounts: SUM(ei.line_amount) from expense_items — NO amount_due on expense table!
- status values: PAID, PARTIALLY_PAID, PENDING, CANCELLED, VOIDED — NEVER use PARTIAL_PAID/OVERDUE
- Overdue = CAST(due_date AS DATE) < CURRENT_DATE AND status IN ('PENDING','PARTIALLY_PAID')
- contacts: contact_type_id 4=customer, 1/2/3=vendor
- bank cash: SELECT SUM(balance) FROM bank_accounts WHERE organization_id={org_id}
- ALWAYS filter: AND e.organization_id={org_id} / AND inc.organization_id={org_id}

## TASK ROUTING — CRITICAL
Use pre-built tasks before execute_sql. Match question type to the right task:
- defaulters / who owes most / top N companies unpaid → **customer_overdue_summary**
- overdue invoices / oldest unpaid invoice listed → **overdue_invoices**
- AR aging / receivables aging buckets / total AR → **ar_aging**
- bank balance / how much cash / accounts balance → **bank_balances**
- upcoming payments / 7/15/30 day payment forecast → **payment_forecast** {days: N}
- AP aging / payables aging / what we owe vendors → **ap_aging**
- overdue bills / vendor bills overdue → **overdue_bills**
- last N receipts / recent customer payments → **customer_payments** {limit: N}
- last N vendor payments / vendor outflows → **vendor_payments** {limit: N}
- unallocated receipts / unmatched payments → **unallocated_payments**
- cash flow / cash in vs out → **cash_flow_summary**
- bank transactions / unmatched bank items → **bank_transactions**
- invoice status / draft invoices / invoices sent → **invoice_status_summary**
- last N invoices / list recent invoices → **list_invoices** {limit: N}
- bill status / draft bills / pending bills → **bill_status_summary**
- expenses by category / top expense categories → **expense_by_category**
- VAT / tax summary / VAT payable → **vat_summary**
- P&L / profit / net income → **profit_and_loss**
- last N expenses / recent expenditures → **list_expenses** {limit: N}
- recent transactions / last N transactions → **recent_transactions** {limit: N}
- inventory / stock / low stock → **inventory_status**
- journal entries / manual journals → **journal_entry_search**
- audit / activity log / who changed → **audit_activity**
"""

def _build_simple_system_prompt(org_id: int = 199) -> str:
    """Compact prompt for SIMPLE (Haiku) tier — no schema block."""
    today = _get_current_date_str()
    d = datetime.date.today()
    return _SIMPLE_SYSTEM_PROMPT_TEMPLATE.replace(
        "<<TODAY>>", today
    ).replace(
        "<<MONTH_NAME>>", d.strftime("%B %Y")
    ).replace(
        "{org_id}", str(org_id)
    )


# ──────────────────────────────────────────────────
# QUERY CLASSIFIER — now delegates to api_agent.classify_route()
# Returns: 'API' | 'SIMPLE' | 'COMPLEX'
# ──────────────────────────────────────────────────

def _classify_query(question: str) -> str:
    """
    3-way route classifier.
    API     → answer via Accutax REST API call (no DB SQL needed)
    SIMPLE  → single-period DB analytics via Haiku coordinator
    COMPLEX → multi-period / trend analytics via Sonnet coordinator
    """
    return api_agent.classify_route(question)

# ──────────────────────────────────────────────────
# TOOL DEFINITIONS (Bedrock Converse toolConfig format)
# ──────────────────────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "toolSpec": {
            "name": "schema_agent",
            "description": (
                "Database introspection agent. Use to discover tables, columns, "
                "resolve entity names to real table/column names, verify field existence, "
                "and find join paths between tables."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "One of: get_tables, get_table_schema, resolve_entity, check_field_exists, get_join_path, get_sample_values",
                            "enum": ["get_tables", "get_table_schema", "resolve_entity", "check_field_exists", "get_join_path", "get_sample_values"],
                        },
                        "params": {
                            "type": "object",
                            "description": "Task-specific parameters. For get_table_schema: {table}. For resolve_entity: {entity}. For check_field_exists: {table, field}. For get_join_path: {from_table, to_table}. For get_sample_values: {table, column, limit}.",
                        },
                    },
                    "required": ["task"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "finance_agent",
            "description": (
                "Financial data agent. Executes read-only SQL or pre-built financial queries. "
                "Handles invoices, expenses, GL/accounting (trial balance, balance sheet, P&L, general ledger), "
                "banking, inventory, AR/AP aging, audit trails, project profitability, and cost center analysis."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": (
                                "The task to execute. Options: "
                                "ORIGINAL: execute_sql, get_invoice_total, get_expense_total, list_invoices, "
                                "list_expenses, top_customers, top_vendors, count_records, get_invoice_details, aggregate_metric. "
                                "ACCOUNTING: trial_balance, balance_sheet, profit_and_loss, general_ledger, chart_of_accounts, journal_entry_search. "
                                "BANKING: bank_balances, bank_transactions. "
                                "INVENTORY: inventory_status, inventory_valuation, inventory_movements. "
                                "AR/AP: ar_aging, ap_aging, overdue_invoices (per-invoice rows sorted by days overdue), overdue_bills, customer_overdue_summary (top defaulters — sums all overdue amounts PER COMPANY, use for 'top N defaulters'/'who owes most'). "
                                "AUDIT: audit_trail, audit_activity. "
                                "ADVANCED: project_profitability, expense_by_category, cost_center_breakdown."
                            ),
                            "enum": [
                                "execute_sql", "get_invoice_total", "get_expense_total",
                                "list_invoices", "list_expenses", "top_customers", "top_vendors",
                                "count_records", "get_invoice_details", "aggregate_metric",
                                "trial_balance", "balance_sheet", "profit_and_loss",
                                "general_ledger", "chart_of_accounts", "journal_entry_search",
                                "bank_balances", "bank_transactions",
                                "inventory_status", "inventory_valuation", "inventory_movements",
                                "ar_aging", "ap_aging", "overdue_invoices", "overdue_bills",
                                "audit_trail", "audit_activity",
                                "project_profitability", "expense_by_category", "cost_center_breakdown",
                                "customer_payments", "vendor_payments", "unallocated_payments", "payment_forecast",
                                "reconciliation_status", "branch_summary", "cash_flow_summary",
                                "invoice_status_summary", "bill_status_summary",
                                "weekly_transaction_summary", "monthly_revenue_trend", "vat_summary",
                                "recent_transactions", "customer_overdue_summary",
                            ],
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "Task params. Common: {filters: {year, month, date_from, date_to, status, customer, vendor}, limit: N}. "
                                "execute_sql: {sql: 'SELECT ...'}. "
                                "count_records: {entity: 'invoice'|'expense'|'customer'|'vendor'|'item'|'journal_entry'}. "
                                "get_invoice_details: {invoice_number}. "
                                "aggregate_metric: {entity, metric: 'sum'|'avg'|'count', field, filters}. "
                                "general_ledger: {account_name or account_code, filters, limit}. "
                                "chart_of_accounts: {account_type: 'Asset'|'Liability'|'Equity'}. "
                                "journal_entry_search: {reference, notes, min_amount, filters}. "
                                "bank_transactions: {bank_account, category, filters}. "
                                "inventory_status: {item_name, warehouse, limit}. "
                                "overdue_invoices: {customer, limit} — per-invoice rows sorted by days overdue. "
                                "overdue_bills: {vendor, limit}. "
                                "customer_overdue_summary: {limit, min_days_overdue} — top defaulters by total outstanding amount, grouped per company. Use for 'defaulters', 'who owes most', 'top N companies with unpaid invoices'. "
                                "audit_trail: {entity_type, entity_id, action, user, filters}. "
                                "payment_forecast: {days: 7|15|30, entity: 'both'|'customer'|'vendor'}. "
                                "detect_anomalies: {vendor_name}. "
                                "Most other tasks accept {filters} only."
                            ),
                        },
                    },
                    "required": ["task"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "tax_agent",
            "description": (
                "Tax computation agent. Applies VAT/tax rules for Middle East jurisdictions "
                "(UAE 5%, KSA 15%, Bahrain 10%, Oman 5%). Deterministic — no database access."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "One of: compute_vat, get_tax_rate, classify_category, compute_invoice_tax, get_regime_info, list_jurisdictions",
                            "enum": ["compute_vat", "get_tax_rate", "classify_category", "compute_invoice_tax", "get_regime_info", "list_jurisdictions"],
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "For compute_vat: {amount, jurisdiction, category, vat_inclusive}. "
                                "For get_tax_rate: {jurisdiction}. "
                                "For classify_category: {jurisdiction, category}. "
                                "For compute_invoice_tax: {jurisdiction, line_items: [{name, amount, category}] or total_amount}. "
                                "For get_regime_info: {jurisdiction}. "
                                "For list_jurisdictions: {}."
                            ),
                        },
                    },
                    "required": ["task"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "reasoning_agent",
            "description": (
                "Reasoning and narrative agent. Post-processes results to generate "
                "natural-language answers, compare datasets, derive insights, "
                "format answers for different roles, and compute confidence scores."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "One of: synthesize_answer, compare_results, derive_insights, format_for_role, compute_confidence, narrative_synthesis",
                            "enum": ["synthesize_answer", "compare_results", "derive_insights", "format_for_role", "compute_confidence", "narrative_synthesis"],
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "For synthesize_answer: {plan, sql_results, question}. "
                                "For compare_results: {datasets: [{label, value}], metric}. "
                                "For derive_insights: {plan, sql_results, assumptions}. "
                                "For format_for_role: {answer, role, plan}. "
                                "For compute_confidence: {sql_results, plan}. "
                                "For narrative_synthesis: {question, data, context, style: 'brief'|'detailed'|'executive'}."
                            ),
                        },
                    },
                    "required": ["task"],
                }
            },
        }
    },
]

# ──────────────────────────────────────────────────
# Agent dispatch table
# ──────────────────────────────────────────────────

AGENT_HANDLERS = {
    "schema_agent":     schema_agent.handle,
    "finance_agent":    finance_agent.handle,
    "tax_agent":        tax_agent.handle,
    "reasoning_agent":  reasoning_agent.handle,
}


def _deep_serialize(obj):
    """Recursively convert any non-JSON-safe value so json.dumps never fails."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    if isinstance(obj, datetime.timedelta):
        return obj.total_seconds()
    if isinstance(obj, dict):
        return {k: _deep_serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_deep_serialize(i) for i in obj]
    return str(obj)


def _format_raw_results(question: str, results: list) -> str:
    """Format raw DB results into readable text — NEVER dump raw JSON to user."""
    if not results:
        return "No relevant data found for this query."
    row = results[0] if len(results) == 1 else None
    lines = []
    if row and isinstance(row, dict):
        lines.append(f"Based on the financial data ({len(results)} record{'s' if len(results) > 1 else ''}):\n")
        for key, val in row.items():
            label = key.replace("_", " ").title()
            if isinstance(val, (int, float)) and val > 999:
                lines.append(f"- {label}: AED {val:,.2f}")
            elif isinstance(val, (int, float)):
                lines.append(f"- {label}: {val:,.2f}")
            elif val is not None:
                lines.append(f"- {label}: {val}")
    else:
        lines.append(f"Retrieved {len(results)} records.\n")
        for i, r in enumerate(results[:10]):
            if isinstance(r, dict):
                summary_parts = []
                for k, v in list(r.items())[:4]:
                    label = k.replace("_", " ").title()
                    if isinstance(v, (int, float)) and v > 999:
                        summary_parts.append(f"{label}: AED {v:,.2f}")
                    else:
                        summary_parts.append(f"{label}: {v}")
                lines.append(f"{i+1}. {' | '.join(summary_parts)}")
        if len(results) > 10:
            lines.append(f"... and {len(results) - 10} more records")
    return "\n".join(lines)


def _strip_sql_from_answer(answer: str) -> str:
    """Remove raw SQL that leaked into user-facing answers."""
    if not answer:
        return answer
    # Remove SELECT ... FROM ... patterns (multi-line)
    answer = _re.sub(
        r'```sql.*?```',
        '[SQL query executed]',
        answer,
        flags=_re.DOTALL | _re.IGNORECASE,
    )
    answer = _re.sub(
        r'(?:^|\n)\s*SELECT\s+[\s\S]{10,}?(?:FROM\s+\w+[\s\S]*?)(?:;|$|\n\n)',
        '\n[SQL query executed]\n',
        answer,
        flags=_re.IGNORECASE,
    )
    # Remove inline SQL fragments like "SELECT ... FROM ... WHERE ..."
    answer = _re.sub(
        r'SELECT\s+\w[\w.,\s*()]+FROM\s+\w+(?:\s+\w+)?(?:\s+(?:LEFT |RIGHT |INNER |OUTER |CROSS )?JOIN\s+\w+(?:\s+\w+)?(?:\s+ON\s+[^\n]+)?)*(?:\s+WHERE\s+[^\n]+)?(?:\s+(?:GROUP|ORDER|HAVING|LIMIT)\s+[^\n]+)*',
        '[SQL query executed]',
        answer,
        flags=_re.IGNORECASE,
    )
    return answer.strip()


# ──────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────

def run(user_question: str, *, organization_id: int = 168, model_id_override: Optional[str] = None) -> Dict[str, Any]:
    """
    Execute a user question through the 3-tier model routing system.

    Tier 1 (Haiku ~0.3s): Classify complexity → SIMPLE or COMPLEX
    Tier 2 (Haiku ~3-8s): SIMPLE queries — compact prompt + Haiku (saves ~3 500 tokens vs full prompt)
    Tier 3 (Sonnet ~15-40s): COMPLEX queries — full schema prompt + Sonnet

    model_id_override: bypass tier routing and force a specific Bedrock model ID.
                       Useful for A/B benchmarking.  Always uses full system prompt.
    """
    reset_token_usage()
    start_time = time.time()

    # ── Tier 1: Classify (skip when caller forces a model) ────────
    if model_id_override:
        complexity = "FORCED"
        active_model = model_id_override
        system_prompt = _build_system_prompt(org_id=organization_id)
        logger.info("Model override active: %s", active_model)
    else:
        complexity = _classify_query(user_question)

        # ── API TIER: route to live Accutax backend REST API ──────────────
        if complexity == "API":
            logger.info("Routing to API tier for question: %.80s", user_question)
            api_result = api_agent.run_api_query(
                user_question,
                org_id=organization_id,
                user_id=int(os.getenv("ACCUTAX_USER_ID", "1")),
            )
            # If API succeeded → return its result directly
            if api_result.get("route") != "agent_fallback":
                return api_result
            # API failed (unreachable / auth error) → fall through to AGENT
            logger.warning("API tier failed (%s) — falling back to AGENT", api_result.get("error"))
            complexity = "SIMPLE"  # treat as simple DB query as fallback

        if complexity == "SIMPLE":
            active_model = MODEL_ID_FAST
            system_prompt = _build_simple_system_prompt(org_id=organization_id)
        else:
            active_model = MODEL_ID
            system_prompt = _build_system_prompt(org_id=organization_id)
        logger.info("Routing to model tier: %s (%s)", complexity, active_model)

    messages = [{"role": "user", "content": [{"text": user_question}]}]

    agent_trace: List[Dict] = []
    last_sql = None
    last_results = None
    final_answer = None
    question_type = "unknown"

    for iteration in range(MAX_ITERATIONS):

        # ─── Time-budget guard: if we're running out of time, force final answer ───
        elapsed_so_far = time.time() - start_time
        if iteration >= 1 and elapsed_so_far > TIME_BUDGET_SECONDS:
            logger.warning("Time budget exceeded (%.1fs > %ds) at iteration %d, forcing final answer",
                          elapsed_so_far, TIME_BUDGET_SECONDS, iteration + 1)
            # Try to compose answer from existing tool results
            if last_results and isinstance(last_results, list) and len(last_results) > 0:
                # We have data — make a short formatting LLM call
                try:
                    data_preview = json.dumps(last_results[:20], default=str)[:3000]
                    force_msg = [
                        {"role": "user", "content": [{"text": user_question}]},
                        {"role": "user", "content": [{"text": f"DATA FROM DATABASE:\n{data_preview}\n\nBased on this data, give a clear, direct answer to the question. Format numbers as AED X,XXX.XX. Do not include raw JSON or SQL. Present the data as a professional financial summary with bullet points."}]},
                    ]
                    force_resp = converse_with_tools(
                        system_prompt="You are a financial data analyst. Format the provided database results into a clear, professional answer. Use AED currency format. Be concise and direct. NEVER output raw JSON — always format as readable text with bullet points.",
                        messages=force_msg,
                        tools=[],
                        temperature=0.0,
                        max_tokens=1500,
                        model_id=MODEL_ID_FAST,
                    )
                    final_answer = extract_text(force_resp)
                    if not final_answer or len(final_answer) < 20:
                        final_answer = _format_raw_results(user_question, last_results)
                except Exception:
                    final_answer = _format_raw_results(user_question, last_results)
            else:
                # No data — make one last quick LLM call with existing context
                try:
                    force_msg = messages + [{"role": "user", "content": [{"text": "Please give your best answer now based on what you know. If no data was retrieved, explain what data limitation applies and what the typical answer would be based on financial context."}]}]
                    force_resp = converse_with_tools(
                        system_prompt=system_prompt,
                        messages=force_msg,
                        tools=[],
                        temperature=0.0,
                        max_tokens=600,
                        model_id=MODEL_ID_FAST,
                    )
                    final_answer = extract_text(force_resp)
                    if not final_answer:
                        final_answer = "No relevant data found in the database for this query. Please check if the relevant records exist."
                except Exception:
                    final_answer = "No relevant data found in the database for this query."
            break

        logger.info("Coordinator iteration %d/%d", iteration + 1, MAX_ITERATIONS)
        try:
            response = converse_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                temperature=0.0,
                model_id=active_model,
            )
        except Exception as e:
            logger.exception("Bedrock converse_with_tools failed on iteration %d", iteration)
            return _error_response(user_question, f"LLM call failed: {str(e)}", agent_trace)

        stop_reason = response.get("stopReason", "end_turn")
        tool_calls = extract_tool_calls(response)

        # If no tool calls → model is done, extract final text
        if not tool_calls or stop_reason == "end_turn":
            final_answer = extract_text(response)
            break

        # ─── Execute each tool call ───
        # Append the assistant message (with tool_use blocks) first
        assistant_content = response.get("output", {}).get("message", {}).get("content", [])
        messages.append({"role": "assistant", "content": assistant_content})

        tool_result_blocks = []
        for tc in tool_calls:
            tool_name = tc["name"]
            tool_input = tc.get("input", {})
            tool_use_id = tc["toolUseId"]

            task = tool_input.get("task", "")
            params = tool_input.get("params", {})
            if not isinstance(params, dict):
                params = {}
            # If task is empty/missing, return an informative error immediately
            if not task:
                tool_result_blocks.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content": [{"text": json.dumps({"error": "Missing 'task' field. You must specify a task. For finance_agent use one of: execute_sql, top_customers, top_vendors, profit_and_loss, ar_aging, ap_aging, overdue_invoices, overdue_bills, bank_balances, payment_forecast, expense_by_category, balance_sheet, etc."})}],
                    }
                })
                agent_trace.append({"iteration": iteration + 1, "agent": tool_name, "task": "(missing)", "success": False, "error": "missing task"})
                continue

            # Add organization_id for agents that need it
            if tool_name == "finance_agent":
                params.setdefault("organization_id", organization_id)

            # ── Fix: extract SQL from model text when execute_sql has no sql param ──
            if (tool_name == "finance_agent" and task == "execute_sql"
                    and not params.get("sql", "").strip()):
                for block in assistant_content:
                    if isinstance(block, dict) and "text" in block:
                        import re as _re_sql
                        m = _re_sql.search(
                            r'```(?:sql)?\s*(SELECT .+?)```',
                            block["text"], flags=_re_sql.DOTALL | _re_sql.IGNORECASE,
                        )
                        if not m:
                            m = _re_sql.search(
                                r'(SELECT\s+.+?(?:LIMIT\s+\d+|;|\Z))',
                                block["text"], flags=_re_sql.DOTALL | _re_sql.IGNORECASE,
                            )
                        if m:
                            params["sql"] = m.group(1).strip().rstrip(';')
                            logger.info("Extracted SQL from model text for execute_sql")
                            break

            logger.info("  → %s.%s(%s)", tool_name, task, json.dumps(params, default=str)[:200])

            handler = AGENT_HANDLERS.get(tool_name)
            if handler is None:
                agent_result = {"error": f"Unknown agent: {tool_name}"}
            else:
                try:
                    agent_result = handler(task, params)
                except Exception as e:
                    logger.exception("Agent %s.%s raised an exception", tool_name, task)
                    agent_result = {"error": f"{tool_name}.{task} failed: {str(e)}"}

            # Track SQL and results for the response
            if tool_name == "finance_agent":
                if "sql" in agent_result:
                    last_sql = agent_result.get("sql")
                if "results" in agent_result:
                    last_results = agent_result.get("results")

            # Detect question type from finance_agent patterns
            if tool_name == "finance_agent":
                question_type = _infer_question_type(task, params, question_type)

            # Log for trace
            trace_entry = {
                "iteration": iteration + 1,
                "agent": tool_name,
                "task": task,
                "params": params,
                "success": "error" not in agent_result,
            }
            # Include lightweight summary in trace (not full data)
            if "error" in agent_result:
                trace_entry["error"] = agent_result["error"]
            elif "row_count" in agent_result:
                trace_entry["row_count"] = agent_result["row_count"]
            agent_trace.append(trace_entry)

            # Deep-serialize to eliminate ALL datetime/Decimal/UUID issues
            agent_result = _deep_serialize(agent_result)

            # For finance tasks: put summary + period first so pruning doesn't hide them
            if tool_name == "finance_agent" and "summary" in agent_result:
                summary_first = {"period": agent_result.get("period", ""), "summary": agent_result["summary"]}
                summary_first.update({k: v for k, v in agent_result.items() if k not in ("period", "summary")})
                agent_result = summary_first

            # Truncate large results AGGRESSIVELY to keep context small and LLM fast
            result_str = json.dumps(agent_result, default=str)
            if len(result_str) > 4000:
                if "results" in agent_result and isinstance(agent_result["results"], list):
                    truncated = dict(agent_result)
                    orig_count = agent_result.get("row_count", len(agent_result["results"]))
                    truncated["results"] = agent_result["results"][:10]
                    truncated["_truncated"] = True
                    truncated["_total_rows"] = orig_count
                    agent_result = truncated
                    result_str = json.dumps(agent_result, default=str)
                    if len(result_str) > 4000:
                        truncated["results"] = agent_result["results"][:5]
                        agent_result = truncated
                        result_str = json.dumps(agent_result, default=str)

            # Use text (not json) so boto3 never chokes on datetime/Decimal/UUID
            # types that may slip through from DB rows in any agent.
            tool_result_blocks.append({
                "toolResult": {
                    "toolUseId": tool_use_id,
                    "content": [{"text": result_str}],
                }
            })

        # Append tool results as a "user" message (Bedrock Converse protocol)
        messages.append({"role": "user", "content": tool_result_blocks})
        
        # ─── Prune ALL earlier tool results to tiny summaries ───
        # Keep only the latest tool results full; compress everything else hard
        if len(messages) > 4:
            for i, msg in enumerate(messages):
                if msg["role"] == "user" and i < len(messages) - 2:
                    content = msg.get("content", [])
                    if isinstance(content, list) and any("toolResult" in c for c in content if isinstance(c, dict)):
                        for j, block in enumerate(content):
                            if isinstance(block, dict) and "toolResult" in block:
                                old_text = block["toolResult"]["content"][0].get("text", "")
                                if len(old_text) > 600:
                                    block["toolResult"]["content"][0]["text"] = old_text[:600] + '...'

    else:
        # Exhausted MAX_ITERATIONS — the last response was a tool_use block, not
        # end_turn, so extract_text() returns "".  Force a Haiku synthesis call
        # using whatever data we collected so far.
        logger.warning("Coordinator exhausted %d iterations without end_turn — forcing Haiku synthesis", MAX_ITERATIONS)
        if last_results and isinstance(last_results, list) and len(last_results) > 0:
            try:
                data_preview = json.dumps(last_results[:20], default=str)[:3000]
                force_msgs = [
                    {"role": "user", "content": [{"text": user_question}]},
                    {"role": "user", "content": [{"text": (
                        f"DATA FROM DATABASE:\n{data_preview}\n\n"
                        "Based on this data, give a clear, direct answer to the question. "
                        "Format numbers as AED X,XXX.XX. Present as a professional financial "
                        "summary with bullet points or a table. Do not include raw JSON or SQL."
                    )}]},
                ]
                force_resp = converse_with_tools(
                    system_prompt=(
                        "You are a financial analyst. Format the provided database results "
                        "into a clear, professional answer. Use AED currency. Be concise. "
                        "NEVER output raw JSON."
                    ),
                    messages=force_msgs,
                    tools=[],
                    temperature=0.0,
                    max_tokens=1500,
                    model_id=MODEL_ID_FAST,
                )
                final_answer = extract_text(force_resp)
            except Exception:
                pass
        # Last-resort: try extracting any text from the most recent response
        if not final_answer and response:
            final_answer = extract_text(response)
        if not final_answer:
            final_answer = _format_raw_results(user_question, last_results or [])

    elapsed = time.time() - start_time
    token_usage = get_token_usage()
    token_usage["elapsed_seconds"] = round(elapsed, 2)

    # Compute total_count — skip expensive COUNT query if we already have a row_count
    total_count = None
    if last_results and isinstance(last_results, list):
        total_count = len(last_results)  # Use what we have — fast
    elif question_type == "list" and last_sql:
        total_count = _compute_total_count(last_sql, question_type)

    # Post-processing: strip any raw SQL that leaked into the final answer
    if final_answer:
        final_answer = _strip_sql_from_answer(final_answer)

    return {
        "query": user_question,
        "answer": final_answer or "No answer generated.",
        "question_type": question_type,
        "sql": last_sql,
        "results": last_results,
        "agent_trace": agent_trace,
        "token_usage": token_usage,
        "total_count": total_count,
        "error": None,
    }


# ──────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────

def _error_response(query: str, error_msg: str, trace: List[Dict]) -> Dict:
    return {
        "query": query,
        "answer": f"Error: {error_msg}",
        "question_type": "error",
        "sql": None,
        "results": None,
        "agent_trace": trace,
        "token_usage": get_token_usage(),
        "total_count": None,
        "error": error_msg,
    }


def _infer_question_type(task: str, params: Dict, current: str) -> str:
    """Infer question_type from finance_agent task."""
    mapping = {
        "get_invoice_total": "aggregate",
        "get_expense_total": "aggregate",
        "aggregate_metric": "aggregate",
        "count_records": "aggregate",
        "list_invoices": "list",
        "list_expenses": "list",
        "top_customers": "extreme",
        "top_vendors": "extreme",
        "get_invoice_details": "detail",
        "execute_sql": current,
        # Accounting
        "trial_balance": "report",
        "balance_sheet": "report",
        "profit_and_loss": "report",
        "general_ledger": "list",
        "chart_of_accounts": "list",
        "journal_entry_search": "list",
        # Banking
        "bank_balances": "report",
        "bank_transactions": "list",
        # Inventory
        "inventory_status": "list",
        "inventory_valuation": "report",
        "inventory_movements": "list",
        # AR/AP
        "ar_aging": "report",
        "ap_aging": "report",
        "overdue_invoices": "list",
        "overdue_bills": "list",
        # Audit
        "audit_trail": "list",
        "audit_activity": "list",
        # Advanced
        "project_profitability": "report",
        "expense_by_category": "report",
        "cost_center_breakdown": "report",
        # Payments
        "customer_payments": "list",
        "vendor_payments": "list",
        "unallocated_payments": "list",
        "payment_forecast": "list",
        # Reconciliation & Structure
        "reconciliation_status": "report",
        "branch_summary": "list",
        "cash_flow_summary": "report",
        "invoice_status_summary": "report",
        "bill_status_summary": "report",
    }
    return mapping.get(task, current)


def _compute_total_count(sql: Optional[str], question_type: str) -> Optional[int]:
    """Run a COUNT(*) wrapper over the last SQL if it's a list query."""
    if question_type != "list" or not sql:
        return None
    try:
        import re
        from gemini_brain.agents.executor import execute_sql
        count_sql = re.sub(r'\s*LIMIT\s+\d+\s*$', '', sql.strip(), flags=re.IGNORECASE)
        count_sql = f"SELECT COUNT(*) FROM ({count_sql}) AS _total_cnt"
        _, rows = execute_sql(count_sql)
        if rows:
            return int(rows[0][0])
    except Exception:
        pass
    return None
