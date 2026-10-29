"""Unit tests for bot event hooks, quiet hours, sentence generation, and scoped document monitoring (Phase 3)."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.bots.monitor import (
    generate_finding_sentence,
    handle_document_event,
    is_in_quiet_hours,
)
from gemini_brain.bots.schema import BotDraft
from gemini_brain.bots.watches import watch_for_rule
from gemini_brain.config.settings import settings

client = TestClient(app)


@pytest.fixture
def service_token(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_token", "test-bot-service-token")
    return "test-bot-service-token"


# =========================================================================
# Task 3.1 & 3.2: POST /api/v1/bots/event Tests
# =========================================================================

def test_event_hook_auth_unauthorized(service_token):
    # No token
    res = client.post("/api/v1/bots/event", json={"event": "bill.saved", "org_id": 101, "document_ids": [1]})
    assert res.status_code == 401

    # Wrong token in header
    res = client.post(
        "/api/v1/bots/event",
        headers={"x-bot-service-token": "invalid-service-token"},
        json={"event": "bill.saved", "org_id": 101, "document_ids": [1]},
    )
    assert res.status_code == 401


def test_event_hook_invalid_event_type(service_token):
    res = client.post(
        "/api/v1/bots/event",
        headers={"x-bot-service-token": service_token},
        json={"event": "user.login", "org_id": 101, "document_ids": [1]},
    )
    assert res.status_code == 400
    assert "Unsupported event type" in res.json()["detail"]


@patch("gemini_brain.bots.monitor.handle_document_event")
def test_event_hook_success(mock_handle, service_token):
    mock_handle.return_value = {
        "status": "ok",
        "event": "bill.saved",
        "org_id": 101,
        "bots_checked": 1,
        "findings_matched": 1,
        "new_findings": 1,
        "quiet_hours": False,
        "details": [{"bot_id": "bot-1", "matched": 1, "new": 1}],
    }

    res = client.post(
        "/api/v1/bots/event",
        headers={"x-bot-service-token": service_token},
        json={"event": "bill.saved", "org_id": 101, "document_ids": [42]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["bots_checked"] == 1
    assert data["new_findings"] == 1
    assert len(data["details"]) == 1
    mock_handle.assert_called_once_with(
        event="bill.saved",
        org_id=101,
        document_ids=[42],
    )


# =========================================================================
# Task 3.2: Quiet Hours Logic Tests
# =========================================================================

def test_is_in_quiet_hours_overnight_window():
    # Window: 20:00 to 08:00 (default)
    # 23:30 at night -> in quiet hours
    t_night = dt.datetime(2026, 4, 15, 23, 30, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_night, tz_name="UTC", start_hour=20, end_hour=8) is True

    # 03:00 morning -> in quiet hours
    t_early = dt.datetime(2026, 4, 15, 3, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_early, tz_name="UTC", start_hour=20, end_hour=8) is True

    # 20:00 start boundary -> in quiet hours
    t_start = dt.datetime(2026, 4, 15, 20, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_start, tz_name="UTC", start_hour=20, end_hour=8) is True

    # 07:59 just before end -> in quiet hours
    t_before_end = dt.datetime(2026, 4, 15, 7, 59, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_before_end, tz_name="UTC", start_hour=20, end_hour=8) is True

    # 08:00 end boundary -> not in quiet hours
    t_end = dt.datetime(2026, 4, 15, 8, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_end, tz_name="UTC", start_hour=20, end_hour=8) is False

    # 14:00 daytime -> not in quiet hours
    t_day = dt.datetime(2026, 4, 15, 14, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_day, tz_name="UTC", start_hour=20, end_hour=8) is False


def test_is_in_quiet_hours_daytime_window():
    # Window: 13:00 to 15:00 (same-day window)
    t_during = dt.datetime(2026, 4, 15, 14, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_during, tz_name="UTC", start_hour=13, end_hour=15) is True

    t_outside = dt.datetime(2026, 4, 15, 16, 0, tzinfo=dt.timezone.utc)
    assert is_in_quiet_hours(now=t_outside, tz_name="UTC", start_hour=13, end_hour=15) is False


# =========================================================================
# Task 3.2: 1-Sentence Explanation Generation Tests
# =========================================================================

def test_generate_finding_sentence_fallback():
    # Duplicate bill finding
    dup_finding = {
        "rule": "duplicate_bill",
        "bills": [{"bill_number": "BILL-901"}, {"bill_number": "BILL-902"}],
        "reason": "Matching amounts and dates",
    }
    sent = generate_finding_sentence(dup_finding, "duplicate_bill")
    assert "BILL-901" in sent
    assert "BILL-902" in sent

    # Wrong VAT rate
    rate_finding = {
        "rule": "vat_rate_wrong",
        "bill": {"bill_number": "INV-77"},
        "tax_rate": 0.03,
        "expected_rate": 0.05,
    }
    sent = generate_finding_sentence(rate_finding, "vat_rate_wrong")
    assert "INV-77" in sent
    assert "VAT rate" in sent

    # Missing details
    missing_finding = {
        "rule": "bill_missing_details",
        "bill": {"bill_number": "REC-01"},
        "missing_fields": ["trn", "issue_date"],
    }
    sent = generate_finding_sentence(missing_finding, "bill_missing_details")
    assert "REC-01" in sent
    assert "trn" in sent

    # Unusual bill
    unusual_finding = {
        "rule": "unusual_bill",
        "bill": {"bill_number": "BIG-1"},
        "amount": 950000,
    }
    sent = generate_finding_sentence(unusual_finding, "unusual_bill")
    assert "BIG-1" in sent
    assert "950,000" in sent


def test_generate_finding_sentence_with_llm():
    mock_llm = MagicMock(return_value="Vendor Acme billed twice under different reference numbers.")
    finding = {"rule": "duplicate_bill", "bills": [{"bill_number": "B1"}, {"bill_number": "B2"}]}
    sent = generate_finding_sentence(finding, "duplicate_bill", call_model=mock_llm)
    assert sent == "Vendor Acme billed twice under different reference numbers."
    mock_llm.assert_called_once()


# =========================================================================
# Task 3.3: Watch Attribution Tests
# =========================================================================

def test_watch_for_rule_attribution():
    # duplicate_bill -> duplicate_bill watch
    w1 = watch_for_rule("duplicate_bill")
    assert w1 is not None
    assert w1.id == "duplicate_bill"
    assert w1.label == "A bill looks recorded twice"

    # vat_rate_wrong -> vat_rate_wrong watch
    w2 = watch_for_rule("vat_rate_wrong")
    assert w2 is not None
    assert w2.id == "vat_rate_wrong"
    assert "VAT on a bill" in w2.label

    # sales_vat_wrong -> sales_vat_wrong watch
    w3 = watch_for_rule("sales_vat_wrong")
    assert w3 is not None
    assert w3.id == "sales_vat_wrong"

    # unknown rule
    assert watch_for_rule("non_existent_rule") is None


# =========================================================================
# Task 3.2 & 3.3: handle_document_event End-to-End Monitoring Engine Tests
# =========================================================================

@patch("gemini_brain.bots.store.list_active_bots_for_org")
def test_handle_document_event_no_bots(mock_list):
    mock_list.return_value = []
    res = handle_document_event(event="bill.saved", org_id=101, document_ids=[10])
    assert res["status"] == "ok"
    assert res["bots_checked"] == 0
    assert res["new_findings"] == 0


@patch("gemini_brain.bots.store.list_active_bots_for_org")
@patch("gemini_brain.bots.monitor.get_user_allowed_orgs", return_value=[101])
@patch("gemini_brain.vat_review.tool.run")
@patch("gemini_brain.bots.store.get_findings", return_value=[])
@patch("gemini_brain.bots.store.upsert_findings")
@patch("gemini_brain.bots.store.record_run")
@patch("gemini_brain.bots.store.finish_run")
def test_handle_document_event_scoped_execution(
    mock_finish_run,
    mock_record_run,
    mock_upsert_findings,
    mock_get_findings,
    mock_vat_run,
    mock_get_allowed,
    mock_list_bots,
):
    # Setup active bot watching bills
    draft = BotDraft(
        org_ids=[101],
        name="Purchases Monitor",
        watches=["duplicate_bill"],
    )
    mock_list_bots.return_value = [
        {
            "id": "bot-test-1",
            "name": "Purchases Monitor",
            "owner_user_id": 1,
            "org_ids": [101],
            "draft": draft.model_dump(),
        }
    ]

    # vat_review_tool.run returns findings: one group containing document 10 (target), one for document 99 (not target)
    mock_vat_run.return_value = {
        "status": "ok",
        "groups": [
            {
                "item_key": "purchases|duplicate_bill|101|10",
                "rule": "duplicate_bill",
                "bills": [{"id": 10, "bill_number": "B-10"}],
                "reason": "Duplicate amount",
            },
            {
                "item_key": "purchases|duplicate_bill|101|99",
                "rule": "duplicate_bill",
                "bills": [{"id": 99, "bill_number": "B-99"}],
                "reason": "Unrelated bill",
            },
        ],
        "findings": [],
        "bills_checked": 5,
    }

    mock_record_run.return_value = {"id": "run-ev-1"}

    res = handle_document_event(
        event="bill.saved",
        org_id=101,
        document_ids=[10],
    )

    assert res["status"] == "ok"
    assert res["bots_checked"] == 1
    assert res["findings_matched"] == 1
    assert res["new_findings"] == 1

    # Check that upsert_findings received only the scoped finding for document 10
    assert mock_upsert_findings.called
    saved_items = mock_upsert_findings.call_args[0][2]
    assert len(saved_items) == 1
    assert saved_items[0]["key"] == "purchases|duplicate_bill|101|10"
    assert saved_items[0]["state"] == "new"

    # Check run recorded with trigger="event"
    mock_record_run.assert_called_once_with("bot-test-1", trigger="event", status="running")
    mock_finish_run.assert_called_once()
