"""Unit tests for Phase 4: Bot Memory, Note Instructions, and Approvals."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots import store
from gemini_brain.bots.run import run_bot
from gemini_brain.bots.schema import BotDraft, Rules
from tests.unit.test_bot_store import FakeConnection

client = TestClient(app)


@pytest.fixture
def fake_conn():
    return FakeConnection()


@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=42, email="owner@example.com", allowed_org_ids=[101])
    return {"Authorization": f"Bearer {token}"}


# =========================================================================
# Memory System Tests (Task 4.1)
# =========================================================================

def test_dismissal_creates_memory_with_supplier(fake_conn):
    draft = BotDraft(name="Supplier Memory Bot", org_ids=[101])
    bot = store.create(owner_user_id=42, draft=draft, conn=fake_conn)
    bot_id = bot["id"]

    # Seed a finding
    run = store.record_run(bot_id=bot_id, trigger="manual", conn=fake_conn)
    finding = {"key": "bill-xyz", "org_id": 101, "rule": "duplicate_bill", "amount": 250.0, "state": "new"}
    store.upsert_findings(bot_id, run["id"], [finding], conn=fake_conn)

    # Dismiss with supplier name
    ok = store.dismiss_finding(
        bot_id=bot_id,
        owner_user_id=42,
        key="bill-xyz",
        supplier="Acme Trading LLC",
        conn=fake_conn,
    )
    assert ok is True

    # Memory list check
    memories = store.list_memory(bot_id, conn=fake_conn)
    assert len(memories) == 1
    assert "Acme Trading LLC" in memories[0]["text"]
    assert memories[0]["text"].endswith("Marked not a problem.")  # in words, not the rule name
    assert memories[0]["source"] == "dismissal"
    assert memories[0]["finding_key"] == "bill-xyz"


def test_delete_memory_undismisses_finding(fake_conn):
    draft = BotDraft(name="Undismiss Bot", org_ids=[101])
    bot = store.create(owner_user_id=42, draft=draft, conn=fake_conn)
    bot_id = bot["id"]

    # Seed finding and dismiss it
    run = store.record_run(bot_id=bot_id, trigger="manual", conn=fake_conn)
    finding = {"key": "bill-999", "org_id": 101, "rule": "vat_rate_wrong", "amount": 120.0, "state": "new"}
    store.upsert_findings(bot_id, run["id"], [finding], conn=fake_conn)

    store.dismiss_finding(bot_id, 42, "bill-999", conn=fake_conn)
    f_after_dismiss = store.get_findings(bot_id, conn=fake_conn)
    assert f_after_dismiss[0]["state"] == "dismissed"

    memories = store.list_memory(bot_id, conn=fake_conn)
    assert len(memories) == 1
    mem_id = memories[0]["id"]

    # Deleting the dismissal memory entry un-dismisses the finding
    del_ok = store.delete_memory(bot_id, mem_id, conn=fake_conn)
    assert del_ok is True

    f_after_delete = store.get_findings(bot_id, conn=fake_conn)
    assert f_after_delete[0]["state"] == "seen"
    assert len(store.list_memory(bot_id, conn=fake_conn)) == 0


def test_draft_notes_synced_to_memory(fake_conn):
    notes = ["Always verify TRN on bills over 10k", "Ignore 0.01 rounding discrepancies"]
    draft = BotDraft(name="Notes Bot", org_ids=[101], notes=notes)
    bot = store.create(owner_user_id=42, draft=draft, conn=fake_conn)
    bot_id = bot["id"]

    memories = store.list_memory(bot_id, conn=fake_conn)
    note_texts = [m["text"] for m in memories if m["source"] == "note"]
    assert len(note_texts) == 2
    assert "Always verify TRN" in note_texts[0]
    assert "Ignore 0.01 rounding" in note_texts[1]


# =========================================================================
# Prompt Instructions & Approvals in Run Tests (Task 4.2 & 4.3)
# =========================================================================

@patch("gemini_brain.bots.run.get_user_allowed_orgs", return_value=[101])
@patch("gemini_brain.bots.preview.try_period")
def test_run_bot_incorporates_instructions_and_sends_no_email(
    mock_try_period,
    mock_allowed,
):
    draft = BotDraft(
        name="External Mail Bot",
        org_ids=[101],
        watches=["duplicate_bill"],
        deliver=["inbox", "email"],
        rules=Rules(email_outside="ask"),
        notes=["Focus on foreign supplier invoices"],
    )

    fake_bot = {
        "id": "bot-appr-1",
        "name": "External Mail Bot",
        "owner_user_id": 42,
        "org_ids": [101],
        "draft": draft.model_dump(),
        "status": "active",
    }

    mock_try_period.return_value = {
        "bills_checked": 10,
        "invoices_checked": 5,
        "groups_found": 1,
        "findings_found": 0,
        "reviews": [
            {
                "groups": [
                    {
                        "key": "dup-1",
                        "organization_id": 101,
                        "rule": "same_number",
                        "extra_amount": 500.0,
                    }
                ],
                "findings": [],
            }
        ],
    }

    mock_llm = MagicMock(return_value="Audit completed: 1 potential duplicate bill identified.")

    with patch("gemini_brain.bots.store.get", return_value=fake_bot), \
         patch("gemini_brain.bots.store.get_findings", return_value=[]), \
         patch("gemini_brain.bots.store.list_memory", return_value=[{"text": "Focus on foreign supplier invoices", "source": "note"}]), \
         patch("gemini_brain.bots.store.upsert_findings"), \
         patch("gemini_brain.bots.store.record_run", return_value={"id": "run-appr-123"}), \
         patch("gemini_brain.bots.store.finish_run", return_value={"id": "run-appr-123", "status": "ok"}):

        res = run_bot("bot-appr-1", trigger="manual", call_model=mock_llm)

    assert res["status"] == "ok"
    # Verify model call was passed instructions
    mock_llm.assert_called_once()
    prompt_sent = mock_llm.call_args[1]["prompt"]
    assert "<user_instructions>" in prompt_sent
    assert "Focus on foreign supplier invoices" in prompt_sent

    # No email service yet: no approval with a made-up recipient, and the run says the email was not sent
    summary = res["summary"]
    assert "approval" not in summary
    assert "email_dispatched" not in summary
    assert summary["email"].startswith("not_sent")


# =========================================================================
# API Endpoints: Memory and Approvals Tests
# =========================================================================

@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.store.list_memory")
def test_api_list_memory(mock_list, mock_get, auth_headers):
    mock_get.return_value = {"id": "b1", "owner_user_id": 42}
    mock_list.return_value = [
        {"id": 1, "bot_id": "b1", "text": "Note 1", "source": "note", "finding_key": None, "created_at": "2026-10-29T10:00:00Z"}
    ]

    res = client.get("/api/v1/bots/b1/memory", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["text"] == "Note 1"


@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.store.add_memory", return_value=12)
def test_api_add_memory(mock_add, mock_get, auth_headers):
    mock_get.return_value = {"id": "b1", "owner_user_id": 42}
    res = client.post("/api/v1/bots/b1/memory", headers=auth_headers, json={"text": "Review high value bills", "source": "note"})
    assert res.status_code == 200
    assert res.json()["id"] == 12
    assert res.json()["text"] == "Review high value bills"


@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.store.delete_memory", return_value=True)
def test_api_delete_memory(mock_del, mock_get, auth_headers):
    mock_get.return_value = {"id": "b1", "owner_user_id": 42}
    res = client.delete("/api/v1/bots/b1/memory/5", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["deleted"] is True


@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.store.update_approval_status")
def test_api_approval_actions(mock_update, mock_get, auth_headers):
    mock_get.return_value = {"id": "b1", "owner_user_id": 42}

    # Approve
    mock_update.return_value = {"id": "appr-1", "bot_id": "b1", "action": "email_outside", "status": "approved"}
    res = client.post("/api/v1/bots/b1/approvals/appr-1/approve", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "approved"

    # Reject
    mock_update.return_value = {"id": "appr-1", "bot_id": "b1", "action": "email_outside", "status": "rejected"}
    res = client.post("/api/v1/bots/b1/approvals/appr-1/reject", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"

    # Edit & Approve
    mock_update.return_value = {
        "id": "appr-1",
        "bot_id": "b1",
        "action": "email_outside",
        "status": "approved",
        "recipient": "custom@advisor.com",
    }
    res = client.post(
        "/api/v1/bots/b1/approvals/appr-1/edit",
        headers=auth_headers,
        json={"recipient": "custom@advisor.com"},
    )
    assert res.status_code == 200
    assert res.json()["recipient"] == "custom@advisor.com"
