"""Chat threads hold an organization scope: matching, revocation, listing, multi-org memory."""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api import auth
from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.memory import session_memory
from gemini_brain.memory.session_memory import session_scope
from gemini_brain.orchestrator import multi_org

SID = str(uuid.uuid4())


def _bearer(allowed: List[int]) -> dict:
    token = create_access_token(user_id=7, email="u@example.com", allowed_org_ids=allowed)
    return {"Authorization": f"Bearer {token}"}


def _record(scope: List[int], user_id: int = 7) -> Dict[str, Any]:
    return {
        "id": SID, "user_id": user_id, "name": "Chat",
        "organization_id": scope[0] if len(scope) == 1 else None,
        "organization_ids": scope,
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", True)
    return TestClient(app)


# ── session_scope ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rec, expected", [
    (None, []),
    ({"organization_id": None, "organization_ids": None}, []),
    ({"organization_id": 5, "organization_ids": None}, [5]),
    ({"organization_id": None, "organization_ids": [6, 5, 6]}, [5, 6]),
])
def test_session_scope(rec, expected):
    assert session_scope(rec) == expected


# ── Queries are held to the thread's organizations ───────────────────────────

def _query(client, body: dict, scope: Optional[List[int]], allowed: List[int], owner: int = 7):
    """POST /query continuing thread SID; returns (status, org ids the runner ran)."""
    rec = _record(scope, owner) if scope is not None else None
    with patch.object(session_memory, "get_session_record", return_value=rec), \
         patch("gemini_brain.api.routes.GeminiBrainRunner") as runner_cls, \
         patch("gemini_brain.api.routes._org_meta", lambda user, orgs: {o: {} for o in orgs}), \
         patch.object(multi_org, "_persist_turn"), \
         patch.object(multi_org, "_load_history", return_value=[]):
        runner_cls.return_value.run.return_value = {"answer": "ok"}
        resp = client.post("/api/v1/query", json={"query": "revenue", "session_id": SID, **body},
                           headers=_bearer(allowed))
    ran = sorted(c.kwargs["organization_id"] for c in runner_cls.return_value.run.call_args_list)
    return resp.status_code, ran


def test_query_for_other_org_in_same_thread_is_refused(client):
    assert _query(client, {"organization_id": 6}, [5], [5, 6]) == (409, [])


def test_query_naming_no_org_continues_thread_org(client):
    assert _query(client, {}, [5], [5, 6]) == (200, [5])


def test_query_naming_no_org_continues_multi_org_thread(client):
    assert _query(client, {}, [5, 6], [5, 6]) == (200, [5, 6])


def test_same_org_set_in_any_order_matches_thread(client):
    assert _query(client, {"organization_ids": [6, 5]}, [5, 6], [5, 6]) == (200, [5, 6])


def test_thread_with_revoked_org_is_refused(client):
    assert _query(client, {"organization_id": 5}, [5, 9], [5]) == (403, [])


def test_thread_of_another_user_is_refused(client):
    assert _query(client, {"organization_id": 5}, [5], [5], owner=8) == (403, [])


def test_unscoped_thread_takes_requested_org(client):
    assert _query(client, {"organization_id": 6}, [], [5, 6]) == (200, [6])


def test_new_thread_id_runs_normally(client):
    assert _query(client, {"organization_id": 6}, None, [5, 6]) == (200, [6])


# ── Session endpoints ────────────────────────────────────────────────────────

def test_session_list_hides_threads_from_revoked_orgs(client):
    with patch.object(session_memory, "list_sessions_for_user_org", return_value=[]) as listing:
        resp = client.get("/api/v1/sessions", params={"organization_ids": [5, 6]}, headers=_bearer([5, 6]))
    assert resp.status_code == 200
    assert listing.call_args.kwargs["allowed_org_ids"] == [5, 6]
    assert listing.call_args.kwargs["organization_ids"] == [5, 6]


def test_session_list_refuses_unassigned_org_set(client):
    resp = client.get("/api/v1/sessions", params={"organization_ids": [5, 9]}, headers=_bearer([5]))
    assert resp.status_code == 403


def test_create_multi_org_thread_stores_org_set(client):
    with patch.object(session_memory, "ensure_session", return_value=True) as ensure, \
         patch.object(session_memory, "get_session_record", return_value=_record([5, 6])):
        resp = client.post("/api/v1/sessions", json={"organization_ids": [6, 5]}, headers=_bearer([5, 6]))
    assert resp.status_code == 201
    assert ensure.call_args.kwargs["organization_ids"] == [6, 5]
    assert resp.json()["organization_ids"] == [5, 6]


def test_create_multi_org_thread_refused_while_flag_off(client, monkeypatch):
    monkeypatch.setattr(auth.settings, "multi_org_enabled", False)
    with patch.object(session_memory, "ensure_session", return_value=True) as ensure:
        resp = client.post("/api/v1/sessions", json={"organization_ids": [5, 6]}, headers=_bearer([5, 6]))
    assert resp.status_code == 400
    ensure.assert_not_called()


def test_transcript_of_thread_with_revoked_org_is_refused(client):
    with patch.object(session_memory, "get_session_record", return_value=_record([5, 9])), \
         patch.object(session_memory, "verify_session_ownership", return_value=True), \
         patch.object(session_memory, "get_transcript_by_session", return_value=[]) as transcript:
        resp = client.get(f"/api/v1/sessions/{SID}/messages", headers=_bearer([5]))
    assert resp.status_code == 403
    transcript.assert_not_called()


# ── ensure_session / listing SQL ─────────────────────────────────────────────

class FakeCursor:
    def __init__(self, row=None, rows=None):
        self.row, self.rows, self.executed = row, rows or [], []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows

    def close(self):
        pass


def _fake_db(monkeypatch, cursor):
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(session_memory, "get_connection", lambda db_name="": conn)
    return conn


def test_ensure_session_accepts_same_org_set(monkeypatch):
    cur = FakeCursor(row=(7, None, [5, 6]))
    _fake_db(monkeypatch, cur)
    assert session_memory.ensure_session(SID, 7, organization_ids=[6, 5]) is True


def test_ensure_session_refuses_different_org_set(monkeypatch):
    cur = FakeCursor(row=(7, None, [5, 6]))
    _fake_db(monkeypatch, cur)
    assert session_memory.ensure_session(SID, 7, organization_id=5) is False


def test_ensure_session_refuses_single_org_legacy_thread_for_other_org(monkeypatch):
    cur = FakeCursor(row=(7, 5, None))
    _fake_db(monkeypatch, cur)
    assert session_memory.ensure_session(SID, 7, organization_id=6) is False


def test_ensure_session_scopes_unscoped_thread(monkeypatch):
    cur = FakeCursor(row=(7, None, None))
    _fake_db(monkeypatch, cur)
    assert session_memory.ensure_session(SID, 7, organization_ids=[6, 5]) is True
    update = cur.executed[-1]
    assert update[0].startswith("UPDATE public.model_arena_chat_sessions SET organization_id")
    assert update[1] == (None, [5, 6], SID)


def test_new_single_org_thread_sets_both_columns(monkeypatch):
    cur = FakeCursor(row=None)
    _fake_db(monkeypatch, cur)
    assert session_memory.ensure_session(SID, 7, organization_id=5) is True
    assert cur.executed[-1][1] == (SID, 7, 5, [5])


def test_listing_filters_by_allowed_orgs(monkeypatch):
    cur = FakeCursor(rows=[])
    _fake_db(monkeypatch, cur)
    session_memory.list_sessions_for_user_org(7, None, organization_ids=[6, 5], allowed_org_ids=[5, 6])
    sql, params = cur.executed[-1]
    assert "<@ %s::int[]" in sql and "= %s::int[]" in sql
    assert params == (7, [5, 6], [5, 6], 20)


# ── Multi-org thread memory ──────────────────────────────────────────────────

class RewriteRunner:
    calls: List[Dict[str, Any]] = []

    def run(self, **kwargs):
        RewriteRunner.calls.append(kwargs)
        return {"answer": "x", "status": "ok", "results": [{"v": 1}], "token_usage": {}}

    def _call_llm(self, system, user_text, **kwargs):
        if kwargs.get("purpose") == "multi_org_standalone":
            return "What was revenue in Q2 2026?", 40, 10
        return "Compared.", 0, 0


def test_period_follow_up_is_resolved_in_code_before_fan_out():
    RewriteRunner.calls = []
    history = [{"role": "user", "content": "Revenue in Q1 2025?"}, {"role": "assistant", "content": "..."}]
    with patch.object(multi_org, "_load_history", return_value=history), \
         patch.object(multi_org, "_persist_turn") as persist:
        res = multi_org.run_multi_org(
            "and Q2?", [5, 6], {}, runner_factory=RewriteRunner,
            run_kwargs={"session_id": SID, "user_id": 7, "allowed_org_ids": [5, 6]},
        )
    # The thread's year carries over; no model rewrite is needed.
    assert {c["query"] for c in RewriteRunner.calls} == {"Revenue in Q2 2025?"}
    assert all(c["session_id"] is None for c in RewriteRunner.calls)
    assert res["token_usage"]["input_tokens"] == 0
    args = persist.call_args.args
    assert args[0] == SID and args[2] == [5, 6] and args[3] == "and Q2?"


def test_open_follow_up_is_rewritten_with_thread_history_before_fan_out():
    RewriteRunner.calls = []
    history = [{"role": "user", "content": "Revenue in Q2 2026?"}, {"role": "assistant", "content": "..."}]
    with patch.object(multi_org, "_load_history", return_value=history), \
         patch.object(multi_org, "_persist_turn"):
        res = multi_org.run_multi_org(
            "why is it so different between them?", [5, 6], {}, runner_factory=RewriteRunner,
            run_kwargs={"session_id": SID, "user_id": 7, "allowed_org_ids": [5, 6]},
        )
    assert {c["query"] for c in RewriteRunner.calls} == {"What was revenue in Q2 2026?"}
    assert res["token_usage"]["input_tokens"] >= 40


def test_no_session_means_no_rewrite_and_no_persist():
    RewriteRunner.calls = []
    with patch.object(multi_org, "_load_history") as load, patch.object(multi_org, "_persist_turn") as persist:
        multi_org.run_multi_org("revenue", [5, 6], {}, runner_factory=RewriteRunner,
                                run_kwargs={"allowed_org_ids": [5, 6]})
    load.assert_not_called()
    persist.assert_not_called()
    assert {c["query"] for c in RewriteRunner.calls} == {"revenue"}


def test_persist_refused_for_thread_of_other_org_set():
    with patch.object(session_memory, "ensure_session", return_value=False), \
         patch("gemini_brain.memory.conversation_window.persist_turn_and_maybe_summarize") as write:
        multi_org._persist_turn(SID, 7, [5, 6], "q", {"answer": "a"}, RewriteRunner(), "")
    write.assert_not_called()
