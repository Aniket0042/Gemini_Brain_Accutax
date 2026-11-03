"""Bots for any agent (docs/BOT_AGENTS_AND_AP_PLAN.md, part A): the registry, VAT as its first agent with the same
behaviour as before, the agents API, the daily schedule kind, and findings that change rule under one key."""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots import agents, cost, monitor, preview, store, validate, watches
from gemini_brain.bots.schedule import next_runs
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.watches import Watch
from gemini_brain.config.settings import settings

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=18, email="tester@example.com", allowed_org_ids=[101, 102])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def test_agent():
    """A second agent with one document watch and one state watch, registered for the test only."""
    vat = agents.get("vat")
    engine = MagicMock()
    engine.calls.side_effect = lambda ids, sensitivity: [{"side": "purchases", "rules": sorted(ids)}] if ids else []
    engine.run.return_value = {"groups": [], "findings": []}
    agent = dataclasses.replace(
        vat, key="test", name="Test",
        watches=(Watch(id="doc_check", label="Doc", side="purchases", rules=("r1",), default_on=True),
                 Watch(id="late_check", label="Late", side="purchases", rules=("r2",), default_on=True,
                       kind="state")),
        templates=(), schedule_kinds=("weekly", "daily"), engine=engine, credits_per_run=10)
    agents.register(agent)
    yield agent
    agents._AGENTS.pop("test", None)


# --- registry -----------------------------------------------------------------------------------------------------

def test_the_vat_agent_is_registered():
    assert agents.get("vat").key == "vat"
    assert "vat" in agents.keys()


def test_an_unknown_agent_is_a_key_error():
    with pytest.raises(KeyError):
        agents.get("nope")


def test_public_has_no_engine_and_is_json():
    for a in agents.all_agents():
        p = agents.public(a)
        assert "engine" not in p and "draft_prompt" not in p and "summary_cards" not in p
        json.dumps(p)


def test_watch_ids_are_unique_within_each_agent():
    for a in agents.all_agents():
        ids = [w.id for w in a.watches]
        assert len(ids) == len(set(ids))


# --- VAT keeps its behaviour --------------------------------------------------------------------------------------

def test_the_vat_engine_builds_the_same_review_calls():
    calls = agents.get("vat").engine.calls(["duplicate_bill", "vat_rate_wrong", "sales_vat_wrong"], "balanced")
    assert calls == [
        {"mode": "all", "side": "purchases",
         "rules": ["same_number", "same_total_near_date", "similar_vendor_same_day", "vat_rate_not_standard",
                   "vat_without_amount", "vat_wrong_sign"]},
        {"mode": "all", "side": "sales", "rules": ["vat_rate_not_standard", "vat_wrong_sign", "vat_zero"]},
    ]


def test_defaults_keep_vat_behaviour():
    assert watches.review_calls(["duplicate_bill"]) == agents.get("vat").engine.calls(["duplicate_bill"], "balanced")
    assert watches.default_ids() == ["duplicate_bill", "vat_rate_wrong", "sales_vat_wrong"]
    assert BotDraft().agent == "vat"
    assert cost.monthly_credits(BotDraft(schedules=[Schedule(kind="weekly", day="mon")]), {}) == 390


def test_a_draft_of_an_unknown_agent_is_rejected():
    with pytest.raises(ValueError):
        BotDraft(agent="nope")


def test_the_try_run_reports_the_vat_return_as_its_position():
    review = {"side": "purchases", "bills_checked": 2, "groups": [], "findings": [], "organizations": [
        {"organization_id": 101, "currency": "AED", "complete": True}], "period": {"start": "2026-10-01"}}
    ret = {"organizations": [{"organization_id": 101, "period": {"start": "2026-10-01", "end": "2026-12-31"},
                              "due_date": "2027-01-28", "net_vat_payable": 10.0}]}
    with patch("gemini_brain.vat_review.tool.run", return_value=review), \
         patch("gemini_brain.vat_return.tool.run", return_value=ret):
        res = preview.try_period(BotDraft(org_ids=[101], watches=["duplicate_bill"]), preset="last_month", subject="t")
    assert res["position"]["kind"] == "vat_return" and res["position"]["due_date"] == "2027-01-28"
    assert res["return"] == ret["organizations"]


# --- another agent ------------------------------------------------------------------------------------------------

def test_a_vat_bot_cannot_run_daily():
    draft = BotDraft(org_ids=[101], watches=["duplicate_bill"], schedules=[Schedule(kind="daily")])
    _, issues = validate.validate(draft, [101], [101])
    assert any(i.field == "schedules[0].kind" and not i.fixed for i in issues)


def test_an_agent_with_daily_runs_validates_with_its_own_watches(test_agent):
    draft = BotDraft(agent="test", org_ids=[101], name=" ", schedules=[Schedule(kind="daily")])
    fixed, issues = validate.validate(draft, [101], [101])
    assert fixed.agent == "test" and fixed.watches == ["doc_check", "late_check"]
    assert not [i for i in issues if not i.fixed]


def test_the_monitor_skips_state_watches(test_agent):
    bot = {"id": "b1", "owner_user_id": 7, "name": "T", "agent": "test",
           "draft": BotDraft(agent="test", org_ids=[101], watches=["doc_check", "late_check"]).model_dump()}
    with patch.object(store, "list_active_bots_for_org", return_value=[bot]), \
         patch.object(store, "get_findings", return_value=[]), \
         patch.object(monitor, "get_user_allowed_orgs", return_value=[101]):
        monitor.handle_document_event("bill.saved", 101, [5])
    test_agent.engine.calls.assert_called_once_with(["doc_check"], "balanced")
    assert test_agent.engine.run.call_count == 1


def test_inbox_findings_carry_their_agent(test_agent):
    from gemini_brain.bots import inbox
    out = inbox.build([{"bot_id": "b1", "key": "k", "rule": "r1", "state": "new", "detail": {}}], [],
                      [{"id": "b1", "name": "T", "agent": "test"}])
    assert out["findings"][0]["agent"] == "test"


# --- API ----------------------------------------------------------------------------------------------------------

def test_get_agents_lists_vat(auth_headers):
    res = client.get("/api/v1/bots/agents", headers=auth_headers)
    assert res.status_code == 200
    vat = next(a for a in res.json()["agents"] if a["key"] == "vat")
    assert vat["copy"]["label"] == "VAT agent" and len(vat["watches"]) == 5 and len(vat["templates"]) == 3


def test_an_unknown_agent_is_404(auth_headers):
    assert client.get("/api/v1/bots/templates?agent=nope", headers=auth_headers).status_code == 404
    assert client.get("/api/v1/bots/watches?agent=nope", headers=auth_headers).status_code == 404


def test_a_draft_with_an_unknown_agent_is_422(auth_headers):
    res = client.post("/api/v1/bots/draft/check", json={"draft": {"agent": "nope"}}, headers=auth_headers)
    assert res.status_code == 422


def test_from_chat_rejects_an_unknown_agent(auth_headers):
    res = client.post("/api/v1/bots/draft/from-chat", json={"question": "x", "agent": "nope"}, headers=auth_headers)
    assert res.status_code == 400


# --- daily schedule, findings that change rule --------------------------------------------------------------------

def test_daily_runs_every_day_after_now():
    tz = ZoneInfo(settings.report_timezone)
    now = dt.datetime(2026, 10, 30, 10, 0, tzinfo=tz)  # Friday
    runs = next_runs(BotDraft(schedules=[Schedule(kind="daily", time="08:00")]), now, frequencies={}, count=3)
    assert [r.at for r in runs] == [dt.datetime(2026, 10, d, 8, 0, tzinfo=tz) for d in (31,)] + \
        [dt.datetime(2026, 11, d, 8, 0, tzinfo=tz) for d in (1, 2)]
    assert all(r.reason == "daily" for r in runs)


def test_a_daily_schedule_costs_thirty_runs():
    assert cost.monthly_credits(BotDraft(org_ids=[101], schedules=[Schedule(kind="daily")]), {}) == 30 * 90


def test_a_finding_keeps_its_key_when_its_rule_changes():
    cur = MagicMock()
    with patch.object(store, "_get_cur") as get_cur:
        get_cur.return_value.__enter__.return_value = cur
        store.upsert_findings("b1", "r1", [{"key": "k", "org_id": 1, "rule": "bill_overdue", "state": "new"}])
    sql = cur.executemany.call_args.args[0] if cur.executemany.called else cur.execute.call_args.args[0]
    assert "rule = EXCLUDED.rule" in sql


def test_the_browser_fallback_has_the_vat_agents_words():
    """frontend botText.js DEFAULT_AGENT shows the VAT agent until GET /bots/agents loads; its words must match."""
    from pathlib import Path
    js_path = Path(__file__).resolve().parents[3] / "frontend" / "src" / "components" / "bots" / "botText.js"
    if not js_path.exists():
        pytest.skip("frontend not checked out next to backend")
    js = js_path.read_text(encoding="utf-8")

    def strings(v):
        if isinstance(v, dict):
            return [s for x in v.values() for s in strings(x)]
        if isinstance(v, list):
            return [s for x in v for s in strings(x)]
        return [v] if isinstance(v, str) else []

    pub = agents.public(agents.get("vat"))
    words = strings(pub["copy"]) + [w[f] for w in pub["watches"] for f in ("id", "label", "desc", "bar")] + \
        [t[f] for t in pub["templates"] for f in ("id", "tag", "runs")] + strings(pub["columns"])
    missing = [w for w in words if w not in js]
    assert not missing, missing
