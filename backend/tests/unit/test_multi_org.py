"""Multi-organization fan-out: per-org isolation, merge, partial failure, currency labels."""
from __future__ import annotations

import contextvars
import json
import threading
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api import auth
from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.orchestrator import multi_org
from gemini_brain.orchestrator.multi_org import run_multi_org, run_multi_org_stream

META = {
    5: {"name": "Alpha LLC", "currency": "AED"},
    6: {"name": "Beta Inc", "currency": "USD"},
}

_probe: contextvars.ContextVar[str] = contextvars.ContextVar("probe", default="")


class FakeRunner:
    """Stands in for GeminiBrainRunner; records every per-org call."""

    calls: List[Dict[str, Any]] = []
    lock = threading.Lock()
    fail_orgs: set = set()
    compare_answer: Any = "Alpha LLC earned AED 100; Beta Inc earned USD 200."

    def run(self, **kwargs: Any) -> Dict[str, Any]:
        with self.lock:
            FakeRunner.calls.append({**kwargs, "probe": _probe.get()})
        oid = kwargs["organization_id"]
        if oid in FakeRunner.fail_orgs:
            raise RuntimeError("upstream exploded with secret detail")
        return {
            "answer": f"Revenue for org {oid}",
            "status": "ok",
            "results": [{"month": "Jan", "revenue": oid * 20}],
            "blocks": [{"type": "table", "columns": [], "rows": []}],
            "token_usage": {"input_tokens": 10, "output_tokens": 5, "llm_calls": 2, "cost_usd": 0.01},
            "routing_info": {"type": 4, "type_label": "Data query", "path": "api_then_anthropic"},
        }

    def _call_llm(self, system: str, user_text: str, **kwargs: Any):
        FakeRunner.last_prompt = (system, user_text)
        if isinstance(FakeRunner.compare_answer, Exception):
            raise FakeRunner.compare_answer
        return FakeRunner.compare_answer, 100, 50


@pytest.fixture(autouse=True)
def _reset_fake():
    FakeRunner.calls = []
    FakeRunner.fail_orgs = set()
    FakeRunner.compare_answer = "Alpha LLC earned AED 100; Beta Inc earned USD 200."
    yield


def _run(orgs=(5, 6), **run_kwargs):
    return run_multi_org(
        "revenue this year", list(orgs), META,
        runner_factory=FakeRunner,
        run_kwargs={"allowed_org_ids": [5, 6], "session_id": "abc", **run_kwargs},
    )


# ── Fan-out ──────────────────────────────────────────────────────────────────

def test_each_org_runs_separately_with_its_own_org_id():
    _run()
    assert sorted(c["organization_id"] for c in FakeRunner.calls) == [5, 6]


def test_allow_list_is_passed_to_every_per_org_run():
    _run()
    assert all(c["allowed_org_ids"] == [5, 6] for c in FakeRunner.calls)


def test_per_org_runs_skip_rest_endpoints_without_org_param():
    _run()
    assert all(c["org_scoped_rest_only"] is True for c in FakeRunner.calls)


def test_per_org_runs_never_touch_session_memory():
    _run()
    assert all(c["session_id"] is None for c in FakeRunner.calls)


def test_request_context_reaches_worker_threads():
    token = _probe.set("request-ctx")
    try:
        _run()
    finally:
        _probe.reset(token)
    assert [c["probe"] for c in FakeRunner.calls] == ["request-ctx", "request-ctx"]


# ── Merge ────────────────────────────────────────────────────────────────────

def test_rows_are_labelled_with_their_organization():
    res = _run()
    by_org = {r["organization_id"]: r for r in res["results"]}
    assert by_org[5]["organization"] == "Alpha LLC"
    assert by_org[6]["organization_currency"] == "USD"
    assert by_org[6]["revenue"] == 120


def test_a_row_cannot_claim_another_organization():
    run = multi_org.OrgRun(5, "Alpha LLC", "AED", result={
        "status": "ok", "results": [{"organization_id": 6, "organization": "Beta Inc", "currency": "EUR"}],
    })
    row = multi_org._tagged_rows(run)[0]
    assert (row["organization_id"], row["organization"]) == (5, "Alpha LLC")
    assert row["currency"] == "EUR"  # the row's own field is kept


def test_answer_comes_from_one_comparison_call_with_currency_rules():
    res = _run()
    assert res["answer"].startswith("Alpha LLC earned AED 100")
    system, user_text = FakeRunner.last_prompt
    assert "Never add, subtract or rank amounts in different currencies" in system
    assert "Alpha LLC (currency: AED" in user_text and "Beta Inc (currency: USD" in user_text


def test_token_usage_sums_every_org_plus_comparison():
    usage = _run()["token_usage"]
    assert usage["input_tokens"] == 10 + 10 + 100
    assert usage["llm_calls"] == 2 + 2 + 1


def test_organizations_listed_in_request_order():
    res = _run(orgs=(6, 5))
    assert [o["id"] for o in res["organizations"]] == [6, 5]
    assert res["routing_info"]["path"] == "multi_org"
    assert res["status"] == "ok"


def test_org_blocks_sit_in_collapsed_sections_per_org():
    blocks = _run()["blocks"]
    assert [b["type"] for b in blocks] == ["collapsible_group"]
    group = blocks[0]
    assert group["default_open"] is True and group["sections_open"] is True  # 2 orgs: all open
    assert [s["title"] for s in group["sections"]] == ["Alpha LLC", "Beta Inc"]
    assert all(s["blocks"][0]["type"] == "table" for s in group["sections"])


# ── Failure handling ─────────────────────────────────────────────────────────

def test_one_failed_org_gives_partial_answer_without_substitute_data():
    FakeRunner.fail_orgs = {6}
    res = _run()
    assert res["status"] == "partial"
    assert {r["organization_id"] for r in res["results"]} == {5}
    assert "Beta Inc" in res["notice"]["message"]
    assert "secret detail" not in json.dumps(res)
    assert "No data could be retrieved" in FakeRunner.last_prompt[1]


def test_all_orgs_failed_is_a_failure_with_no_comparison_call():
    FakeRunner.fail_orgs = {5, 6}
    FakeRunner.last_prompt = None
    res = _run()
    assert res["status"] == "failed"
    assert res["results"] == []
    assert FakeRunner.last_prompt is None


def test_comparison_call_failure_falls_back_to_per_org_answers():
    FakeRunner.compare_answer = RuntimeError("bedrock down")
    res = _run()
    assert "Alpha LLC" in res["answer"] and "Revenue for org 6" in res["answer"]
    assert res["status"] == "ok"


def test_stream_reports_each_org_then_final_result():
    chunks = list(run_multi_org_stream(
        "revenue", [5, 6], META, runner_factory=FakeRunner, run_kwargs={"allowed_org_ids": [5, 6]},
    ))
    progress = [c for c in chunks if c.get("organization_id")]
    assert sorted(c["organization_id"] for c in progress) == [5, 6]
    assert "final_result" in chunks[-1]


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", True)
    # These tests cover the route and the per-org fan-out; planning has its own tests.
    monkeypatch.setattr("gemini_brain.api.routes.plan_query", lambda *_a: None)
    return TestClient(app)


def _bearer(allowed):
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=allowed)
    return {"Authorization": f"Bearer {token}"}


def _meta_stub(current_user, orgs):
    return {o: META.get(o, {"name": f"Organization {o}", "currency": ""}) for o in orgs}


def test_query_route_fans_out_and_returns_merged_answer(client):
    with patch("gemini_brain.api.routes.GeminiBrainRunner", FakeRunner), \
         patch("gemini_brain.api.routes._org_meta", _meta_stub):
        resp = client.post("/api/v1/query", json={"query": "revenue", "organization_ids": [5, 6]},
                           headers=_bearer([5, 6]))
    assert resp.status_code == 200
    body = resp.json()
    assert [o["name"] for o in body["organizations"]] == ["Alpha LLC", "Beta Inc"]
    assert sorted(c["organization_id"] for c in FakeRunner.calls) == [5, 6]


def test_stream_route_fans_out(client):
    with patch("gemini_brain.api.routes.GeminiBrainRunner", FakeRunner), \
         patch("gemini_brain.api.routes._org_meta", _meta_stub):
        resp = client.post("/api/v1/query/stream", json={"query": "revenue", "organization_ids": [5, 6]},
                           headers=_bearer([5, 6]))
    assert resp.status_code == 200
    events = [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]
    final = next(e["final_result"] for e in events if "final_result" in e)
    assert final["routing_info"]["path"] == "multi_org"


def test_model_comparison_route_stays_single_org(client):
    with patch("gemini_brain.api.routes.GeminiBrainRunner", FakeRunner):
        resp = client.post("/api/v1/query/all", json={"query": "revenue", "organization_ids": [5, 6]},
                           headers=_bearer([5, 6]))
    assert resp.status_code == 400
    assert FakeRunner.calls == []


def test_unassigned_org_still_refused_before_fan_out(client):
    with patch("gemini_brain.api.routes.GeminiBrainRunner", FakeRunner):
        resp = client.post("/api/v1/query", json={"query": "revenue", "organization_ids": [5, 9]},
                           headers=_bearer([5, 6]))
    assert resp.status_code == 403
    assert FakeRunner.calls == []


def test_org_meta_never_invents_a_currency(monkeypatch):
    from gemini_brain.api import routes
    monkeypatch.setattr(routes, "fetch_organizations_from_db", lambda ids: [])
    user = auth.CurrentUser(user_id=7, email="", allowed_org_ids=[5])
    assert routes._org_meta(user, [5]) == {5: {"name": "Organization 5", "currency": ""}}


def test_org_meta_adds_id_when_two_reachable_orgs_share_a_name(monkeypatch):
    from gemini_brain.api import routes
    db = {
        17: {"id": 17, "name": "Services_Org4", "currency": "AED"},
        27: {"id": 27, "name": "Services_Org4", "currency": "AED"},
        29: {"id": 29, "name": "Services_Org6", "currency": "AED"},
    }
    monkeypatch.setattr(routes, "fetch_organizations_from_db", lambda ids: [db[i] for i in ids if i in db])
    user = auth.CurrentUser(user_id=7, email="", allowed_org_ids=[17, 27, 29])
    # Org 17 is not selected, yet its shared name still earns org 27 an ID.
    meta = routes._org_meta(user, [27, 29])
    assert meta[27]["name"] == "Services_Org4 (ID 27)"
    assert meta[29]["name"] == "Services_Org6"



# ── Follow-up rewrite only when needed ───────────────────────────────────────

from gemini_brain.orchestrator.multi_org import needs_rewrite


@pytest.mark.parametrize("question, rewrite", [
    ("Compare total revenue this year", False),
    ("What is the total revenue for each organization this year?", False),
    ("List vendors with overdue bills in each organization", False),
    ("and Q2?", True),
    ("what about last quarter", True),
    ("same for payables", True),
    ("Compare total revenue for them", True),
    ("Tell me more", True),
])
def test_rewrite_only_follow_ups(question, rewrite):
    assert needs_rewrite(question) is rewrite


def test_standalone_question_in_existing_chat_is_not_rewritten():
    calls = []

    class Runner(FakeRunner):
        def _call_llm(self, system, user_text, **kwargs):
            calls.append(kwargs.get("purpose"))
            return "Rephrased question?", 1, 1

    with patch.object(multi_org, "_load_history", return_value=[{"role": "user", "content": "earlier"}]), \
         patch.object(multi_org, "_persist_turn"):
        run_multi_org("Compare total revenue this year", [5, 6], META, runner_factory=Runner,
                      run_kwargs={"session_id": "abc", "allowed_org_ids": [5, 6]})
    assert "multi_org_standalone" not in calls


def test_collapsed_layout_keeps_ten_org_sections_open():
    from gemini_brain.orchestrator.multi_org_present import collapsed_org_sections
    runs = [multi_org.OrgRun(i, f"Org {i}", "AED", result={"status": "ok", "blocks": []}) for i in range(10)]
    assert collapsed_org_sections(runs)["sections_open"] is True
