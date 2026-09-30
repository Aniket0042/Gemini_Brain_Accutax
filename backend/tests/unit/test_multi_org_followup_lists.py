"""Multi-org follow-ups folded in code, and invoice/bill lists filtered in SQL.

Cases come from the role-based manual test run: "and last quarter?" re-ran this
year, "top 2" showed all 10, "drop Org1 and redo" kept Org1, the auditor lists
showed the newest rows instead of the largest or oldest, and shared vendors
got made-up amounts.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org, multi_org_followup as followup, multi_org_plan
from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results, needs_rewrite, run_multi_org
from gemini_brain.orchestrator.multi_org_lists import (
    describe,
    document_filters,
    document_kind,
    document_list_selection,
)
from gemini_brain.orchestrator.multi_org_metrics import BY_KEY, build_comparison, computed_answer, limit_rows
from gemini_brain.orchestrator.multi_org_plan import DATA, UNSUPPORTED, plan_query
from gemini_brain.reports import definitions

META = {1: {"name": "Agri_Org1", "currency": "AED"}, 2: {"name": "Build_Org2", "currency": "AED"},
        10: {"name": "Agri_Org10", "currency": "AED"}}
IDS = [1, 2, 10]


def _user(*texts):
    return [{"role": "user", "content": t} for t in texts]


def _resolve(query, *history):
    return followup.resolve(query, _user(*history), META, IDS, stands_alone=lambda t: not needs_rewrite(t))


# ── Follow-ups ───────────────────────────────────────────────────────────────

def test_period_follow_up_swaps_the_period():
    assert _resolve("and last quarter ?", "Compare revenue this year").question == "Compare revenue last quarter"


def test_quarter_without_year_takes_the_thread_year():
    assert _resolve("and Q2?", "Compare revenue for Q1 2025").question == "Compare revenue for Q2 2025"


def test_top_n_follow_up_keeps_figure_and_period():
    r = _resolve("now only show the top 2", "Compare revenue this year", "and last quarter ?")
    assert r.question == "Compare revenue last quarter top 2"


def test_new_figure_follow_up_keeps_the_period():
    assert _resolve("same for payables", "Compare revenue last quarter").question == "same for payables last quarter"


def test_open_follow_up_is_left_to_the_model():
    assert _resolve("why is the second one so low?", "Compare revenue this year") is None


def test_earlier_why_question_does_not_block_later_follow_ups():
    r = _resolve("drop Agri_Org1 and redo", "Compare revenue this year", "why is the second one so low?")
    assert r.question == "Compare revenue this year"
    assert r.org_ids(IDS) == [2, 10]


def test_org_name_is_matched_whole_so_org1_is_not_org10():
    r = _resolve("drop Agri_Org10 and redo", "Compare revenue this year")
    assert r.org_ids(IDS) == [1, 2]


def test_exclusions_persist_through_the_thread():
    r = _resolve("and last quarter?", "Compare revenue this year", "drop Agri_Org1 and redo")
    assert r.org_ids(IDS) == [2, 10] and r.question == "Compare revenue last quarter"


def test_dropping_every_org_keeps_all():
    r = followup.Resolved("q", exclude={1, 2, 10})
    assert r.org_ids(IDS) == IDS


def test_model_rewrite_gets_the_thread_period_back():
    history = _user("Compare revenue this year")
    assert followup.keep_period("Why is Org2's revenue lower than Org1's?", history).endswith("this year")


def test_why_question_needs_the_conversation():
    assert needs_rewrite("why is the second one so low?") is True
    assert needs_rewrite("What is VAT?") is False


def _metric_runs(values):
    return [OrgRun(i, f"Org{i}", "AED", result={"status": "ok", "results": [
        {"summary": {"total_income": v, "invoice_count": 1}}]}) for i, v in enumerate(values, 1)]


def test_top_n_cuts_the_table_but_not_the_total():
    comp = build_comparison(BY_KEY["revenue"], _metric_runs([10, 50, 30, 20]), "compare revenue top 2")
    limited = limit_rows(comp, "compare revenue top 2")
    assert [r["organization"] for r in limited["rows"]] == ["Org2", "Org3"]
    answer = computed_answer(limited)
    assert "top 2 of 4 organizations" in answer
    assert "Combined total of all 4 organizations: 110.00 AED." in answer


def test_bottom_n_keeps_the_lowest():
    comp = build_comparison(BY_KEY["revenue"], _metric_runs([10, 50, 30, 20]), "revenue")
    assert [r["organization"] for r in limit_rows(comp, "bottom 2")["rows"]] == ["Org4", "Org1"]


def test_first_3_months_is_not_a_row_limit():
    comp = build_comparison(BY_KEY["revenue"], _metric_runs([10, 50, 30, 20]), "revenue")
    assert limit_rows(comp, "revenue for the first 3 months") is comp


def test_drop_follow_up_skips_the_org_and_says_so():
    fetched = []

    class Runner:
        def _enforce_tenant_isolation(self, **kwargs):
            return kwargs["organization_id"]

        def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
            from gemini_brain.resilience.outcomes import Outcome, Retrieved
            fetched.append(organization_id)
            return Retrieved(Outcome.OK, payload={"summary": {"total_income": 10.0 * organization_id,
                                                              "invoice_count": 1}},
                             tier="sql_report", endpoint=sel["endpoint"])

        def _call_llm(self, *args, **kwargs):
            raise AssertionError("no model call expected")

    saved = []
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org, "_load_history", return_value=_user("Compare revenue this year")), \
         patch.object(multi_org, "_persist_turn", side_effect=lambda *a: saved.append(a[2])):
        env = run_multi_org("drop Agri_Org1 and redo", IDS, META, runner_factory=Runner,
                            run_kwargs={"allowed_org_ids": IDS, "user_id": 1, "session_id": "s1"},
                            planner=plan_query)
    assert sorted(fetched) == [2, 10]
    assert [o["id"] for o in env["organizations"]] == [2, 10]
    assert "Left out as asked: Agri_Org1." in env["answer"]
    assert saved == [IDS]  # the thread keeps its full org set


# ── Reading list questions ───────────────────────────────────────────────────

@pytest.mark.parametrize("question, kind", [
    ("Top 10 largest bills overall", "bill"),
    ("List every unpaid invoice older than 180 days per org", "invoice"),
    ("List supplier invoices over 5k", "bill"),
    ("List bills and invoices", None),
    ("List vendors", None),
])
def test_document_kind(question, kind):
    assert document_kind(question) == kind


def test_unpaid_older_than_180_days():
    f = document_filters("List every unpaid invoice older than 180 days per org")
    assert (f["status"], f["older_than_days"], f["order"]) == ("open", 180, "amount_desc")


def test_largest_bills_default_status_excludes_cancelled():
    f = document_filters("Top 10 largest bills overall")
    assert "status" not in f and f["limit"] == 10 and f["order"] == "amount_desc"
    assert "(cancelled and voided excluded)" in describe({"kind": "bill", **f})


def test_amount_and_overdue_days():
    f = document_filters("List bills overdue by more than 60 days over AED 10k")
    assert (f["status"], f["overdue_days"], f["min_amount"]) == ("open", 60, 10000.0)


def test_amount_threshold_is_not_read_from_days_or_org_counts():
    f = document_filters("invoices paid by more than 2 organizations older than 30 days")
    assert "min_amount" not in f


def test_latest_is_date_order_and_period_is_used():
    sel = document_list_selection("Latest 5 invoices for each org this year")
    qp = sel["query_params"]
    assert (qp["order"], qp["limit"]) == ("date_desc", 5)
    assert qp["start_date"].endswith("-01-01")


def test_no_period_means_all_history():
    assert "start_date" not in document_list_selection("List unpaid invoices older than 180 days")["query_params"]


# ── The report applies the filters in SQL ────────────────────────────────────

def test_document_list_sql_applies_every_filter():
    calls = []

    def fake_query(sql, args, org_id, db_name):
        calls.append((sql, args))
        return [{"match_count": 3, "matched_amount": 900.0}] if "COUNT(*)" in sql else [
            {"number": "INV-1", "customer": "Acme", "doc_date": "2025-01-05", "due_date": "2025-02-04",
             "status": "PENDING", "amount": 500.0}]

    with patch.object(definitions, "query", side_effect=fake_query):
        out = definitions.document_list({"kind": "invoice", "status": "open", "older_than_days": 180,
                                         "min_amount": 100, "order": "amount_desc", "limit": 10}, 7, "")
    sql, args = calls[0]
    assert "st.value IN ('PENDING','PARTIALLY_PAID')" in sql
    assert f"{definitions._safe_date_sql('d.invoice_date')} <= %s::DATE" in sql
    assert "amount > %s" in sql and "ORDER BY amount DESC" in sql
    assert args[0] == 7 and 100 in args and args[-1] == 10
    assert out["summary"]["match_count"] == 3 and out["summary"]["amount_key"] == "balance"
    assert out["invoices"][0]["balance"] == 500.0 and out["invoices"][0]["age_days"] > 180


def test_text_that_is_not_a_date_becomes_null_instead_of_failing():
    # Seed data holds a due_date of "string" (org 24); a plain CAST failed the whole org.
    sql = definitions._safe_date_sql("d.due_date")
    assert sql.startswith("(CASE WHEN CAST(d.due_date AS TEXT) ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}'")
    assert sql.endswith("END)")


def test_document_list_default_excludes_cancelled_and_voided():
    with patch.object(definitions, "query", return_value=[]) as q:
        definitions.document_list({"kind": "bill"}, 7, "")
    assert "st.value NOT IN ('CANCELLED','VOIDED')" in q.call_args_list[0].args[0]
    assert "FROM   expense d" in q.call_args_list[0].args[0]


def test_unknown_order_falls_back_to_largest_first():
    with patch.object(definitions, "query", return_value=[]) as q:
        out = definitions.document_list({"kind": "bill", "order": "DROP TABLE"}, 7, "")
    assert "ORDER BY amount DESC" in q.call_args_list[0].args[0] and out["summary"]["order"] == "amount_desc"


# ── Presentation and answers ─────────────────────────────────────────────────

def _doc_run(oid, rows, count, total):
    payload = {"report": "Bill List", "summary": {"match_count": count, "matched_amount": total, "listed": len(rows),
                                                  "amount_key": "total", "name_key": "vendor", "order": "amount_desc",
                                                  "sorted": True}, "bills": rows}
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok" if rows else "empty", "answer": "",
                                                   "results": [payload] if rows else [], "blocks": [],
                                                   "routing_info": {"path": "multi_org_plan"}})


class _NoModel:
    def _call_llm(self, *args, **kwargs):
        raise AssertionError("list answers are written in code")


def test_per_org_counts_every_match_not_the_rows_listed():
    runs = [_doc_run(1, [{"bill_number": "B1", "vendor": "Max", "total": 900.0, "bill_date": "2026-01-01"}], 591, 4689018.0),
            _doc_run(2, [], 0, 0.0)]
    env = merge_org_results("List every unpaid bill older than 180 days per org", runs, _NoModel(),
                            title="Unpaid bills, dated more than 180 days ago")
    assert env["answer"].startswith("**Unpaid bills, dated more than 180 days ago**")
    assert "Org1: 591 items, total 4,689,018.00 AED; largest: Max, 900.00." in env["answer"]
    assert "Org2: none found." in env["answer"]
    assert env["token_usage"]["llm_calls"] == 0
    assert env["answer"].count("Org1: 591") == 1  # never repeated


def test_merged_top_items_come_from_every_org_by_amount():
    runs = [_doc_run(1, [{"bill_number": "B1", "vendor": "A", "total": 300.0, "bill_date": "2026-01-01"},
                         {"bill_number": "B2", "vendor": "B", "total": 100.0, "bill_date": "2026-01-02"}], 40, 5000.0),
            _doc_run(2, [{"bill_number": "B3", "vendor": "C", "total": 200.0, "bill_date": "2026-01-03"}], 12, 900.0)]
    env = merge_org_results("Top 2 largest bills overall", runs, _NoModel())
    table = env["blocks"][0]
    assert [r["vendor"] for r in table["rows"]] == ["A", "C"]
    assert table["total_rows"] == 52
    assert "2 shown of 52 matching items across 2 organizations." in env["answer"]


def _vendors(oid, *pairs):
    rows = [{"vendor": v, "bill_count": 1, "total_spend": t} for v, t in pairs]
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "answer": "", "blocks": [],
                                                   "results": [{"summary": {"contact_count": len(rows)},
                                                                "vendors": rows}]})


def test_common_to_all_means_every_org_and_threshold_is_on_the_combined_total():
    runs = [_vendors(1, ("Ranveer", 120000.0), ("Noah", 10.0), ("Ojasvi", 50.0)),
            _vendors(2, ("Ranveer", 30000.0), ("Noah", 20.0)),
            _vendors(3, ("Ranveer", 5000.0), ("Ojasvi", 70.0))]
    env = merge_org_results("Vendors common to all orgs paid more than 100,000 AED", runs, _NoModel())
    rows = env["blocks"][0]["rows"]
    assert [r["item"] for r in rows] == ["Ranveer"] and rows[0]["combined_total"] == 155000.0
    assert "1 found in all 3 organizations with a combined total over 100,000.00" in env["answer"]
    assert "Ranveer: 3 organizations, combined 155,000.00 AED." in env["answer"]


# ── Planning ─────────────────────────────────────────────────────────────────

class _Planner:
    def _call_llm(self, *args, **kwargs):
        raise AssertionError("planned without a model call")

    @staticmethod
    def _parse_json(text, default=None):
        return default


def _plan(question):
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        return plan_query(question, 1, _Planner(), 1)


@pytest.mark.parametrize("question", [
    "Top 10 largest bills overall",
    "List every unpaid invoice older than 180 days per org",
    "List overdue bills older than 90 days over 10k in each org",
])
def test_document_lists_are_planned_in_code(question):
    plan = _plan(question)
    assert (plan.kind, plan.source, plan.selection["endpoint"]) == (DATA, "document_list", "rpt_document_list")


@pytest.mark.parametrize("question", [
    "Which vendors got paid by more than one entity in the same week?",
    "Duplicate invoice numbers across organizations",
    "Is any customer in Org A also a vendor in Org B?",
    "Show consolidated balance sheet with intercompany eliminated",
    "Round-amount payments above 50,000 across all orgs",
    "Show all transactions with no description per org",
])
def test_matches_and_filters_nothing_applies_are_refused(question):
    plan = _plan(question)
    assert plan.kind == UNSUPPORTED and plan.notes


def test_filter_that_cannot_be_applied_is_noted_on_the_list():
    plan = _plan("List invoices posted on weekends in each org")
    assert plan.source == "document_list"
    assert plan.notes[0].startswith("Not filtered by posting on weekends")
