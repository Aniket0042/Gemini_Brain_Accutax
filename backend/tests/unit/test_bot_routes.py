from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots.schema import BotDraft, Schedule

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=18, email="tester@example.com", allowed_org_ids=[101, 102])
    return {"Authorization": f"Bearer {token}"}


def test_get_templates(auth_headers):
    resp = client.get("/api/v1/bots/templates", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    assert len(data["templates"]) == 3
    ids = [t["id"] for t in data["templates"]]
    assert "vat_weekly_digest" in ids


def test_get_watches(auth_headers):
    resp = client.get("/api/v1/bots/watches", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "watches" in data
    assert len(data["watches"]) >= 4
    ids = [w["id"] for w in data["watches"]]
    assert "duplicate_bill" in ids


@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
def test_draft_from_template_success(mock_freq, auth_headers):
    resp = client.post(
        "/api/v1/bots/draft/from-template",
        json={"template_id": "vat_weekly_digest", "org_ids": [101]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft"]["name"] == "VAT Watch"
    assert data["draft"]["org_ids"] == [101]
    assert "next_runs" in data
    assert "monthly_credits" in data


def test_draft_from_template_unauthorized_org(auth_headers):
    resp = client.post(
        "/api/v1/bots/draft/from-template",
        json={"template_id": "vat_weekly_digest", "org_ids": [999]},
        headers=auth_headers,
    )
    assert resp.status_code == 403


@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
@patch("gemini_brain.bots.draft.from_sentence")
def test_draft_from_sentence_success(mock_from_sent, mock_freq, auth_headers):
    mock_from_sent.return_value = (
        BotDraft(org_ids=[101], name="Sentence Bot", schedules=[Schedule(kind="weekly", day="mon", time="08:00")]),
        [],
    )
    resp = client.post(
        "/api/v1/bots/draft/from-sentence",
        json={"text": "Check VAT every Monday", "org_ids": [101]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft"]["name"] == "Sentence Bot"
    assert data["draft"]["org_ids"] == [101]


@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
@patch("gemini_brain.bots.draft.from_sentence")
def test_draft_from_chat_success(mock_from_sent, mock_freq, auth_headers):
    mock_from_sent.return_value = (
        BotDraft(org_ids=[101], name="Chat Bot", schedules=[Schedule(kind="weekly", day="mon", time="08:00")]),
        [],
    )
    resp = client.post(
        "/api/v1/bots/draft/from-chat",
        json={"question": "Check duplicate bills", "agent": "vat", "org_ids": [101]},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft"]["name"] == "Chat Bot"


def test_draft_from_chat_invalid_agent(auth_headers):
    resp = client.post(
        "/api/v1/bots/draft/from-chat",
        json={"question": "Check bills", "agent": "ap", "org_ids": [101]},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
def test_draft_check_success(mock_freq, auth_headers):
    draft_payload = {
        "name": "Edited Bot",
        "org_ids": [101],
        "schedules": [{"kind": "weekly", "day": "tue", "time": "09:00"}],
        "watches": ["duplicate_bill"],
    }
    resp = client.post(
        "/api/v1/bots/draft/check",
        json={"draft": draft_payload},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["draft"]["name"] == "Edited Bot"
    assert len(data["next_runs"]) > 0


@patch("gemini_brain.bots.preview.try_period")
def test_try_draft_success(mock_try_period, auth_headers):
    mock_try_period.return_value = {
        "bills_checked": 50,
        "findings_found": 3,
        "groups_found": 1,
        "would_send": {"alerts": 4, "digests": 5},
    }
    draft_payload = {
        "name": "Test Try Bot",
        "org_ids": [101],
        "schedules": [{"kind": "weekly", "day": "mon", "time": "08:00"}],
        "watches": ["duplicate_bill"],
    }
    resp = client.post(
        "/api/v1/bots/try",
        json={"draft": draft_payload, "preset": "last_month"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["bills_checked"] == 50
    assert data["would_send"]["alerts"] == 4


def test_try_draft_invalid_preset(auth_headers):
    draft_payload = {
        "name": "Test Try Bot",
        "org_ids": [101],
        "schedules": [],
        "watches": [],
    }
    resp = client.post(
        "/api/v1/bots/try",
        json={"draft": draft_payload, "preset": "ytd"},
        headers=auth_headers,
    )
    assert resp.status_code == 400


@patch("gemini_brain.bots.store.create")
@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
def test_create_and_list_and_delete_bot_routes(mock_freq, mock_create, auth_headers):
    mock_create.return_value = {
        "id": "bot-test-uuid",
        "owner_user_id": 18,
        "name": "Live Bot",
        "agent": "vat",
        "org_ids": [101],
        "draft": {"name": "Live Bot", "org_ids": [101]},
        "status": "active",
    }

    # 1. POST /api/v1/bots
    create_payload = {
        "draft": {
            "name": "Live Bot",
            "org_ids": [101],
            "schedules": [{"kind": "weekly", "day": "mon", "time": "08:00"}],
            "watches": ["duplicate_bill"],
        },
        "status": "active",
    }
    res = client.post("/api/v1/bots", json=create_payload, headers=auth_headers)
    assert res.status_code == 201
    assert res.json()["id"] == "bot-test-uuid"

    # 2. GET /api/v1/bots
    with patch("gemini_brain.bots.store.list_for_user", return_value=[mock_create.return_value]):
        res_list = client.get("/api/v1/bots", headers=auth_headers)
        assert res_list.status_code == 200
        assert len(res_list.json()["bots"]) == 1

    # 3. GET /api/v1/bots/{id}
    with patch("gemini_brain.bots.store.get", return_value=mock_create.return_value):
        res_get = client.get("/api/v1/bots/bot-test-uuid", headers=auth_headers)
        assert res_get.status_code == 200
        assert res_get.json()["name"] == "Live Bot"

    # 4. PATCH /api/v1/bots/{id}
    with patch("gemini_brain.bots.store.get", return_value=mock_create.return_value), \
         patch("gemini_brain.bots.store.update", return_value={**mock_create.return_value, "name": "Renamed Bot"}):
        res_patch = client.patch(
            "/api/v1/bots/bot-test-uuid",
            json={"name": "Renamed Bot"},
            headers=auth_headers,
        )
        assert res_patch.status_code == 200
        assert res_patch.json()["name"] == "Renamed Bot"

    # 5. POST /api/v1/bots/{id}/pause
    with patch("gemini_brain.bots.store.set_status", return_value={**mock_create.return_value, "status": "paused"}):
        res_pause = client.post("/api/v1/bots/bot-test-uuid/pause", headers=auth_headers)
        assert res_pause.status_code == 200
        assert res_pause.json()["status"] == "paused"

    # 6. POST /api/v1/bots/{id}/resume
    with patch("gemini_brain.bots.store.get", return_value=mock_create.return_value), \
         patch("gemini_brain.bots.store.update", return_value={**mock_create.return_value, "status": "active"}):
        res_resume = client.post("/api/v1/bots/bot-test-uuid/resume", headers=auth_headers)
        assert res_resume.status_code == 200
        assert res_resume.json()["status"] == "active"

    # 7. DELETE /api/v1/bots/{id}
    with patch("gemini_brain.bots.store.delete", return_value=True):
        res_del = client.delete("/api/v1/bots/bot-test-uuid", headers=auth_headers)
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

