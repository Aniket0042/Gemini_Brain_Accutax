"""AI usage limits (docs/ORG_TOKEN_LIMITS_STRATEGY.md): owner buckets, lite model, personal caps, speed, APIs."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from gemini_brain.agent import loop, preview
from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.usage import ledger, limits, plans, store


class FakeDB:
    """The usage tables in memory. Owner 7 owns orgs 24 and 25, owner 9 owns org 30; user 50 is a collaborator."""

    def __init__(self):
        self.used = {}             # owner -> credits this month
        self.user_today = {}       # (owner, user) -> credits today
        self.plans = {}            # owner -> plan row
        self.caps = {}             # (owner, user) -> cap row
        self.topups = {}
        self.seat_count = {7: 2, 9: 1}
        self.platform = 0.0
        self.alerts = []
        self.admins = set()
        self.set_caps = []

    def install(self, monkeypatch):
        m = monkeypatch.setattr
        m(store, "plan_row", lambda o: self.plans.get(o))
        m(store, "seats", lambda o: self.seat_count.get(o, 1))
        m(store, "topup_credits", lambda o: self.topups.get(o, 0.0))
        m(store, "owner_used", lambda o, since: self.used.get(o, 0.0))
        m(store, "user_used", lambda o, u, since: self.user_today.get((o, u), 0.0))
        m(store, "cap_row", lambda o, u: self.caps.get((o, u)))
        m(store, "caps_of", lambda o: {u: c for (oo, u), c in self.caps.items() if oo == o})
        m(store, "platform_cost", lambda since: self.platform)
        m(store, "is_super_admin", lambda u: u in self.admins)
        m(store, "alert_once", lambda *a: self.alerts.append(a) or True)
        m(store, "user_daily_history", lambda *a: 0.0)
        m(store, "owned_by", lambda u: {7: [24, 25], 9: [30]}.get(u, []))
        m(store, "collaborators_of", lambda o: [{"user_id": 50, "email": "c@x.com", "status": "ACTIVE"}] if o == 7 else [])
        m(store, "member_usage", lambda o, a, b: [{"user_id": 50, "month_credits": 40.0, "today_credits": 5.0, "events": 2}])
        m(store, "user_emails", lambda ids: {i: f"u{i}@x.com" for i in ids})
        m(store, "set_cap", lambda *a: self.set_caps.append(a))
        m(ledger, "owners_of", lambda orgs: {o: {24: 7, 25: 7, 30: 9}.get(o) for o in orgs})
        m(ledger.settings, "usage_db", "on")
        m(ledger.settings, "usage_ledger", "")
        m(store, "open_event", lambda *a, **k: 1)
        m(store, "close_event", lambda *a, **k: None)
        limits._raised.clear()
        return self


@pytest.fixture
def db(monkeypatch):
    return FakeDB().install(monkeypatch)


@pytest.fixture
def hard(monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_enforce", "hard")


# Standard plan: 10,000 credits a seat; owner 7 has 2 seats -> 20,000; lite until 22,000.

def test_off_never_touches_the_database(monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_enforce", "off")
    monkeypatch.setattr(store, "owner_used", lambda *a: 1 / 0)
    assert limits.check(50, [24]).action == limits.ALLOW


def test_under_the_bucket_is_allowed(db, hard):
    db.used[7] = 5_000
    d = limits.check(50, [24])
    assert d.action == limits.ALLOW and d.owners[0]["bucket"] == 20_000 and d.owners[0]["percent"] == 25


def test_used_bucket_switches_to_lite_then_blocks(db, hard):
    db.used[7] = 20_000
    d = limits.check(50, [24])
    assert d.action == limits.LITE and "lighter model" in d.banner
    db.used[7] = 22_000
    d = limits.check(50, [24])
    assert d.action == limits.BLOCK and d.code == "USAGE_LIMIT"


def test_topups_and_monthly_override_size_the_bucket(db, hard):
    db.plans[7] = {"plan": "pro", "monthly_credits": 1_000, "user_daily_credits": None}
    db.topups[7] = 500
    assert limits.check(50, [24]).owners[0]["bucket"] == 1_500


def test_unlimited_plan_has_no_bucket_or_cap(db, hard):
    db.plans[7] = {"plan": "unlimited", "monthly_credits": None, "user_daily_credits": None}
    db.used[7] = 10**9
    db.user_today[(7, 50)] = 10**9
    d = limits.check(50, [24])
    assert d.action == limits.ALLOW and d.owners[0]["bucket"] is None


def test_question_across_owners_takes_the_strictest(db, hard):
    db.used[9] = 11_000          # owner 9: 1 seat -> 10,000, lite until 11,000 -> blocked
    d = limits.check(50, [24, 30])
    assert d.action == limits.BLOCK and "One of the organizations" in d.message
    assert {o["owner_user_id"] for o in d.owners} == {7, 9}


def test_personal_cap_set_by_owner_and_plan_default(db, hard):
    db.user_today[(7, 50)] = 1_000            # the standard default cap
    assert limits.check(50, [24]).code == "USAGE_USER_DAILY"
    db.caps[(7, 50)] = {"daily_credits": 3_000, "disabled": False}   # the owner raised it
    assert limits.check(50, [24]).action == limits.ALLOW
    db.caps[(7, 50)] = {"daily_credits": 3_000, "disabled": True}
    assert limits.check(50, [24]).code == "USAGE_DISABLED"


def test_super_admin_is_internal_and_not_limited(db, hard):
    db.admins.add(1)
    db.used[7] = 10**9
    db.platform = 10**6
    d = limits.check(1, [24])
    assert d.action == limits.ALLOW and d.internal


def test_platform_pause_and_alert(db, hard):
    db.platform = 30
    assert limits.check(50, [24]).action == limits.ALLOW
    assert any(a[2] == "platform_alert" for a in db.alerts)
    db.platform = 150
    assert limits.check(50, [24]).code == "USAGE_PAUSED"


def test_owner_alerts_fire_once(db, hard):
    db.used[7] = 17_000                       # 85%
    limits.check(50, [24])
    limits.check(50, [24])
    levels = [a[2] for a in db.alerts]
    assert levels.count("50") == 1 and levels.count("80") == 1 and "100" not in levels


def test_soft_shows_the_state_but_blocks_nothing(db, monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_enforce", "soft")
    db.used[7] = 30_000
    d = limits.check(50, [24])
    assert d.action == limits.ALLOW and limits.public(d)["owners"][0]["state"] == "blocked"


def test_log_mode_returns_no_usage_block(db, monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_enforce", "log")
    db.used[7] = 30_000
    d = limits.check(50, [24])
    assert d.action == limits.ALLOW and limits.public(d) is None


def test_failed_check_lets_the_question_through(db, hard, monkeypatch):
    monkeypatch.setattr(store, "owner_used", lambda *a: 1 / 0)
    assert limits.check(50, [24]).action == limits.ALLOW


def test_file_read_is_never_lite(db, hard):
    db.used[7] = 20_000
    assert limits.check(50, [24], kind="document_read").action == limits.ALLOW


def test_gate_one_at_a_time_and_per_minute(monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_rate_per_minute", 2)
    g = limits.Gate()
    assert g.enter(5) is None
    assert g.enter(5).code == "USAGE_BUSY"
    g.leave(5)
    assert g.enter(5) is None
    g.leave(5)
    assert g.enter(5).code == "USAGE_RATE"


# ── The answer path ──────────────────────────────────────────────────────────

def _agent(monkeypatch, seen):
    def run_agent(*a, **k):
        seen.append(k)
        return loop.AgentResult(answer="Revenue AED 5.", status="ok")
    monkeypatch.setattr(loop, "run_agent", run_agent)


def test_blocked_question_never_calls_the_model(db, hard, monkeypatch):
    seen = []
    _agent(monkeypatch, seen)
    db.used[7] = 25_000
    r = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=50)
    assert seen == [] and r["status"] == "failed" and r["notice"]["code"] == "USAGE_LIMIT"
    assert r["notice"]["kind"] == "limit"


def test_lite_answer_uses_the_lite_model(db, hard, monkeypatch):
    from gemini_brain.agent import models as agent_models
    seen = []
    _agent(monkeypatch, seen)
    db.used[7] = 20_500
    r = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=50)
    assert seen and seen[0]["model_id"] == agent_models.get("haiku-4.5").bedrock_id()
    assert r["usage"]["mode"] == "lite" and r["usage"]["banner"]


def test_answer_carries_usage_and_releases_the_gate(db, hard, monkeypatch):
    seen = []
    _agent(monkeypatch, seen)
    preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=51)
    r = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=51)
    assert r["status"] == "ok" and r["usage"]["owners"][0]["owner_user_id"] == 7


# ── API ──────────────────────────────────────────────────────────────────────

def _client(user_id, orgs=(24, 25, 30)):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, email=f"u{user_id}@x.com", allowed_org_ids=list(orgs))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _no_overrides():
    yield
    app.dependency_overrides.clear()


def test_my_usage_shows_percent_not_credits(db, hard):
    db.used[7] = 10_000
    body = _client(50).get("/api/v1/usage?org_ids=24").json()
    assert body["owners"][0]["percent"] == 50 and "used" not in body["owners"][0]
    assert body["is_owner"] is False and body["is_admin"] is False


def test_owner_page_and_caps(db, hard):
    c = _client(7)
    body = c.get("/api/v1/usage/owner").json()
    assert {m["user_id"] for m in body["members"]} == {7, 50}
    assert c.put("/api/v1/usage/owner/members/50", json={"daily_credits": 2_500, "disabled": False}).status_code == 200
    assert db.set_caps[-1][:4] == (7, 50, 2_500, False)
    assert c.put("/api/v1/usage/owner/members/50", json={"daily_credits": 10**7}).status_code == 400
    assert c.put("/api/v1/usage/owner/members/999", json={"daily_credits": 5}).status_code == 404


def test_owner_page_needs_an_owner(db, hard):
    assert _client(50).get("/api/v1/usage/owner").status_code == 403


def test_admin_page_is_for_admins_only(db, hard, monkeypatch):
    assert _client(50).get("/api/v1/usage/admin").status_code == 403
    monkeypatch.setattr(store, "owners_usage", lambda since: [{"owner_user_id": 7, "month_credits": 9.0, "users": 1, "events": 1}])
    monkeypatch.setattr(store, "plans_all", lambda: {})
    monkeypatch.setattr(store, "recent_alerts", lambda n: [])
    db.admins.add(1)
    body = _client(1).get("/api/v1/usage/admin").json()
    assert body["owners"][0]["owner_user_id"] == 7 and body["owners"][0]["email"] == "u7@x.com"


def test_admin_sets_plan_and_topup(db, hard, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "set_plan", lambda *a: calls.append(("plan", a)))
    monkeypatch.setattr(store, "add_topup", lambda *a: calls.append(("topup", a)) or 3)
    db.admins.add(1)
    c = _client(1)
    assert c.put("/api/v1/usage/admin/owners/7/plan", json={"plan": "nope"}).status_code == 400
    assert c.put("/api/v1/usage/admin/owners/7/plan", json={"plan": "pro"}).status_code == 200
    assert c.post("/api/v1/usage/admin/owners/7/topups", json={"credits": 500, "reason": "pilot"}).json()["id"] == 3
    assert [k for k, _ in calls] == ["plan", "topup"]


def test_usage_api_is_off_without_the_tables(monkeypatch):
    monkeypatch.setattr(ledger.settings, "usage_db", "off")
    assert _client(50).get("/api/v1/usage").status_code == 503


def test_lite_answer_does_not_become_the_threads_model(db, hard, monkeypatch):
    saved = []
    monkeypatch.setattr(preview, "record_turn", lambda *a, **k: saved.append(k.get("model_key")))
    monkeypatch.setattr(preview, "_save_turn", lambda *a, **k: True)
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="Revenue AED 5.", status="ok"))
    sid = "8f14e45f-ceea-4e1a-9a3b-7c3c0c2e1d11"
    db.used[7] = 20_500                      # lite
    preview.answer("Revenue?", [24], lambda: {}, session_id=sid, user_id=52)
    db.used[7] = 0                           # renewed
    preview.answer("Revenue?", [24], lambda: {}, session_id=sid, user_id=52)
    assert saved == [None, "sonnet-5"]


def test_admin_finds_owners_who_have_not_used_ai(db, hard, monkeypatch):
    monkeypatch.setattr(store, "search_owners", lambda q: [{"owner_user_id": 9, "email": "u9@x.com", "organizations": 1}])
    assert _client(50).get("/api/v1/usage/admin/owners?q=u9").status_code == 403
    db.admins.add(1)
    assert _client(1).get("/api/v1/usage/admin/owners?q=u9").json()["owners"][0]["owner_user_id"] == 9
