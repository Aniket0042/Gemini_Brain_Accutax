"""
registry.py — Central tool registry for Gemini Brain.

Appendix A complete registration table:
- Specifies ToolSpec definitions, descriptions, schemas, handlers, formatters, and flags.
- Pure lookups (item_list, bank_accounts, etc.) have narrate=False.
- gemini_declarations() generates Gemini function declarations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Type
from pydantic import BaseModel

from gemini_brain.tools.handlers import make_api_handler
from gemini_brain.tools.schemas import (
    AnswerDirectlyParams,
    ApAgingParams,
    ArAgingParams,
    AuditLogsParams,
    AuditTrailsParams,
    BalanceSheetParams,
    BankAccountsParams,
    BankRulesParams,
    BillFindParams,
    BillListParams,
    BranchesParams,
    CashFlowParams,
    CashForecastParams,
    ChartOfAccountsListParams,
    ChartOfAccountsParams,
    ContactCountParams,
    ContactListParams,
    ContactSearchParams,
    CostCentersParams,
    CustomerBalanceSummaryParams,
    CustomerPaymentsParams,
    DashboardOverviewParams,
    ExchangeRatesParams,
    ExpenseByCategoryParams,
    ExpenseTotalParams,
    GLProfitabilityParams,
    GeneralLedgerParams,
    GeneralLedgerSummaryParams,
    IncomeTotalParams,
    InventoryMovementParams,
    InvoiceFindParams,
    InvoiceListParams,
    ItemListParams,
    ItemSearchParams,
    JournalEntriesParams,
    ProfitLossParams,
    ProfitLossWithAccountsParams,
    ProjectExpenseRollupParams,
    ProjectsListParams,
    PurchasesByItemParams,
    PurchasesByVendorParams,
    SalesByCustomerParams,
    SalesByItemParams,
    SupplierPaymentsParams,
    SupportedCurrenciesParams,
    TaxLiabilityParams,
    TrialBalanceParams,
    UncategorizedTransactionsParams,
    UnsupportedParams,
    VatSummaryParams,
    VendorBalanceSummaryParams,
)
from gemini_brain.tools.handlers import make_api_handler, make_sql_function_handler


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    endpoint: str
    params: Type[BaseModel]
    handler: Callable[..., Awaitable[Any]]
    narrate: bool
    formatter: str
    intent: int
    cache_ttl: int = 300
    timeout: float = 6.0


async def _noop_handler(params: Any, ctx: Any) -> Dict[str, Any]:
    return {}


REGISTRY: Dict[str, ToolSpec] = {
    "profit_loss": ToolSpec(
        name="profit_loss",
        description=(
            "Profit and Loss (P&L) income statement showing revenue, cost of goods sold (COGS), "
            "gross profit, operating expenses, and net profit over a date period. "
            "Use for: 'show profit and loss', 'P&L for this year', 'net income statement'. "
            "Do NOT use for general cash flow or balance sheet."
        ),
        endpoint="/report/profit-loss",
        params=ProfitLossParams,
        handler=make_api_handler("/report/profit-loss"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "balance_sheet": ToolSpec(
        name="balance_sheet",
        description=(
            "Balance Sheet statement showing assets, liabilities, and equity as of a specific date. "
            "Use for: 'show balance sheet', 'what are our total assets', 'current liabilities'. "
            "Do NOT use for P&L or expense breakdowns."
        ),
        endpoint="/report/balance-sheet",
        params=BalanceSheetParams,
        handler=make_api_handler("/report/balance-sheet"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "cash_flow": ToolSpec(
        name="cash_flow",
        description=(
            "Cash Flow statement showing operating, investing, and financing cash flows. "
            "Use for: 'cash flow statement', 'cash inflows and outflows'. "
            "Do NOT use for future projections (use cash_forecast instead)."
        ),
        endpoint="/report/cash-flow",
        params=CashFlowParams,
        handler=make_api_handler("/report/cash-flow"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "cash_forecast": ToolSpec(
        name="cash_forecast",
        description=(
            "Cash forecast projection and runway estimation over future months. "
            "Use for: 'projected cash flow', 'cash runway', 'expected cash next month'. "
            "Do NOT use for historical cash flow statements."
        ),
        endpoint="/report/cash-forecast",
        params=CashForecastParams,
        handler=make_api_handler("/report/cash-forecast"),
        narrate=True,
        formatter="kv_summary",
        intent=5,
    ),
    "ar_aging": ToolSpec(
        name="ar_aging",
        description=(
            "Accounts Receivable (AR) aging report breaking down overdue customer invoices by age brackets (1-30, 31-60, 61-90, 90+ days). "
            "Use for: 'who owes us money', 'overdue invoices aging', 'aged receivables'. "
            "Do NOT use for single customer lookup (use customer_balance_summary or contact_search)."
        ),
        endpoint="/report/ar-aging-summary",
        params=ArAgingParams,
        handler=make_api_handler("/report/ar-aging-summary"),
        narrate=True,
        formatter="aging_buckets",
        intent=3,
    ),
    "ap_aging": ToolSpec(
        name="ap_aging",
        description=(
            "Accounts Payable (AP) aging report breaking down overdue supplier bills by age brackets. "
            "Use for: 'who do we owe money to', 'unpaid vendor bills aging', 'aged payables'. "
            "Do NOT use for customer receivables."
        ),
        endpoint="/report/ap-aging-summary",
        params=ApAgingParams,
        handler=make_api_handler("/report/ap-aging-summary"),
        narrate=True,
        formatter="aging_buckets",
        intent=3,
    ),
    "customer_balance_summary": ToolSpec(
        name="customer_balance_summary",
        description=(
            "Per-customer financial summary: total invoiced revenue, total payments collected, and net outstanding balance. "
            "Use for: 'customer breakdown', 'which customers owe us money', 'customer balances'. "
            "Do NOT use for searching a single contact's address or phone (use contact_search)."
        ),
        endpoint="/report/customer-balance-summary",
        params=CustomerBalanceSummaryParams,
        handler=make_api_handler("/report/customer-balance-summary"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "sales_by_customer": ToolSpec(
        name="sales_by_customer",
        description=(
            "Sales breakdown by customer ranking top revenue sources. "
            "Use for: 'top customers', 'sales by customer', 'highest paying clients'. "
            "Do NOT use for vendor payments."
        ),
        endpoint="/report/sales-by-customer",
        params=SalesByCustomerParams,
        handler=make_api_handler("/report/sales-by-customer"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "expense_by_category": ToolSpec(
        name="expense_by_category",
        description=(
            "Operating expenses grouped by category or expense account. "
            "Use for: 'expenses by category', 'spending breakdown', 'what are we spending the most on'. "
            "Do NOT use for full P&L."
        ),
        endpoint="/report/expense-by-category",
        params=ExpenseByCategoryParams,
        handler=make_api_handler("/report/expense-by-category"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "dashboard_overview": ToolSpec(
        name="dashboard_overview",
        description=(
            "High-level executive dashboard summary with KPIs and quick health metrics. "
            "Use for: 'business health check', 'executive overview', 'how are we doing'. "
            "Do NOT use for granular invoice lists. "
            "Note: this endpoint is scoped by user, not organization."
        ),
        endpoint="/dashboard/web",
        params=DashboardOverviewParams,
        handler=make_api_handler("/dashboard/web"),
        narrate=True,
        formatter="dashboard_overview",
        intent=7,
    ),
    "income_total": ToolSpec(
        name="income_total",
        description=(
            "Total aggregated sales, invoices revenue, and income total for a year or period. "
            "Use for: 'total sales this year', 'how much revenue in 2026', 'total income'. "
            "Do NOT use for listing itemized invoices (use invoice_list)."
        ),
        endpoint="/income/total",
        params=IncomeTotalParams,
        handler=make_api_handler("/income/total"),
        narrate=True,
        formatter="kv_summary",
        intent=4,
    ),
    "expense_total": ToolSpec(
        name="expense_total",
        description=(
            "Total aggregated expenses, bills amount, and spending total for a year or period. "
            "Use for: 'total expenses this year', 'how much spending in 2026', 'total bills'. "
            "Do NOT use for listing itemized bills (use bill_list)."
        ),
        endpoint="/expense/total",
        params=ExpenseTotalParams,
        handler=make_api_handler("/expense/total"),
        narrate=True,
        formatter="kv_summary",
        intent=4,
    ),
    "invoice_list": ToolSpec(
        name="invoice_list",
        description=(
            "List recent sales invoices with invoice number, customer name, date, due date, status, and total. "
            "Use for: 'list unpaid invoices', 'show recent sales invoices', 'invoices for this month'. "
            "Do NOT use for aggregate total revenue (use income_total)."
        ),
        endpoint="/income/list",
        params=InvoiceListParams,
        handler=make_api_handler("/income/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "invoice_find": ToolSpec(
        name="invoice_find",
        description="Lookup details of a specific invoice by ID.",
        endpoint="/income/find",
        params=InvoiceFindParams,
        handler=make_api_handler("/income/find"),
        narrate=False,
        formatter="kv_summary",
        intent=4,
    ),
    "bill_list": ToolSpec(
        name="bill_list",
        description="List supplier bills and expenses.",
        endpoint="/expense/list_filter",
        params=BillListParams,
        handler=make_api_handler("/expense/list_filter"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "bill_find": ToolSpec(
        name="bill_find",
        description="Lookup details of a specific bill by ID.",
        endpoint="/expense/find",
        params=BillFindParams,
        handler=make_api_handler("/expense/find"),
        narrate=False,
        formatter="kv_summary",
        intent=4,
    ),
    "customer_payments": ToolSpec(
        name="customer_payments",
        description="List customer payments received.",
        endpoint="/income/customer-payment/list",
        params=CustomerPaymentsParams,
        handler=make_api_handler("/income/customer-payment/list"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "supplier_payments": ToolSpec(
        name="supplier_payments",
        description="List supplier payments made.",
        endpoint="/expense/supplier-payment/list",
        params=SupplierPaymentsParams,
        handler=make_api_handler("/expense/supplier-payment/list"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "contact_search": ToolSpec(
        name="contact_search",
        description="Search for a specific customer or vendor by name, email, or contact type.",
        endpoint="/contact/find",
        params=ContactSearchParams,
        handler=make_api_handler("/contact/find"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "contact_count": ToolSpec(
        name="contact_count",
        description="Count total registered customers and vendors.",
        endpoint="/contact/list",
        params=ContactCountParams,
        handler=make_api_handler("/contact/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "item_list": ToolSpec(
        name="item_list",
        description="List catalog items, products, and services.",
        endpoint="/item/list",
        params=ItemListParams,
        handler=make_api_handler("/item/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "item_search": ToolSpec(
        name="item_search",
        description="Search catalog items by keyword.",
        endpoint="/item/list",
        params=ItemSearchParams,
        handler=make_api_handler("/item/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "bank_accounts": ToolSpec(
        name="bank_accounts",
        description="List bank accounts and current balances.",
        endpoint="/bank/manual/accounts",
        params=BankAccountsParams,
        handler=make_api_handler("/bank/manual/accounts"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "uncategorized_transactions": ToolSpec(
        name="uncategorized_transactions",
        description="List bank transactions pending categorization or review.",
        endpoint="/bank/transactions/uncategorized",
        params=UncategorizedTransactionsParams,
        handler=make_api_handler("/bank/transactions/uncategorized"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "chart_of_accounts": ToolSpec(
        name="chart_of_accounts",
        description="List Chart of Accounts and account codes.",
        endpoint="/chart-of-accounts",
        params=ChartOfAccountsParams,
        handler=make_api_handler("/chart-of-accounts"),
        narrate=False,
        formatter="account_tree",
        intent=4,
    ),
    "journal_entries": ToolSpec(
        name="journal_entries",
        description="List manual journal entries.",
        endpoint="/accounting/journal-entries",
        params=JournalEntriesParams,
        handler=make_api_handler("/accounting/journal-entries"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "general_ledger": ToolSpec(
        name="general_ledger",
        description="General Ledger transactions for accounts.",
        endpoint="/accounting/general-ledger",
        params=GeneralLedgerParams,
        handler=make_api_handler("/accounting/general-ledger"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "audit_logs": ToolSpec(
        name="audit_logs",
        description="View system audit logs and user actions.",
        endpoint="/audit-logs",
        params=AuditLogsParams,
        handler=make_api_handler("/audit-logs"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "projects_list": ToolSpec(
        name="projects_list",
        description="List active and archived projects.",
        endpoint="/projects/list",
        params=ProjectsListParams,
        handler=make_api_handler("/projects/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "project_expense_rollup": ToolSpec(
        name="project_expense_rollup",
        description=(
            "Analytical project expense rollup showing project name, vendor contact name, bank account used, "
            "transaction count, and total spend per project. "
            "Use for: 'list all project expenses', 'project spending breakdown', 'expenses by project and vendor'."
        ),
        endpoint="fn_project_expense_rollup",
        params=ProjectExpenseRollupParams,
        handler=make_sql_function_handler("fn_project_expense_rollup"),
        narrate=True,
        formatter="project_expense_rollup",
        intent=4,
    ),
    "inventory_movement": ToolSpec(
        name="inventory_movement",
        description=(
            "Analytical inventory movement summary showing item name, SKU, warehouse location, "
            "units sold from invoices, and units dispatched from delivery notes. "
            "Use for: 'inventory movement', 'items with warehouse and delivery notes', 'units sold and dispatched'."
        ),
        endpoint="fn_inventory_movement",
        params=InventoryMovementParams,
        handler=make_sql_function_handler("fn_inventory_movement"),
        narrate=True,
        formatter="inventory_movement",
        intent=4,
    ),
    "gl_profitability": ToolSpec(
        name="gl_profitability",
        description=(
            "Analytical GL account profitability breakdown by chart of accounts account type with total income, "
            "total expense, and net margin. "
            "Use for: 'GL profitability', 'general ledger margin analysis', 'profitability by account type'."
        ),
        endpoint="fn_gl_profitability",
        params=GLProfitabilityParams,
        handler=make_sql_function_handler("fn_gl_profitability"),
        narrate=True,
        formatter="gl_profitability",
        intent=4,
    ),
    "contact_list": ToolSpec(
        name="contact_list",
        description="List registered contacts filtered by customer or vendor type.",
        endpoint="/contact/list",
        params=ContactListParams,
        handler=make_api_handler("/contact/list"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "trial_balance": ToolSpec(
        name="trial_balance",
        description="Trial balance report showing debit and credit balances for all accounts.",
        endpoint="/report/trial-balance",
        params=TrialBalanceParams,
        handler=make_api_handler("/report/trial-balance"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "bank_rules": ToolSpec(
        name="bank_rules",
        description="List bank categorization and reconciliation rules.",
        endpoint="/bank/rules",
        params=BankRulesParams,
        handler=make_api_handler("/bank/rules"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "chart_of_accounts_list": ToolSpec(
        name="chart_of_accounts_list",
        description="Chart of accounts with type breakdown.",
        endpoint="/chart-of-accounts/list",
        params=ChartOfAccountsListParams,
        handler=make_api_handler("/chart-of-accounts/list"),
        narrate=False,
        formatter="account_tree",
        intent=4,
    ),
    "audit_trails": ToolSpec(
        name="audit_trails",
        description="List audit trails and user activity records.",
        endpoint="/audit-trails",
        params=AuditTrailsParams,
        handler=make_api_handler("/audit-trails"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "branches_list": ToolSpec(
        name="branches_list",
        description="List corporate branches.",
        endpoint="/branches",
        params=BranchesParams,
        handler=make_api_handler("/branches"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "cost_centers_list": ToolSpec(
        name="cost_centers_list",
        description="List cost centers.",
        endpoint="/cost-centers",
        params=CostCentersParams,
        handler=make_api_handler("/cost-centers"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "supported_currencies": ToolSpec(
        name="supported_currencies",
        description="List supported currencies in Accutax.",
        endpoint="/currency/supported",
        params=SupportedCurrenciesParams,
        handler=make_api_handler("/currency/supported"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "exchange_rates": ToolSpec(
        name="exchange_rates",
        description="Current foreign currency exchange rates.",
        endpoint="/currency/exchange-rates",
        params=ExchangeRatesParams,
        handler=make_api_handler("/currency/exchange-rates"),
        narrate=False,
        formatter="row_table",
        intent=4,
    ),
    "vendor_balance_summary": ToolSpec(
        name="vendor_balance_summary",
        description="Outstanding balance summary owed to vendors/suppliers (aged payables).",
        endpoint="/report/aged-payables",
        params=VendorBalanceSummaryParams,
        handler=make_api_handler("/report/aged-payables"),
        narrate=True,
        formatter="aging_buckets",
        intent=4,
    ),
    "sales_by_item": ToolSpec(
        name="sales_by_item",
        description="Sales ranked by item or product.",
        endpoint="/report/sales-by-items",
        params=SalesByItemParams,
        handler=make_api_handler("/report/sales-by-items"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "purchases_by_vendor": ToolSpec(
        name="purchases_by_vendor",
        description="Purchases and vendor spending ranked by supplier.",
        endpoint="/report/purchases-by-vendor",
        params=PurchasesByVendorParams,
        handler=make_api_handler("/report/purchases-by-vendor"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "purchases_by_item": ToolSpec(
        name="purchases_by_item",
        description="Purchases breakdown ranked by item or product.",
        endpoint="/report/purchases-by-item",
        params=PurchasesByItemParams,
        handler=make_api_handler("/report/purchases-by-item"),
        narrate=True,
        formatter="row_table",
        intent=4,
    ),
    "vat_summary": ToolSpec(
        name="vat_summary",
        description="VAT tax return summary report for UAE VAT (5%).",
        endpoint="/report/vat-summary",
        params=VatSummaryParams,
        handler=make_api_handler("/report/vat-summary"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "tax_liability": ToolSpec(
        name="tax_liability",
        description="Tax liability report.",
        endpoint="/report/tax-liability",
        params=TaxLiabilityParams,
        handler=make_api_handler("/report/tax-liability"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "general_ledger_summary": ToolSpec(
        name="general_ledger_summary",
        description="General Ledger summary report across all account groups.",
        endpoint="/report/general-ledger-summary",
        params=GeneralLedgerSummaryParams,
        handler=make_api_handler("/report/general-ledger-summary"),
        narrate=True,
        formatter="row_table",
        intent=3,
    ),
    "profit_loss_with_accounts": ToolSpec(
        name="profit_loss_with_accounts",
        description="Profit and Loss statement with full account-level breakdown.",
        endpoint="/report/profit-loss-with-accounts",
        params=ProfitLossWithAccountsParams,
        handler=make_api_handler("/report/profit-loss-with-accounts"),
        narrate=True,
        formatter="financial_statement",
        intent=3,
    ),
    "answer_directly": ToolSpec(
        name="answer_directly",
        description="Answer how-to questions, accounting definitions, or app guidance directly.",
        endpoint="",
        params=AnswerDirectlyParams,
        handler=_noop_handler,
        narrate=True,
        formatter="",
        intent=1,
    ),
    "unsupported": ToolSpec(
        name="unsupported",
        description="When the query cannot be answered by any registered tools.",
        endpoint="",
        params=UnsupportedParams,
        handler=_noop_handler,
        narrate=True,
        formatter="",
        intent=4,
    ),
}


def gemini_declarations() -> List[Dict[str, Any]]:
    """Build Gemini function declaration dictionaries for all registered tools."""
    decls = []
    for spec in REGISTRY.values():
        schema = spec.params.model_json_schema()
        # Clean schema for Gemini declaration
        props = {}
        for prop_name, prop_def in schema.get("properties", {}).items():
            props[prop_name] = {
                "type": prop_def.get("type", "string").upper(),
                "description": prop_def.get("description", ""),
            }
            if "enum" in prop_def:
                props[prop_name]["enum"] = prop_def["enum"]

        decl = {
            "name": spec.name,
            "description": spec.description,
            "parameters": {
                "type": "OBJECT",
                "properties": props,
                "required": schema.get("required", []),
            },
        }
        decls.append(decl)
    return decls
