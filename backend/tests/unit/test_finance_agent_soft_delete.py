"""
test_finance_agent_soft_delete.py — Regression guard for schema-compliant filtering.

In accutax_bk, only `contacts` and `sub_contacts` have an `is_deleted` column.
`income`, `expense`, `income_items`, and `expense_items` do not have `is_deleted`.
Instead, cancellations / voids are tracked via `status_type_id` and `voided_at`.

These tests ensure:
1. Tasks scope properly to organization_id.
2. Direct contact queries apply soft-delete filtering.
3. Tasks do NOT inject invalid column references (like ei.is_deleted or ii.is_deleted).
"""
import inspect
import pytest

from gemini_brain.agents import finance_agent


ORG_ID = 199

TASKS = [
    ("get_invoice_total", {}),
    ("get_expense_total", {}),
    ("top_customers", {"limit": 5}),
    ("top_vendors", {"limit": 5}),
    ("ar_aging", {}),
    ("ap_aging", {}),
    ("overdue_invoices", {}),
    ("overdue_bills", {}),
    ("expense_by_category", {}),
    ("invoice_status_summary", {}),
    ("bill_status_summary", {}),
    ("list_invoices", {}),
    ("list_expenses", {}),
    ("customer_overdue_summary", {}),
    ("recent_transactions", {}),
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
    """Run a task against the stub and return every statement it built, joined."""
    finance_agent.handle(task, {"organization_id": ORG_ID, **params})
    assert statements, f"{task} issued no SQL to inspect"
    return "\n".join(statements)


@pytest.mark.parametrize("task,params", TASKS, ids=[t for t, _ in TASKS])
def test_task_sql_scopes_to_organization(task, params, captured_sql):
    """Every tenant task must filter by organization_id."""
    sql = _sql_for(task, params, captured_sql)
    assert "organization_id" in sql, f"{task} builds SQL with no tenant filter.\n\n{sql}"


@pytest.mark.parametrize("task,params", TASKS, ids=[t for t, _ in TASKS])
def test_no_invalid_line_item_is_deleted(task, params, captured_sql):
    """Neither income_items nor expense_items has an is_deleted column in accutax_bk."""
    sql = _sql_for(task, params, captured_sql)
    assert "ii.is_deleted" not in sql, f"{task} references non-existent ii.is_deleted column"
    assert "ei.is_deleted" not in sql, f"{task} references non-existent ei.is_deleted column"


def test_build_where_adds_soft_delete_only_for_contacts():
    """_build_where should add is_deleted only when querying contacts."""
    for entity in ("contacts", "contact"):
        clauses, _joins = finance_agent._build_where({}, entity, {"organization_id": ORG_ID})
        assert any("is_deleted = false" in c for c in clauses), (
            f"_build_where({entity!r}) omitted soft-delete for contacts"
        )
    for entity in ("income", "expense"):
        clauses, _joins = finance_agent._build_where({}, entity, {"organization_id": ORG_ID})
        assert not any("is_deleted = false" in c for c in clauses), (
            f"_build_where({entity!r}) improperly added is_deleted for {entity}"
        )

