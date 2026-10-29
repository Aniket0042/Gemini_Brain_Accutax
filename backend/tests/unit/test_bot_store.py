"""Unit tests for bots PostgreSQL store layer (Task 2.2)."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from gemini_brain.bots import store
from gemini_brain.bots.schema import BotDraft, Rules, Schedule


class FakeCursor:
    """In-memory fake PostgreSQL cursor supporting store.py query patterns."""

    def __init__(self, db: Dict[str, Any]):
        self.db = db
        self.last_query = ""
        self.last_params: tuple = ()
        self.rowcount = 0
        self._results: List[Any] = []

    def execute(self, query: str, params: tuple = ()):
        q = " ".join(query.strip().split())
        self.last_query = q
        self.last_params = params
        self._results = []
        self.rowcount = 0

        if "INSERT INTO ai_bots" in q:
            # (owner_user_id, agent, name, org_ids, draft_json, status, status_note, next_run_at)
            bot_id = str(uuid.uuid4())
            owner_id, agent, name, org_ids, draft_json, status, status_note, next_run = params
            now = dt.datetime.now(dt.timezone.utc)
            bot_row = (
                bot_id, owner_id, agent, name, list(org_ids), draft_json,
                status, status_note, now, now, None, next_run
            )
            self.db["bots"][bot_id] = bot_row
            self._results = [bot_row]
            self.rowcount = 1

        elif "SELECT id, owner_user_id, agent, name, org_ids, draft, status, status_note" in q and "FROM ai_bots" in q:
            if "WHERE id = %s AND owner_user_id = %s" in q:
                bot_id, owner_id = params
                bot = self.db["bots"].get(str(bot_id))
                if bot and bot[1] == owner_id:
                    self._results = [bot]
                    self.rowcount = 1
            elif "WHERE id = %s" in q:
                bot_id = params[0]
                bot = self.db["bots"].get(str(bot_id))
                if bot:
                    self._results = [bot]
                    self.rowcount = 1
            elif "WHERE owner_user_id = %s" in q:
                owner_id = params[0]
                matching = [b for b in self.db["bots"].values() if b[1] == owner_id]
                self._results = sorted(matching, key=lambda r: r[8], reverse=True)
                self.rowcount = len(self._results)
            elif "WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= %s" in q:
                now_cutoff = params[0]
                limit = params[1]
                matching = [
                    b for b in self.db["bots"].values()
                    if b[6] == "active" and b[11] is not None and b[11] <= now_cutoff
                ]
                self._results = sorted(matching, key=lambda r: r[11])[:limit]
                self.rowcount = len(self._results)

        elif "UPDATE ai_bots" in q:
            if len(params) == 1:
                bot_id = params[0]
                bot = self.db["bots"].get(str(bot_id))
                if bot:
                    bot_list = list(bot)
                    bot_list[10] = dt.datetime.now(dt.timezone.utc)  # last_run_at
                    self.db["bots"][str(bot_id)] = tuple(bot_list)
                    self.rowcount = 1
            elif "next_run_at = %s" in q and len(params) == 2:
                next_run, bot_id = params
                bot = self.db["bots"].get(str(bot_id))
                if bot:
                    bot_list = list(bot)
                    bot_list[11] = next_run
                    self.db["bots"][str(bot_id)] = tuple(bot_list)
                    self.rowcount = 1
            else:
                bot_id, owner_id = params[-2], params[-1]
                bot = self.db["bots"].get(str(bot_id))
                if bot and bot[1] == owner_id:
                    bot_list = list(bot)
                    now = dt.datetime.now(dt.timezone.utc)
                    bot_list[9] = now  # updated_at
                    if "status = %s" in q:
                        status_idx = q.split("status = %s")[0].count("%s")
                        bot_list[6] = params[status_idx]
                    if "status_note = %s" in q:
                        note_idx = q.split("status_note = %s")[0].count("%s")
                        bot_list[7] = params[note_idx]
                    self.db["bots"][str(bot_id)] = tuple(bot_list)
                    self._results = [tuple(bot_list)]
                    self.rowcount = 1

        elif "DELETE FROM ai_bots" in q:
            bot_id, owner_id = params
            bot = self.db["bots"].get(str(bot_id))
            if bot and bot[1] == owner_id:
                del self.db["bots"][str(bot_id)]
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif "INSERT INTO ai_bot_runs" in q:
            run_id = str(uuid.uuid4())
            bot_id, trigger, status, credits, summary_json, error = params
            now = dt.datetime.now(dt.timezone.utc)
            run_row = (run_id, bot_id, trigger, status, now, None, credits, summary_json, error)
            self.db["runs"][run_id] = run_row
            self._results = [run_row]
            self.rowcount = 1

        elif "UPDATE ai_bot_runs" in q:
            status, credits, summary_json, error, run_id = params
            run = self.db["runs"].get(str(run_id))
            if run:
                run_list = list(run)
                now = dt.datetime.now(dt.timezone.utc)
                run_list[3] = status
                run_list[5] = now
                run_list[6] = credits
                run_list[7] = summary_json
                run_list[8] = error
                self.db["runs"][str(run_id)] = tuple(run_list)
                self._results = [tuple(run_list)]
                self.rowcount = 1

        elif "SELECT id, bot_id, trigger, status, started_at, finished_at, credits, summary, error" in q:
            bot_id, limit = params
            matching = [r for r in self.db["runs"].values() if r[1] == str(bot_id)]
            self._results = sorted(matching, key=lambda r: r[4], reverse=True)[:limit]
            self.rowcount = len(self._results)

        elif "INSERT INTO ai_bot_findings" in q:
            bot_id, key, org_id, rule, state, amount, r1, r2 = params
            f_key = (str(bot_id), str(key))
            now = dt.datetime.now(dt.timezone.utc)
            self.db["findings"][f_key] = (bot_id, key, org_id, rule, state, amount, r1, r2, now)
            self.rowcount = 1

        elif "SELECT rule FROM ai_bot_findings" in q:
            bot_id, key = params
            f_key = (str(bot_id), str(key))
            if f_key in self.db["findings"]:
                self._results = [(self.db["findings"][f_key][3],)]
                self.rowcount = 1

        elif "FROM ai_bot_findings f JOIN ai_bots b" in q:
            owner_id = params[0]
            matching = []
            for f in self.db["findings"].values():
                b = self.db["bots"].get(str(f[0]))
                if b and b[1] == owner_id and f[4] in ("new", "seen"):
                    matching.append((f[0], b[3], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8]))
            self._results = matching
            self.rowcount = len(matching)

        elif "UPDATE ai_bot_findings f" in q and "FROM ai_bots b" in q:
            owner_id = params[0]
            count = 0
            for k, f in list(self.db["findings"].items()):
                b = self.db["bots"].get(str(f[0]))
                if b and b[1] == owner_id and f[4] == "new":
                    f_list = list(f)
                    f_list[4] = "seen"
                    self.db["findings"][k] = tuple(f_list)
                    count += 1
            self.rowcount = count

        elif "SELECT bot_id, key, org_id, rule, state, amount, first_seen_run, last_seen_run, updated_at" in q:
            bot_id = params[0]
            matching = [f for f in self.db["findings"].values() if f[0] == str(bot_id)]
            if len(params) > 1:
                state = params[1]
                matching = [f for f in matching if f[4] == state]
            self._results = matching
            self.rowcount = len(matching)

        elif "UPDATE ai_bot_findings" in q:
            if len(params) == 2:
                bot_id, key = params
                state = "dismissed" if "state = 'dismissed'" in q else "seen"
            else:
                state, bot_id, key = params
            f_key = (str(bot_id), str(key))
            if f_key in self.db["findings"]:
                f_list = list(self.db["findings"][f_key])
                f_list[4] = state
                self.db["findings"][f_key] = tuple(f_list)
                self.rowcount = 1
            else:
                self.rowcount = 0

        elif "INSERT INTO ai_bot_memory" in q:
            mem_id = len(self.db["memory"]) + 1
            if len(params) == 2:
                bot_id, text = params
                source = "note"
                f_key = None
            elif len(params) == 3:
                bot_id, text, f_key = params
                source = "dismissal"
            else:
                bot_id, text, source, f_key = params
            now = dt.datetime.now(dt.timezone.utc)
            self.db["memory"][mem_id] = (mem_id, bot_id, text, source, f_key, now)
            self._results = [(mem_id,)]
            self.rowcount = 1

        elif "SELECT finding_key FROM ai_bot_memory" in q:
            mem_id, bot_id = params
            m = self.db["memory"].get(int(mem_id))
            if m and m[1] == str(bot_id):
                self._results = [(m[4],)]
            else:
                self._results = []
            self.rowcount = len(self._results)

        elif "SELECT id, bot_id, text, source, finding_key, created_at" in q:
            bot_id = params[0]
            matching = [m for m in self.db["memory"].values() if m[1] == str(bot_id)]
            self._results = sorted(matching, key=lambda m: m[0])
            self.rowcount = len(matching)

        elif "DELETE FROM ai_bot_memory" in q:
            if "finding_key = %s" in q:
                bot_id, f_key = params
                to_delete = [mid for mid, m in self.db["memory"].items() if m[1] == str(bot_id) and m[4] == str(f_key)]
                for mid in to_delete:
                    del self.db["memory"][mid]
                self.rowcount = len(to_delete)
            elif "source = 'note'" in q:
                bot_id = params[0]
                to_delete = [mid for mid, m in self.db["memory"].items() if m[1] == str(bot_id) and m[3] == "note"]
                for mid in to_delete:
                    del self.db["memory"][mid]
                self.rowcount = len(to_delete)
            else:
                mem_id, bot_id = params
                if int(mem_id) in self.db["memory"] and self.db["memory"][int(mem_id)][1] == str(bot_id):
                    del self.db["memory"][int(mem_id)]
                    self.rowcount = 1
                else:
                    self.rowcount = 0

    def fetchone(self):
        return self._results[0] if self._results else None

    def fetchall(self):
        return list(self._results)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class FakeConnection:
    """Fake connection wrapping FakeCursor."""

    def __init__(self):
        self.db = {
            "bots": {},
            "runs": {},
            "findings": {},
            "memory": {},
        }

    def cursor(self):
        return FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def fake_conn():
    return FakeConnection()


def test_create_and_get_bot(fake_conn):
    draft = BotDraft(
        name="Weekly VAT Bot",
        org_ids=[1, 2],
        schedules=[Schedule(kind="weekly", day="mon", time="09:00")],
        watches=["duplicate_bill"],
    )
    next_run = dt.datetime(2026, 11, 2, 9, 0, tzinfo=dt.timezone.utc)

    created = store.create(
        owner_user_id=42,
        draft=draft,
        status="active",
        next_run_at=next_run,
        conn=fake_conn,
    )

    assert created["name"] == "Weekly VAT Bot"
    assert created["owner_user_id"] == 42
    assert created["agent"] == "vat"
    assert created["org_ids"] == [1, 2]
    assert created["status"] == "active"
    assert created["draft"]["watches"] == ["duplicate_bill"]

    # Retrieve by ID with ownership
    fetched = store.get(created["id"], owner_user_id=42, conn=fake_conn)
    assert fetched is not None
    assert fetched["id"] == created["id"]
    assert fetched["name"] == "Weekly VAT Bot"

    # Refuse retrieval by different user
    denied = store.get(created["id"], owner_user_id=99, conn=fake_conn)
    assert denied is None


def test_list_and_update_and_delete_bot(fake_conn):
    draft1 = BotDraft(name="Bot 1", org_ids=[1])
    draft2 = BotDraft(name="Bot 2", org_ids=[2])

    b1 = store.create(owner_user_id=10, draft=draft1, conn=fake_conn)
    b2 = store.create(owner_user_id=10, draft=draft2, conn=fake_conn)
    b_other = store.create(owner_user_id=20, draft=draft1, conn=fake_conn)

    user_bots = store.list_for_user(owner_user_id=10, conn=fake_conn)
    assert len(user_bots) == 2
    bot_ids = {b["id"] for b in user_bots}
    assert b1["id"] in bot_ids and b2["id"] in bot_ids
    assert b_other["id"] not in bot_ids

    # Update status to paused
    paused = store.set_status(b1["id"], owner_user_id=10, status="paused", status_note="Manual pause", conn=fake_conn)
    assert paused["status"] == "paused"
    assert paused["status_note"] == "Manual pause"

    # Delete
    assert store.delete(b1["id"], owner_user_id=10, conn=fake_conn) is True
    assert store.get(b1["id"], owner_user_id=10, conn=fake_conn) is None
    # Second delete returns False
    assert store.delete(b1["id"], owner_user_id=10, conn=fake_conn) is False


def test_runs_and_findings(fake_conn):
    draft = BotDraft(name="Audit Bot", org_ids=[1])
    b = store.create(owner_user_id=5, draft=draft, conn=fake_conn)

    # Record run
    run = store.record_run(bot_id=b["id"], trigger="schedule", conn=fake_conn)
    assert run["status"] == "running"
    assert run["trigger"] == "schedule"

    # Upsert findings
    findings = [
        {"key": "dup_101", "org_id": 1, "rule": "same_number", "amount": 1500.0, "state": "new"},
        {"key": "vat_202", "org_id": 1, "rule": "vat_rate_not_standard", "amount": 350.0, "state": "new"},
    ]
    store.upsert_findings(bot_id=b["id"], run_id=run["id"], findings=findings, conn=fake_conn)

    all_f = store.get_findings(bot_id=b["id"], conn=fake_conn)
    assert len(all_f) == 2

    # Dismiss one finding
    assert store.update_finding_state(bot_id=b["id"], key="dup_101", state="dismissed", conn=fake_conn) is True
    dismissed = store.get_findings(bot_id=b["id"], state="dismissed", conn=fake_conn)
    assert len(dismissed) == 1 and dismissed[0]["key"] == "dup_101"

    # Finish run
    fin = store.finish_run(
        run_id=run["id"],
        status="ok",
        credits=90,
        summary={"bills_checked": 45, "findings": 2},
        conn=fake_conn,
    )
    assert fin["status"] == "ok"
    assert fin["credits"] == 90
    assert fin["summary"]["bills_checked"] == 45


def test_due_bots_and_memory(fake_conn):
    now = dt.datetime(2026, 10, 29, 12, 0, tzinfo=dt.timezone.utc)
    past = now - dt.timedelta(minutes=10)
    future = now + dt.timedelta(minutes=30)

    draft = BotDraft(name="Due Bot", org_ids=[1])
    b_due = store.create(owner_user_id=1, draft=draft, status="active", next_run_at=past, conn=fake_conn)
    b_future = store.create(owner_user_id=1, draft=draft, status="active", next_run_at=future, conn=fake_conn)
    b_paused = store.create(owner_user_id=1, draft=draft, status="paused", next_run_at=past, conn=fake_conn)

    due = store.due_bots(now=now, conn=fake_conn)
    due_ids = [b["id"] for b in due]
    assert b_due["id"] in due_ids
    assert b_future["id"] not in due_ids
    assert b_paused["id"] not in due_ids

    # Memory
    m1 = store.add_memory(bot_id=b_due["id"], text="Ignore Amazon AWS bills", source="note", conn=fake_conn)
    m2 = store.add_memory(bot_id=b_due["id"], text="Dismissed utility bill", source="dismissal", finding_key="f_1", conn=fake_conn)

    memories = store.list_memory(bot_id=b_due["id"], conn=fake_conn)
    assert len(memories) == 2
    assert memories[0]["text"] == "Ignore Amazon AWS bills"

    assert store.delete_memory(bot_id=b_due["id"], memory_id=m1, conn=fake_conn) is True
    assert len(store.list_memory(bot_id=b_due["id"], conn=fake_conn)) == 1


def test_store_inbox_and_dismissal(fake_conn):
    draft = BotDraft(name="Inbox Bot", org_ids=[101])
    bot = store.create(owner_user_id=42, draft=draft, status="active", conn=fake_conn)
    bot_id = bot["id"]

    # Test set_next_run
    new_time = dt.datetime(2026, 11, 1, 9, 0, tzinfo=dt.timezone.utc)
    assert store.set_next_run(bot_id, new_time, conn=fake_conn) is True
    refreshed = store.get(bot_id, owner_user_id=42, conn=fake_conn)
    assert refreshed["next_run_at"] == new_time.isoformat()

    # Record a run and findings
    run = store.record_run(bot_id=bot_id, trigger="schedule", conn=fake_conn)
    findings = [
        {"key": "item-1", "org_id": 101, "rule": "duplicate_bill", "amount": 100.0, "state": "new"},
        {"key": "item-2", "org_id": 101, "rule": "vat_rate_wrong", "amount": 50.0, "state": "new"},
    ]
    store.upsert_findings(bot_id, run["id"], findings, conn=fake_conn)

    # Inbox check
    inbox = store.get_inbox(owner_user_id=42, conn=fake_conn)
    assert inbox["unread_count"] == 2
    assert inbox["bots_count"] == 1
    assert len(inbox["findings"]) == 2

    # Dismiss item-1
    assert store.dismiss_finding(bot_id=bot_id, owner_user_id=42, key="item-1", note="Allowed", conn=fake_conn) is True
    # item-1 should no longer be active in inbox
    inbox_after = store.get_inbox(owner_user_id=42, conn=fake_conn)
    assert len(inbox_after["findings"]) == 1
    assert inbox_after["findings"][0]["key"] == "item-2"

    # Memory entry should have been created
    memories = store.list_memory(bot_id=bot_id, conn=fake_conn)
    assert any(m["finding_key"] == "item-1" for m in memories)

    # Undismiss item-1
    assert store.undismiss_finding(bot_id=bot_id, owner_user_id=42, key="item-1", conn=fake_conn) is True
    inbox_undismiss = store.get_inbox(owner_user_id=42, conn=fake_conn)
    assert len(inbox_undismiss["findings"]) == 2

    # Dismissal memory should have been removed
    memories_after = store.list_memory(bot_id=bot_id, conn=fake_conn)
    assert not any(m["finding_key"] == "item-1" for m in memories_after)

    # Mark inbox seen
    marked = store.mark_inbox_seen(owner_user_id=42, conn=fake_conn)
    assert marked >= 1
    inbox_seen = store.get_inbox(owner_user_id=42, conn=fake_conn)
    assert inbox_seen["unread_count"] == 0

