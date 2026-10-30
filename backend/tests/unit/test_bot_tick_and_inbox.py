"""Unit tests for bot scheduler tick, inbox, and finding dismissal endpoints (Tasks 2.5 and 2.6)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.config.settings import settings

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=18, email="tester@example.com", allowed_org_ids=[101, 102])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def valid_service_token(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_token", "test-bot-service-token")
    return "test-bot-service-token"


def test_service_routes_off_without_token(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_token", "")
    for path in ("/api/v1/bots/tick", "/api/v1/bots/event"):
        res = client.post(path, headers={"x-bot-service-token": "accutax-bot-service-tick-token"},
                          json={"event": "bill.saved", "org_id": 101, "document_ids": [1]})
        assert res.status_code == 503


def test_tick_auth_unauthorized(valid_service_token):
    # No token provided
    res = client.post("/api/v1/bots/tick")
    assert res.status_code == 401

    # Wrong token
    res = client.post("/api/v1/bots/tick", headers={"x-bot-service-token": "wrong-secret-token"})
    assert res.status_code == 401


@patch("gemini_brain.bots.store.due_bots")
@patch("gemini_brain.bots.run.run_bot")
@patch("gemini_brain.bots.store.set_next_run")
@patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={101: "quarterly"})
def test_tick_execution_success(
    mock_freq,
    mock_set_next,
    mock_run,
    mock_due,
    valid_service_token,
):
    draft_obj = BotDraft(
        org_ids=[101],
        name="Scheduled VAT Bot",
        schedules=[Schedule(kind="weekly", day="mon", time="08:00")],
    )
    due_bot = {
        "id": "bot-sched-1",
        "name": "Scheduled VAT Bot",
        "owner_user_id": 18,
        "org_ids": [101],
        "draft": draft_obj.model_dump(),
        "status": "active",
    }
    mock_due.return_value = [due_bot]
    mock_run.return_value = {
        "status": "ok",
        "bot_id": "bot-sched-1",
        "summary": {"headline": "All clean", "new_findings": 0},
    }

    # Test via x-bot-service-token
    res = client.post(
        "/api/v1/bots/tick",
        headers={"x-bot-service-token": valid_service_token},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["ran"] == 1
    assert data["results"][0]["bot_id"] == "bot-sched-1"
    assert data["results"][0]["status"] == "ok"
    mock_run.assert_called_once_with("bot-sched-1", trigger="schedule")
    mock_set_next.assert_called_once()


@patch("gemini_brain.bots.store.get_inbox")
def test_get_inbox(mock_get_inbox, auth_headers):
    mock_get_inbox.return_value = {
        "unread_count": 2,
        "bots_count": 1,
        "runs": [
            {
                "id": "run-1",
                "bot_id": "bot-1",
                "bot_name": "VAT Watch",
                "trigger": "schedule",
                "status": "ok",
                "started_at": "2026-10-29T10:00:00Z",
                "summary": {"headline": "Found 2 problems", "new_findings": 2},
            }
        ],
        "findings": [
            {
                "bot_id": "bot-1",
                "bot_name": "VAT Watch",
                "key": "bill-101-dup",
                "org_id": 101,
                "rule": "duplicate_bill",
                "state": "new",
                "amount": 250.0,
                "updated_at": "2026-10-29T10:05:00Z",
            }
        ],
    }

    res = client.get("/api/v1/bots/inbox", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["unread_count"] == 2
    assert data["bots_count"] == 1
    assert len(data["runs"]) == 1
    assert len(data["findings"]) == 1
    assert data["findings"][0]["key"] == "bill-101-dup"


@patch("gemini_brain.bots.store.mark_inbox_seen")
def test_mark_inbox_read(mock_mark_seen, auth_headers):
    mock_mark_seen.return_value = 3
    res = client.post("/api/v1/bots/inbox/mark-read", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["marked_seen"] == 3
    mock_mark_seen.assert_called_once_with(owner_user_id=18)


@patch("gemini_brain.bots.store.dismiss_finding")
def test_dismiss_finding(mock_dismiss, auth_headers):
    mock_dismiss.return_value = True
    res = client.post(
        "/api/v1/bots/bot-1/findings/item-key-1/dismiss",
        json={"note": "Approved exception"},
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["state"] == "dismissed"
    mock_dismiss.assert_called_once_with(
        bot_id="bot-1",
        owner_user_id=18,
        key="item-key-1",
        note="Approved exception",
    )


@patch("gemini_brain.bots.store.dismiss_finding")
def test_dismiss_finding_not_found(mock_dismiss, auth_headers):
    mock_dismiss.return_value = False
    res = client.post(
        "/api/v1/bots/bot-1/findings/missing-key/dismiss",
        headers=auth_headers,
    )
    assert res.status_code == 404


@patch("gemini_brain.bots.store.undismiss_finding")
def test_undismiss_finding(mock_undismiss, auth_headers):
    mock_undismiss.return_value = True
    res = client.post(
        "/api/v1/bots/bot-1/findings/item-key-1/undismiss",
        headers=auth_headers,
    )
    assert res.status_code == 200
    assert res.json()["state"] == "seen"
    mock_undismiss.assert_called_once_with(
        bot_id="bot-1",
        owner_user_id=18,
        key="item-key-1",
    )
