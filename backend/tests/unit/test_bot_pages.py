"""Bots pages (phase 1 of the redesign): the home numbers, a bot's runs and findings, Run now, the bot limit, and the
details each finding keeps for the inbox."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api import bot_routes
from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots import run as run_mod
from gemini_brain.bots import store
from gemini_brain.bots.schema import BotDraft

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=18, email="tester@example.com", allowed_org_ids=[101, 102])
    return {"Authorization": f"Bearer {token}"}


def _bot(bot_id="b1", status="active", next_run="2026-11-02T08:00:00+04:00", schedules=None):
    draft = BotDraft(name=f"Bot {bot_id}", org_ids=[101], watches=["duplicate_bill", "vat_rate_wrong"],
                     schedules=schedules or [{"kind": "weekly", "day": "mon", "time": "08:00"}])
    return {"id": bot_id, "owner_user_id": 18, "agent": "vat", "name": draft.name, "org_ids": [101],
            "draft": draft.model_dump(), "status": status, "status_note": None, "next_run_at": next_run}


def _overview(**kw):
    base = {"last_run": None, "open": 0, "new": 0, "fixed": 0, "found": 0, "runs": 0, "credits_this_month": 0}
    return {**base, **kw}


def test_the_bots_list_carries_each_bots_overview_and_cost(auth_headers):
    with patch.object(store, "list_for_user", return_value=[_bot()]), \
         patch.object(store, "overviews", return_value={"b1": _overview(runs=3, credits_this_month=180)}):
        res = client.get("/api/v1/bots", headers=auth_headers)
    bot = res.json()["bots"][0]
    assert bot["overview"]["runs"] == 3 and bot["overview"]["credits_this_month"] == 180
    assert bot["monthly_credits"] == 390


def test_the_home_summary(auth_headers):
    bots = [_bot("b1", next_run="2026-11-09T08:00:00+04:00"), _bot("b2", next_run="2026-11-02T08:00:00+04:00"),
            _bot("b3", status="paused", next_run="2026-10-31T08:00:00+04:00")]
    findings = {
        "b1": [{"state": "new", "rule": "same_number"}, {"state": "seen", "rule": "vat_rate_not_standard"},
               {"state": "fixed", "rule": "same_number"}],
        "b2": [{"state": "dismissed", "rule": "same_number"}],
        "b3": [],
    }
    ret = {"organizations": [{"organization_id": 101, "period": {"start": "2026-10-01", "end": "2026-12-31"},
                              "due_date": "2027-01-28", "days_to_due": 90}]}
    with patch.object(store, "list_for_user", return_value=bots), \
         patch.object(store, "overviews", return_value={b["id"]: _overview(credits_this_month=90) for b in bots}), \
         patch.object(store, "get_findings", side_effect=lambda bot_id, *a, **k: findings[bot_id]), \
         patch.object(bot_routes.vat_return_tool, "run", return_value=ret):
        out = client.get("/api/v1/bots/summary", headers=auth_headers).json()
    assert out["next_run"]["bot_id"] == "b2"  # the paused bot's earlier time does not count
    assert out["open_problems"] == {"total": 2, "new": 1, "duplicates": 1, "other": 1}
    assert out["next_return"]["due_date"] == "2027-01-28"
    assert out["credits"] == {"estimate_per_month": 780, "this_month": 270}
    assert out["bots"] == 3 and out["active"] == 2 and out["max_bots"] == 10


def test_runs_and_findings_are_only_for_the_owner(auth_headers):
    with patch.object(store, "get", return_value=None):
        assert client.get("/api/v1/bots/b1/runs", headers=auth_headers).status_code == 404
        assert client.get("/api/v1/bots/b1/findings", headers=auth_headers).status_code == 404
    with patch.object(store, "get", return_value=_bot()), \
         patch.object(store, "list_runs", return_value=[{"id": "r1"}]) as runs, \
         patch.object(store, "get_findings", return_value=[{"key": "k1"}]):
        assert client.get("/api/v1/bots/b1/runs?limit=5", headers=auth_headers).json() == {"runs": [{"id": "r1"}]}
        assert client.get("/api/v1/bots/b1/findings", headers=auth_headers).json() == {"findings": [{"key": "k1"}]}
    runs.assert_called_once_with("b1", limit=5)


def test_run_now(auth_headers):
    with patch.object(store, "get", return_value=_bot(status="paused")):
        assert client.post("/api/v1/bots/b1/run", headers=auth_headers).status_code == 409
    with patch.object(store, "get", return_value=_bot()), \
         patch.object(run_mod, "run_bot", return_value={"status": "ok", "summary": {"headline": "Checked 3 bills."}}) as run:
        res = client.post("/api/v1/bots/b1/run", headers=auth_headers)
    assert res.status_code == 200 and res.json()["summary"]["headline"] == "Checked 3 bills."
    run.assert_called_once_with("b1", trigger="manual")


def test_no_more_than_ten_bots(auth_headers):
    with patch.object(store, "list_for_user", return_value=[_bot(str(i)) for i in range(10)]), \
         patch.object(store, "create") as create:
        res = client.post("/api/v1/bots", json={"draft": {"org_ids": [101]}}, headers=auth_headers)
    assert res.status_code == 409
    create.assert_not_called()


def test_each_finding_keeps_its_bills_and_sentence():
    group = {"key": "g1", "organization_id": 101, "rule": "same_total_near_date", "extra_amount": 600.0,
             "confidence": "medium", "currency": "AED", "why": "Same total, two days apart.",
             "bills": [{"document_id": 11, "document_number": "INV-2231", "document_date": "2026-10-14",
                        "vendor_name": "Gulf Agri Supplies", "net_purchases": 12000.0, "input_vat": 600.0},
                       {"document_id": 12, "document_number": "INV-2231A", "document_date": "2026-10-16",
                        "vendor_name": "Gulf Agri Supplies", "net_purchases": 12000.0, "input_vat": 600.0}]}
    out = run_mod._current_findings([{"side": "purchases", "groups": [group], "findings": []}], 101)
    d = out[0]["detail"]
    assert d["side"] == "purchases" and d["why"] == "Same total, two days apart." and d["level"] == "medium"
    assert [b["number"] for b in d["bills"]] == ["INV-2231", "INV-2231A"]
    assert d["bills"][0] == {"id": 11, "number": "INV-2231", "date": "2026-10-14", "party": "Gulf Agri Supplies",
                             "amount": 12000.0, "vat": 600.0}
