"""Multi-org periods, ordering, per-org verification and repeated model output.

Cases come from the role-based manual test run: "FY 2025-26" and "since
January 2025" were read as the year 2025, "this year" changed meaning between
answers, "rank by expenses" ordered by cash, a wrong 41.40-point gap showed as
verified, and one list answer repeated its bullets eight times.
"""
from __future__ import annotations

import datetime
from unittest.mock import patch

import pytest

from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results
from gemini_brain.orchestrator.multi_org_dates import period_phrase, resolve_window
from gemini_brain.orchestrator.multi_org_lists import document_filters
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    build_multi_comparison,
    computed_answer,
    limit_rows,
    match_metrics,
    metrics_selections,
    ranking_metric,
)
from gemini_brain.policy.verifier import verify_attributed
from gemini_brain.resilience.envelope import normalize_envelope
from gemini_brain.resilience.output_guard import strip_repeated_lines
from gemini_brain.router import dates

TODAY = datetime.date(2026, 9, 29)


@pytest.fixture(autouse=True)
def _fixed_today():
    with patch.object(dates, "today", return_value=TODAY):
        yield


def _w(text):
    start, end = resolve_window(text)
    return start.isoformat(), end.isoformat()


# ── Periods ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text, window", [
    ("Compare revenue this year", ("2026-01-01", "2026-09-29")),
    ("compare revenue", ("2026-01-01", "2026-09-29")),
    ("revenue year to date", ("2026-01-01", "2026-09-29")),
    ("P&L for each entity for FY 2025-26", ("2025-04-01", "2026-03-31")),
    ("Compare revenue FY2025", ("2025-01-01", "2025-12-31")),
    ("Monthly net profit trend since January 2025", ("2025-01-01", "2026-09-29")),
    ("revenue between 1 Jan 2026 and 31 Mar 2026", ("2026-01-01", "2026-03-31")),
    ("revenue from 01/02/2026 to 15/03/2026", ("2026-02-01", "2026-03-15")),
    ("revenue between 31 Mar 2026 and 1 Jan 2026", ("2026-01-01", "2026-03-31")),
    ("revenue in March 2025", ("2025-03-01", "2025-03-31")),
    ("revenue next quarter", ("2026-10-01", "2026-12-31")),
    ("revenue in 2019", ("2019-01-01", "2019-12-31")),
])
def test_period_windows(text, window):
    assert _w(text) == window


def test_this_year_never_includes_future_dated_documents():
    # Org4 showed 16.0M (1 Jan - 31 Dec) in one answer and 12.18M (to date) in the next.
    assert _w("Compare revenue this year")[1] == _w("compare revenue")[1] == "2026-09-29"


def test_amount_range_is_not_a_year():
    assert period_phrase("bills between 2000 and 5000 AED") is None
    f = document_filters("List bills between 2000 and 5000 AED")
    assert (f["min_amount"], f["max_amount"]) == (2000.0, 5000.0)


def test_since_phrase_is_swapped_whole_in_follow_ups():
    assert period_phrase("revenue since January 2025") == "since January 2025"


@pytest.mark.parametrize("question, start, basis", [
    ("Revenue and profit growth quarter on quarter", "2026-07-01", "period"),
    ("How did expenses change compared to the previous quarter?", "2026-07-01", "period"),
    ("revenue growth month on month", "2026-09-01", "period"),
    ("Compare revenue this year vs last year", "2026-01-01", "year"),
])
def test_growth_compares_the_period_it_names(question, start, basis):
    qp = metrics_selections(match_metrics(question), question)[0]["query_params"]
    assert (qp["start_date"], qp["basis"]) == (start, basis)


# ── Ordering by the figure named ─────────────────────────────────────────────

def _run(oid, **summary):
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "results": [{"summary": summary}]})


def test_rank_by_orders_by_that_figure_and_direction():
    q = "Which entity burns the most cash? Rank by expenses last quarter, lowest first"
    metrics = match_metrics(q)
    assert ranking_metric(metrics, q) == "expenses"
    runs = [_run(1, total_balance=900.0, total_expenses=50.0, bill_count=1),
            _run(2, total_balance=100.0, total_expenses=10.0, bill_count=1),
            _run(3, total_balance=500.0, total_expenses=30.0, bill_count=1)]
    comp = build_multi_comparison(metrics, runs, q, {})
    assert [r["organization"] for r in comp["rows"]] == ["Org2", "Org3", "Org1"]
    assert "Ordered by total expenses, lowest first." in computed_answer(comp)


def test_top_n_on_multi_metric_uses_the_ranking_figure():
    q = "Compare cash and revenue, ranked by revenue, top 1"
    runs = [_run(1, total_balance=900.0, total_income=5.0, invoice_count=1),
            _run(2, total_balance=100.0, total_income=50.0, invoice_count=1)]
    comp = limit_rows(build_multi_comparison(match_metrics(q), runs, q, {}), q)
    assert [r["organization"] for r in comp["rows"]] == ["Org2"]


def test_without_rank_by_the_first_figure_orders():
    q = "Compare revenue and expenses"
    assert ranking_metric(match_metrics(q), q) == "revenue"


# ── Verification per organization ────────────────────────────────────────────

ORG_DATA = {
    "Org1": [{"summary": {"profit_margin_pct": 100.0}}],
    "Org4": [{"summary": {"profit_margin_pct": 89.63}}],
    "Org9": [{"summary": {"overdue_amount": 4125316.0}}],
    "Org5": [{"summary": {"overdue_amount": 10802905.0}}],
}


def test_wrong_gap_between_two_orgs_is_not_verified():
    # The gap between 100.00 and 89.63 is 10.37; "41.40" used to match some other ratio.
    report = verify_attributed("- The gap between Org1 (100.00%) and Org4 (89.63%) is 41.40 percentage points.", ORG_DATA)
    assert "41.40" in report.unmatched
    assert report.method == "attributed"


def test_right_gap_between_two_orgs_is_verified():
    report = verify_attributed("- The gap between Org1 and Org4 is 10.37 percentage points.", ORG_DATA)
    assert report.grounded


def test_figure_of_another_org_is_misattributed():
    report = verify_attributed("- Org9 has the highest overdue receivables with 10,802,905.00 AED.", ORG_DATA)
    assert report.misattributed == ["10,802,905.00 (belongs to Org5, not Org9)"]


def test_org1_line_is_not_read_as_org10():
    data = {"Org1": [{"v": 111.0}], "Org10": [{"v": 222.0}]}
    assert verify_attributed("- Org10: 222.00", data).grounded
    assert not verify_attributed("- Org1: 222.00", data).grounded


class _Model:
    def __init__(self, answer):
        self.answer = answer

    def _call_llm(self, *args, **kwargs):
        return self.answer, 10, 5


def _plain_run(oid, value):
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "answer": "", "blocks": [],
                                                   "results": [{"amount_due": value}]})


def test_model_answer_with_untraceable_figure_gets_a_visible_note():
    runs = [_plain_run(1, 500.0), _plain_run(2, 300.0)]
    env = merge_org_results("summary of each org", runs, _Model("- Org1 has 500.00 AED.\n- Org2 has 777.00 AED."))
    assert env["verification"]["method"] == "attributed"
    assert env["verification"]["unmatched"] == ["777.00"]
    assert "Unverified figures:** 777.00" in env["answer"]


def test_code_written_answer_is_marked_computed():
    runs = [_run(1, total_income=5.0, invoice_count=1), _run(2, total_income=50.0, invoice_count=1)]
    from gemini_brain.orchestrator.multi_org_metrics import build_comparison

    comp = build_comparison(BY_KEY["revenue"], runs, "compare revenue")
    env = merge_org_results("compare revenue", runs, _Model("unused"), comparison=comp)
    assert env["verification"]["method"] == "computed" and env["verification"]["grounded"]


# ── Repeated model output ────────────────────────────────────────────────────

def test_looping_answer_keeps_each_bullet_once():
    block = "\n".join(f"- Org{i}: 591 unpaid invoices totaling 4,689,018.00 AED." for i in range(1, 9))
    looped = "Here is a summary:\n" + "\n".join([block] * 8)
    out = strip_repeated_lines(looped)
    assert out.count("- Org1: 591 unpaid") == 1 and out.count("\n- ") == 8


def test_table_rows_and_short_lines_may_repeat():
    text = "| Michael | 9801 |\n| Michael | 9801 |\n- None.\n- None."
    assert strip_repeated_lines(text) == text


def test_envelope_removes_repeats_on_every_path():
    looped = "\n".join(["- Org3: 194 unpaid invoices totaling 1,386,411.00 AED."] * 5)
    assert normalize_envelope({"answer": looped})["answer"].count("Org3: 194") == 1
