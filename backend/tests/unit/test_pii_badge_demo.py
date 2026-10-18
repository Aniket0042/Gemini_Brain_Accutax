"""The privacy badge under each answer and the privacy page's live before/after demo."""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.agent import loop, preview
from gemini_brain.api.app import app
from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.config.settings import settings
from gemini_brain.privacy import dictionary, masking, policy

SUPPLIER, OTHER = "Michael", "Theodore"
META = {25: {"name": "Construction Org Two", "currency": "AED"}}


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


@pytest.fixture
def agent(monkeypatch, tmp_path):
    names = dictionary.Dictionary([dictionary.Entry(n, "SUPPLIER", policy.normalize_name(n)) for n in (SUPPLIER, OTHER)]
                                  + [dictionary.Entry("Construction Org Two", "ORG", "construction org two")])
    monkeypatch.setattr(dictionary, "for_orgs", lambda *a, **k: names)
    monkeypatch.setattr(settings, "pii_mode", "on")
    monkeypatch.setattr(settings, "pii_badge", "on")
    monkeypatch.setattr(settings, "pii_snapshots", "off")
    monkeypatch.setattr(settings, "pii_snapshot_key", "")
    monkeypatch.setattr(settings, "pii_snapshot_dir", str(tmp_path))
    FakeAdapter.step = 0
    FakeAdapter.script = [
        {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
            {"toolUse": {"toolUseId": "t1", "name": "query_metrics", "input": {"view": "payments"}}}]}}},
        {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
            {"text": "We paid [SUPPLIER_1] AED 17,424.00 this year at [ORG_1]."}]}}}]
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "_no_cache", set())
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "query_metrics"}}])
    monkeypatch.setattr(loop.tools, "execute", lambda name, params, ctx: (
        {"rows": [{"payments.counterparty_name": SUPPLIER, "amount_paid": "17424.00"},
                  {"payments.counterparty_name": OTHER, "amount_paid": "900.00"}], "row_count": 2}, True))
    yield
    masking._current.set(None)


def test_each_answer_says_what_the_model_saw(agent):
    result = loop.run_agent(f"How much did we pay {SUPPLIER} this year?", [25], META, user_id=7)
    view = result.ai_view
    assert view["question"] == "How much did we pay [SUPPLIER_1] this year?"
    assert view["answer"] == "We paid [SUPPLIER_1] AED 17,424.00 this year at [ORG_1]."
    assert {(i["token"], i["value"]) for i in view["items"]} == {("[SUPPLIER_1]", SUPPLIER),
                                                                ("[ORG_1]", "Construction Org Two")}
    assert OTHER not in json.dumps(view["items"])                  # hidden too, but not part of this Q&A
    assert view["total_hidden"] == 3 and result.answer.startswith(f"We paid {SUPPLIER}")
    block = preview.privacy_block(result)
    assert block["type"] == "privacy" and block["total_hidden"] == 3 and len(block["items"]) == 2


def test_no_badge_when_masking_or_the_badge_is_off(agent, monkeypatch):
    monkeypatch.setattr(settings, "pii_badge", "off")
    assert preview.privacy_block(loop.run_agent(f"Paid {SUPPLIER}?", [25], META)) is None
    monkeypatch.setattr(settings, "pii_badge", "on")
    monkeypatch.setattr(settings, "pii_mode", "off")
    FakeAdapter.step = 0
    result = loop.run_agent(f"Paid {SUPPLIER}?", [25], META)
    assert result.ai_view == {} and preview.privacy_block(result) is None


@pytest.fixture
def client():
    with patch("gemini_brain.api.auth.init_auth_db"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.pop(get_current_user, None)


def _as(email, orgs=(25, 26)):
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=5, email=email, allowed_org_ids=list(orgs), raw_token="t")


def test_live_demo_is_for_admins_on_demo_orgs_and_masks_whatever_pii_mode_says(agent, client, monkeypatch):
    monkeypatch.setattr(settings, "pii_mode", "off")                # the demo masks anyway
    monkeypatch.setattr(settings, "pii_snapshot_admins", "privacy@accutax.ae")
    monkeypatch.setattr(settings, "pii_demo_orgs", "25")
    from gemini_brain.semantic import org_directory
    monkeypatch.setattr(org_directory, "lookup", lambda orgs, subject: {o: META.get(o, {"name": "X"}) for o in orgs})
    _as("someone@accutax.ae")
    assert client.post("/api/v1/privacy/demo", json={"question": "q", "organization_ids": [25]}).status_code == 403
    _as("privacy@accutax.ae")
    assert client.post("/api/v1/privacy/demo", json={"question": "q", "organization_ids": [26]}).status_code == 400
    r = client.post("/api/v1/privacy/demo", json={"question": f"How much did we pay {SUPPLIER}?",
                                                  "organization_ids": [25]})
    assert r.status_code == 200, r.text
    body = r.json()
    [call] = body["calls"]
    assert SUPPLIER not in json.dumps(call["sent"]) and SUPPLIER in json.dumps(call["raw"])
    assert call["reply_model"].startswith("We paid [SUPPLIER_1]") and call["reply_user"].startswith(f"We paid {SUPPLIER}")
    assert body["vault"]["SUPPLIER_1"] == SUPPLIER and body["answer"].startswith(f"We paid {SUPPLIER}")
    assert call["sentinel"] == {} and body["organization_ids"] == [25]
