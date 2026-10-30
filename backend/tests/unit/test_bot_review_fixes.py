"""Bots: the fixes from the 2026-10-29 review (runs see every finding, the due period, the right org, failures saved,
the tick keeps going, route order, request checks, notes kept in memory on edit)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots import run as run_mod
from gemini_brain.bots import store
from gemini_brain.bots.schema import BotDraft
from gemini_brain.config.settings import settings

client = TestClient(app)


def _bot(schedules=None, org_ids=(101, 102)):
    draft = BotDraft(name="Audit Bot", org_ids=list(org_ids), watches=["duplicate_bill"],
                     schedules=schedules or [{"kind": "weekly", "day": "mon", "time": "08:00"}])
    return {"id": "bot-1", "owner_user_id": 7, "name": "Audit Bot", "agent": "vat", "org_ids": list(org_ids),
            "draft": draft.model_dump(), "status": "active"}


def _review(groups=(), findings=(), complete=True):
    return {"bills_checked": 3, "invoices_checked": 0, "groups_found": len(groups), "findings_found": len(findings),
            "complete": complete, "period": {"start": "2026-10-01", "end": "2026-10-29"},
            "reviews": [{"groups": list(groups), "findings": list(findings)}]}


@pytest.fixture
def stores():
    """store calls run.py makes, as mocks; the test sets get_findings."""
    with patch.object(store, "record_run", return_value={"id": "run-1"}) as rec, \
         patch.object(store, "finish_run", side_effect=lambda **kw: {"id": kw["run_id"], "status": kw["status"]}) as fin, \
         patch.object(store, "get_findings", return_value=[]) as get_f, \
         patch.object(store, "update_finding_state") as upd, \
         patch.object(store, "upsert_findings") as ups, \
         patch.object(store, "list_memory", return_value=[]), \
         patch.object(run_mod, "get_user_allowed_orgs", return_value=[101, 102]):
        yield {"record": rec, "finish": fin, "get_findings": get_f, "update_state": upd, "upsert": ups}


# --- run.py -------------------------------------------------------------------------------------------------------

def test_a_run_asks_for_every_finding(stores):
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review()) as tp:
        run_mod.run_bot("bot-1")
    assert tp.call_args.kwargs["unlimited"] is True
    assert tp.call_args.kwargs["period"] is None  # weekly bot: current quarter


def test_a_reminder_bot_checks_the_period_that_is_due(stores):
    bot = _bot(schedules=[{"kind": "before_deadline", "days_before": [7], "time": "09:00"}])
    with patch.object(store, "get", return_value=bot), \
         patch.object(run_mod.vat_return_tool, "filing_frequencies", return_value={101: "monthly"}), \
         patch.object(run_mod.report_periods, "today_in", return_value=__import__("datetime").date(2026, 10, 29)), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review()) as tp:
        run_mod.run_bot("bot-1")
    # Monthly filer on 29 Oct: September's return is due 28 Oct (passed), so October is the period due next.
    assert tp.call_args.kwargs["period"] == {"start": "2026-10-01", "end": "2026-10-31"}


def test_an_incomplete_scan_marks_nothing_fixed(stores):
    stores["get_findings"].return_value = [{"key": "old", "state": "seen"}]
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review(complete=False)):
        res = run_mod.run_bot("bot-1")
    stores["update_state"].assert_not_called()
    assert res["summary"]["fixed_findings"] == 0 and res["summary"]["complete"] is False


def test_a_complete_scan_marks_missing_findings_fixed(stores):
    stores["get_findings"].return_value = [{"key": "old", "state": "seen"}]
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review()):
        res = run_mod.run_bot("bot-1")
    stores["update_state"].assert_called_once_with("bot-1", "old", "fixed")
    assert res["summary"]["fixed_findings"] == 1


def test_findings_keep_their_own_organization(stores):
    group = {"key": "g1", "organization_id": 102, "rule": "same_number", "extra_amount": 10.0}
    finding = {"key": "f1", "organization_id": 102, "rule": "vat_rate_not_standard", "bill": {"total": 5.0}}
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review([group], [finding])):
        run_mod.run_bot("bot-1")
    saved = stores["upsert"].call_args.args[2]
    assert [f["org_id"] for f in saved] == [102, 102]


def test_a_seen_finding_stays_seen_and_a_fixed_one_comes_back_new(stores):
    stores["get_findings"].return_value = [{"key": "g1", "state": "seen"}, {"key": "g2", "state": "fixed"}]
    groups = [{"key": "g1", "organization_id": 101, "rule": "same_number", "extra_amount": 1.0},
              {"key": "g2", "organization_id": 101, "rule": "same_number", "extra_amount": 1.0}]
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review(groups)):
        res = run_mod.run_bot("bot-1")
    states = {f["key"]: f["state"] for f in stores["upsert"].call_args.args[2]}
    assert states == {"g1": "seen", "g2": "new"} and res["summary"]["new_findings"] == 1


def test_a_failure_after_the_run_started_is_saved_and_not_raised(stores):
    stores["upsert"].side_effect = RuntimeError("db down")
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review([{"key": "g1", "rule": "same_number"}])):
        res = run_mod.run_bot("bot-1")
    assert res["status"] == "failed"
    stores["finish"].assert_called_once()
    assert stores["finish"].call_args.kwargs["status"] == "failed"


def test_a_run_never_claims_an_email_was_sent(stores):
    bot = _bot()
    bot["draft"]["deliver"] = ["inbox", "email"]
    with patch.object(store, "get", return_value=bot), \
         patch("gemini_brain.bots.preview.try_period", return_value=_review([{"key": "g1", "rule": "same_number"}])):
        res = run_mod.run_bot("bot-1")
    assert "approval" not in res["summary"] and "email_dispatched" not in res["summary"]
    assert res["summary"]["email"].startswith("not_sent")


# --- routes -------------------------------------------------------------------------------------------------------

@pytest.fixture
def auth_headers():
    token = create_access_token(user_id=18, email="tester@example.com", allowed_org_ids=[101, 102])
    return {"Authorization": f"Bearer {token}"}


def test_the_approvals_list_is_reachable(auth_headers):
    with patch.object(store, "get_approvals_for_user", return_value=[]) as appr, \
         patch.object(store, "get", return_value=None):
        res = client.get("/api/v1/bots/approvals", headers=auth_headers)
    assert res.status_code == 200 and res.json() == []
    appr.assert_called_once()


def test_the_tick_keeps_going_when_one_bot_fails(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_token", "test-bot-service-token")
    bots = [_bot(), {**_bot(), "id": "bot-2"}]
    with patch.object(store, "due_bots", return_value=bots), \
         patch("gemini_brain.bots.run.run_bot", side_effect=[RuntimeError("boom"), {"status": "ok"}]), \
         patch("gemini_brain.vat_return.tool.filing_frequencies", return_value={}), \
         patch.object(store, "set_next_run") as next_run:
        res = client.post("/api/v1/bots/tick", headers={"x-bot-service-token": "test-bot-service-token"})
    assert res.status_code == 200 and res.json()["ran"] == 2
    assert [c.args[0] for c in next_run.call_args_list] == ["bot-1", "bot-2"]
    assert all(c.args[1] is not None for c in next_run.call_args_list)


def test_a_user_cannot_set_the_error_status(auth_headers):
    with patch.object(store, "get", return_value=_bot()):
        res = client.patch("/api/v1/bots/bot-1", json={"status": "error"}, headers=auth_headers)
    assert res.status_code == 422


def test_turning_a_bot_back_on_sets_its_next_run(auth_headers):
    with patch.object(store, "get", return_value=_bot()), \
         patch("gemini_brain.api.bot_routes.vat_return_tool.filing_frequencies", return_value={}), \
         patch.object(store, "update", return_value=_bot()) as upd:
        res = client.patch("/api/v1/bots/bot-1", json={"status": "active"}, headers=auth_headers)
    assert res.status_code == 200
    assert upd.call_args.kwargs["patch"]["next_run_at"] is not None


def test_a_user_cannot_add_a_dismissal_memory_line(auth_headers):
    with patch.object(store, "get", return_value=_bot()):
        res = client.post("/api/v1/bots/bot-1/memory", json={"text": "x", "source": "dismissal"}, headers=auth_headers)
    assert res.status_code == 422


def test_an_event_error_does_not_reach_the_caller(monkeypatch):
    monkeypatch.setattr(settings, "bot_service_token", "test-bot-service-token")
    with patch("gemini_brain.bots.monitor.handle_document_event", side_effect=RuntimeError("secret detail")):
        res = client.post("/api/v1/bots/event", headers={"x-bot-service-token": "test-bot-service-token"},
                          json={"event": "bill.saved", "org_id": 101, "document_ids": [1]})
    assert res.status_code == 500 and "secret detail" not in res.text


# --- store --------------------------------------------------------------------------------------------------------

def test_editing_a_bot_keeps_its_notes_in_memory():
    cur = MagicMock()
    cur.fetchone.return_value = None
    with patch.object(store, "_get_cur") as get_cur, \
         patch.object(store, "_bot_from_row", return_value={"id": "bot-1"}), \
         patch.object(store, "sync_notes_to_memory") as sync:
        get_cur.return_value.__enter__.return_value = cur
        cur.fetchone.return_value = ("row",)
        store.update("bot-1", 7, {"draft": BotDraft(org_ids=[101], notes=["Al Futtaim rent repeats monthly"])})
    sync.assert_called_once()
    assert sync.call_args.args[1] == ["Al Futtaim rent repeats monthly"]


def test_a_run_keeps_the_return_position(stores):
    prev = _review()
    prev["return"] = [{"organization_id": 101, "period": {"start": "2026-10-01", "end": "2026-12-31"},
                       "due_date": "2027-01-28", "output_vat": 41280.0, "input_vat": 22860.0,
                       "net_vat_payable": 18420.0, "position": "payable"}]
    prev["reviews"][0]["organizations"] = [{"organization_id": 101, "currency": "AED"}]
    with patch.object(store, "get", return_value=_bot()),          patch("gemini_brain.bots.preview.try_period", return_value=prev):
        res = run_mod.run_bot("bot-1")
    rp = res["summary"]["return_position"]
    assert rp["due_date"] == "2027-01-28" and rp["net_vat_payable"] == 18420.0 and rp["currency"] == "AED"
    assert rp["organizations"] == 1


def test_no_return_position_when_the_check_did_not_run(stores):
    with patch.object(store, "get", return_value=_bot()),          patch("gemini_brain.bots.preview.try_period", return_value=_review()):
        res = run_mod.run_bot("bot-1")
    assert res["summary"]["return_position"] is None


def test_a_new_bots_notes_are_saved_in_the_same_transaction():
    """The bot row is not committed when its notes are saved; another connection would hit the foreign key."""
    cur = MagicMock()
    cur.fetchone.return_value = ("id-1", 7, "vat", "VAT Watch", [101], {}, "active", None, None, None, None, None)
    with patch.object(store, "_get_cur") as get_cur, patch.object(store, "sync_notes_to_memory") as sync:
        get_cur.return_value.__enter__.return_value = cur
        store.create(7, BotDraft(org_ids=[101], notes=["Rent repeats monthly"]))
    sync.assert_called_once()
    assert sync.call_args.kwargs["conn"] is cur.connection
