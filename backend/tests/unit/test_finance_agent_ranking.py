"""
test_finance_agent_ranking.py — Regression guard for count + direction on every
ranked finance_agent task.

Two bugs shipped from the same root cause: a hardcoded LIMIT that ignored a
model-supplied count ("top 5" silently returning 10/50 rows), and — even once
count was threaded through — every ranked task's ORDER BY was hardcoded DESC
with no way to ever honor "bottom N" / "least" phrasing. These tests pin both
down directly against the SQL text finance_agent builds (params are
interpolated, not bound, in this module — see _run_sql).
"""
import re

import pytest

from gemini_brain.agents import finance_agent


ORG_ID = 199

# Every finance_agent task that ranks rows by a value column and accepts
# limit/sort_order. (list_invoices/list_expenses/bank_transactions/etc. order
# by date for a "most recent" default, not by ranked value, and are out of
# scope here — see the conversation this test file was added from.)
RANKED_TASKS = [
    "top_customers",
    "top_vendors",
    "expense_by_category",
    "overdue_invoices",
    "overdue_bills",
    "unallocated_payments",
    "customer_overdue_summary",
    "vat_summary",
]


@pytest.fixture
def captured_sql(monkeypatch):
    """Stub execute_sql with a recorder — keeps these tests hermetic and fast."""
    statements = []

    def _recording_execute_sql(sql, *args, **kwargs):
        statements.append(sql)
        raise RuntimeError("database intentionally unavailable in unit tests")

    monkeypatch.setattr(finance_agent, "execute_sql", _recording_execute_sql)
    return statements


def _sql_for(task: str, params: dict, statements: list) -> str:
    finance_agent.handle(task, {"organization_id": ORG_ID, **params})
    assert statements, f"{task} issued no SQL to inspect"
    return "\n".join(statements)


@pytest.mark.parametrize("task", RANKED_TASKS)
def test_ranked_task_honors_a_caller_supplied_limit(task, captured_sql):
    """'top 5' must come back as exactly 5, never a hardcoded/default row count."""
    sql = _sql_for(task, {"limit": 5}, captured_sql)
    assert re.search(r"\bLIMIT\s+5\b", sql), f"{task}: limit=5 never reached the SQL\n\n{sql}"


@pytest.mark.parametrize("task", RANKED_TASKS)
def test_ranked_task_clamps_an_out_of_range_limit(task, captured_sql):
    sql = _sql_for(task, {"limit": 999999}, captured_sql)
    assert not re.search(r"\bLIMIT\s+999999\b", sql), (
        f"{task}: an oversized model-supplied limit reached raw SQL unclamped\n\n{sql}"
    )


@pytest.mark.parametrize("task", RANKED_TASKS)
def test_ranked_task_defaults_to_descending(task, captured_sql):
    sql = _sql_for(task, {}, captured_sql)
    assert re.search(r"ORDER\s+BY\s+\S.*\bDESC\b", sql, re.IGNORECASE), (
        f"{task}: default direction should be DESC (top/highest) when unspecified\n\n{sql}"
    )


@pytest.mark.parametrize("task", RANKED_TASKS)
def test_ranked_task_honors_ascending_sort_order(task, captured_sql):
    """'bottom N' / 'least' must actually flip the SQL to ASC, not just relabel a DESC slice."""
    sql = _sql_for(task, {"sort_order": "asc"}, captured_sql)
    assert re.search(r"ORDER\s+BY\s+\S.*\bASC\b", sql, re.IGNORECASE), (
        f"{task}: sort_order='asc' did not produce an ASC ordering\n\n{sql}"
    )
    assert not re.search(r"ORDER\s+BY\s+\S.*\bDESC\b", sql, re.IGNORECASE), (
        f"{task}: sort_order='asc' still produced a DESC ordering\n\n{sql}"
    )


@pytest.mark.parametrize("task", RANKED_TASKS)
def test_ranked_task_bottom_phrasing_via_sort_order_keyword(task, captured_sql):
    """The 'bottom'/'lowest'/'least' spellings accepted from the router must all mean ascending."""
    for word in ("bottom", "lowest", "least"):
        sql = _sql_for(task, {"sort_order": word}, captured_sql)
        assert re.search(r"ORDER\s+BY\s+\S.*\bASC\b", sql, re.IGNORECASE), (
            f"{task}: sort_order={word!r} did not produce an ASC ordering\n\n{sql}"
        )
        captured_sql.clear()
