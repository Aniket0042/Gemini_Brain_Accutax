"""Multi-org questions are routed once and fetched per org with no per-org model calls."""
from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from gemini_brain.orchestrator import multi_org_plan
from gemini_brain.orchestrator.multi_org import run_multi_org
from gemini_brain.orchestrator.multi_org_plan import DATA, DIRECT, QueryPlan, fetch_for_org, plan_query
from gemini_brain.resilience.outcomes import Outcome, Retrieved

SEL = {"endpoint": "rpt_income_total", "path_params": {}, "query_params": {"start_date": "2026-01-01"}}


class PlanRunner:
    """Runner stand-in: records fetches, full runs and model calls."""

    fetches: List[Dict[str, Any]] = []
    full_runs: List[int] = []
    llm_purposes: List[str] = []
    outcome_by_org: Dict[int, Outcome] = {}
    denied_orgs: set = set()

    def _enforce_tenant_isolation(self, organization_id, query, db_name, allowed_org_ids, user_id, session_id):
        if organization_id in PlanRunner.denied_orgs or organization_id not in (allowed_org_ids or []):
            raise ValueError("Access denied")
        return organization_id

    def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
        PlanRunner.fetches.append({"org": organization_id, "sel": sel,
                                   "org_scoped": getattr(self, "_org_scoped_rest_only", False)})
        outcome = PlanRunner.outcome_by_org.get(organization_id, Outcome.OK)
        payload = {"summary": {"total_income": organization_id * 100}} if outcome is Outcome.OK else None
        return Retrieved(outcome, payload=payload, tier="sql_report", endpoint=sel["endpoint"])

    def run(self, **kwargs):
        PlanRunner.full_runs.append(kwargs["organization_id"])
        return {"answer": f"full run {kwargs['organization_id']}", "status": "ok",
                "results": [], "token_usage": {"input_tokens": 5, "output_tokens": 5, "llm_calls": 3}}

    def _call_llm(self, system, user_text, **kwargs):
        PlanRunner.llm_purposes.append(kwargs.get("purpose", ""))
        return "Compared.", 10, 5

    @staticmethod
    def _parse_json(text, default=None):
        return default


@pytest.fixture(autouse=True)
def _reset():
    PlanRunner.fetches, PlanRunner.full_runs, PlanRunner.llm_purposes = [], [], []
    PlanRunner.outcome_by_org, PlanRunner.denied_orgs = {}, set()


def _data_planner(*_args):
    return QueryPlan(DATA, selection=dict(SEL), intent=4, source="llm", input_tokens=50, output_tokens=10, llm_calls=2)


def _run(orgs=(5, 6, 7), planner=_data_planner, **extra):
    return run_multi_org(
        "total revenue this year", list(orgs), {},
        runner_factory=PlanRunner,
        run_kwargs={"allowed_org_ids": [5, 6, 7], "user_id": 1, **extra},
        planner=planner,
    )


# ── Planned fetch ────────────────────────────────────────────────────────────

def test_every_org_fetches_the_same_selection_with_its_own_org():
    _run()
    assert sorted(f["org"] for f in PlanRunner.fetches) == [5, 6, 7]
    assert all(f["sel"]["endpoint"] == "rpt_income_total" for f in PlanRunner.fetches)


def test_no_per_org_model_calls_only_the_comparison():
    res = _run()
    assert PlanRunner.full_runs == []
    assert PlanRunner.llm_purposes == ["multi_org_compare"]
    # 2 planning calls + 1 comparison call
    assert res["token_usage"]["llm_calls"] == 3


def test_planned_fetch_keeps_org_scoped_rest_guard_on():
    _run()
    assert all(f["org_scoped"] for f in PlanRunner.fetches)


def test_plan_step_is_traced():
    res = _run()
    assert res["agent_trace"][0] == {"step": "multi_org_plan", "kind": "data", "source": "llm",
                                     "endpoint": "rpt_income_total"}


def test_unusable_fetch_falls_back_to_full_pipeline_for_that_org_only():
    PlanRunner.outcome_by_org = {6: Outcome.UNAVAILABLE}
    res = _run()
    assert PlanRunner.full_runs == [6]
    assert res["status"] == "ok"


def test_empty_fetch_is_a_real_answer_not_a_fallback():
    PlanRunner.outcome_by_org = {6: Outcome.EMPTY}
    res = _run()
    assert PlanRunner.full_runs == []
    assert {o["id"]: o["status"] for o in res["organizations"]}[6] == "empty"


def test_org_failing_allow_list_check_is_never_fetched():
    PlanRunner.denied_orgs = {7}
    res = _run()
    assert 7 not in [f["org"] for f in PlanRunner.fetches]
    assert res["status"] == "partial"


def test_no_plan_uses_per_org_pipeline():
    _run(planner=lambda *_a: None)
    assert sorted(PlanRunner.full_runs) == [5, 6, 7]
    assert PlanRunner.fetches == []


# ── Questions that need no data are answered once ────────────────────────────

def test_direct_question_runs_once_not_per_org():
    res = _run(planner=lambda *_a: QueryPlan(DIRECT, source="how_to_guide"))
    assert PlanRunner.full_runs == [5]
    assert res["routing_info"]["path"] == "multi_org_direct"
    assert "multi_org_compare" not in PlanRunner.llm_purposes


# ── plan_query ───────────────────────────────────────────────────────────────

def test_plan_uses_fast_router_without_model_calls():
    class Hit:
        intent = 4
        rule_name = "revenue_total"

        def to_selection_dict(self):
            return dict(SEL)

    with patch.object(multi_org_plan, "fast_route", return_value=Hit()), \
         patch.object(multi_org_plan, "_how_to_guide_section", return_value=None):
        plan = plan_query("Show expenses by category", 5, PlanRunner(), 1)
    assert plan.kind == DATA and plan.source == "fast" and plan.llm_calls == 0
    assert PlanRunner.llm_purposes == []


def test_plan_marks_non_data_intent_as_direct():
    with patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", return_value=({"type": 1, "reason": "faq"}, 5, 2)):
        plan = plan_query("what is VAT?", 5, PlanRunner(), 1)
    assert plan.kind == DIRECT and plan.llm_calls == 1


def test_plan_selects_endpoint_once_for_data_intent():
    with patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", return_value=({"type": 4}, 5, 2)), \
         patch.object(multi_org_plan, "select_endpoint", return_value=(dict(SEL), 7, 3)) as select:
        plan = plan_query("show the aging report", 5, PlanRunner(), 1)
    select.assert_called_once()
    assert plan.kind == DATA and plan.selection["endpoint"] == "rpt_income_total" and plan.llm_calls == 2


def test_plan_returns_none_when_no_endpoint_fits():
    with patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", return_value=({"type": 4}, 5, 2)), \
         patch.object(multi_org_plan, "select_endpoint", return_value=(None, 7, 3)):
        assert plan_query("something odd", 5, PlanRunner(), 1) is None


def test_planner_sees_redacted_question():
    seen = []
    run_multi_org(
        "revenue for john@example.com", [5, 6], {},
        runner_factory=PlanRunner, run_kwargs={"allowed_org_ids": [5, 6], "user_id": 1},
        planner=lambda q, *_a: seen.append(q) or None,
    )
    assert "john@example.com" not in seen[0]


# ── fetch_for_org ────────────────────────────────────────────────────────────

def test_fetch_result_shape_for_merge():
    plan = _data_planner()
    res = fetch_for_org(plan, 5, PlanRunner(), query="q", allowed_org_ids=[5], user_id=1)
    assert res["status"] == "ok" and res["answer"] == ""
    assert res["results"] == [{"summary": {"total_income": 500}}]
    assert res["routing_info"]["path"] == "multi_org_plan"



def test_unavailable_fetch_is_retried_once_before_falling_back(monkeypatch):
    monkeypatch.setattr(multi_org_plan, "RETRY_DELAY_SECONDS", 0)
    attempts = []

    class Flaky(PlanRunner):
        def _retrieve(self, sel, organization_id, db_name, trace, auth_token=""):
            attempts.append(organization_id)
            outcome = Outcome.UNAVAILABLE if len(attempts) == 1 else Outcome.OK
            payload = {"summary": {"total_income": 1}} if outcome is Outcome.OK else None
            return Retrieved(outcome, payload=payload, tier="live_api", endpoint=sel["endpoint"])

    res = fetch_for_org(_data_planner(), 5, Flaky(), query="q", allowed_org_ids=[5], user_id=1)
    assert attempts == [5, 5] and res["status"] == "ok"


def test_parallel_fetches_are_bounded(monkeypatch):
    from gemini_brain.orchestrator import multi_org
    seen = []
    real = multi_org.ThreadPoolExecutor

    def spy(max_workers=None, **kw):
        seen.append(max_workers)
        return real(max_workers=max_workers, **kw)

    monkeypatch.setattr(multi_org, "ThreadPoolExecutor", spy)
    run_multi_org("total revenue", list(range(1, 11)), {}, runner_factory=PlanRunner,
                  run_kwargs={"allowed_org_ids": list(range(1, 11)), "user_id": 1}, planner=_data_planner)
    assert seen == [multi_org.MAX_PARALLEL_ORGS]
