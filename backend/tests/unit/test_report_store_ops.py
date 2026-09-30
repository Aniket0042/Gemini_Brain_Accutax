"""Phase 6 operations: artifact store audit, retention, regeneration, render limits."""
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.artifacts import generate, store
from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.config.settings import settings
from gemini_brain.observability.metrics import METRICS

ROWS = [{"customer_name": "Apex Retail", "sales": 220500}, {"customer_name": "Falcon Energy", "sales": 191310}]


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset_for_tests()
    yield
    store.reset_for_tests()


def _spec():
    out = attach_delivery("sales by customer as csv", ROWS, [], user_id=7, organization_id=3)
    return next(b for b in out if b["type"] == "canvas")["spec"]


# ── store ────────────────────────────────────────────────────────────────────

def test_put_records_integrity_fields_spec_and_audit():
    rec = store.put(b"hello", filename="a.csv", mime="text/csv", user_id=7, organization_id=3,
                    fmt="csv", spec={"title": "T"})
    assert rec.size_bytes == 5 and len(rec.sha256) == 64 and rec.fmt == "csv"
    assert store.get_spec(rec.id)[1] == {"title": "T"}
    [event] = store.audit_events(rec.id)
    assert event["event"] == "created" and event["user_id"] == 7 and event["detail"]["sha256"] == rec.sha256


def test_oversized_file_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "artifact_max_bytes", 1024)
    with pytest.raises(store.ArtifactTooLarge):
        store.put(b"x" * 2048, filename="a.pdf", mime="application/pdf", user_id=1)


def test_expiry_deletes_the_file_but_keeps_the_spec_and_the_verdict():
    rec = store.put(b"x", filename="a.csv", mime="text/csv", user_id=1, ttl=1, now=1000, spec={"k": 1})
    assert store.get(rec.id, now=1002) is None
    store._records.clear()  # a restart: the expired verdict must survive it
    assert store.was_expired(rec.id)
    assert store.get_spec(rec.id)[1] == {"k": 1}
    assert [e["event"] for e in store.audit_events(rec.id)] == ["created", "expired"]


def test_sweep_purges_specs_after_retention(monkeypatch):
    monkeypatch.setattr(settings, "artifact_spec_retention_days", 1)
    rec = store.put(b"x", filename="a.csv", mime="text/csv", user_id=1, ttl=1, now=1000, spec={"k": 1})
    assert store.sweep(now=2000) == 1
    assert store.get_spec(rec.id) is not None
    store.sweep(now=2000 + 86_400 + 1)
    assert store.get_spec(rec.id) is None
    assert store.audit_events(rec.id)[-1]["event"] == "purged"


# ── routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    with patch("gemini_brain.api.auth.init_auth_db"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def _as(user_id, orgs=(3,)):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=user_id, email="u@example.com", allowed_org_ids=list(orgs), raw_token="t")


@pytest.fixture(autouse=True)
def _no_override():
    yield
    app.dependency_overrides.pop(get_current_user, None)


def test_download_is_audited_and_other_users_are_refused(client):
    block = generate.generate_and_store(_spec(), "csv", user_id=7, organization_id=3)
    _as(7)
    assert client.get(f"/api/v1/artifacts/{block['id']}").status_code == 200
    _as(8)
    assert client.get(f"/api/v1/artifacts/{block['id']}").status_code == 403
    _as(7, orgs=(99,))
    assert client.get(f"/api/v1/artifacts/{block['id']}").status_code == 403
    events = [(e["event"], e["detail"].get("reason")) for e in store.audit_events(block["id"])]
    assert events == [("created", None), ("downloaded", None), ("denied", "not_owner"), ("denied", "organization")]


def test_expired_file_can_be_regenerated_with_identical_figures(client):
    original = generate.generate_and_store(_spec(), "csv", user_id=7, organization_id=3)
    rec = store.get(original["id"])
    content = store.read_content(rec)
    rec.expires_at = time.time() - 1  # let it expire
    _as(7)
    resp = client.get(f"/api/v1/artifacts/{original['id']}")
    assert resp.status_code == 410 and resp.json()["detail"]["regenerate"] is True

    regen = client.post(f"/api/v1/artifacts/{original['id']}/regenerate")
    assert regen.status_code == 200
    new_id = regen.json()["id"]
    assert new_id != original["id"]
    assert client.get(f"/api/v1/artifacts/{new_id}").content == content
    assert store.audit_events(new_id)[0]["event"] == "regenerated"
    assert store.audit_events(new_id)[0]["detail"]["from"] == original["id"]


def test_regeneration_checks_ownership(client):
    original = generate.generate_and_store(_spec(), "pdf", user_id=7, organization_id=3)
    _as(8)
    assert client.post(f"/api/v1/artifacts/{original['id']}/regenerate").status_code == 403


# ── rendering limits and metrics ─────────────────────────────────────────────

def test_slow_format_is_dropped_and_the_rest_still_ship(monkeypatch):
    monkeypatch.setattr(settings, "report_render_timeout_seconds", 1.0)

    def slow(spec):
        time.sleep(3)
        return b"late"

    spec = _spec()  # built first: it renders files of its own
    monkeypatch.setitem(generate.RENDERERS, "md", slow)
    METRICS.reset()
    t0 = time.perf_counter()
    out = generate.render_formats(spec, ["md", "csv"], user_id=7, organization_id=3)
    assert time.perf_counter() - t0 < 2.5
    assert out["md"] is None and out["csv"] is not None
    snap = METRICS.snapshot()
    assert snap["report_render_md_timeout"] == 1
    assert snap["report_render_csv_ok"] == 1 and snap["report_render_csv_bytes"] > 0
