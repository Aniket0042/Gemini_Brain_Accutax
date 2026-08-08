"""
api_catalog.py — Compact API endpoint catalog for the Gemini endpoint selector.

Extracted verbatim from ``agents/api_agent.py`` lines 85-420 (the ``_API_CATALOG``
constant).  This is the text-based catalog injected into the Gemini endpoint-
selector prompt — NOT the large ``api-endpoints-catalog.json`` (419 KB), which
is unused by the Brain.
"""
from __future__ import annotations

API_CATALOG: str = """## AVAILABLE REST API ENDPOINTS

### INVOICES / INCOME
GET /income/list
  query: userId* (string — REQUIRED, camelCase), page=1, pageSize=20, status (PAID|PENDING|PARTIALLY_PAID|CANCELLED), payment, search (text), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD), sortBy, sortOrder
  → Paginated list of invoices/income records. NOTE: uses userId (camelCase), NOT user_id or organization_id

GET /income/find
  query: organization_id*, search (invoice number or name)
  → Search for specific income record

GET /income/total
  query: user_id* (string — REQUIRED, snake_case, must be a string like "18"), filter_year* (string — REQUIRED, e.g. "2026"), filter_type* (string — REQUIRED, MUST be one of: YEARLY, QUARTERLY, MONTHLY), organization_id
  → Income totals: returns total_income and total_tax for the specified period
  USE FOR: "total sales", "total revenue", "total income", "how much income", "total invoiced amount", "annual revenue"
  NOTE: Do NOT use start_date/end_date — use filter_year + filter_type instead

GET /income/customer-payment/list
  query: organization_id*, userId (camelCase), page=1, pageSize=20
  → Customer payment records

### EXPENSES / BILLS
GET /expense/list
  query: userId* (string — REQUIRED, camelCase), page=1, pageSize=20, status (PAID|PENDING|PARTIALLY_PAID|CANCELLED), payment, sortBy, sortOrder
  → Paginated list of expense bills. NOTE: uses userId (camelCase), NOT user_id or organization_id

GET /expense/find
  query: organization_id*, search
  → Search for specific expense

GET /expense/total
  query: organization_id*, start_date, end_date, userId (camelCase)
  → Expense totals and summary stats

GET /expense/supplier-payment/list
  query: organization_id*, userId (camelCase), page=1, pageSize=20
  → Supplier/vendor payment records

### BANKING
GET /bank/manual/accounts
  query: organization_id* (required), user_id
  → List of bank accounts with current balances

GET /bank/transactions/uncategorized
  query: organization_id* (required)
  → Uncategorized bank transactions

GET /bank/rules
  query: organization_id* (required)
  → Bank categorization rules

### CONTACTS
GET /contact/list
  query: organization_id* (required), user_id, contact_type_id (4=customer, 1=vendor, 2=vendor, 3=vendor), name, page=1, pageSize=20
  → List of contacts (customers or vendors)

GET /contact/find
  query: organization_id*, search (name or email)
  → Search for a specific contact

### CHART OF ACCOUNTS
GET /chart-of-accounts
  query: organization_id* (required)
  → Full chart of accounts

GET /chart-of-accounts/list
  query: organizationId* (required), userId
  → Chart of accounts with type breakdown

### DASHBOARD
GET /dashboard/web/v3
  query: user_id* (required), organization_id, year (YYYY e.g. "2026"), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Financial dashboard: revenue, expenses, outstanding, key metrics, monthly trend

### ACCOUNTING
GET /accounting/journal-entries
  query: userId* (string — camelCase, REQUIRED), organizationId* (camelCase, REQUIRED), search, page=1, pageSize=20
  → Journal entries list. NOTE: uses userId and organizationId (both camelCase)

GET /accounting/general-ledger
  query: organization_id*, account_code, start_date, end_date
  → General ledger entries

### AUDIT
GET /audit-logs
  query: organization_id*, page=1, limit=50, search, user_id, start_date, end_date
  → Audit log entries (who changed what)

GET /audit-trails
  query: organization_id*, page=1, limit=50
  → Audit trails

### ORGANIZATIONAL
GET /branches
  query: organization_id* (required)
  → Branches list

GET /cost-centers
  query: organization_id* (required)
  → Cost centers

GET /projects/list
  query: organization_id* (required)
  → Projects list

### CURRENCY
GET /currency/supported
  (no params needed)
  → List of supported currencies

GET /currency/exchange-rates
  (no params needed)
  → Current exchange rates

GET /currency/convert
  query: amount (number), targetCurrency (e.g. "USD")
  → Convert amount to target currency

### ITEMS / PRODUCTS
GET /item/list
  query: user_id* (string — REQUIRED, snake_case, must be a quoted string value e.g. "18" not integer 18), page=1, pageSize=20
  NOTE: user_id is required and MUST be a string. Do NOT include organization_id for this endpoint.
  → Items/products list

### FINANCIAL REPORTS
GET /report/profit-loss
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Profit & Loss / income statement for the period
  USE FOR: "P&L", "profit and loss", "income statement", "net profit", "gross profit"

GET /report/balance-sheet
  query: organization_id* (required), start_date, end_date
  → Balance sheet (assets, liabilities, equity)
  USE FOR: "balance sheet", "assets", "liabilities", "equity"

GET /report/cash-flow
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Cash flow statement (direct method)
  USE FOR: "cash flow", "cash movement", "cash in/out"

GET /report/cash-flow-indirect
  query: organization_id* (required), start_date, end_date
  → Cash flow statement (indirect method)

GET /report/cash-forecast
  query: organization_id* (required), start_date, end_date
  → Cash forecast / projected cash position — weekly cash flow projection with overdue, upcoming inflows and outflows
  USE FOR: "cash forecast", "projected cash", "payment forecast", "expected collections", "cash projection", "forecast cash flow", "cash flow forecast", "cash flow next months", "predict cash", "upcoming cash", "future cash position"

GET /report/profit-loss-with-accounts
  query: organization_id* (required), start_date, end_date
  → P&L with full account-level breakdown

### AR / AP AGING REPORTS
GET /report/ar-aging-summary
  query: organization_id* (required), as_of_date (YYYY-MM-DD, defaults to today)
  → Accounts receivable aging summary (current, 30d, 60d, 90d, 120d+ buckets)
  USE FOR: "AR aging", "aging report", "receivables aging", "overdue invoices", "outstanding AR", "defaulters", "who owes us"

GET /report/customer-balance-summary
  query: organization_id* (required)
  → Outstanding balance per customer (who owes what) — shows invoiced_amount, invoice_received, closing_balance per customer
  USE FOR: "customer balances", "top defaulters", "who owes us the most", "customer outstanding", "total outstanding receivables", "total receivables", "outstanding receivables", "total amount owed", "who owes us"

GET /report/statement-of-account
  query: organization_id* (required), contact_id, start_date, end_date
  → Customer statement of account (full history)

GET /report/ap-aging-summary
  query: organization_id* (required), as_of_date (YYYY-MM-DD)
  → Accounts payable aging summary (what we owe vendors)
  USE FOR: "AP aging", "payables aging", "vendor outstanding", "bills overdue"

GET /report/ap-aging-details
  query: organization_id* (required), as_of_date
  → Detailed AP aging per bill

GET /report/aged-payables
  query: organization_id* (required)
  → Aged payables summary

GET /report/supplier-statement-of-account
  query: organization_id* (required), contact_id, start_date, end_date
  → Supplier statement of account

### SALES REPORTS
GET /report/sales-by-customer
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Sales ranked by customer — revenue per customer
  USE FOR: "top customers", "sales by customer", "best customers", "customer revenue ranking"

GET /report/sales-by-items
  query: organization_id* (required), start_date, end_date
  → Sales breakdown by item/product
  USE FOR: "top selling items", "sales by product", "best selling products"

GET /report/sales-by-branch
  query: organization_id* (required), start_date, end_date
  → Revenue by branch

GET /report/sales-by-project
  query: organization_id* (required), start_date, end_date
  → Revenue by project

GET /report/invoice-details
  query: organization_id* (required), start_date, end_date
  → Detailed invoice listing report

### EXPENSE REPORTS
GET /report/expense-by-category
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Expenses grouped by category
  USE FOR: "expense breakdown", "expense by category", "spending by category", "what are we spending on"

GET /report/expenses-by-contact
  query: organization_id* (required), start_date, end_date
  → Expenses grouped by vendor/supplier

GET /report/expenses-by-branch
  query: organization_id* (required), start_date, end_date
  → Expenses by branch

GET /report/purchases-by-vendor
  query: organization_id* (required), start_date, end_date
  → Purchases ranked by vendor
  USE FOR: "top vendors", "purchases by supplier", "spending by vendor"

GET /report/purchases-by-item
  query: organization_id* (required), start_date, end_date
  → Purchases by item

GET /report/bills-by-contact
  query: organization_id* (required), start_date, end_date
  → Bills grouped by contact

GET /report/bills-details
  query: organization_id* (required), start_date, end_date
  → Detailed bills report

### TAX / VAT REPORTS
GET /report/vat-report
  query: organization_id* (required), start_date (YYYY-MM-DD), end_date (YYYY-MM-DD)
  → Full VAT return report (output tax, input tax, net payable)
  USE FOR: "VAT report", "VAT return", "VAT filing"

GET /report/vat-summary
  query: organization_id* (required), start_date, end_date
  → VAT summary (totals by rate)

GET /report/vat-input-output
  query: organization_id* (required), start_date, end_date
  → VAT input vs output breakdown

GET /report/tax-liability
  query: organization_id* (required), start_date, end_date
  → Tax liability report
  USE FOR: "tax liability", "taxes owed", "tax payable"

GET /tax-rate/list
  query: organization_id* (required)
  → List all configured tax rates

GET /vat-config/main
  query: organization_id* (required)
  → Active VAT configuration and rates

### INVENTORY REPORTS
GET /inventory/low-stock
  query: organization_id* (required)
  → Items below reorder level (low stock alerts)
  USE FOR: "low stock", "items running out", "reorder needed"

GET /inventory/quantities
  query: organization_id* (required)
  → Current stock quantities for all items
  USE FOR: "stock levels", "inventory quantities", "how many in stock"

GET /inventory/reports/valuation
  query: organization_id* (required)
  → Inventory valuation (stock value at cost)
  USE FOR: "inventory value", "stock worth", "inventory valuation"

GET /inventory/reports/cogs
  query: organization_id* (required), start_date, end_date
  → Cost of goods sold
  USE FOR: "COGS", "cost of goods sold", "cost of sales"

GET /inventory/reports/movement
  query: organization_id* (required), start_date, end_date
  → Inventory movement (in/out)
  USE FOR: "stock movement", "inventory in/out", "goods received/sold"

GET /inventory/movements
  query: organization_id* (required)
  → Raw inventory movement records

### P&L BY DIMENSION
GET /report/profit-loss-by-branch
  query: organization_id* (required), start_date, end_date
  → P&L broken down by branch

GET /report/profit-loss-by-project
  query: organization_id* (required), start_date, end_date
  → P&L broken down by project

GET /report/profit-loss-by-cost-center
  query: organization_id* (required), start_date, end_date
  → P&L broken down by cost center

GET /report/consolidated-pnl
  query: organization_id* (required), start_date, end_date
  → Consolidated P&L across entities

GET /report/consolidated-balance-sheet
  query: organization_id* (required)
  → Consolidated balance sheet

GET /report/consolidated-cash
  query: organization_id* (required), start_date, end_date
  → Consolidated cash flow

### TOP ENTRIES
GET /report/top-entries
  query: organization_id* (required), start_date, end_date, type (income|expense|contact)
  → Top income / expense entries for a period
  USE FOR: "top transactions", "largest entries", "biggest invoices"

### WAREHOUSES / TRANSFERS
GET /warehouse/list
  query: organization_id* (required)
  → Warehouse list

GET /transfers
  query: organization_id* (required), page=1, limit=20
  → Inventory transfers between warehouses
"""
