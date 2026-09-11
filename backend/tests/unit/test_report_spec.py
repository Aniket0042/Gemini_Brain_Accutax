"""Tests for any-shape ReportSpec normalization."""
from gemini_brain.artifacts.report_spec import build_report_spec, spec_has_content


def test_empty_payload():
    spec = build_report_spec({}, "show me a chart")
    assert spec["kpis"] == []
    assert spec["charts"] == []
    assert spec["tables"] == []
    assert spec_has_content(spec) is False
    assert spec["notes"]


def test_scalar_dict_becomes_kpis():
    spec = build_report_spec({"total_income": 1612654.5, "invoice_count": 1275}, "totals")
    labels = {k["label"]: k["formatted"] for k in spec["kpis"]}
    assert labels["Total Income"] == "AED 1,612,654.50"
    assert labels["Invoice Count"] == "1,275"
    chart = spec["charts"][0]
    assert chart["bar_colors"]
    assert len(chart["bar_colors"]) == len(chart["categories"])
    assert len(set(chart["bar_colors"])) == len(chart["categories"])


def test_pnl_envelope():
    spec = build_report_spec(
        {
            "report": "Total Income",
            "period": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
            "summary": {"total_income": 1612654.5, "invoice_count": 1275},
        },
        "P&L",
    )
    assert spec["title"] == "Total Income"
    assert spec["period"] == "2026-01-01 to 2026-12-31"
    labels = {k["label"] for k in spec["kpis"]}
    assert "Total Income" in labels
    assert "Invoice Count" in labels


def test_invoice_list_becomes_table_and_chart():
    rows = [
        {"contact_name": "Acme", "amount": 5000, "status": "PAID"},
        {"contact_name": "Beta", "amount": 3000, "status": "UNPAID"},
        {"contact_name": "Gamma", "amount": 1000, "status": "PAID"},
    ]
    spec = build_report_spec(rows, "graph outstanding by customer")
    assert spec["tables"]
    assert spec["tables"][0]["total_rows"] == 3
    assert spec["charts"]
    assert spec["charts"][0]["chart_type"] in ("bar", "line", "area", "pie")
    assert "Acme" in spec["charts"][0]["categories"]


def test_dashboard_graph_data():
    data = {
        "graphData": {
            "labels": ["Jan", "Feb"],
            "incomeValues": [100, 200],
            "expenseValues": [50, 60],
        },
        "totals": {"income": 300, "expense": 110},
        "dateRange": {"start": "2026-01-01", "end": "2026-02-28"},
    }
    spec = build_report_spec(data, "chart the dashboard")
    assert spec["charts"]
    chart = spec["charts"][0]
    assert chart["categories"] == ["Jan", "Feb"]
    names = {s["name"] for s in chart["series"]}
    assert "Income" in names
    assert "Expense" in names
    kpi_labels = {k["label"] for k in spec["kpis"]}
    assert "Income" in kpi_labels


def test_nested_items_list():
    spec = build_report_spec(
        {"items": [{"name": "A", "amount": 10}, {"name": "B", "amount": 20}]},
        "table please",
    )
    assert spec["tables"][0]["total_rows"] == 2


def test_too_many_categories_collapse_to_other():
    rows = [{"name": f"C{i}", "amount": 100 - i} for i in range(20)]
    spec = build_report_spec(rows, "bar chart of customers")
    cats = spec["charts"][0]["categories"]
    assert len(cats) <= 12
    assert "Other" in cats


def test_pie_hint_small_set():
    rows = [{"category": "Rent", "amount": 40}, {"category": "Payroll", "amount": 60}]
    spec = build_report_spec(rows, "pie chart of expenses by category", chart_hint="pie")
    assert spec["charts"][0]["chart_type"] == "pie"


def test_time_series_prefers_line():
    rows = [
        {"month": "2026-01", "income": 100},
        {"month": "2026-02", "income": 120},
        {"month": "2026-03", "income": 90},
    ]
    spec = build_report_spec(rows, "show the trend")
    assert spec["charts"][0]["chart_type"] in ("line", "area")


def test_nested_pnl_builds_grouped_bar_and_tables():
    spec = build_report_spec(
        {
            "statement": "profit_and_loss",
            "period": {"from": "2026-04-01", "to": "2026-06-30", "label": "Q2 2026"},
            "entity": "AccuTax Client Co.",
            "revenue": {"total": 174300, "line_items": [
                {"name": "Services", "amount": 174300},
            ]},
            "expenses": {
                "total": 114660,
                "line_items": [
                    {"name": "Cost of Goods", "amount": 52290},
                    {"name": "Payroll", "amount": 42600},
                    {"name": "Overheads", "amount": 17430},
                    {"name": "Marketing", "amount": 2340},
                ],
            },
            "net_profit": 59640,
            "margin_pct": 34.2,
            "monthly": [
                {"month": "April", "revenue": 52400, "expenses": 34100},
                {"month": "May", "revenue": 58200, "expenses": 36800},
                {"month": "June", "revenue": 63700, "expenses": 41900},
            ],
        },
        "Generate a detailed profit & loss report",
    )
    assert spec["charts"]
    chart = spec["charts"][0]
    assert chart["chart_type"] == "bar"
    assert chart["categories"] == ["April", "May", "June"]
    names = {s["name"] for s in chart["series"]}
    assert "Revenue" in names
    assert "Expenses" in names
    labels = {k["label"] for k in spec["kpis"]}
    assert "Total Revenue" in labels
    assert "Net Profit" in labels
    titles = {t["title"] for t in spec["tables"]}
    assert "Monthly Breakdown" in titles
    assert spec["period"] == "Q2 2026"
    assert spec["entity"] == "AccuTax Client Co."
    assert spec["charts"][0].get("caption")


def test_sql_pnl_summary_charts_without_monthly():
    spec = build_report_spec(
        {
            "success": True,
            "results": [
                {"account_name": "Sales", "account_type": "Revenue", "net_amount": 50000},
                {"account_name": "Rent", "account_type": "Expense", "net_amount": -12000},
            ],
            "period": "Q2 2026",
            "summary": {
                "total_revenue": 50000,
                "total_expenses": 12000,
                "net_profit": 38000,
                "profit_margin_pct": 76.0,
            },
        },
        "P&L report",
    )
    assert spec["charts"]
    assert spec["kpis"]
    assert spec["tables"]
