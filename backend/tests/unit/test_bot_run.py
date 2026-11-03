"""Unit tests for bot runner (Task 2.4)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.bots.run import run_bot
from gemini_brain.bots.schema import BotDraft, Schedule


@pytest.fixture
def sample_bot():
    return {
        "id": "bot-run-123",
        "owner_user_id": 7,
        "name": "Audit Bot",
        "agent": "vat",
        "org_ids": [101],
        "draft": {
            "name": "Audit Bot",
            "org_ids": [101],
            "schedules": [{"kind": "weekly", "day": "mon", "time": "08:00"}],
            "watches": ["duplicate_bill"],
        },
        "status": "active",
    }


@patch("gemini_brain.bots.store.get", return_value=None)
def test_run_bot_not_found(mock_get):
    res = run_bot("unknown-id")
    assert res["status"] == "skipped"
    assert res["reason"] == "not_found"


@patch("gemini_brain.bots.store.get")
def test_run_bot_not_active(mock_get, sample_bot):
    sample_bot["status"] = "paused"
    mock_get.return_value = sample_bot

    res = run_bot(sample_bot["id"])
    assert res["status"] == "skipped"
    assert res["reason"] == "not_active"


@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.run.get_user_allowed_orgs", return_value=[999])
@patch("gemini_brain.bots.store.set_status")
@patch("gemini_brain.bots.store.record_run", return_value={"id": "run-fail"})
@patch("gemini_brain.bots.store.finish_run")
def test_run_bot_no_org_access(mock_fin, mock_rec, mock_status, mock_orgs, mock_get, sample_bot):
    mock_get.return_value = sample_bot

    res = run_bot(sample_bot["id"])
    assert res["status"] == "error"
    assert "No access" in res["error"]
    mock_status.assert_called_once_with(sample_bot["id"], 7, status="error", status_note="No access to the bot's organizations")


@patch("gemini_brain.bots.store.get")
@patch("gemini_brain.bots.run.get_user_allowed_orgs", return_value=[101])
@patch("gemini_brain.bots.store.get_findings")
@patch("gemini_brain.bots.preview.try_period")
@patch("gemini_brain.bots.store.record_run", return_value={"id": "run-ok-1"})
@patch("gemini_brain.bots.store.upsert_findings")
@patch("gemini_brain.bots.store.update_finding_state")
@patch("gemini_brain.bots.store.finish_run")
@patch("gemini_brain.bots.store.list_memory", return_value=[])
def test_run_bot_success_and_diff(
    mock_mem, mock_fin, mock_update_state, mock_upsert, mock_rec, mock_try, mock_get_findings, mock_orgs, mock_get, sample_bot
):
    mock_get.return_value = sample_bot
    # Existing findings in DB: one active finding that will disappear (fixed), one dismissed
    mock_get_findings.side_effect = [
        [{"key": "dismissed_item", "state": "dismissed"}],  # dismissed check
        [{"key": "old_problem", "state": "new"}],           # diff check
    ]

    mock_try.return_value = {
        "bills_checked": 30,
        "invoices_checked": 10,
        "groups_found": 1,
        "findings_found": 0,
        "reviews": [
            {
                "groups": [
                    {"key": "new_dup", "rule": "same_number", "extra_amount": 1200.0, "organization_id": 101}
                ],
                "findings": [],
            }
        ],
    }

    mock_fin.return_value = {
        "id": "run-ok-1",
        "bot_id": sample_bot["id"],
        "status": "ok",
        "credits": 90,
    }

    res = run_bot(sample_bot["id"])

    assert res["status"] == "ok"
    assert res["summary"]["new_findings"] == 1
    assert res["summary"]["fixed_findings"] == 1
    assert "1 new problem" in res["summary"]["headline"]
    assert "1 problem was fixed since the last run" in res["summary"]["headline"]

    # Verify old_problem marked fixed
    mock_update_state.assert_called_once_with(sample_bot["id"], "old_problem", "fixed")
    # Verify upsert_findings called with new_dup
    mock_upsert.assert_called_once()
    upserted = mock_upsert.call_args[0][2]
    assert len(upserted) == 1
    assert upserted[0]["key"] == "new_dup"
    assert upserted[0]["state"] == "new"
