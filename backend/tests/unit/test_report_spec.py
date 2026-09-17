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


def test_single_row_category_pie_chart():
    data = [{"category_id": 1, "category_name": "Uncategorized", "total_expense": 6720.0}]
    spec = build_report_spec(data, "Show me a pie chart of expenses by category", chart_hint="pie")
    assert spec["charts"]
    chart = spec["charts"][0]
    assert chart["chart_type"] == "pie"
    assert chart["categories"] == ["Uncategorized"]
    assert chart["series"][0]["data"] == [6720.0]


def test_kpi_excludes_database_ids():
    data = {"category_id": 1, "user_id": 42, "total_expense": 6720.0, "status": "active"}
    spec = build_report_spec(data, "show summary")
    labels = {k["label"] for k in spec["kpis"]}
    assert "Category Id" not in labels
    assert "User Id" not in labels
    assert "Total Expense" in labels


def test_pnl_monthly_with_line_hint():
    data = {
        "statement": "profit_and_loss",
        "monthly": [
            {"month": "2026-01", "revenue": 100000, "expenses": 60000},
            {"month": "2026-02", "revenue": 120000, "expenses": 70000},
        ],
    }
    spec = build_report_spec(data, "Plot monthly income trend for this year", chart_hint="line")
    assert spec["charts"]
    chart = spec["charts"][0]
    assert chart["chart_type"] == "line"
    assert chart["categories"] == ["2026-01", "2026-02"]


def test_formatted_sales_display_noise_excluded():
    rows = [
        {"customer_id": 1, "customer_name": "Apex Retail", "sales": 220500, "formatted_sales": "220,500.00 AED"},
        {"customer_id": 2, "customer_name": "Falcon Energy", "sales": 191310, "formatted_sales": "191,310.00 AED"},
        {"customer_id": 3, "customer_name": "Al Habtoor", "sales": 181755, "formatted_sales": "181,755.00 AED"},
    ]
    # Bar chart must only have 1 single numeric series ('Sales'), not duplicate 'Formatted Sales'
    spec = build_report_spec(rows, "show me top 5 customers with their spend in bar graph", chart_hint="bar")
    assert spec["charts"]
    chart = spec["charts"][0]
    assert chart["chart_type"] == "bar"
    assert len(chart["series"]) == 1
    assert chart["series"][0]["name"] == "Sales"
    assert chart["series"][0]["data"] == [220500.0, 191310.0, 181755.0]
    assert "bar_colors" in chart  # Single series gets pastel colors for each customer

    # Pie chart hint must succeed and not be downgraded by a duplicate series
    pie_spec = build_report_spec(rows, "pie chart of top customers", chart_hint="pie")
    assert pie_spec["charts"]
    pie_chart = pie_spec["charts"][0]
    assert pie_chart["chart_type"] == "pie"
    assert len(pie_chart["series"]) == 1
    assert pie_chart["categories"] == ["Apex Retail", "Falcon Energy", "Al Habtoor"]


def test_top_n_explicit_limit_in_charts_and_tables():
    raw_rows = [
        {"customer_name": "Dubai Logistics FZCO", "sales": 166635.0},
        {"customer_name": "Emirates Tech Solutions LLC", "sales": 174405.0},
        {"customer_name": "Burj Digital Consultancies", "sales": 161280.0},
        {"customer_name": "Falcon Energy & Engineering", "sales": 191310.0},
        {"customer_name": "Apex Retail Group LLC", "sales": 220500.0},
        {"customer_name": "Al Habtoor Trading Co.", "sales": 181755.0},
        {"customer_name": "Gulf Creative Media Agency", "sales": 176190.0},
        {"customer_name": "Oasis Hospitality Services", "sales": 158400.0},
    ]
    # When user asks for top 5, chart must have exactly 5 slices sorted descending
    pie_spec = build_report_spec(raw_rows, "Top 5 customers by revenue share", chart_hint="pie")
    assert pie_spec["charts"]
    pie = pie_spec["charts"][0]
    assert pie["chart_type"] == "pie"
    assert len(pie["categories"]) == 5
    assert pie["categories"] == [
        "Apex Retail Group LLC",
        "Falcon Energy & Engineering",
        "Al Habtoor Trading Co.",
        "Gulf Creative Media Agency",
        "Emirates Tech Solutions LLC",
    ]
    assert pie["series"][0]["data"] == [220500.0, 191310.0, 181755.0, 176190.0, 174405.0]

    # Bar chart must also have exactly 5 bars sorted descending
    bar_spec = build_report_spec(raw_rows, "show me top 5 customers with their spend in bar graph", chart_hint="bar")
    assert bar_spec["charts"]
    bar = bar_spec["charts"][0]
    assert bar["chart_type"] == "bar"
    assert len(bar["categories"]) == 5
    assert bar["categories"] == pie["categories"]
    assert bar["series"][0]["data"] == pie["series"][0]["data"]
    # Table must also be limited to 5 and sorted
    assert len(bar_spec["tables"][0]["rows"]) == 5
    assert bar_spec["tables"][0]["rows"][0]["customer_name"] == "Apex Retail Group LLC"



