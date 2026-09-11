"""Phrase-matrix tests for delivery detection."""
from gemini_brain.artifacts.delivery import detect_delivery


def test_bar_chart_phrase():
    d = detect_delivery("show as a bar chart")
    assert d.mode == "chart"
    assert d.chart_hint == "bar"
    assert not d.wants_file


def test_graph_revenue():
    d = detect_delivery("graph revenue this year")
    assert d.mode == "chart"
    assert d.wants_chart


def test_export_pnl_excel():
    d = detect_delivery("export P&L to excel")
    assert d.mode == "file"
    assert d.format == "xlsx"


def test_send_me_a_pdf():
    d = detect_delivery("send me a pdf")
    assert d.mode == "file"
    assert d.format == "pdf"


def test_csv_of_overdue():
    d = detect_delivery("csv of overdue invoices")
    assert d.mode == "file"
    assert d.format == "csv"


def test_generic_export_defaults_xlsx():
    d = detect_delivery("download this report")
    assert d.mode == "file"
    assert d.format == "xlsx"


def test_pdf_with_chart_is_both():
    d = detect_delivery("export this as a PDF with a chart")
    assert d.mode == "both"
    assert d.format == "pdf"
    assert d.wants_chart


def test_powerpoint():
    d = detect_delivery("make a powerpoint of the P&L")
    assert d.format == "pptx"
    assert d.wants_file


def test_word_doc():
    d = detect_delivery("give me a word document of this")
    assert d.format == "docx"


def test_chart_of_accounts_is_not_a_graph():
    d = detect_delivery("show the chart of accounts")
    assert d.mode == "none"


def test_chart_of_accounts_plus_pdf():
    d = detect_delivery("export chart of accounts as pdf")
    assert d.mode == "file"
    assert d.format == "pdf"
    assert not d.wants_chart


def test_plain_data_query_is_none():
    d = detect_delivery("what is total income this year?")
    assert d.mode == "none"


def test_pnl_with_trend_opens_chart():
    d = detect_delivery(
        "Generate a detailed profit & loss report for this quarter with trend analysis and key insights."
    )
    assert d.mode == "chart"
    assert d.wants_chart


def test_plain_pnl_statement_is_not_a_graph():
    d = detect_delivery(
        "Generate our profit and loss statement for this fiscal year, broken down by month, "
        "including revenue, cost of sales, operating expenses, and net profit."
    )
    assert d.mode == "none"
    assert not d.wants_chart


def test_explicit_graph_on_pnl_is_a_chart():
    d = detect_delivery("profit and loss with a monthly revenue vs expenses graph")
    assert d.wants_chart


def test_visualize():
    d = detect_delivery("visualize monthly expenses")
    assert d.mode == "chart"


def test_pie_hint():
    d = detect_delivery("pie chart of expenses by category")
    assert d.chart_hint == "pie"
