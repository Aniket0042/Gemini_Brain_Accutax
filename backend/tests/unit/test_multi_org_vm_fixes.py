"""Gaps found in the VM plan log on 2026-09-30.

- "P&L … broken down by month" came back as net profit by month only.
- "top 5 vendors … with their overdue invoices" listed customer invoices.
- "hi" / "how are you" cost three model calls.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org_plan
from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results, run_multi_org
from gemini_brain.orchestrator.multi_org_lists import document_kind, document_list_selection, groups_by_contact
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    build_series_set,
    computed_answer,
    computed_view,
    match_series,
    match_series_metrics,
)
from gemini_brain.orchestrator.multi_org_plan import DATA, REPLY, plan_query, small_talk_reply
from gemini_brain.reports import definitions


class _NoModel:
    def _call_llm(self, *args, **kwargs):
        raise AssertionError("no model call expected")

    @staticmethod
    def _parse_json(text, default=None):
        return default


def _plan(question):
    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        return plan_query(question, 1, _NoModel(), 1)


# ── P&L over time ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, grain", [
    ("Generate our profit and loss statement for this fiscal year, broken down by month", "month"),
    ("Quarterly P&L trend", "quarter"),
    ("Income statement by month for each org", "month"),
])
def test_pnl_over_time_is_revenue_expenses_and_profit(question, grain):
    metrics, got_grain = match_series_metrics(question)
    assert [m.key for m in metrics] == ["revenue", "expenses", "net_profit"]
    assert got_grain == grain


def test_single_figure_series_is_unchanged():
    assert match_series("Revenue by month")[0].key == "revenue"
    assert [m.key for m in match_series_metrics("Revenue by month")[0]] == ["revenue"]


def test_pnl_series_plan_fetches_one_series_per_figure():
    plan = _plan("P&L by month this year")
    assert plan.kind == DATA and plan.source == "series"
    assert plan.series["metrics"] == ["revenue", "expenses", "net_profit"]
    assert [s["query_params"]["base"] for s in plan.all_selections()] == ["income", "expenses", "profit"]


def _series_run(oid, income, expenses):
    payloads = {
        "rpt_metric_series:income:month": {"series": [{"period": "2026-01", "value": income[0]},
                                                      {"period": "2026-02", "value": income[1]}]},
        "rpt_metric_series:expenses:month": {"series": [{"period": "2026-01", "value": expenses[0]},
                                                        {"period": "2026-02", "value": expenses[1]}]},
        "rpt_metric_series:profit:month": {"series": [{"period": "2026-01", "value": income[0] - expenses[0]},
                                                      {"period": "2026-02", "value": income[1] - expenses[1]}]},
    }
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "payloads": payloads, "results": []})


def test_series_set_reads_each_figure_from_its_own_payload():
    runs = [_series_run(1, (100, 200), (40, 50)), _series_run(2, (10, 20), (30, 40))]
    metrics = [BY_KEY[k] for k in ("revenue", "expenses", "net_profit")]
    comp = build_series_set(metrics, "month", runs)
    totals = {item["metric"]: {o["organization"]: o["total"] for o in item["organizations"]} for item in comp["items"]}
    assert totals == {"revenue": {"Org1": 300, "Org2": 30},
                      "expenses": {"Org1": 90, "Org2": 70},
                      "net_profit": {"Org1": 210, "Org2": -40}}
    blocks, _prompt, rows = computed_view(comp)
    assert [b["type"] for b in blocks].count("table") == 3
    answer = computed_answer(comp)
    assert answer.startswith("**Total revenue, Total expenses, Net profit by month**")
    assert "**Net profit**" in answer and "Negative for 1 of 2: Org2." in answer


def test_series_set_answer_needs_no_model_call():
    runs = [_series_run(1, (100, 200), (40, 50)), _series_run(2, (10, 20), (30, 40))]
    comp = build_series_set([BY_KEY[k] for k in ("revenue", "expenses", "net_profit")], "month", runs)
    env = merge_org_results("P&L by month", runs, _NoModel(), comparison=comp)
    assert env["token_usage"]["llm_calls"] == 0
    assert env["routing_info"]["layout"] == "series_set"


# ── Vendors and customers ranked by their documents ──────────────────────────

@pytest.mark.parametrize("question, kind", [
    ("Show top 5 vendors across all org's with their overdue invoices", "bill"),
    ("Which suppliers do we owe the most", "bill"),
    ("which customers owe us the most", "invoice"),
    ("Top vendors by spend across all organizations", "bill"),
    ("Top 10 customers across all organizations", None),  # no document words: left to the sales report
    ("List vendors", None),
])
def test_contact_words_decide_the_document_kind(question, kind):
    assert document_kind(question) == kind


def test_top_vendors_with_overdue_invoices_is_overdue_bills_per_vendor():
    qp = document_list_selection("Show top 5 vendors across all org's with their overdue invoices")["query_params"]
    assert (qp["kind"], qp["status"], qp["overdue"], qp["group_by"], qp["limit"]) == ("bill", "open", True, "contact", 5)


def test_customers_who_owe_are_open_invoices_per_customer():
    qp = document_list_selection("which customers owe us the most")["query_params"]
    assert (qp["kind"], qp["status"], qp["group_by"]) == ("invoice", "open", "contact")


def test_document_rows_are_not_grouped_without_a_ranking():
    assert not groups_by_contact("List vendors with overdue bills in each organization")


def test_grouped_report_sums_per_contact_in_sql():
    calls = []

    def fake_query(sql, args, org_id, db_name):
        calls.append(sql)
        if "COUNT(DISTINCT" in sql:
            return [{"contact_count": 2, "document_count": 7, "matched_amount": 900.0}]
        return [{"vendor": "Max", "documents": 5, "amount": 700.0}, {"vendor": "Deepa", "documents": 2, "amount": 200.0}]

    with patch.object(definitions, "query", side_effect=fake_query):
        out = definitions.document_list({"kind": "bill", "status": "open", "overdue": True,
                                         "group_by": "contact", "limit": 5}, 27, "")
    assert "GROUP BY vendor ORDER BY amount DESC LIMIT %s" in calls[0]
    assert out["vendors"] == [{"vendor": "Max", "documents": 5, "balance": 700.0},
                              {"vendor": "Deepa", "documents": 2, "balance": 200.0}]
    assert (out["summary"]["match_count"], out["summary"]["document_count"]) == (2, 7)


def test_grouped_rows_merge_across_orgs_by_amount():
    def run(oid, rows, count, total):
        payload = {"report": "Bills by Vendor",
                   "summary": {"match_count": count, "matched_amount": total, "amount_key": "balance",
                               "name_key": "vendor", "order": "amount_desc", "sorted": True},
                   "vendors": rows}
        return OrgRun(oid, f"Org{oid}", "AED", result={"status": "ok", "answer": "", "blocks": [],
                                                       "results": [payload],
                                                       "routing_info": {"path": "multi_org_plan"}})

    runs = [run(1, [{"vendor": "Max", "documents": 5, "balance": 700.0}], 3, 900.0),
            run(2, [{"vendor": "Noah", "documents": 1, "balance": 800.0}], 1, 800.0)]
    env = merge_org_results("top 5 vendors across all orgs with overdue bills", runs, _NoModel())
    assert [r["vendor"] for r in env["blocks"][0]["rows"]] == ["Noah", "Max"]


# ── Small talk ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("message", ["hi", "Hi!", "hello there", "how are you", "Good morning", "thanks", "Thank you!", "bye"])
def test_small_talk_gets_a_fixed_reply(message):
    assert small_talk_reply(message)


@pytest.mark.parametrize("message", ["hi, compare revenue", "how are my companies doing", "thanks, now show cash",
                                     "what is VAT"])
def test_questions_are_not_small_talk(message):
    assert small_talk_reply(message) is None


def test_greeting_is_answered_without_model_or_data():
    class Runner(_NoModel):
        def _retrieve(self, *args, **kwargs):
            raise AssertionError("nothing should be fetched")

    with patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        env = run_multi_org("hi", [1, 2], {1: {"name": "A"}, 2: {"name": "B"}}, runner_factory=Runner,
                            run_kwargs={"allowed_org_ids": [1, 2], "user_id": 1}, planner=plan_query)
    assert env["answer"].startswith("Hi. I can compare the selected organizations")
    assert env["token_usage"]["llm_calls"] == 0
    assert env["routing_info"]["layout"] == "greeting"
    assert _plan("hi").kind == REPLY
