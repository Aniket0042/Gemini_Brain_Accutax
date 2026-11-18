"""Rules added after the 18 Nov replay: undated payables, by-month tables, drafts, margins, duplicates, monthly P&L."""
from gemini_brain.agent import prompt
from gemini_brain.config.settings import settings


def test_overdue_with_no_due_dates_is_not_reported_as_none_overdue():
    assert "HAVE a due date" in prompt.RULES
    assert "aging_bucket" in prompt.RULES and "No due date" in prompt.RULES


def test_by_month_for_each_organization_gets_the_full_table():
    assert "periods as rows, one column" in prompt.RULES


def test_no_drafts_and_margin_over_100_percent():
    assert "Write only the final answer" in prompt.RULES
    assert "above 100%" in prompt.RULES


def test_duplicate_numbers_checked_beyond_the_review_rule():
    assert 'contains "DUP" or "COPY"' in prompt.RULES


def test_monthly_pnl_is_one_call():
    assert "ONE query_metrics call" in prompt.RULES


def test_portal_deadline_allows_slow_answers():
    assert settings.agent_deadline_seconds >= 60
