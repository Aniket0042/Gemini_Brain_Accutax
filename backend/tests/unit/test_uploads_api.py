"""Upload endpoints: access, the checks at the door, owner-only reads, and thread delete."""
import uuid
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.config.settings import settings
from gemini_brain.uploads import store
from tests.unit import upload_files as f

SESSION = str(uuid.uuid4())
client = TestClient(app)


def _as(user_id=7, orgs=(3, 4), email="a@x.com"):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(user_id, email, list(orgs))


@pytest.fixture(autouse=True)
def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "uploads_enabled", True)
    monkeypatch.setattr(settings, "upload_users", "")
    store.reset_for_tests()
    _as()
    # Reading is tested in test_uploads_reader.py; here the upload stays as stored.
    with patch("gemini_brain.memory.session_memory.verify_session_ownership", return_value=True),          patch("gemini_brain.uploads.reader.start", side_effect=lambda rec: rec):
        yield
    app.dependency_overrides.pop(get_current_user, None)
    store.reset_for_tests()


def _post(content, name="bill.pdf", session=SESSION, orgs="3", mime="application/octet-stream"):
    return client.post("/api/v1/uploads", files={"file": (name, content, mime)},
                       data={"session_id": session, "organization_ids": orgs})


def test_config_when_enabled_lists_the_limits():
    body = client.get("/api/v1/uploads/config").json()
    assert body["enabled"] is True and body["max_bytes"] == 10 * 1024 * 1024
    assert (body["max_pages"], body["max_files"], body["daily_pages"]) == (20, 3, 100)
    assert ".pdf" in body["accept"] and ".docx" in body["accept"] and ".csv" in body["accept"]


def test_config_when_off_hides_everything(monkeypatch):
    monkeypatch.setattr(settings, "uploads_enabled", False)
    assert client.get("/api/v1/uploads/config").json() == {
        "enabled": False, "max_bytes": 0, "max_pages": 0, "max_files": 0, "daily_pages": 0,
        "pages_used_today": 0, "accept": []}
    assert _post(f.pdf()).status_code == 403


def test_a_user_off_the_allowlist_cannot_upload(monkeypatch):
    monkeypatch.setattr(settings, "upload_users", "someone@else.com")
    assert client.get("/api/v1/uploads/config").json()["enabled"] is False
    assert _post(f.pdf()).status_code == 403


def test_login_is_required():
    app.dependency_overrides.pop(get_current_user, None)
    assert client.get("/api/v1/uploads/config").status_code == 401
    assert _post(f.pdf()).status_code == 401


def test_upload_returns_the_chip_and_stores_the_file():
    r = _post(f.pdf(2), name="Supplier Bill.pdf")
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Supplier Bill.pdf" and body["kind"] == "pdf" and body["pages"] == 2
    assert body["status"] == "uploaded" and body["attached"] is False
    rec = store.get(body["id"])
    assert rec.session_id == SESSION and rec.user_id == 7 and rec.organization_ids == [3]


def test_the_content_decides_the_type_not_the_browsers_claim():
    r = _post(f.image("PNG"), name="scan.pdf", mime="application/pdf")
    assert r.json()["kind"] == "png"


@pytest.mark.parametrize("make,name,status,code", [
    (lambda: b"MZ\x90\x00" + b"\x00" * 50, "setup.exe", 415, "unsupported_type"),
    (lambda: f.pdf(21), "long.pdf", 422, "too_many_pages"),
    (lambda: f.pdf(1, password="x"), "locked.pdf", 422, "encrypted"),
    (lambda: f.macro_docx(), "m.docx", 422, "macros"),
    (lambda: f.zip_bomb(), "b.docx", 413, "too_large"),
    (lambda: b"", "empty.pdf", 422, "empty"),
], ids=["exe", "21_pages", "encrypted", "macros", "zip_bomb", "empty"])
def test_refusals_say_why(make, name, status, code):
    r = _post(make(), name=name)
    assert r.status_code == status
    detail = r.json()["detail"]
    assert detail["code"] == code and detail["message"]


def test_a_file_over_the_limit_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "upload_max_bytes", 2048)
    r = _post(b"%PDF-1.4\n" + b"x" * 4096)
    assert r.status_code == 413 and "limit" in r.json()["detail"]
    assert list(store._all()) == []


def test_the_daily_cap_returns_429(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 3)
    assert _post(f.pdf(3)).status_code == 201
    r = _post(f.pdf(1))
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit"


def test_session_id_must_be_a_uuid():
    assert _post(f.pdf(), session="chat-12345").status_code == 400


def test_a_thread_owned_by_someone_else_is_refused():
    with patch("gemini_brain.memory.session_memory.verify_session_ownership", return_value=False):
        assert _post(f.pdf()).status_code == 403


def test_organizations_outside_the_users_scope_are_refused():
    assert _post(f.pdf(), orgs="3,99").status_code == 403
    assert list(store._all()) == []


def test_organization_ids_must_be_numbers():
    assert _post(f.pdf(), orgs="abc").status_code == 400


def test_owner_can_read_state_and_file():
    up = _post(f.image("PNG")).json()
    assert client.get(f"/api/v1/uploads/{up['id']}").json()["name"] == "bill.png"
    r = client.get(f"/api/v1/uploads/{up['id']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
    assert r.headers["content-disposition"].startswith("inline")


def test_word_and_sheet_files_download_instead_of_opening_in_the_browser():
    up = _post(f.docx(), name="c.docx").json()
    assert client.get(f"/api/v1/uploads/{up['id']}/file").headers["content-disposition"].startswith("attachment")


def test_another_user_gets_not_found_for_state_file_and_delete():
    up = _post(f.pdf()).json()
    _as(user_id=8, orgs=(3,), email="b@x.com")
    for call in (client.get(f"/api/v1/uploads/{up['id']}"), client.get(f"/api/v1/uploads/{up['id']}/file"),
                 client.delete(f"/api/v1/uploads/{up['id']}")):
        assert call.status_code == 404
    assert store.get(up["id"]) is not None
    assert any(e["event"] == "denied" for e in store.audit_events(up["id"]))


def test_removing_a_draft_deletes_it_and_gives_pages_back(monkeypatch):
    monkeypatch.setattr(settings, "upload_daily_pages", 2)
    up = _post(f.pdf(2)).json()
    assert client.delete(f"/api/v1/uploads/{up['id']}").status_code == 204
    assert store.get(up["id"]) is None
    assert _post(f.pdf(2)).status_code == 201


def test_a_file_sent_with_a_question_cannot_be_removed():
    up = _post(f.pdf()).json()
    store.mark_attached([up["id"]])
    assert client.get(f"/api/v1/uploads/{up['id']}").json()["attached"] is True
    assert client.delete(f"/api/v1/uploads/{up['id']}").status_code == 409


def test_deleting_the_thread_deletes_its_files():
    up = _post(f.pdf()).json()
    store.mark_attached([up["id"]])
    with patch("gemini_brain.memory.session_memory.delete_session", return_value=True), \
         patch("gemini_brain.memory.session_memory.get_session_record", return_value=None):
        assert client.delete(f"/api/v1/sessions/{SESSION}").status_code == 204
    assert store.get(up["id"]) is None


def test_a_thread_that_was_not_deleted_keeps_its_files():
    up = _post(f.pdf()).json()
    with patch("gemini_brain.memory.session_memory.delete_session", return_value=False), \
         patch("gemini_brain.memory.session_memory.get_session_record", return_value=None):
        assert client.delete(f"/api/v1/sessions/{SESSION}").status_code == 404
    assert store.get(up["id"]) is not None


def test_a_bad_id_in_the_path_never_touches_the_disk():
    assert client.get("/api/v1/uploads/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.get("/api/v1/uploads/not-a-uuid").status_code == 404


def test_the_chip_carries_the_exact_problems_in_words():
    up = _post(f.pdf()).json()
    assert up["problems"] == []
    store.update(up["id"], status="ready", checks_failed=1, problems=["VAT is 5%. VAT of 52.40 is not 5% of 1,000.00 (5% is 50.00)."])
    got = client.get(f"/api/v1/uploads/{up['id']}").json()
    assert got["checks_failed"] == 1 and got["problems"] == ["VAT is 5%. VAT of 52.40 is not 5% of 1,000.00 (5% is 50.00)."]


def test_a_file_read_before_problems_were_kept_still_shows_them():
    up = _post(f.pdf()).json()
    store.save_extract(up["id"], {"checks": [{"name": "tax_id", "status": "fail", "detail": "The supplier's TRN is not on the document."},
                                              {"name": "currency", "status": "pass", "detail": ""}]})
    store.update(up["id"], status="ready", checks_failed=1)          # no problems on the record: an older read
    assert client.get(f"/api/v1/uploads/{up['id']}").json()["problems"] == ["Supplier's TRN. The supplier's TRN is not on the document."]


def test_upload_without_organizations_belongs_to_the_first_one():
    """Reading the file costs AI: with no organization named, the caller's first one is charged, as for a chat."""
    r = _post(f.pdf(), orgs="")
    assert r.status_code == 201 and store.get(r.json()["id"]).organization_ids == [3]


def test_upload_refused_when_the_bucket_cannot_pay(monkeypatch):
    from gemini_brain.usage import limits
    monkeypatch.setattr(limits, "check", lambda *a, **k: limits.Decision(
        action=limits.BLOCK, code="USAGE_LIMIT", message="Used up.", enforce="hard"))
    r = _post(f.pdf())
    assert r.status_code == 429 and r.json()["detail"]["code"] == "USAGE_LIMIT"
