"""Bots run inside the AI service: the scheduler (due bots, the advisory lock) and the poll for new documents."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gemini_brain.bots import poller, scheduler, store
from gemini_brain.bots.schema import BotDraft
from gemini_brain.config.settings import settings


def _bot(bot_id):
    draft = BotDraft(name="Audit Bot", org_ids=[101], watches=["duplicate_bill"],
                     schedules=[{"kind": "weekly", "day": "mon", "time": "08:00"}])
    return {"id": bot_id, "name": "Audit Bot", "draft": draft.model_dump()}


class FakeCursor:
    """Answers the SQL the poller and the scheduler send, from fixed data."""

    def __init__(self, tables=None, lock=True):
        self.tables = tables or {}
        self.lock = lock
        self.sql = []
        self._result = []

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if "pg_try_advisory_lock" in sql:
            self._result = [(self.lock,)]
        elif "pg_advisory_unlock" in sql:
            self._result = [(True,)]
        elif sql.startswith("SELECT COALESCE(MAX(id)"):
            table = sql.split("FROM ")[1].strip()
            self._result = [(max((r[0] for r in self.tables.get(table, [])), default=0),)]
        elif sql.startswith("SELECT id, organization_id FROM"):
            table = sql.split("FROM ")[1].split(" WHERE")[0]
            since, top, orgs, types, limit = params
            rows = [(i, o) for i, o, t in self.tables.get(table, []) if since < i <= top and o in orgs and t in types]
            self._result = sorted(rows)[:limit]

    def fetchone(self):
        return self._result[0]

    def fetchall(self):
        return list(self._result)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, cur):
        self.cur = cur
        self.closed = False

    def cursor(self):
        return self.cur

    def commit(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def _fresh_poller():
    poller.reset_for_tests()
    yield
    poller.reset_for_tests()


# --- run_due_bots -------------------------------------------------------------------------------------------------

def test_due_bots_all_run_and_move_on_even_after_a_failure():
    with patch.object(store, "due_bots", return_value=[_bot("b1"), _bot("b2")]), \
         patch("gemini_brain.bots.run.run_bot", side_effect=[RuntimeError("boom"), {"status": "ok"}]), \
         patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={}), \
         patch.object(store, "set_next_run") as next_run:
        out = scheduler.run_due_bots()
    assert [r["status"] for r in out] == ["failed", "ok"]
    assert [c.args[0] for c in next_run.call_args_list] == ["b1", "b2"]
    assert all(c.args[1] is not None for c in next_run.call_args_list)


def test_a_tick_stops_starting_bots_after_its_time_budget():
    with patch.object(store, "due_bots", return_value=[_bot("b1"), _bot("b2")]), \
         patch("gemini_brain.bots.run.run_bot", return_value={"status": "ok"}) as run, \
         patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={}), \
         patch.object(store, "set_next_run"):
        out = scheduler.run_due_bots(budget_seconds=-1)
    assert out == [] and run.call_count == 0


# --- run_cycle and the lock ---------------------------------------------------------------------------------------

def test_a_cycle_without_the_lock_does_nothing():
    cur = FakeCursor(lock=False)
    with patch.object(scheduler.connection, "get_connection", return_value=FakeConn(cur)), \
         patch.object(scheduler, "run_due_bots") as due:
        assert scheduler.run_cycle() is None
    due.assert_not_called()
    assert not any("pg_advisory_unlock" in s for s, _ in cur.sql)


def test_a_cycle_runs_bots_then_polls_and_always_unlocks():
    cur = FakeCursor(lock=True)
    conn = FakeConn(cur)
    with patch.object(scheduler.connection, "get_connection", return_value=conn), \
         patch.object(scheduler, "run_due_bots", return_value=[{"status": "ok"}, {"status": "failed"}]), \
         patch.object(poller, "poll", side_effect=RuntimeError("db down")):
        out = scheduler.run_cycle()
    assert out == {"ran": 2, "failed": 1}
    assert any("pg_advisory_unlock" in s for s, _ in cur.sql) and conn.closed


def test_the_scheduler_is_off_unless_turned_on(monkeypatch):
    monkeypatch.setattr(settings, "bot_scheduler_enabled", False)
    assert scheduler.start() is False


# --- the poll -----------------------------------------------------------------------------------------------------

TABLES = {
    "public.expense": [(10, 101, "BILL"), (11, 101, "BILL"), (12, 999, "BILL"), (13, 101, "PURCHASE_ORDER"),
                       (14, 102, "VENDOR_CREDIT")],
    "public.income": [(50, 101, "INVOICE"), (51, 101, "QUOTATION")],
}


def _poll_with(cur, since):
    poller.reset_for_tests(since)
    with patch.object(poller.connection, "get_connection", return_value=FakeConn(cur)):
        return poller.new_documents([101, 102])


def test_the_first_poll_only_records_where_to_start():
    cur = FakeCursor(TABLES)
    with patch.object(poller.connection, "get_connection", return_value=FakeConn(cur)):
        assert poller.new_documents([101]) == {}
    assert poller._last_id == {"purchases": 14, "sales": 51}


def test_new_documents_by_side_and_org_for_watched_orgs_and_types_only():
    found = _poll_with(FakeCursor(TABLES), {"purchases": 9, "sales": 49})
    assert found == {"purchases": {101: [10, 11], 102: [14]}, "sales": {101: [50]}}
    assert poller._last_id == {"purchases": 14, "sales": 51}


def test_a_full_page_carries_on_from_its_last_document(monkeypatch):
    monkeypatch.setattr(poller, "MAX_DOCUMENTS", 1)
    found = _poll_with(FakeCursor(TABLES), {"purchases": 9, "sales": 51})
    assert found == {"purchases": {101: [10]}}
    assert poller._last_id["purchases"] == 10


def test_no_watched_orgs_moves_on_without_reading_documents():
    cur = FakeCursor(TABLES)
    poller.reset_for_tests({"purchases": 9, "sales": 49})
    with patch.object(poller.connection, "get_connection", return_value=FakeConn(cur)):
        assert poller.new_documents([]) == {}
    assert not any(s.startswith("SELECT id, organization_id") for s, _ in cur.sql)
    assert poller._last_id == {"purchases": 14, "sales": 51}


def test_poll_hands_each_org_batch_to_the_monitor_and_survives_one_failure():
    found = {"purchases": {101: [10, 11], 102: [14]}, "sales": {101: [50]}}
    calls = []

    def handle(event, org_id, document_ids):
        calls.append((event, org_id, document_ids))
        if org_id == 102:
            raise RuntimeError("boom")
        return {"new_findings": 1}

    with patch.object(poller, "new_documents", return_value=found):
        out = poller.poll(active_org_ids=lambda: {101, 102}, handle_event=handle)
    assert ("bill.saved", 101, [10, 11]) in calls and ("invoice.saved", 101, [50]) in calls
    assert out == {"orgs": 2, "documents": 3, "new_findings": 2}
