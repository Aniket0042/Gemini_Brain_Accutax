"""Golden report fixtures: realistic payloads, each with the request that produced it.

Shared by the golden snapshot tests, the property tests, the chart-intent and
narrative evaluations, visual regression, and scripts/eval/report_eval.py.
Every fixture is plain data — no network, no database, no model.
"""
from __future__ import annotations

from typing import Any, Dict, List

MONTHLY = [
    {"month": "April", "revenue": 52400, "expenses": 34100},
    {"month": "May", "revenue": 58200, "expenses": 36800},
    {"month": "June", "revenue": 63700, "expenses": 43760},
]

PNL = {
    "statement": "profit_and_loss",
    "period": {"label": "Q2 2026"},
    "entity": "AccuTax Client Co.",
    "revenue": {"total": 174300, "line_items": [{"name": "Services", "amount": 174300}]},
    "expenses": {"total": 114660, "line_items": [
        {"name": "Cost of Goods", "amount": 52290},
        {"name": "Payroll", "amount": 42600},
        {"name": "Overheads", "amount": 17430},
        {"name": "Marketing", "amount": 2340},
    ]},
    "net_profit": 59640,
    "margin_pct": 34.2,
    "monthly": MONTHLY,
}

CUSTOMERS = [
    {"customer_name": n, "sales": v} for n, v in [
        ("Apex Retail Trading LLC", 220500), ("Falcon Energy", 191310), ("Al Habtoor Group", 181755),
        ("Marina Foods", 96400), ("Desert Labs", 72210), ("Gulf Motors", 51000), ("Nakheel Co", 33000),
        ("Blue Wave", 21000), ("Oasis Traders", 9000), ("Zenith Supplies", 4000),
    ]
]

MANY_CUSTOMERS = [{"customer_name": f"Customer {chr(65 + i)}", "sales": 1000 * (20 - i)} for i in range(15)]

INVOICES = [
    {"invoice_number": f"INV-{1000 + i}", "customer_name": f"Customer with a fairly long trading name {i} LLC",
     "issue_date": "2026-03-01", "due_date": "2026-04-01", "amount": round(1234.5 * i, 2), "status": "UNPAID"}
    for i in range(1, 80)
]

AGING = [
    {"customer_name": "Apex Retail Trading LLC", "current": 12000, "days_1_30": 8000, "days_31_60": 4000, "days_over_90": 15000, "outstanding_amount": 39000},
    {"customer_name": "Falcon Energy", "current": 5000, "days_1_30": 0, "days_31_60": 2500, "days_over_90": 0, "outstanding_amount": 7500},
    {"customer_name": "Marina Foods", "current": 0, "days_1_30": 3000, "days_31_60": 0, "days_over_90": 9000, "outstanding_amount": 12000},
]

VAT_MONTHLY = [
    {"month": "April", "vat_amount": 2620, "net_sales": 52400},
    {"month": "May", "vat_amount": 2910, "net_sales": 58200},
    {"month": "June", "vat_amount": 3185, "net_sales": 63700},
]

#: name -> (request, payload, chart hint the delivery layer would detect)
FIXTURES: Dict[str, Dict[str, Any]] = {
    "pnl_full": {"query": "P&L report for Q2 as pdf", "data": PNL},
    "pnl_waterfall": {"query": "show the P&L waterfall", "data": PNL},
    "pnl_monthly_only": {"query": "P&L report as pdf", "data": {"statement": "profit_and_loss", "monthly": MONTHLY}},
    "pnl_zero_revenue": {"query": "P&L report as pdf", "data": {
        "statement": "profit and loss", "total_revenue": 0, "gross_profit": 900, "total_expenses": 100}},
    "pnl_net_mismatch": {"query": "P&L report as pdf", "data": dict(PNL, net_profit=70000)},
    "pnl_subtotal_double_count": {"query": "P&L report as pdf", "data": dict(PNL, expenses={
        "total": 114660, "line_items": PNL["expenses"]["line_items"] + [{"name": "Subtotal", "amount": 114660}]})},
    "pnl_missing_month": {"query": "P&L report as pdf", "data": {"statement": "profit_and_loss", "monthly": [
        {"month": "April", "revenue": 52400, "expenses": 34100},
        {"month": "May", "revenue": None, "expenses": 36800},
        {"month": "June", "revenue": 63700, "expenses": 43760}]}},
    "dashboard_graph": {"query": "income vs expense trend chart", "data": {"graphData": {
        "labels": ["Jan 2026", "Feb 2026", "Mar 2026"], "incomeValues": [100000, 120000, 90000],
        "expenseValues": [60000, 65000, 70000], "cashflowValues": [40000, 55000, 20000]}}},
    "customers_ranking": {"query": "sales by customer chart", "data": CUSTOMERS},
    "customers_bar_explicit": {"query": "bar chart of sales by customer", "data": CUSTOMERS},
    "customers_pie": {"query": "pie chart of sales by customer", "data": CUSTOMERS},
    "customers_donut_many": {"query": "donut of sales by customer", "data": MANY_CUSTOMERS},
    "customers_breakdown": {"query": "breakdown chart of sales by customer", "data": CUSTOMERS[:6]},
    "customers_horizontal": {"query": "horizontal bar of sales by customer", "data": CUSTOMERS[:4]},
    "top5_customers": {"query": "top 5 customers by sales as pdf", "data": CUSTOMERS},
    "invoices_long": {"query": "overdue invoices as pdf", "data": INVOICES},
    "aging_outstanding": {"query": "outstanding receivables by customer chart", "data": AGING},
    "vat_trend": {"query": "VAT by month line chart", "data": VAT_MONTHLY},
    "vat_stacked": {"query": "stacked bar of vat and net sales by month", "data": VAT_MONTHLY},
    "revenue_stacked_refused": {"query": "stacked bar of revenue and expenses by month",
                                "data": {"statement": "profit_and_loss", "monthly": MONTHLY}},
    "negative_pie_refused": {"query": "pie of net amount by account", "data": [
        {"account_name": "Sales", "net_amount": 50000}, {"account_name": "Refunds", "net_amount": -4200},
        {"account_name": "Other Income", "net_amount": 3100}]},
    "accounting_negatives": {"query": "chart of balance by account", "data": [
        {"account_name": "Bank", "balance": "AED 12,400.00"}, {"account_name": "Loan", "balance": "(8,000.00)"},
        {"account_name": "Cash", "balance": "1,250.50"}]},
    "kpis_mixed_units": {"query": "summary of totals chart", "data": {
        "total_income": 1612654.5, "invoice_count": 1275, "total_expense": 902330.25}},
    "arabic_names": {"query": "sales by customer chart", "data": [
        {"customer_name": "شركة النخيل للتجارة", "sales": 84000}, {"customer_name": "مؤسسة الواحة", "sales": 51000},
        {"customer_name": "Gulf Motors", "sales": 22000}]},
    "usd_currency": {"query": "revenue by region chart", "data": {"currency": "USD", "items": [
        {"region": "North", "revenue": 125000}, {"region": "South", "revenue": 98000}, {"region": "West", "revenue": 43000}]}},
    "empty_rows": {"query": "sales by customer chart", "data": []},
}


def fixture_names() -> List[str]:
    return list(FIXTURES)
