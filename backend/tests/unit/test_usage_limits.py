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
        m(store, "seats", lambda o, as_of=None: self.seat_count.get(o, 1))
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
    monkeypatch.setattr(store, "search_collaborators", lambda q: [])
    assert _client(50).get("/api/v1/usage/admin/owners?q=u9").status_code == 403
    db.admins.add(1)
    assert _client(1).get("/api/v1/usage/admin/owners?q=u9").json()["owners"][0]["owner_user_id"] == 9


def test_admin_search_finds_collaborators_with_their_owner(db, hard, monkeypatch):
    monkeypatch.setattr(store, "search_owners", lambda q: [])
    monkeypatch.setattr(store, "search_collaborators", lambda q: [
        {"user_id": 50, "email": "c@x.com", "status": "ACTIVE", "owner_user_id": 7, "owner_email": "u7@x.com"}])
    db.admins.add(1)
    body = _client(1).get("/api/v1/usage/admin/owners?q=c@x").json()
    assert body["owners"] == [] and body["collaborators"][0]["owner_user_id"] == 7


def test_store_collaborator_search_matches_email_or_id(monkeypatch):
    cur = _open_with((0,), monkeypatch)
    cur.fetchall = lambda: [(1034, "user_org27@accutax.com", "ACTIVE", 5, "owner@x.com")]
    found = store.search_collaborators("user_org27")
    assert found == [{"user_id": 1034, "email": "user_org27@accutax.com", "status": "ACTIVE",
                      "owner_user_id": 5, "owner_email": "owner@x.com"}]
    assert "c.status IN ('ACTIVE', 'INVITED')" in cur.sql[0] and "c.email ILIKE" in cur.sql[0]


def test_admin_list_names_the_people_who_used_each_bucket(db, hard, monkeypatch):
    monkeypatch.setattr(store, "owners_usage", lambda since: [
        {"owner_user_id": 7, "month_credits": 9.0, "users": 2, "events": 3, "user_ids": [50, 7]}])
    monkeypatch.setattr(store, "plans_all", lambda: {})
    monkeypatch.setattr(store, "recent_alerts", lambda n: [])
    db.admins.add(1)
    owner = _client(1).get("/api/v1/usage/admin").json()["owners"][0]
    assert owner["people"] == ["u7@x.com", "u50@x.com"] and "user_ids" not in owner


# ── Speed limits in the usage tables (shared by every copy of the service) ────

def test_db_speed_refusal_never_calls_the_model(db, hard, monkeypatch):
    seen, gates = [], []

    def open_event(*a, gate=None, **k):
        gates.append(gate)
        raise store.Refused("USAGE_BUSY")
    monkeypatch.setattr(store, "open_event", open_event)
    _agent(monkeypatch, seen)
    r = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=50)
    assert seen == [] and r["notice"]["code"] == "USAGE_BUSY" and gates == [(1, 5)]


def test_db_speed_limit_is_skipped_when_not_hard(db, monkeypatch):
    monkeypatch.setattr(limits.settings, "usage_enforce", "soft")
    gates = []
    monkeypatch.setattr(store, "open_event", lambda *a, gate=None, **k: gates.append(gate) or 1)
    _agent(monkeypatch, [])
    preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=50)
    assert gates == [None]


class _Cursor:
    def __init__(self, counts):
        self.counts, self.sql = counts, []

    def execute(self, sql, params=None):
        self.sql.append(sql)

    def fetchone(self):
        return self.counts if "COUNT(" in self.sql[-1] else (11,)

    def close(self):
        pass


def _open_with(counts, monkeypatch):
    cur = _Cursor(counts)
    conn = SimpleNamespace(cursor=lambda: cur, commit=lambda: cur.sql.append("COMMIT"),
                           rollback=lambda: cur.sql.append("ROLLBACK"), close=lambda: None)
    monkeypatch.setattr(store, "get_connection", lambda: conn)
    return cur


@pytest.mark.parametrize("counts,code", [((1, 1), "USAGE_BUSY"), ((0, 5), "USAGE_RATE")])
def test_store_refuses_under_the_lock_and_inserts_nothing(monkeypatch, counts, code):
    cur = _open_with(counts, monkeypatch)
    with pytest.raises(store.Refused) as exc:
        store.open_event("agent_answer", 50, None, [24], {7: 35}, gate=(1, 5))
    assert exc.value.code == code
    assert "pg_advisory_xact_lock" in cur.sql[0] and not any("INSERT" in s for s in cur.sql)
    assert cur.sql[-1] == "ROLLBACK"


def test_store_opens_the_event_when_under_the_limits(monkeypatch):
    cur = _open_with((0, 4), monkeypatch)
    assert store.open_event("agent_answer", 50, None, [24], {7: 35}, gate=(1, 5)) == 11
    assert any("INSERT INTO public.ai_usage_events" in s for s in cur.sql) and cur.sql[-1] == "COMMIT"


# ── Seats and lite law answers ───────────────────────────────────────────────

def test_seats_are_capped_by_the_plan(db, hard):
    db.seat_count[7] = 40                                 # 40 people, Standard allows 10 seats
    st = limits.check(50, [24]).owners[0]
    assert st["seats"] == 10 and st["seats_counted"] == 40 and st["bucket"] == 100_000


def test_seats_are_counted_as_the_month_began(db, hard, monkeypatch):
    asked = []
    monkeypatch.setattr(store, "seats", lambda o, as_of=None: asked.append(as_of) or 1)
    limits.check(50, [24])
    assert asked == [limits.periods().month_start]


def test_store_seats_ignore_people_who_joined_this_month(monkeypatch):
    cur = _open_with((3,), monkeypatch)
    month = limits.periods().month_start
    assert store.seats(7, month) == 4
    sql = cur.sql[0]
    assert "COALESCE(c.approved_at, c.accepted_at) < %s::timestamptz" in sql and "status = 'ACTIVE'" in sql


def test_lite_answers_use_the_lite_model_for_vat_law_too(db, hard, monkeypatch):
    from gemini_brain.agent import models as agent_models
    seen = []
    _agent(monkeypatch, seen)
    db.used[7] = 20_500                                   # lite
    preview.answer("Is VAT due on exports?", [24], lambda: {}, session_id=None, user_id=53)
    assert seen[0]["law_model_id"] == agent_models.get("haiku-4.5").bedrock_id()
    db.used[7] = 0                                        # full
    preview.answer("Is VAT due on exports?", [24], lambda: {}, session_id=None, user_id=53)
    assert "law_model_id" not in seen[1]


def test_law_route_uses_the_model_it_is_given(monkeypatch):
    from gemini_brain.agent import law
    from gemini_brain.reasoning import bedrock_client
    from gemini_brain.vat_kb import augment as vat_augment
    used = []

    class Adapter:
        def __init__(self, model_id, label=""):
            used.append(model_id)

        def converse(self, *a, **k):
            return "Exports are zero-rated [1]."

        def get_token_usage(self):
            return {}
    monkeypatch.setattr(bedrock_client, "BedrockAdapter", Adapter)
    monkeypatch.setattr(vat_augment, "augment", lambda *a: SimpleNamespace(
        model_id="kb-model", system="sys", max_tokens=100, blocks=[]))
    law.answer("Is VAT due on exports?", [{"role": "user", "content": [{"text": "q"}]}], model_id="lite-model")
    law.answer("Is VAT due on exports?", [{"role": "user", "content": [{"text": "q"}]}])
    assert used == ["lite-model", "kb-model"]


def _detail(monkeypatch):
    monkeypatch.setattr(store, "member_events", lambda owner, uid, since, limit=50: {
        "totals": {"events": 1, "credits": 39.0}, "events": [{"id": 1, "kind": "agent_answer", "credits": 39.0}]})


def test_owner_sees_one_persons_usage_of_their_bucket(db, hard, monkeypatch):
    _detail(monkeypatch)
    c = _client(7)
    body = c.get("/api/v1/usage/owner/members/50/events").json()
    assert body["user_id"] == 50 and body["totals"]["credits"] == 39.0 and body["events"][0]["kind"] == "agent_answer"
    assert c.get("/api/v1/usage/owner/members/999/events").status_code == 404
    assert _client(50).get("/api/v1/usage/owner/members/50/events").status_code == 403


def test_admin_sees_one_persons_usage_of_any_bucket(db, hard, monkeypatch):
    _detail(monkeypatch)
    assert _client(50).get("/api/v1/usage/admin/owners/7/members/50/events").status_code == 403
    db.admins.add(1)
    assert _client(1).get("/api/v1/usage/admin/owners/7/members/50/events").json()["totals"]["events"] == 1


def test_store_member_events_shape(monkeypatch):
    import datetime as dt
    cur = _open_with((2, 1000, 200, 50, 39.5, 1), monkeypatch)
    at = dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc)
    cur.fetchall = lambda: [(9, at, "agent_answer", "ok", "lite", 2, 1000, 200, 50, 39.5,
                             {"in.anthropic.claude-haiku-4-5": {}}, [24])]
    out = store.member_events(7, 50, at)
    assert out["totals"] == {"events": 2, "input_tokens": 1000, "output_tokens": 200, "cached_tokens": 50,
                             "credits": 39.5, "cost_usd": 0.0395, "lite_events": 1}
    assert out["events"][0]["models"] == ["in.anthropic.claude-haiku-4-5"] and out["events"][0]["mode"] == "lite"
    assert all("c.owner_user_id = %s AND c.user_id = %s" in q for q in cur.sql if "SELECT" in q)
