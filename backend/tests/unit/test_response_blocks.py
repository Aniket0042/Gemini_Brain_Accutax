"""Tests for the structured response-block system (UI rebuild plan, phase 1).

render_blocks() is additive alongside the existing markdown render() — these
tests lock in two things: the ported formatters (kv_summary, row_table)
produce well-formed typed blocks, and every unported formatter still falls
back to a markdown block carrying exactly what render() would have produced,
so a client can always render `blocks` without special-casing formatter names
it doesn't know about yet.
"""
import pytest

from gemini_brain.tools.formatters import (
    render,
    render_account_tree_block,
    render_aging_buckets_blocks,
    render_blocks,
    render_dashboard_overview_blocks,
    render_financial_statement_block,
    render_kpi_grid_block,
    render_table_block,
)


# ── kv_summary -> kpi_grid ──────────────────────────────────────────────

def test_kpi_grid_one_tile_per_key():
    data = {"total_income": 328410.5, "invoice_count": 42}
    blocks = render_blocks("kv_summary", data)
    assert len(blocks) == 1
    assert blocks[0]["type"] == "kpi_grid"
    assert len(blocks[0]["items"]) == 2


def test_kpi_grid_formats_numeric_as_currency():
    block = render_kpi_grid_block({"total_income": 1200.5})
    item = block["items"][0]
    assert item["label"] == "Total Income"
    assert item["value"] == "AED 1,200.50"
    assert item["raw_value"] == 1200.5
    assert item["numeric"] is True


def test_kpi_grid_non_numeric_value_has_no_raw_value():
    block = render_kpi_grid_block({"status": "Active"})
    item = block["items"][0]
    assert item["numeric"] is False
    assert item["raw_value"] is None
    assert item["value"] == "Active"


def test_kpi_grid_formats_period_dict_like_markdown_does():
    block = render_kpi_grid_block({"period": {"start_date": "2026-01-01", "end_date": "2026-12-31"}})
    assert block["items"][0]["label"] == "Period"
    assert block["items"][0]["value"] == "2026-01-01 to 2026-12-31"


def test_kpi_grid_flattens_report_envelope():
    block = render_kpi_grid_block({
        "report": "Total Income",
        "period": {"start_date": "2026-01-01", "end_date": "2026-12-31"},
        "summary": {"total_income": 1612654.5, "invoice_count": 1275},
    })
    labels = {i["label"]: i["value"] for i in block["items"]}
    assert "Report" not in labels
    assert "Summary" not in labels
    assert labels["Period"] == "2026-01-01 to 2026-12-31"
    assert labels["Total Income"] == "AED 1,612,654.50"
    assert labels["Invoice Count"] == "1,275"


def test_kpi_grid_skips_formatted_duplicate_fields():
    block = render_kpi_grid_block({
        "operating_income": 1777028.25,
        "formatted_operating_income": "1777028.25 AED",
        "net_profit_formatted": "1302990.85 AED",
    })
    labels = [i["label"] for i in block["items"]]
    assert "Operating Income" in labels
    assert "Formatted Operating Income" not in labels
    assert "Net Profit Formatted" not in labels


def test_financial_statement_skips_formatted_duplicate_fields():
    block = render_financial_statement_block({
        "operating_income": 100,
        "formatted_operating_income": "100 AED",
        "revenue": {"sales": 80, "formatted_sales": "80 AED"},
    })
    labels = [i["label"] for i in block["items"]]
    assert "Operating Income" in labels
    assert "Formatted Operating Income" not in labels
    assert "Formatted Sales" not in labels
    assert "Sales" in labels


def test_kpi_grid_falls_back_to_table_for_non_dict():
    block = render_kpi_grid_block([{"a": 1}])
    assert block["type"] == "table"


# ── row_table -> table ──────────────────────────────────────────────────

def test_table_block_columns_and_rows():
    rows = [
        {"invoice_number": "INV-1", "amount": 1200.5, "status": "PAID"},
        {"invoice_number": "INV-2", "amount": 340, "status": "DUE"},
    ]
    block = render_table_block(rows)
    assert block["type"] == "table"
    assert [c["key"] for c in block["columns"]] == ["invoice_number", "amount", "status"]
    assert block["rows"][0]["amount"] == "AED 1,200.50"
    assert block["total_rows"] == 2
    assert block["truncated"] is False


def test_journal_table_prefers_business_columns_over_id_and_lines():
    row = {
        "id": 1167289,
        "created_at": "2026-09-08T13:04:58.281Z",
        "journal_number": "JE-2-2026-00026058",
        "reference_number": "PAY-2026-0017",
        "transaction_date": "2026-09-08",
        "description": "Customer Payment PAY-2026-0017 - ABC Corporation",
        "source_type": "PAYMENT",
        "total_debit": 99.0,
        "lines": [{"account": "Cash", "debit": 99}],
    }
    block = render_table_block([row])
    keys = [c["key"] for c in block["columns"]]
    assert "journal_number" in keys
    assert "transaction_date" in keys
    assert "source_type" in keys
    assert "total_debit" in keys
    assert "description" in keys
    assert "id" not in keys
    assert "lines" not in keys
    assert block["rows"][0]["total_debit"] == "AED 99.00"


def test_table_block_amount_columns_are_right_aligned():
    block = render_table_block([{"name": "Acme", "total": 500}])
    aligns = {c["key"]: c["align"] for c in block["columns"]}
    assert aligns["total"] == "right"
    assert aligns["name"] == "left"


def test_table_block_caps_and_flags_truncation():
    rows = [{"id": i, "amount": i} for i in range(10)]
    block = render_table_block(rows, max_rows=5)
    assert len(block["rows"]) == 5
    assert block["total_rows"] == 10
    assert block["truncated"] is True


def test_table_block_unwraps_common_envelope_keys():
    block = render_table_block({"results": [{"id": 1}, {"id": 2}]})
    assert block["type"] == "table"
    assert block["total_rows"] == 2


def test_table_block_scalar_list_gets_single_value_column():
    block = render_table_block(["a", "b", "c"])
    assert block["columns"] == [{"key": "value", "label": "Value", "align": "left"}]
    assert len(block["rows"]) == 3


def test_table_block_honours_requested_count_via_render_blocks():
    rows = [{"id": i, "amount": i} for i in range(10)]
    blocks = render_blocks("row_table", rows, query="top 3 invoices")
    assert len(blocks[0]["rows"]) == 3


# ── account_tree -> table (a pure passthrough to render_table_block) ────

def test_account_tree_block_is_a_table():
    block = render_account_tree_block([{"code": "1000", "name": "Cash"}])
    assert block["type"] == "table"


# ── financial_statement -> kpi_grid with section grouping ────────────────

def test_financial_statement_top_level_items_have_no_section():
    block = render_financial_statement_block({"net_profit": 50000})
    item = block["items"][0]
    assert item["label"] == "Net Profit"
    assert item["value"] == "AED 50,000.00"
    assert item["section"] is None


def test_financial_statement_nested_dict_becomes_a_section():
    data = {"revenue": {"sales": 10000, "services": 5000}}
    block = render_financial_statement_block(data)
    assert len(block["items"]) == 2
    assert all(i["section"] == "Revenue" for i in block["items"])
    assert {i["label"] for i in block["items"]} == {"Sales", "Services"}


def test_financial_statement_period_is_exposed_not_folded_into_a_caption():
    data = {"period": {"start_date": "2026-01-01", "end_date": "2026-03-31"}, "net_profit": 1000}
    block = render_financial_statement_block(data)
    assert block["period"] == "2026-01-01 to 2026-03-31"
    assert len(block["items"]) == 1  # the period dict itself isn't also an item


def test_financial_statement_skips_lists_like_markdown_does():
    block = render_financial_statement_block({"line_items": [1, 2, 3], "total": 100})
    labels = [i["label"] for i in block["items"]]
    assert "Line Items" not in labels
    assert "Total" in labels


# ── aging_buckets -> table(s) + kpi_grid, shape-dependent ────────────────

def test_aging_buckets_report_matrix_shape():
    data = {
        "bins": [{"id": "bin_current", "label": "Current"}, {"id": "bin_30", "label": "30 Days"}],
        "report": [
            {"contact_name": "Acme", "bin_current": 100, "bin_30": 0, "contact_total": 100, "row_type": "data"},
            {"contact_name": "Beta", "bin_current": 0, "bin_30": 50, "contact_total": 50, "row_type": "data"},
            {"contact_total": 150, "row_type": "total"},
        ],
    }
    blocks = render_aging_buckets_blocks(data)
    assert blocks[0]["type"] == "table"
    # bin_30 is all-zero for no row here, so both bucket columns with any
    # nonzero value are kept — Vendor + Current + 30 Days + Total.
    assert [c["key"] for c in blocks[0]["columns"]] == ["contact_name", "bin_current", "bin_30", "total"]
    assert len(blocks[0]["rows"]) == 2
    assert blocks[1]["type"] == "kpi_grid"
    assert blocks[1]["items"][0]["label"] == "Total Outstanding"


def test_aging_detail_bills_are_a_real_table_not_a_kpi_dump():
    """aged-payables-detail ships {report: [bills], bins: [meta]}.

    The generic KPI path used to stringify both lists into one cell, format
    contact/transaction ids as AED, and leave ISO due dates raw.
    """
    data = {
        "bins": [
            {"id": "bin_180_269", "label": "180-269 days", "range": [180, 269]},
            {"id": "bin_30_89", "label": "30-89 days", "range": [30, 89]},
        ],
        "report": [
            {
                "contact": 10,
                "contactName": "IT Equipment Supplier",
                "transactionId": 46,
                "transactionType": "bill",
                "reference": "REC-98978",
                "date": "Thu Feb 12",
                "dueDate": "-",
                "daysOverdue": 211,
                "bucket": "bin_180_269",
                "amount": 600.0,
                "currency": "AED",
                "rowType": "data",
            },
            {
                "contact": 10,
                "contactName": "IT Equipment Supplier",
                "transactionId": 558483,
                "transactionType": "bill",
                "reference": "REP-993376",
                "date": "Mon Jun 29",
                "dueDate": "2026-07-31",
                "daysOverdue": 42,
                "bucket": "bin_30_89",
                "amount": 68.0,
                "currency": "AED",
                "rowType": "data",
            },
        ],
    }
    blocks = render_blocks("row_table", data)
    assert blocks[0]["type"] == "table"
    keys = [c["key"] for c in blocks[0]["columns"]]
    assert keys == ["vendor", "reference", "date", "due_date", "days_overdue", "bucket", "amount"]
    row = blocks[0]["rows"][0]
    assert row["vendor"] == "IT Equipment Supplier"
    assert row["reference"] == "REC-98978"
    assert row["date"] == "Thu Feb 12"
    assert row["days_overdue"] == "211"
    assert row["bucket"] == "180-269 days"
    assert row["amount"] == "AED 600.00"
    assert "AED 10.00" not in str(row)
    assert "AED 46.00" not in str(row)
    assert blocks[0]["rows"][1]["due_date"] == "31 Jul 2026"
    assert blocks[0]["rows"][1]["amount"] == "AED 68.00"
    assert blocks[1]["type"] == "kpi_grid"
    assert blocks[1]["items"][0]["value"] == "AED 668.00"


def test_journal_date_column_is_human_readable():
    block = render_table_block([{
        "journal_number": "JE-1",
        "transaction_date": "2026-09-08",
        "description": "Payment",
        "total_debit": 50,
    }])
    assert block["rows"][0]["transaction_date"] == "8 Sep 2026"
    assert block["rows"][0]["total_debit"] == "AED 50.00"


def test_aging_buckets_report_matrix_empty_data_rows():
    data = {"bins": [], "report": [{"contact_total": 0, "row_type": "total"}]}
    blocks = render_aging_buckets_blocks(data)
    assert blocks == [{"type": "markdown", "text": "_No vendors currently have outstanding aged bills._"}]


def test_aging_buckets_vendors_shape_with_totals():
    data = {
        "vendors": [{"name": "Acme", "balance": 100}],
        "totals": {"aging_buckets": {"0-30": 100, "31-60": 0}, "total_outstanding": 100},
    }
    blocks = render_aging_buckets_blocks(data)
    assert blocks[0]["type"] == "table"
    assert blocks[1]["type"] == "kpi_grid"
    assert any(i["label"] == "Total Outstanding" for i in blocks[1]["items"])


def test_aging_buckets_vendors_shape_empty_list():
    blocks = render_aging_buckets_blocks({"vendors": [], "totals": {}})
    assert blocks[0] == {"type": "markdown", "text": "_No vendors currently have outstanding aged bills._"}


def test_aging_buckets_flat_dict_fallback():
    blocks = render_aging_buckets_blocks({"0-30": 1000, "31-60": 500})
    assert blocks[0]["type"] == "kpi_grid"
    assert {i["label"] for i in blocks[0]["items"]} == {"0-30", "31-60"}


# ── dashboard_overview -> table(s) + kpi_grid ─────────────────────────────

def test_dashboard_overview_graph_and_totals():
    data = {
        "graphData": {"labels": ["Jan", "Feb"], "incomeValues": [100, 200], "expenseValues": [50, 60]},
        "totals": {"income": 300, "expense": 110},
    }
    blocks = render_dashboard_overview_blocks(data)
    assert blocks[0]["type"] == "chart"
    assert blocks[0]["categories"] == ["Jan", "Feb"]
    series_by_name = {s["name"]: s["data"] for s in blocks[0]["series"]}
    assert series_by_name["Income"] == [100.0, 200.0]
    assert series_by_name["Expense"] == [50.0, 60.0]
    assert series_by_name["Cash Flow"] == [0.0, 0.0]  # not supplied in this payload
    assert blocks[1]["type"] == "kpi_grid"


def test_dashboard_overview_falls_back_to_kpi_grid_with_no_graph_or_totals():
    blocks = render_dashboard_overview_blocks({"year": 2026})
    assert blocks[0]["type"] == "kpi_grid"


def test_dashboard_overview_chart_coerces_non_numeric_gracefully():
    data = {"graphData": {"labels": ["Jan"], "incomeValues": ["not a number"]}}
    blocks = render_dashboard_overview_blocks(data)
    assert blocks[0]["series"][0]["data"] == [0.0]


def test_dashboard_overview_no_labels_produces_no_chart():
    data = {"graphData": {"labels": [], "incomeValues": [100]}, "totals": {"income": 100}}
    blocks = render_dashboard_overview_blocks(data)
    assert all(b["type"] != "chart" for b in blocks)
    assert blocks[0]["type"] == "kpi_grid"


# ── render_chart_block ────────────────────────────────────────────────────

def test_chart_block_shape():
    from gemini_brain.tools.formatters import render_chart_block
    block = render_chart_block(
        ["Jan", "Feb"],
        [{"name": "Income", "data": [100.0, 200.0]}],
        chart_type="line",
        x_label="Month",
    )
    assert block == {
        "type": "chart",
        "chart_type": "line",
        "x_label": "Month",
        "categories": ["Jan", "Feb"],
        "series": [{"name": "Income", "data": [100.0, 200.0]}],
    }


# ── Unported formatters: markdown fallback must match render() exactly ──

@pytest.mark.parametrize("formatter", [
    "project_expense_rollup", "inventory_movement", "gl_profitability",
    "not_a_real_formatter",
])
def test_unported_formatter_falls_back_to_matching_markdown(formatter):
    data = {"some_key": 123}
    md = render(formatter, data)
    blocks = render_blocks(formatter, data)
    assert len(blocks) == 1
    assert blocks[0] == {"type": "markdown", "text": md}


# ── Always non-empty, well-formed output ─────────────────────────────────

@pytest.mark.parametrize("data", [None, [], {}])
def test_render_blocks_never_empty_for_empty_input(data):
    blocks = render_blocks("kv_summary", data)
    assert blocks == [{"type": "markdown", "text": "_No records found._"}]


def test_render_blocks_never_raises_on_malformed_data():
    # A string where a dict/list was expected — must degrade, not throw.
    blocks = render_blocks("kv_summary", "not a dict")
    assert isinstance(blocks, list) and len(blocks) >= 1
    assert blocks[0]["type"] in ("table", "markdown")
