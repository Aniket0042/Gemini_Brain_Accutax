"""
schemas.py — Pydantic parameter schemas for all registered Gemini Brain tools.

CRITICAL SECURITY CONSTRAINT:
No schema contains org_id or user_id fields. Tenant parameters are injected
strictly from RequestCtx inside to_query() / to_path_params().
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field

from gemini_brain.router.dates import resolve as resolve_dates
import gemini_brain.router.dates as dates
from gemini_brain.tools.context import RequestCtx


class ProfitLossParams(BaseModel):
    """Parameters for Profit & Loss statement report."""
    period: str = Field(default="this year", description="Time period phrase (e.g. 'this month', 'last quarter', '2026').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class BalanceSheetParams(BaseModel):
    """Parameters for Balance Sheet report."""
    as_of: str = Field(default="today", description="As-of date phrase (e.g. 'today', 'this month', '2026-12-31').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.as_of)
        return {
            "organization_id": ctx.org_id,
            "as_of_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class CashFlowParams(BaseModel):
    """Parameters for Cash Flow Statement."""
    period: str = Field(default="this year", description="Time period phrase (e.g. 'this year', 'last quarter').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class CashForecastParams(BaseModel):
    """Parameters for projected cash forecast."""
    months: int = Field(default=6, ge=1, le=24, description="Number of projection months (1-24).")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {
            "organization_id": ctx.org_id,
            "months": self.months,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ArAgingParams(BaseModel):
    """Parameters for Accounts Receivable Aging report."""
    as_of: str = Field(default="today", description="As-of date (e.g. 'today').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.as_of)
        return {
            "organization_id": ctx.org_id,
            "as_of_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ApAgingParams(BaseModel):
    """Parameters for Accounts Payable Aging report."""
    as_of: str = Field(default="today", description="As-of date (e.g. 'today').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.as_of)
        return {
            "organization_id": ctx.org_id,
            "as_of_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class CustomerBalanceSummaryParams(BaseModel):
    """Parameters for customer balance summary."""
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {
            "organization_id": ctx.org_id,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class SalesByCustomerParams(BaseModel):
    """Parameters for sales breakdown by customer."""
    period: str = Field(default="this year", description="Time period phrase.")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ExpenseByCategoryParams(BaseModel):
    """Parameters for expenses by category breakdown."""
    period: str = Field(default="this year", description="Time period phrase.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class DashboardOverviewParams(BaseModel):
    """Parameters for high-level business health & dashboard metrics.

    /dashboard/web is scoped by user_id, not organization_id — the real API
    doesn't accept an org filter on this endpoint at all (confirmed against
    the live OpenAPI spec). If a user belongs to multiple organizations, this
    dashboard reflects whatever the backend scopes to that user by default,
    not necessarily just the currently-active organization.
    """
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"user_id": ctx.user_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class IncomeTotalParams(BaseModel):
    """Parameters for total sales and income."""
    period: str = Field(default="this year", description="Period phrase (e.g. 'this year', '2026').")
    filter_type: Literal["YEARLY", "QUARTERLY", "MONTHLY"] = Field(default="YEARLY")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "user_id": str(ctx.user_id),
            "filter_year": str(w.date_to.year),
            "filter_type": self.filter_type,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ExpenseTotalParams(BaseModel):
    """Parameters for total expenses."""
    period: str = Field(default="this year", description="Period phrase (e.g. 'this year', '2026').")
    filter_type: Literal["YEARLY", "QUARTERLY", "MONTHLY"] = Field(default="YEARLY")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "user_id": str(ctx.user_id),
            "filter_year": str(w.date_to.year),
            "filter_type": self.filter_type,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class InvoiceListParams(BaseModel):
    """Parameters for listing invoices."""
    period: str = Field(default="this year")
    status: Literal["paid", "unpaid", "overdue", "all"] = Field(default="all")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        q = {
            "userId": ctx.user_id,
            "limit": self.limit,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }
        if self.status != "all":
            q["status"] = self.status
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class InvoiceFindParams(BaseModel):
    """Parameters for retrieving a single invoice."""
    invoice_id: str = Field(..., description="Invoice ID or reference number.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id, "invoice_id": self.invoice_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"id": self.invoice_id}


class BillListParams(BaseModel):
    """Parameters for listing bills and expenses.

    /expense/list_filter requires expense_type (default "EXPENSE" — matches
    ~99.9% of real bill records) and takes organization_id/user_id in snake_case,
    unlike the old fictional /expense/list endpoint this replaced.
    """
    period: str = Field(default="this year")
    status: Literal["paid", "unpaid", "overdue", "all"] = Field(default="all")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        q = {
            "expense_type": "EXPENSE",
            "organization_id": ctx.org_id,
            "user_id": ctx.user_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }
        if self.status != "all":
            q["status"] = self.status
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class BillFindParams(BaseModel):
    """Parameters for finding a single bill."""
    bill_id: str = Field(..., description="Bill ID.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id, "bill_id": self.bill_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"id": self.bill_id}


class CustomerPaymentsParams(BaseModel):
    """Parameters for customer payments list."""
    period: str = Field(default="this year")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class SupplierPaymentsParams(BaseModel):
    """Parameters for supplier payments list."""
    period: str = Field(default="this year")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ContactSearchParams(BaseModel):
    """Parameters for searching contacts / customers / vendors."""
    name: str = Field(default="", description="Name or business search string.")
    contact_type: Optional[Literal["customer", "vendor", "all"]] = Field(default="all")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        q = {"organization_id": ctx.org_id}
        if self.name:
            q["search"] = self.name
        if self.contact_type and self.contact_type != "all":
            q["type"] = self.contact_type
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ContactCountParams(BaseModel):
    """Parameters for counting contacts."""
    contact_type: Optional[str] = None

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ItemListParams(BaseModel):
    """Parameters for listing products and items."""
    sort_by: Optional[Literal["price", "name", "created"]] = None
    order: Literal["asc", "desc"] = "desc"
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        q = {"user_id": str(ctx.user_id), "limit": self.limit}
        if self.sort_by:
            q["sort_by"] = self.sort_by
            q["order"] = self.order
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ItemSearchParams(BaseModel):
    """Parameters for searching catalog items."""
    search: str = Field(..., description="Product / item search string.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"user_id": str(ctx.user_id), "search": self.search, "limit": 20}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class BankAccountsParams(BaseModel):
    """Parameters for bank accounts list."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class UncategorizedTransactionsParams(BaseModel):
    """Parameters for uncategorized / unassigned transactions."""
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id, "limit": self.limit}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ChartOfAccountsParams(BaseModel):
    """Parameters for Chart of Accounts list."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class JournalEntriesParams(BaseModel):
    """Parameters for journal entries."""
    period: str = Field(default="this year")
    limit: int = Field(default=20, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {
            "userId": ctx.user_id,
            "organizationId": ctx.org_id,
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class GeneralLedgerParams(BaseModel):
    """Parameters for General Ledger report."""
    period: str = Field(default="this year")
    account_id: Optional[str] = None

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        q = {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }
        if self.account_id:
            q["account_id"] = self.account_id
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class AuditLogsParams(BaseModel):
    """Parameters for audit logs."""
    period: str = Field(default="this year")
    limit: int = Field(default=20, ge=1, le=100)
    action: Optional[str] = None

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {
            "organization_id": ctx.org_id,
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ProjectsListParams(BaseModel):
    """Parameters for projects list."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class AnswerDirectlyParams(BaseModel):
    """Route to direct conversational assistance without external API calls."""
    topic: str = Field(default="", description="Topic or question summary.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class UnsupportedParams(BaseModel):
    """Route when query cannot be answered by registered tools."""
    reason: str = Field(default="", description="Why no registered tool fits this question.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


# ─────────────────────────────────────────────────────────────
# Phase 4 — Analytical SQL Function Schemas
# ─────────────────────────────────────────────────────────────

class ProjectExpenseRollupParams(BaseModel):
    """Parameters for analytical project expense rollup by vendor and bank account."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', 'last quarter', '2026'.")
    limit: int = Field(default=50, ge=1, le=500, description="Max rows to return.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_sql_args(self, ctx: RequestCtx) -> tuple[Any, ...]:
        w = dates.resolve(self.period)
        return (ctx.org_id, w.date_from, w.date_to)


class InventoryMovementParams(BaseModel):
    """Parameters for analytical inventory movement across warehouse locations, invoices, and delivery notes."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', 'last month', '2026'.")
    limit: int = Field(default=50, ge=1, le=500, description="Max rows to return.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
            "limit": self.limit,
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_sql_args(self, ctx: RequestCtx) -> tuple[Any, ...]:
        w = dates.resolve(self.period)
        return (ctx.org_id, w.date_from, w.date_to)


class GLProfitabilityParams(BaseModel):
    """Parameters for general ledger account type profitability analysis."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', 'last year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_sql_args(self, ctx: RequestCtx) -> tuple[Any, ...]:
        w = dates.resolve(self.period)
        return (ctx.org_id, w.date_from, w.date_to)


# ─────────────────────────────────────────────────────────────
# Extended API Catalog Schemas (Phase B)
# ─────────────────────────────────────────────────────────────

class ContactListParams(BaseModel):
    """Parameters for contact listing (customers or vendors)."""
    contact_type: Optional[Literal["customer", "vendor", "all"]] = Field(default="all", description="Contact type filter.")
    name: Optional[str] = Field(default=None, description="Filter contact by name.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        q: Dict[str, Any] = {"organization_id": ctx.org_id, "user_id": str(ctx.user_id), "page": 1, "pageSize": 20}
        if self.contact_type == "customer":
            q["contact_type_id"] = 4
        elif self.contact_type == "vendor":
            q["contact_type_id"] = 5
        if self.name:
            q["name"] = self.name
        return q

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class TrialBalanceParams(BaseModel):
    """Parameters for Trial Balance financial report."""
    period: str = Field(default="this year", description="Period phrase e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class BankRulesParams(BaseModel):
    """Parameters for listing bank categorization rules."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ChartOfAccountsListParams(BaseModel):
    """Parameters for chart of accounts breakdown."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organizationId": ctx.org_id, "userId": str(ctx.user_id)}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class AuditTrailsParams(BaseModel):
    """Parameters for audit trails."""
    limit: int = Field(default=50, ge=1, le=100)

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id, "limit": self.limit, "page": 1}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class BranchesParams(BaseModel):
    """Parameters for corporate branches."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class CostCentersParams(BaseModel):
    """Parameters for cost centers."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class SupportedCurrenciesParams(BaseModel):
    """Parameters for supported currencies."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ExchangeRatesParams(BaseModel):
    """Parameters for currency exchange rates."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class VendorBalanceSummaryParams(BaseModel):
    """Parameters for outstanding balances owed to vendors."""
    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {"organization_id": ctx.org_id}

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class SalesByItemParams(BaseModel):
    """Parameters for sales breakdown by item / product."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class PurchasesByVendorParams(BaseModel):
    """Parameters for purchases breakdown by vendor."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class PurchasesByItemParams(BaseModel):
    """Parameters for purchases breakdown by item / product."""
    period: str = Field(default="this year", description="Time period e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class VatSummaryParams(BaseModel):
    """Parameters for VAT summary / tax return report."""
    period: str = Field(default="this quarter", description="Period phrase e.g. 'this quarter', 'Q1 2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class TaxLiabilityParams(BaseModel):
    """Parameters for VAT / Tax liability calculation."""
    period: str = Field(default="this year", description="Period phrase e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class GeneralLedgerSummaryParams(BaseModel):
    """Parameters for general ledger summary report."""
    period: str = Field(default="this year", description="Period phrase e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ProfitLossWithAccountsParams(BaseModel):
    """Parameters for P&L with full account breakdown."""
    period: str = Field(default="this year", description="Period phrase e.g. 'this year', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}




# ─────────────────────────────────────────────────────────────────────────────
# Deterministic SQL report parameters (see reports/definitions.py)
#
# These mirror the period/as-of shape of the REST params above so the router
# treats them identically — the only difference is that _retrieve() dispatches
# their `rpt_` endpoint to the report engine instead of an HTTP call.
# ─────────────────────────────────────────────────────────────────────────────

class ReportPeriodParams(BaseModel):
    """Start/end period for a date-ranged SQL report."""
    period: str = Field(default="this year", description="Period phrase e.g. 'this month', 'last quarter', '2026'.")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        w = dates.resolve(self.period)
        return {
            "organization_id": ctx.org_id,
            "start_date": w.date_from.isoformat(),
            "end_date": w.date_to.isoformat(),
        }

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}


class ReportPeriodLimitParams(ReportPeriodParams):
    """Date-ranged SQL report that also ranks, so it takes a row limit and direction."""
    limit: int = Field(default=20, ge=1, le=50, description="Maximum rows to return (1-50).")
    sort_order: str = Field(
        default="desc",
        description=(
            "'desc' for top/highest/most/best (the default), 'asc' for "
            "bottom/lowest/least/worst/smallest."
        ),
    )

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        params = super().to_query(ctx)
        params["limit"] = self.limit
        params["sort_order"] = self.sort_order
        return params


class ReportAsOfParams(BaseModel):
    """Point-in-time parameters for an aging-detail SQL report."""
    as_of: str = Field(default="today", description="As-of date phrase (e.g. 'today').")

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        # Clamp to today: dates.resolve() has no "today" phrase, so it falls
        # through to "this year" and hands back 31 December. Aging measured
        # against a future date inflates days_overdue by up to a year, which is
        # exactly the number these reports exist to get right.
        as_of = min(dates.resolve(self.as_of).date_to, dates.today())
        return {
            "organization_id": ctx.org_id,
            "as_of_date": as_of.isoformat(),
        }


class ReportAsOfLimitParams(ReportAsOfParams):
    """Point-in-time SQL report that also ranks, so it takes a row limit and direction."""
    limit: int = Field(default=20, ge=1, le=50, description="Maximum rows to return (1-50).")
    sort_order: str = Field(
        default="desc",
        description=(
            "'desc' for top/highest/most/worst-overdue (the default), 'asc' for "
            "bottom/lowest/least/smallest."
        ),
    )

    def to_query(self, ctx: RequestCtx) -> Dict[str, Any]:
        params = super().to_query(ctx)
        params["limit"] = self.limit
        params["sort_order"] = self.sort_order
        return params

    def to_path_params(self, ctx: RequestCtx) -> Dict[str, Any]:
        return {}
