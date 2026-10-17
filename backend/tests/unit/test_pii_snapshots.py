"""PII plan phase 4: before/after snapshots of masked requests, encrypted raw side, admin-only page."""
import datetime
import json
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from gemini_brain.agent import loop
from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.artifacts import narrator
from gemini_brain.config.settings import settings
from gemini_brain.privacy import dictionary, masking, policy, snapshot_html, snapshots

ORGS = [24]
META = {24: {"name": "Zenith Foods Org", "currency": "AED"}}
SUPPLIER = "Al Noor Trading L.L.C"
TRN = "100234567800003"
KEY = Fernet.generate_key().decode()


class FakeAdapter:
    script, step = [], 0

    def __init__(self, model_id, label=""):
        self.model_id, self.calls = model_id, 0

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        step = FakeAdapter.script[min(FakeAdapter.step, len(FakeAdapter.script) - 1)]
        FakeAdapter.step += 1
        self.calls += 1
        return step

    def get_token_usage(self):
        return {"llm_calls": self.calls, "cost_usd": 0.0}


def _tool_use(params):
    return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "list_documents", "input": params}}]}}}


def _final(text):
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


@pytest.fixture
def on(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "pii_mode", "on")
    monkeypatch.setattr(settings, "pii_snapshots", "on")
    monkeypatch.setattr(settings, "pii_snapshot_users", "")
    monkeypatch.setattr(settings, "pii_snapshot_sample_pct", 100)
    monkeypatch.setattr(settings, "pii_snapshot_key", KEY)
    monkeypatch.setattr(settings, "pii_snapshot_dir", str(tmp_path / "snaps"))
    monkeypatch.setattr(settings, "pii_snapshot_admins", "privacy@accutax.ae")
    names = dictionary.Dictionary([dictionary.Entry(SUPPLIER, "SUPPLIER", policy.normalize_name(SUPPLIER)),
                                   dictionary.Entry("Zenith Foods Org", "ORG", "zenith foods org")])
    monkeypatch.setattr(dictionary, "for_orgs", lambda *a, **k: names)
    FakeAdapter.step = 0
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "_no_cache", set())
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "list_documents"}}])
    monkeypatch.setattr(loop.tools, "execute", lambda name, params, ctx: (
        {"rows": [{"purchases.vendor_name": SUPPLIER, "purchases.vendor_trn": TRN, "net": "18450.00"}],
         "row_count": 1}, True))
    FakeAdapter.script = [_tool_use({"type": "bills"}), _final("[SUPPLIER_1] owes AED 18,450.00; TRN [TRN_1].")]
    yield tmp_path / "snaps"
    masking._current.set(None)


def _files(root):
    return sorted(p for p in root.rglob("*") if p.is_file())


def test_off_or_unselected_requests_keep_nothing(monkeypatch):
    monkeypatch.setattr(settings, "pii_snapshots", "off")
    assert not snapshots.wanted(7)
    monkeypatch.setattr(settings, "pii_snapshots", "on")
    monkeypatch.setattr(settings, "pii_snapshot_users", "7,9")
    monkeypatch.setattr(settings, "pii_snapshot_sample_pct", 100)
    assert snapshots.wanted(7) and not snapshots.wanted(8)
    monkeypatch.setattr(settings, "pii_snapshot_users", "")
    monkeypatch.setattr(settings, "pii_snapshot_sample_pct", 0)
    assert not snapshots.wanted(7)


def test_admins_only(monkeypatch):
    monkeypatch.setattr(settings, "pii_snapshot_admins", "")
    assert not snapshots.is_admin(1, "x@y.ae")
    monkeypatch.setattr(settings, "pii_snapshot_admins", "Privacy@Accutax.ae, 42")
    assert snapshots.is_admin(1, "privacy@accutax.ae") and snapshots.is_admin(42, "") and not snapshots.is_admin(1, "z@y.ae")


def test_a_masked_request_is_kept_with_its_raw_side_encrypted(on):
    result = loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7, session_id="s1")
    sid = result.privacy["snapshot"]
    files = _files(on)
    assert [p.suffix for p in files] == [".json", ".raw"]
    plain = files[0].read_text(encoding="utf-8")
    for raw in ("Al Noor", TRN, "Zenith Foods"):
        assert raw not in plain                                           # the sent side holds no personal data
        assert raw.encode() not in files[1].read_bytes()                  # the raw side is encrypted
    snap = snapshots.load(sid)
    [call] = snap["calls"]
    assert call["purpose"] == "agent" and call["sentinel"] == {}
    assert "[SUPPLIER_1]" in json.dumps(call["sent"]) and "18450.00" in json.dumps(call["sent"])
    assert call["reply_model"] == "[SUPPLIER_1] owes AED 18,450.00; TRN [TRN_1]."
    assert snap["raw_available"] and SUPPLIER in json.dumps(call["raw"]) and "Zenith Foods Org" in call["raw"]["system"]
    assert call["reply_user"] == f"{SUPPLIER} owes AED 18,450.00; TRN {TRN}."
    assert snap["vault"]["SUPPLIER_1"] == SUPPLIER and snap["user_id"] == 7 and snap["mode"] == "on"


def test_without_a_key_only_the_sent_side_is_kept(on, monkeypatch):
    monkeypatch.setattr(settings, "pii_snapshot_key", "")
    sid = loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7).privacy["snapshot"]
    assert [p.suffix for p in _files(on)] == [".json"]
    snap = snapshots.load(sid)
    assert not snap["raw_available"] and "raw" not in snap["calls"][0]


def test_the_narrator_adds_its_call_to_the_same_snapshot(on, monkeypatch):
    sid = loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7).privacy["snapshot"]
    monkeypatch.setattr(narrator, "_model_call", lambda system, user: json.dumps(
        {"headline": "[SUPPLIER_1] leads", "summary": ["[SUPPLIER_1] leads [F1]."]}))
    brief = narrator.Brief(kind="general")
    brief.add(f"{SUPPLIER} is the largest supplier at AED 18,450.00.")
    narrator._narrate_with_model(brief, "report", timeout=5)
    snap = snapshots.load(sid)
    assert [c["purpose"] for c in snap["calls"]] == ["agent", "report_narration"]
    narration = snap["calls"][1]
    assert SUPPLIER not in json.dumps(narration["sent"]) and narration["sentinel"] == {}
    assert SUPPLIER in narration["reply_user"] and "[SUPPLIER_1]" in narration["reply_model"]


def test_retention_drops_the_raw_side_first_then_the_day(on, monkeypatch):
    loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7)
    today = datetime.datetime.now(datetime.timezone.utc).date()
    monkeypatch.setattr(settings, "pii_snapshot_raw_days", 7)
    monkeypatch.setattr(settings, "pii_snapshot_days", 30)
    snapshots.sweep(today + datetime.timedelta(days=8))
    assert [p.suffix for p in _files(on)] == [".json"]
    snapshots.sweep(today + datetime.timedelta(days=31))
    assert _files(on) == []


def test_html_export_marks_both_sides(on):
    sid = loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7).privacy["snapshot"]
    page = snapshot_html.render(snapshots.load(sid))
    assert '<mark class="tok">[SUPPLIER_1]</mark>' in page and '<mark class="raw">' in page
    assert "Sentinel: nothing personal sent" in page and "<script" not in page
    no_raw = snapshot_html.render(snapshots.load(sid, include_raw=False))
    assert SUPPLIER not in no_raw and "Not kept" in no_raw


@pytest.fixture
def client():
    with patch("gemini_brain.api.auth.init_auth_db"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.pop(get_current_user, None)


def _as(email):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=5, email=email, allowed_org_ids=[24], raw_token="t")


def test_api_is_for_admins_only(on, client):
    sid = loop.run_agent(f"Bills from {SUPPLIER}?", ORGS, META, user_id=7).privacy["snapshot"]
    _as("someone@accutax.ae")
    assert client.get("/api/v1/privacy/access").json() == {"admin": False}
    for url in ("/api/v1/privacy/snapshots", f"/api/v1/privacy/snapshots/{sid}", f"/api/v1/privacy/snapshots/{sid}/export"):
        assert client.get(url).status_code == 403
    _as("privacy@accutax.ae")
    access = client.get("/api/v1/privacy/access").json()
    assert access["admin"] and access["mode"] == "on" and access["raw_kept"]
    [item] = client.get("/api/v1/privacy/snapshots").json()["snapshots"]
    assert item["id"] == sid and item["sentinel_pass"] is True and item["raw_kept"] and item["purposes"] == ["agent"]
    detail = client.get(f"/api/v1/privacy/snapshots/{sid}").json()
    assert detail["calls"][0]["reply_user"].startswith(SUPPLIER)
    export = client.get(f"/api/v1/privacy/snapshots/{sid}/export")
    assert export.status_code == 200 and "attachment" in export.headers["content-disposition"]
    assert client.get("/api/v1/privacy/snapshots/../../etc").status_code in (403, 404)
    assert client.get("/api/v1/privacy/snapshots/0123456789abcdef").status_code == 404
