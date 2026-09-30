"""Phase 1: the typed Report IR — validators, one number formatter, provenance."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.artifacts.generate import render_md
from gemini_brain.artifacts.ir import (
    ChartSection,
    Measure,
    Provenance,
    ReportDocument,
    Series,
    format_value,
    infer_unit,
    provenance_from_result,
    to_decimal,
)
from gemini_brain.artifacts.report_spec import build_report_spec
from gemini_brain.tools.formatters import format_aed


def _chart(**kw):
    base = dict(chart_type="bar", categories=["A", "B"], series=[Series(name="Sales", values=[Decimal(1), Decimal(2)])])
    base.update(kw)
    return ChartSection(**base)


# ── numbers ──────────────────────────────────────────────────────────────────

def test_to_decimal_is_exact_and_missing_aware():
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal("1,234.50") == Decimal("1234.50")
    assert to_decimal(float("nan")) is None
    assert to_decimal(None) is None
    assert to_decimal(True) is None
    assert to_decimal("n/a") is None


def test_format_value_rounds_half_up_and_marks_missing():
    assert format_value(Decimal("0.005"), "money", "AED") == "AED 0.01"
    assert format_value(Decimal("-1234.5"), "money", "AED") == "AED -1,234.50"
    assert format_value(Decimal("1275"), "count") == "1,275"
    assert format_value(Decimal("76"), "percent") == "76.0%"
    assert format_value(None, "money", "AED") == "—"


@pytest.mark.parametrize("value", [0, 12.5, 1612654.5, -38850, 0.005])
def test_report_money_matches_chat_money(value):
    """Canvas/files and the chat answer must print the same figure."""
    assert format_value(to_decimal(value), "money", "AED") == format_aed(value)


def test_infer_unit():
    assert infer_unit("Total Revenue") == "money"
    assert infer_unit("Invoice Count") == "count"
    assert infer_unit("Profit Margin %") == "percent"
    assert infer_unit("Days Overdue") == "days"


def test_money_measure_requires_currency():
    with pytest.raises(ValidationError):
        Measure(label="Revenue", value=Decimal(10), unit="money")
    m = Measure(label="Revenue", value=Decimal(10), unit="money", currency="AED")
    assert m.display == "AED 10.00"


def test_measure_display_cannot_be_hand_written():
    m = Measure(label="Revenue", value=Decimal(10), unit="money", currency="AED", display="AED 99.00")
    assert m.display == "AED 10.00"


# ── chart validators ─────────────────────────────────────────────────────────

def test_pie_must_have_one_series():
    with pytest.raises(ValidationError, match="exactly one series"):
        _chart(chart_type="pie", series=[
            Series(name="Sales", values=[Decimal(1), Decimal(2)]),
            Series(name="Tax", values=[Decimal(1), Decimal(2)]),
        ])


def test_pie_rejects_negatives_and_all_zero():
    with pytest.raises(ValidationError, match="negative"):
        _chart(chart_type="pie", series=[Series(name="Net", values=[Decimal(5), Decimal(-1)])])
    with pytest.raises(ValidationError, match="above zero"):
        _chart(chart_type="donut", series=[Series(name="Net", values=[Decimal(0), None])])


def test_pie_slice_cap():
    cats = [f"C{i}" for i in range(9)]
    with pytest.raises(ValidationError, match="at most 8"):
        _chart(chart_type="pie", categories=cats, series=[Series(name="Sales", values=[Decimal(1)] * 9)])


def test_series_length_must_match_categories():
    with pytest.raises(ValidationError, match="values for 2 categories"):
        _chart(series=[Series(name="Sales", values=[Decimal(1)])])


def test_money_and_count_cannot_share_an_axis():
    with pytest.raises(ValidationError, match="mix units"):
        _chart(series=[
            Series(name="Revenue", values=[Decimal(1), Decimal(2)]),
            Series(name="Invoice Count", values=[Decimal(1), Decimal(2)]),
        ])


def test_colors_must_be_valid_and_complete():
    with pytest.raises(ValidationError):
        _chart(series_colors=["teal"])
    with pytest.raises(ValidationError, match="one color per series"):
        _chart(series_colors=["#0E8A75", "#2A78D6"])
    with pytest.raises(ValidationError, match="point colors"):
        _chart(point_colors=["#0E8A75"])


# ── from_spec: never crash, never pass bad sections through ─────────────────

def test_invalid_chart_is_dropped_with_visible_caveat():
    spec = {
        "title": "Test",
        "kpis": [{"label": "Total Revenue", "value": 100}],
        "charts": [{"chart_type": "pie", "title": "Bad pie", "categories": ["A", "B"],
                    "series": [{"name": "Net", "data": [5, -3]}]}],
        "tables": [],
    }
    doc = ReportDocument.from_spec(spec)
    assert doc.charts == []
    out = doc.to_spec()
    assert any("Bad pie could not be drawn" in n and "negative" in n for n in out["notes"])
    assert out["kpis"][0]["formatted"] == "AED 100.00"


def test_short_series_is_padded_with_gaps_not_zeros():
    doc = ReportDocument.from_spec({
        "title": "T", "charts": [{"chart_type": "line", "categories": ["Jan 2026", "Feb 2026", "Mar 2026"],
                                   "series": [{"name": "Revenue", "data": [10, 20]}]}],
    })
    assert doc.to_spec()["charts"][0]["series"][0]["data"] == [10.0, 20.0, None]


def test_non_numeric_kpi_keeps_its_text():
    doc = ReportDocument.from_spec({"title": "T", "kpis": [
        {"label": "Status", "value": None, "formatted": "Active"},
        {"label": "Total Revenue", "value": 5},
    ]})
    kpis = doc.to_spec()["kpis"]
    assert kpis[0]["formatted"] == "Active"


def test_builder_output_always_validates():
    """Every chart the builder emits must survive the IR validators."""
    rows = [{"customer_name": f"Customer {i}", "sales": 100 * i, "invoice_count": i} for i in range(1, 15)]
    for hint in (None, "bar", "line", "area", "pie", "donut"):
        spec = build_report_spec(rows, f"{hint or ''} chart of sales by customer", chart_hint=hint)
        doc = ReportDocument.from_spec(spec)
        assert len(doc.charts) == len(spec["charts"]), hint


# ── provenance ───────────────────────────────────────────────────────────────

def test_provenance_from_runner_result():
    result = {
        "data_source": {"tier": "live_api", "endpoint": "/report/sales-by-customer", "row_count": 42, "truncated": False},
        "notice": {"code": "WIDENED_WINDOW", "message": "No sales this month, so this covers the last 3 months."},
    }
    p = provenance_from_result(result)
    assert p.tier == "live_api" and p.row_count == 42
    line = p.source_line()
    assert "Accutax ledger (live API)" in line and "42 records" in line


def test_window_notice_becomes_a_caveat_on_every_output():
    p = Provenance(tier="sql_report", endpoint="rpt_aged_receivables_detail", row_count=3,
                   notice_code="WIDENED_WINDOW", notice_message="Showing the last 90 days instead.")
    rows = [{"name": "Acme", "amount": 100}, {"name": "Beta", "amount": 50}]
    out = attach_delivery("chart of amount by customer as markdown", rows, [], provenance=p)
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    assert spec["callouts"][0] == {"tone": "caveat", "text": "Showing the last 90 days instead."}
    assert spec["source"].startswith("Source: Accutax ledger (direct report query) · 3 records")
    md = render_md(spec).decode("utf-8")
    assert "## About this report" in md
    assert "Showing the last 90 days instead." in md
    assert "Report reference: rpt_aged_receivables_detail" in md


def test_canvas_and_files_read_the_validated_document():
    rows = [{"name": "Acme", "amount": 100}, {"name": "Beta", "amount": 50}]
    out = attach_delivery("pie chart of amount by customer", rows, [])
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    assert "chart" in spec["section_order"] and "methodology" in spec["section_order"]
    chart = spec["charts"][0]
    assert chart["chart_type"] == "pie"
    assert chart["unit"] == "money"
    assert len(chart["point_colors"]) == len(chart["categories"])


@pytest.mark.parametrize("query,title", [
    ("donut chart of sales by customer as pdf", "Sales by Customer"),
    ("Can you show me the top 5 customers by revenue?", "Top 5 Customers by Revenue"),
    ("show me expenses by category in a pie chart", "Expenses by Category"),
    ("VAT summary for Q2 2026", "VAT Summary for Q2 2026"),
    ("Linecraft sales", "Linecraft Sales"),
])
def test_title_names_the_subject_not_the_request(query, title):
    rows = [{"name": "Acme", "amount": 100}]
    assert build_report_spec(rows, query)["title"] == title
