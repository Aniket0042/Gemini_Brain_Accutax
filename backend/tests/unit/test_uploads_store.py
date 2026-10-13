"""Upload store: ownership, retention with the thread, drafts that expire, the daily cap, the audit log."""
import json
import os

import pytest

from gemini_brain.config.settings import settings
from gemini_brain.uploads import enabled_for, store
from gemini_brain.uploads.validate import inspect
from tests.unit import upload_files as f

DAY = 86_400


@pytest.fixture(autouse=True)
def _clean_store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _put(user=7, session="s-1", content=None, name="bill.pdf", now=1_000_000.0, orgs=(3,)):
    content = content if content is not None else f.pdf(2)
    return store.put(content, filename=name, inspected=inspect(content, name), user_id=user,
                     session_id=session, organization_ids=orgs, now=now)


def test_put_stores_the_file_and_its_facts():
    rec = _put()
    assert rec.kind == "pdf" and rec.pages == 2 and rec.organization_ids == [3] and rec.status == "uploaded"
    assert len(rec.sha256) == 64 and rec.attached_at is None
    assert store.read_bytes(store.get(rec.id))[:5] == b"%PDF-" and store.get(rec.id).size_bytes == rec.size_bytes
    [event] = store.audit_events(rec.id)
    assert event["event"] == "uploaded" and event["user_id"] == 7 and event["detail"]["session_id"] == "s-1"


def test_get_only_returns_real_uuids():
    assert store.get("../../etc/passwd") is None and store.get("not-a-uuid") is None


def test_another_user_cannot_read_an_upload_and_the_refusal_is_audited():
    rec = _put(user=7)
    assert store.get_for_user(rec.id, 8, action="read") is None  # same answer as "no such file"
    assert store.get_for_user(rec.id, 7) is not None
    denied = [e for e in store.audit_events(rec.id) if e["event"] == "denied"]
    assert denied and denied[0]["user_id"] == 8 and denied[0]["detail"]["reason"] == "not_owner"


def test_an_upload_belongs_to_its_thread():
    rec = _put(session="s-1")
    assert store.get_for_user(rec.id, 7, session_id="s-1") is not None
    assert store.get_for_user(rec.id, 7, session_id="s-2") is None
    assert [e for e in store.audit_events(rec.id) if e["event"] == "denied"][-1]["detail"]["reason"] == "other_thread"


def test_deleting_a_thread_deletes_only_its_files():
    a, b, other = _put(session="s-1"), _put(session="s-1"), _put(session="s-2")
    assert store.delete_for_session("s-1") == 2
    assert store.get(a.id) is None and store.get(b.id) is None and not os.path.exists(a.path)
    assert store.get(other.id) is not None
    assert [e["detail"]["reason"] for e in store.audit_events(a.id) if e["event"] == "deleted"] == ["thread_deleted"]


def test_delete_removes_the_metadata_and_anything_keyed_on_the_id():
    rec = _put()
    extract = os.path.join(os.path.dirname(rec.path), f"{rec.id}.extract.json")
    open(extract, "w").write("{}")
    assert store.delete(rec.id) is True
    assert not os.path.exists(extract) and store.get(rec.id) is None and store.delete(rec.id) is False


def test_unattached_drafts_expire_after_24_hours_and_attached_files_do_not():
    draft, kept = _put(now=1000.0), _put(now=1000.0)
    store.mark_attached([kept.id], now=1500.0)
    assert store.sweep(now=1000.0 + DAY - 1) == {"unattached": 0, "orphaned": 0}
    assert store.sweep(now=1000.0 + DAY + 1)["unattached"] == 1
    assert store.get(draft.id) is None and store.get(kept.id) is not None


def test_orphans_of_vanished_threads_are_swept_when_the_database_answers():
    gone, alive, draft = _put(session="s-gone"), _put(session="s-alive"), _put(session="s-gone")
    store.mark_attached([gone.id, alive.id])
    out = store.sweep(now=1_000_010.0, existing_sessions=lambda ids: {"s-alive"}, check_orphans=True)
    assert out["orphaned"] == 1
    assert store.get(gone.id) is None and store.get(alive.id) is not None and store.get(draft.id) is not None


def test_no_orphan_is_deleted_when_the_database_cannot_be_asked():
    rec = _put()
    store.mark_attached([rec.id])
    assert store.sweep(existing_sessions=lambda ids: None, check_orphans=True)["orphaned"] == 0
    assert store.get(rec.id) is not None


def test_orphan_check_runs_only_when_asked():
    rec = _put()
    store.mark_attached([rec.id])
    assert store.sweep(existing_sessions=lambda ids: set(), check_orphans=False)["orphaned"] == 0


def test_daily_page_cap(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 5)
    _put(content=f.pdf(3))
    _put(content=f.pdf(2))
    assert store.pages_used_today(7, now=1_000_000.0) == 5
    with pytest.raises(store.DailyLimitReached) as err:
        _put(content=f.pdf(1))
    assert (err.value.used, err.value.limit) == (5, 5)
    assert len(list(store._all())) == 2  # the refused file left nothing behind
    _put(user=8, content=f.pdf(5))  # the cap is per user


def test_text_files_do_not_use_the_daily_cap(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 1)
    for _ in range(3):
        _put(content=f.csv_bytes(), name="a.csv")
    assert store.pages_used_today(7, now=1_000_000.0) == 0


def test_the_cap_resets_the_next_day(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 2)
    _put(content=f.pdf(2), now=1_000_000.0)
    _put(content=f.pdf(2), now=1_000_000.0 + DAY)  # does not raise


def test_removing_a_draft_gives_its_pages_back(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 4)
    rec = _put(content=f.pdf(4))
    assert store.delete(rec.id, refund_pages=True, now=1_000_000.0)
    assert store.pages_used_today(7, now=1_000_000.0) == 0
    _put(content=f.pdf(4))  # fits again


def test_update_changes_only_status_fields():
    rec = _put()
    assert store.update(rec.id, status="ready").status == "ready"
    with pytest.raises(ValueError):
        store.update(rec.id, user_id=99)


def test_old_usage_files_are_swept():
    _put(now=1_000_000.0)
    assert any(store._dir().glob("usage-*.json"))
    store.sweep(now=1_000_000.0 + 10 * DAY)
    assert not any(store._dir().glob("usage-1970-01-12.json"))


def test_the_audit_log_is_append_only_json_lines():
    rec = _put()
    store.mark_attached([rec.id])
    store.delete(rec.id)
    assert [e["event"] for e in store.audit_events(rec.id)] == ["uploaded", "attached", "deleted"]
    assert all(json.loads(line) for path in store._dir().glob("audit-*.jsonl") for line in path.read_text().splitlines())


@pytest.mark.parametrize("flag,users,uid,email,expected", [
    (False, "", 7, "a@x.com", False),
    (True, "", 7, "a@x.com", True),
    (True, "a@x.com, 12", 7, "A@X.com", True),
    (True, "a@x.com, 12", 12, "z@x.com", True),
    (True, "a@x.com, 12", 7, "z@x.com", False),
])
def test_who_may_upload(monkeypatch, flag, users, uid, email, expected):
    monkeypatch.setattr(settings, "uploads_enabled", flag)
    monkeypatch.setattr(settings, "upload_users", users)
    assert enabled_for(uid, email) is expected
