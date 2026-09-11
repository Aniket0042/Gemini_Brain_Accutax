"""Unit tests for Phase 3: Formatters, empty answer generator, and markdown normalizer."""
import pytest

from gemini_brain.formatting.markdown import normalize_markdown
from gemini_brain.formatting.empty_answer import subject_for, build_empty_answer
from gemini_brain.tools.formatters import render, format_aed


def test_normalize_markdown_repairs_unclosed_fence():
    broken = "Here is the code:\n```json\n{\"id\": 1}"
    repaired = normalize_markdown(broken)
    assert repaired.endswith("```")
    assert repaired.count("```") == 2


def test_normalize_markdown_repairs_unclosed_bold():
    broken = "Total revenue is **AED 50,000"
    repaired = normalize_markdown(broken)
    assert repaired.endswith("**")
    assert repaired.count("**") == 2


def test_normalize_markdown_unescapes_entities():
    raw = "Sales &amp; Marketing &gt; Operations &lt; 50% &quot;Special&#39;s&quot;"
    cleaned = normalize_markdown(raw)
    assert cleaned == "Sales & Marketing > Operations < 50% \"Special's\""


def test_normalize_markdown_table_blank_lines():
    raw = "Summary before table\n| Col A | Col B |\n|---|---|\n| 1 | 2 |\nText after table"
    cleaned = normalize_markdown(raw)
    assert "Summary before table\n\n| Col A | Col B |" in cleaned
    assert "| 1 | 2 |\n\nText after table" in cleaned


def test_normalize_markdown_empty_or_none():
    assert normalize_markdown(None) == ""
    assert normalize_markdown("") == ""
    assert normalize_markdown("   \n\n  ") == ""


def test_subject_for_discovery():
    class DummyTool:
        name = "fetch_overdue_invoices"

    assert subject_for(None, tool_spec=DummyTool()) == "overdue invoices"
    assert subject_for("/report/ar-aging-summary") == "report ar aging summary"
    assert subject_for(None, None, "give me all bills for Al Futtaim") == "bills"
    assert subject_for(None, None, "what is our total tax liability") == "tax records"
    assert subject_for(None, None, "show something unknown") == "your records"


def test_build_empty_answer():
    ans = build_empty_answer("any overdue invoices?", "invoices")
    assert "invoices" in ans
    assert "confirmed result from your books" in ans
    assert "Suggestions:" in ans
    assert "Try expanding your date range" in ans


def test_render_empty_or_none_data():
    assert render("row_table", None) == "_No records found._"
    assert render("row_table", []) == "_No records found._"
    assert render("kv_summary", {}) == "_No records found._"


def test_format_aed():
    assert format_aed(1234567.89) == "AED 1,234,567.89"
    assert format_aed("5000") == "AED 5,000.00"
    assert format_aed(0) == "AED 0.00"
    assert format_aed(None) == "AED 0.00"
