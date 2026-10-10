"""Unit tests for Phase 3: Formatters and markdown normalizer."""
import pytest

from gemini_brain.formatting.markdown import normalize_markdown
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


def test_render_empty_or_none_data():
    assert render("row_table", None) == "_No records found._"
    assert render("row_table", []) == "_No records found._"
    assert render("kv_summary", {}) == "_No records found._"


def test_format_aed():
    assert format_aed(1234567.89) == "AED 1,234,567.89"
    assert format_aed("5000") == "AED 5,000.00"
    assert format_aed(0) == "AED 0.00"
    assert format_aed(None) == "—"  # missing is not zero
