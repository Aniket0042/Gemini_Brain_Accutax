"""The AP bot (docs/BOT_AGENTS_AND_AP_PLAN.md, part B): overdue and due-soon bills, new suppliers, the payables
position, and the whole flow through run_bot, the monitor, validate and the API. Cube is faked with fixed rows."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from gemini_brain.api.app import app
from gemini_brain.api.auth import create_access_token
from gemini_brain.bots import agents, ap_engine, monitor, payables_scan, store, validate
from gemini_brain.bots import run as run_mod
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.vat_review import scan as scan_mod

TODAY = dt.date(2026, 10, 30)
ENGINE = agents.get("ap").engine
client = TestClient(app)


def open_bill(doc, due, outstanding=1000.0, vendor="Gulf Agri Supplies"):
    return {"document_id": doc, "document_number": f"B-{doc}", "vendor_name": vendor, "document_date": "2026-09-01",
            "due_date": due.isoformat() + "T00:00:00.000" if due else None, "outstanding": outstanding, "currency": "AED",
            "organization_name": "Org 1"}


class FakeCube:
    """Answers the payables and purchases queries the AP bot makes, from plain lists."""

    def __init__(self, payables=(), bills=(), history=()):
        self.payables = list(payables)      # open bills
        self.bills = list(bills)            # purchases in any period
        self.history = set(history)         # vendors with bills before the period
        self.queries = []

    def load(self, query, *, organization_ids, subject, deadline):
        self.queries.append(query)
        measures = query.get("measures") or []
        filters = {f["member"]: f for f in query.get("filters") or []}
        if "payables.overdue_amount" in measures:
            over = [b for b in self.payables if b["due_date"] and b["due_date"][:10] < TODAY.isoformat()]
            return SimpleNamespace(rows=[{"payables.organization_id": 1, "payables.currency": "AED",
                                          "payables.overdue_amount": sum(b["outstanding"] for b in over),
                                          "payables.overdue_count": len(over)}])
        if "payables.open_count" in measures and filters["payables.due_date"]["operator"] == "notSet":
            undated = [x for x in self.payables if not x["due_date"]]
            return SimpleNamespace(rows=[{"payables.organization_id": 1, "payables.currency": "AED",
                                          "payables.outstanding": sum(x["outstanding"] for x in undated),
                                          "payables.open_count": len(undated)}])
        if "payables.open_count" in measures:
            a, b = filters["payables.due_date"]["values"]
            soon = [x for x in self.payables if x["due_date"] and a <= x["due_date"][:10] <= b]
            return SimpleNamespace(rows=[{"payables.organization_id": 1, "payables.currency": "AED",
                                          "payables.outstanding": sum(x["outstanding"] for x in soon),
                                          "payables.open_count": len(soon)}])
        if "payables.outstanding" in measures:
            before = filters["payables.due_date"]["values"][0]
            rows = [b for b in self.payables if b["due_date"] and b["due_date"][:10] < before]
            for b in rows:
                b["days_overdue"] = max((TODAY - dt.date.fromisoformat(b["due_date"][:10])).days, 0)
            return SimpleNamespace(rows=[{f"payables.{k}": v for k, v in b.items()} for b in rows])
        if "purchases.bill_count" in measures:
            vendors = filters["purchases.vendor_name"]["values"]
            return SimpleNamespace(rows=[{"purchases.vendor_name": v, "purchases.bill_count": 3}
                                         for v in vendors if v in self.history])
        a, b = query["timeDimensions"][0]["dateRange"]
        return SimpleNamespace(rows=[{f"purchases.{k}": v for k, v in x.items()} for x in self.bills
                                     if a <= x["document_date"] <= b])


@pytest.fixture
def cube(monkeypatch):
    fake = FakeCube()
    monkeypatch.setattr(payables_scan.cube_client, "load", fake.load)
    monkeypatch.setattr(scan_mod.cube_client, "load", fake.load)
    monkeypatch.setattr(ap_engine.report_periods, "today_in", lambda tz: TODAY)
    return fake


def payables_call(sensitivity="balanced", rules=("bill_overdue", "bill_due_soon")):
    return next(c for c in ENGINE.calls(list(rules), sensitivity) if c["kind"] == "payables")


def run_call(call, exclude=frozenset()):
    return ENGINE.run(call, org_ids=[1], period=None, subject="t", deadline=1e12, exclude=exclude, unlimited=True)


# --- overdue and due soon -----------------------------------------------------------------------------------------

def test_overdue_and_due_soon_bills_and_nothing_further_out(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=12)), open_bill(2, TODAY + dt.timedelta(days=2)),
                     open_bill(3, TODAY + dt.timedelta(days=20))]
    res = run_call(payables_call())
    rules = {f["bill"]["document_id"]: f["rule"] for f in res["findings"]}
    assert rules == {1: "bill_overdue", 2: "bill_due_soon"}
    overdue = next(f for f in res["findings"] if f["rule"] == "bill_overdue")
    assert overdue["why"] == "Gulf Agri Supplies bill B-1 for AED 1,000.00 was due on 18 Oct and is 12 days overdue."
    assert overdue["bill"]["outstanding"] == 1000.0 and overdue["bill"]["total"] == 1000.0
    assert res["findings_by_rule"] == {"bill_overdue": 1, "bill_due_soon": 1}


def test_fewer_alerts_hides_a_bill_five_days_overdue(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=5))]
    assert run_call(payables_call("balanced"))["findings_found"] == 1
    assert run_call(payables_call("fewer"))["findings_found"] == 0


def test_the_key_stays_when_a_bill_turns_overdue(cube):
    cube.payables = [open_bill(7, TODAY + dt.timedelta(days=1))]
    soon = run_call(payables_call())["findings"][0]
    cube.payables = [open_bill(7, TODAY - dt.timedelta(days=3))]
    late = run_call(payables_call())["findings"][0]
    assert soon["rule"] == "bill_due_soon" and late["rule"] == "bill_overdue" and soon["key"] == late["key"]


def test_only_the_chosen_rule_is_reported(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=12)), open_bill(2, TODAY + dt.timedelta(days=2))]
    res = run_call(payables_call(rules=("bill_overdue",)))
    assert [f["rule"] for f in res["findings"]] == ["bill_overdue"]


def test_a_dismissed_bill_is_left_out(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=12))]
    key = payables_scan.bill_key(1, 1)
    res = run_call(payables_call(), exclude={key})
    assert res["findings"] == [] and res["dismissed"] == 1


# --- new suppliers ------------------------------------------------------------------------------------------------

def bill(doc, vendor, net, date="2026-10-10"):
    return {"document_id": doc, "document_number": f"B-{doc}", "document_date": date, "document_type": "BILL",
            "status": "PAID", "vendor_name": vendor, "net_purchases": net, "input_vat": net * 0.05,
            "currency": "AED", "organization_name": "Org 1"}


def test_a_large_first_bill_from_a_new_supplier_is_found(cube):
    cube.bills = [bill(1, "Fresh Vendor LLC", 10_000.0), bill(2, "Old Friend LLC", 50_000.0),
                  bill(3, "Tiny New Co", 100.0)]
    cube.history = {"Old Friend LLC"}
    call = next(c for c in ENGINE.calls(["new_supplier"], "balanced") if c["kind"] == "new_supplier")
    res = run_call(call)
    assert [f["bill"]["vendor_name"] for f in res["findings"]] == ["Fresh Vendor LLC"]
    f = res["findings"][0]
    assert f["rule"] == "new_supplier_large" and f["bill"]["total"] == 10_500.0
    assert f["key"] == payables_scan.supplier_key(1, "fresh vendor llc")


def test_catch_more_lowers_the_new_supplier_amount(cube):
    cube.bills = [bill(3, "Tiny New Co", 2_000.0)]
    calls = {s: next(c for c in ENGINE.calls(["new_supplier"], s) if c["kind"] == "new_supplier")
             for s in ("balanced", "more")}
    assert run_call(calls["balanced"])["findings_found"] == 0
    assert run_call(calls["more"])["findings_found"] == 1


# --- position, stake, calls ---------------------------------------------------------------------------------------

def test_the_position_adds_up_overdue_and_due_in_seven_days(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=12), 1000.0), open_bill(2, TODAY - dt.timedelta(days=40), 500.0),
                     open_bill(3, TODAY + dt.timedelta(days=2), 300.0), open_bill(4, TODAY + dt.timedelta(days=20), 900.0),
                     open_bill(5, None, 250.0)]
    pos = ENGINE.position([1], "t", 1e12)
    assert pos["kind"] == "payables" and pos["overdue"] == 1500.0 and pos["overdue_count"] == 2
    assert pos["due_next_7_days"] == 300.0 and pos["due_next_7_days_count"] == 1
    assert pos["no_due_date"] == 250.0 and pos["no_due_date_count"] == 1


def test_a_duplicate_could_be_paid_twice_and_other_rules_stake_nothing():
    detail = {"bills": [{"amount": 1000.0, "vat": 50.0}, {"amount": 1000.0, "vat": 50.0}]}
    assert ENGINE.stake("same_number", detail) == 1050.0
    assert ENGINE.stake("bill_overdue", detail) == 0.0


def test_calls_follow_the_chosen_watches():
    assert [c["kind"] for c in ENGINE.calls(["duplicate_bill"], "balanced")] == ["review"]
    assert ENGINE.calls(["bill_due_soon"], "more")[0]["due_soon_days"] == 7
    with pytest.raises(KeyError):
        ENGINE.calls(["vat_rate_wrong"], "balanced")


def test_the_review_call_passes_unlimited_and_the_last_90_days(cube):
    with patch.object(ap_engine.vat_review_tool, "run", return_value={}) as review:
        ENGINE.run(ENGINE.calls(["duplicate_bill"], "balanced")[0], org_ids=[1], period=None, subject="t",
                   deadline=1e12, exclude=frozenset(), unlimited=True)
    kw = review.call_args.kwargs
    assert kw["unlimited"] is True
    assert review.call_args.args[0]["period"] == {"start": "2026-08-02", "end": "2026-10-30"}
    assert "kind" not in review.call_args.args[0]


# --- the whole flow -----------------------------------------------------------------------------------------------

def ap_bot(watches=("bill_overdue", "bill_due_soon")):
    draft = BotDraft(agent="ap", name="Bill Guard", org_ids=[1], watches=list(watches),
                     schedules=[Schedule(kind="daily", time="08:00")])
    return {"id": "ap-1", "owner_user_id": 7, "name": "Bill Guard", "agent": "ap", "org_ids": [1],
            "draft": draft.model_dump(), "status": "active"}


def test_a_paid_bill_is_marked_fixed_on_the_next_run(cube):
    cube.payables = [open_bill(1, TODAY - dt.timedelta(days=12)), open_bill(2, TODAY + dt.timedelta(days=2))]
    saved = {}

    def upsert(bot_id, run_id, findings):
        for f in findings:
            saved[f["key"]] = {"key": f["key"], "state": f["state"], "rule": f["rule"], "amount": f["amount"]}

    fixed = []
    with patch.object(store, "get", return_value=ap_bot()), \
         patch.object(store, "record_run", return_value={"id": "r"}), \
         patch.object(store, "finish_run", side_effect=lambda **kw: kw), \
         patch.object(store, "get_findings", side_effect=lambda bot_id, state=None: [
             v for v in saved.values() if state is None or v["state"] == state]), \
         patch.object(store, "upsert_findings", side_effect=upsert), \
         patch.object(store, "update_finding_state", side_effect=lambda b, k, s: fixed.append(k)), \
         patch.object(store, "list_memory", return_value=[]), \
         patch.object(run_mod, "get_user_allowed_orgs", return_value=[1]):
        first = run_mod.run_bot("ap-1")
        assert first["summary"]["new_findings"] == 2 and first["summary"]["position"]["kind"] == "payables"
        assert first["summary"]["return_position"] is None
        assert first["run"]["credits"] == 90
        assert saved[payables_scan.bill_key(1, 1)]["amount"] == 1000.0  # the outstanding amount
        cube.payables = [open_bill(2, TODAY + dt.timedelta(days=2))]  # bill 1 was paid
        second = run_mod.run_bot("ap-1")
    assert fixed == [payables_scan.bill_key(1, 1)] and second["summary"]["fixed_findings"] == 1


def test_the_monitor_runs_only_document_watches_for_an_ap_bot(cube):
    bot = ap_bot(watches=("bill_overdue", "duplicate_bill"))
    with patch.object(store, "list_active_bots_for_org", return_value=[bot]), \
         patch.object(store, "get_findings", return_value=[]), \
         patch.object(monitor, "get_user_allowed_orgs", return_value=[1]), \
         patch.object(ap_engine.vat_review_tool, "run", return_value={"groups": [], "findings": []}) as review, \
         patch.object(payables_scan, "due_items") as due:
        monitor.handle_document_event("bill.saved", 1, [5])
        monitor.handle_document_event("invoice.saved", 1, [5])
    assert review.call_count == 1 and due.call_count == 0


def test_ap_templates_validate_and_ap_cannot_run_before_the_return():
    for t in agents.get("ap").templates:
        _, issues = validate.validate(t.draft, [1], [1])
        assert not [i for i in issues if not i.fixed], t.id
    draft = BotDraft(agent="ap", org_ids=[1], schedules=[Schedule(kind="before_deadline", days_before=[7])])
    _, issues = validate.validate(draft, [1], [1])
    assert any(i.field == "schedules[0].kind" and not i.fixed for i in issues)


def test_the_api_lists_the_ap_agent_and_its_templates():
    headers = {"Authorization": f"Bearer {create_access_token(user_id=18, email='t@example.com', allowed_org_ids=[1])}"}
    ap = next(a for a in client.get("/api/v1/bots/agents", headers=headers).json()["agents"] if a["key"] == "ap")
    assert [c["key"] for c in ap["columns"]] == ["number", "date", "due_date", "party", "total", "outstanding"]
    assert ap["schedule_kinds"] == ["daily", "weekly", "monthly"]
    res = client.get("/api/v1/bots/templates?agent=ap", headers=headers).json()
    assert {t["id"] for t in res["templates"]} == {"ap_weekly_check", "ap_pay_run_prep", "ap_daily_watch"}
    daily = next(t for t in res["templates"] if t["id"] == "ap_daily_watch")
    assert daily["monthly_credits"] == 30 * 90


def test_inbox_keeps_each_agents_stake_apart():
    from gemini_brain.bots import inbox
    dup = {"bills": [{"amount": 100.0, "vat": 5.0}, {"amount": 100.0, "vat": 5.0}], "currency": "AED"}
    out = inbox.build([{"bot_id": "a", "key": "k1", "rule": "same_number", "state": "new", "detail": dup},
                       {"bot_id": "v", "key": "k2", "rule": "same_number", "state": "new", "detail": dup}], [],
                      [{"id": "a", "name": "A", "agent": "ap"}, {"id": "v", "name": "V", "agent": "vat"}])
    assert out["at_stake_by_agent"] == {"ap": [{"currency": "AED", "amount": 105.0}],
                                        "vat": [{"currency": "AED", "amount": 5.0}]}
