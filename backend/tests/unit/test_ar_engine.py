"""The AR bot (docs/BOT_AR_PLAN.md, part C): invoices just overdue and due soon, customers with old debt, new
customers, the receivables position, confirming "fixed", and the flow through run_bot, the monitor, validate and the
inbox. Cube is faked with fixed rows."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gemini_brain.bots import agents, ap_engine, ar_engine, inbox, monitor, open_docs_scan, store, validate
from gemini_brain.bots import run as run_mod
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.vat_review import scan as scan_mod

TODAY = dt.date(2026, 10, 30)
ENGINE = agents.get("ar").engine


def invoice(doc, due, outstanding=1000.0, customer="Arjun"):
    return {"document_id": doc, "document_number": f"INV-{doc}", "customer_name": customer,
            "document_date": "2026-09-01", "due_date": due.isoformat() + "T00:00:00.000" if due else None,
            "outstanding": outstanding, "currency": "AED", "organization_name": "Org 1"}


def bucket(d: dt.date | None) -> str:
    if d is None:
        return "No due date"
    days = (TODAY - d).days
    if days <= 0:
        return "Not yet due"
    return "1-30 days" if days <= 30 else "31-60 days" if days <= 60 else "61-90 days" if days <= 90 else "Over 90 days"


class FakeCube:
    """Answers the receivables and sales queries the AR bot makes, from plain lists."""

    def __init__(self, open_invoices=(), sales=(), history=()):
        self.open = list(open_invoices)
        self.sales = list(sales)
        self.history = set(history)
        self.queries = []

    def _due(self, x):
        return dt.date.fromisoformat(x["due_date"][:10]) if x["due_date"] else None

    def _sum(self, rows, measures):
        return SimpleNamespace(rows=[{"receivables.organization_id": 1, "receivables.currency": "AED",
                                      **{f"receivables.{m}": v for m, v in measures(rows).items()}}])

    def load(self, query, *, organization_ids, subject, deadline):
        self.queries.append(query)
        measures = query.get("measures") or []
        dims = query.get("dimensions") or []
        filters = {f["member"]: f for f in query.get("filters") or []}
        if dims == ["receivables.document_id"]:
            ids = set(filters["receivables.document_id"]["values"])
            return SimpleNamespace(rows=[{"receivables.document_id": str(x["document_id"])} for x in self.open
                                         if str(x["document_id"]) in ids])
        if "receivables.overdue_amount" in measures:
            over = [x for x in self.open if self._due(x) and self._due(x) < TODAY]
            return self._sum(over, lambda r: {"overdue_amount": sum(x["outstanding"] for x in r), "overdue_count": len(r)})
        if "receivables.customer_name" in dims and "receivables.aging_bucket" in dims:
            wanted = set(filters["receivables.aging_bucket"]["values"])
            groups = {}
            for x in self.open:
                b = bucket(self._due(x))
                if b in wanted:
                    g = groups.setdefault((x["customer_name"], b), {"outstanding": 0.0, "open_count": 0})
                    g["outstanding"] += x["outstanding"]
                    g["open_count"] += 1
            return SimpleNamespace(rows=[{"receivables.customer_name": c, "receivables.aging_bucket": b,
                                          "receivables.currency": "AED", "receivables.organization_name": "Org 1",
                                          "receivables.outstanding": g["outstanding"],
                                          "receivables.open_count": g["open_count"]} for (c, b), g in groups.items()])
        if "receivables.open_count" in measures:
            if "receivables.aging_bucket" in filters:
                rows = [x for x in self.open if bucket(self._due(x)) == "Over 90 days"]
            elif filters["receivables.due_date"]["operator"] == "notSet":
                rows = [x for x in self.open if not x["due_date"]]
            else:
                a, b = filters["receivables.due_date"]["values"]
                rows = [x for x in self.open if x["due_date"] and a <= x["due_date"][:10] <= b]
            return self._sum(rows, lambda r: {"outstanding": sum(x["outstanding"] for x in r), "open_count": len(r)})
        if "receivables.outstanding" in measures:
            a, b = filters["receivables.due_date"]["values"]
            rows = [x for x in self.open if x["due_date"] and a <= x["due_date"][:10] <= b]
            rows.sort(key=lambda x: x["due_date"], reverse=True)
            for x in rows:
                x["days_overdue"] = max((TODAY - self._due(x)).days, 0)
            return SimpleNamespace(rows=[{f"receivables.{k}": v for k, v in x.items()} for x in rows])
        if "sales.invoice_count" in measures:
            names = filters["sales.customer_name"]["values"]
            return SimpleNamespace(rows=[{"sales.customer_name": n, "sales.invoice_count": 2}
                                         for n in names if n in self.history])
        a, b = query["timeDimensions"][0]["dateRange"]
        return SimpleNamespace(rows=[{f"sales.{k}": v for k, v in x.items()} for x in self.sales
                                     if a <= x["document_date"] <= b])


@pytest.fixture
def cube(monkeypatch):
    fake = FakeCube()
    monkeypatch.setattr(open_docs_scan.cube_client, "load", fake.load)
    monkeypatch.setattr(scan_mod.cube_client, "load", fake.load)
    monkeypatch.setattr(ap_engine.report_periods, "today_in", lambda tz: TODAY)
    return fake


def days(n):
    return TODAY + dt.timedelta(days=n)


def run_call(kind, sensitivity="balanced", watches=None):
    ids = watches or [w.id for w in agents.get("ar").watches]
    call = next(c for c in ENGINE.calls(ids, sensitivity) if c["kind"] == kind)
    return ENGINE.run(call, org_ids=[1], period=None, subject="t", deadline=1e12)


# --- the registry ---------------------------------------------------------------------------------------------------

def test_ar_agent_registers_with_unique_watches_and_valid_templates():
    agent = agents.get("ar")
    ids = [w.id for w in agent.watches]
    assert len(ids) == len(set(ids)) and agent.schedule_kinds == ("daily", "weekly", "monthly")
    assert all(w.side == "sales" for w in agent.watches)
    for t in agent.templates:
        _, issues = validate.validate(t.draft, [1], [1])
        assert not [i for i in issues if not getattr(i, "fixed", True)], t.id
    ENGINE.calls(ids, "balanced")  # every watch is known to the engine


def test_calls_follow_watches_and_sensitivity():
    calls = ENGINE.calls(["invoice_overdue", "customer_overdue"], "fewer")
    assert [c["kind"] for c in calls] == ["receivables", "customers"]
    assert calls[0]["overdue_days"] == 7 and calls[1]["min_amount"] == 50_000
    review = ENGINE.calls(["duplicate_invoice", "unusual_invoice"], "more")[0]
    assert review["side"] == "sales" and "round_amount" in review["rules"]
    with pytest.raises(KeyError):
        ENGINE.calls(["bill_overdue"], "balanced")


def test_ar_draft_rejects_return_schedules():
    draft = BotDraft(agent="ar", name="x", org_ids=[1], watches=["invoice_overdue"],
                     schedules=[Schedule(kind="before_deadline", days_before=[7])])
    _, issues = validate.validate(draft, [1], [1])
    assert any(not getattr(i, "fixed", True) for i in issues)


# --- invoices just overdue and due soon -----------------------------------------------------------------------------

def test_recent_overdue_and_due_soon(cube):
    cube.open = [invoice(1, days(-3)), invoice(2, days(-45)), invoice(3, days(2)), invoice(4, days(6)),
                 invoice(5, days(-20))]
    res = run_call("receivables")
    by = {f["bill"]["document_id"]: f for f in res["findings"]}
    assert set(by) == {1, 3, 5}  # 45 days overdue is old debt (customer finding); 6 days ahead is past the window
    assert by[1]["rule"] == "invoice_overdue" and by[1]["severity"] == "medium"
    assert by[5]["severity"] == "high"
    assert by[3]["rule"] == "invoice_due_soon" and "due in 2 days" in by[3]["why"]
    assert by[1]["why"].startswith("Invoice INV-1 to Arjun for AED 1,000.00")
    assert res["side"] == "sales"
    more = run_call("receivables", "more")
    assert 4 in {f["bill"]["document_id"] for f in more["findings"]}


def test_fewer_hides_a_few_days_overdue(cube):
    cube.open = [invoice(1, days(-3)), invoice(2, days(-10))]
    res = run_call("receivables", "fewer")
    assert [f["bill"]["document_id"] for f in res["findings"]] == [2]


def test_invoice_key_does_not_change_with_rule(cube):
    cube.open = [invoice(1, days(1))]
    before = run_call("receivables")["findings"][0]
    cube.open = [invoice(1, days(-2))]
    after = run_call("receivables")["findings"][0]
    assert before["rule"] != after["rule"] and before["key"] == after["key"] == open_docs_scan.doc_key("receivables", 1, 1)


# --- customers with old debt ---------------------------------------------------------------------------------------

def test_customer_findings_group_buckets(cube):
    cube.open = [invoice(1, days(-5), 2000), invoice(2, days(-40), 3000), invoice(3, days(-120), 8000),
                 invoice(4, days(-100), 2500, customer="Small Co"), invoice(5, days(3), 9999)]
    res = run_call("customers")
    assert [f["bill"]["vendor_name"] for f in res["findings"]] == ["Arjun"]  # Small Co is below 10,000
    f = res["findings"][0]
    assert f["rule"] == "customer_overdue" and f["severity"] == "high" and f["bill"]["total"] == 13000
    assert [b["bucket"] for b in f["buckets"]] == ["1-30 days", "31-60 days", "Over 90 days"]
    assert "AED 8,000.00 of it is more than 90 days old" in f["why"]
    detail = ENGINE.detail(f, "sales")
    assert detail["bills"] == [] and detail["party"] == "Arjun" and len(detail["buckets"]) == 3
    assert run_call("customers", "more")["findings_found"] == 2


def test_customer_severity_follows_oldest_bucket(cube):
    cube.open = [invoice(1, days(-40), 20000)]
    assert run_call("customers")["findings"][0]["severity"] == "medium"
    cube.open = [invoice(1, days(-10), 20000)]
    assert run_call("customers")["findings"][0]["severity"] == "low"


# --- new customers -------------------------------------------------------------------------------------------------

def sale(doc, customer, net, date="2026-10-01"):
    return {"document_id": doc, "document_number": f"INV-{doc}", "customer_name": customer, "document_date": date,
            "net_sales": net, "output_vat": net * 0.05, "currency": "AED", "organization_name": "Org 1"}


def test_new_customer(cube):
    cube.sales = [sale(1, "Fresh Buyer LLC", 30000), sale(2, "Old Buyer", 90000), sale(3, "Tiny Buyer", 100)]
    cube.history = {"Old Buyer"}
    res = run_call("new_customer")
    assert [f["bill"]["vendor_name"] for f in res["findings"]] == ["Fresh Buyer LLC"]
    f = res["findings"][0]
    assert f["rule"] == "new_customer_large" and f["key"] == open_docs_scan.party_key("receivables", 1, "fresh buyer llc")
    assert "is a new customer: its first invoice" in f["why"]


# --- position, stake, exclude -------------------------------------------------------------------------------------

def test_position(cube):
    cube.open = [invoice(1, days(-3), 100), invoice(2, days(-200), 400), invoice(3, days(4), 50), invoice(4, None, 7)]
    pos = ENGINE.position([1], "t", 1e12)
    assert pos["kind"] == "receivables" and pos["overdue"] == 500 and pos["overdue_count"] == 2
    assert pos["due_next_7_days"] == 50 and pos["over_90_days"] == 400 and pos["no_due_date_count"] == 1
    assert ENGINE.summarize_position(pos, []) is pos


def test_exclude_and_stake(cube):
    cube.open = [invoice(1, days(-3)), invoice(2, days(-4))]
    call = ENGINE.calls(["invoice_overdue"])[0]
    key = open_docs_scan.doc_key("receivables", 1, 1)
    res = ENGINE.run(call, org_ids=[1], period=None, subject="t", deadline=1e12, exclude={key})
    assert [f["bill"]["document_id"] for f in res["findings"]] == [2] and res["dismissed"] == 1
    assert ENGINE.stake("invoice_overdue", {"bills": [{"amount": 5}]}) == 0
    assert ENGINE.stake("same_number", {"bills": [{"amount": 100, "vat": 5}, {"amount": 100, "vat": 5}]}) == 105


# --- confirming "fixed" and the whole run ---------------------------------------------------------------------------

def ar_bot(watches=("invoice_overdue", "customer_overdue")):
    draft = BotDraft(agent="ar", name="Collections Watch", org_ids=[1], watches=list(watches),
                     schedules=[Schedule(kind="weekly", day="mon", time="08:00")])
    return {"id": "b1", "status": "active", "owner_user_id": 7, "org_ids": [1], "name": "Collections Watch",
            "agent": "ar", "draft": draft.model_dump()}


def run_twice(cube, first, second):
    saved, fixed, results = {}, [], []

    def upsert(bot_id, run_id, items):
        for i in items:
            saved[i["key"]] = {**i, "state": i["state"]}

    with patch.object(store, "get", return_value=ar_bot()), \
         patch.object(store, "record_run", return_value={"id": "r"}), \
         patch.object(store, "finish_run", side_effect=lambda **kw: kw), \
         patch.object(store, "get_findings", side_effect=lambda bot_id, state=None: [
             v for v in saved.values() if state is None or v["state"] == state]), \
         patch.object(store, "upsert_findings", side_effect=upsert), \
         patch.object(store, "update_finding_state",
                      side_effect=lambda b, k, s: (fixed.append(k), saved[k].update(state=s))), \
         patch.object(store, "list_memory", return_value=[]), \
         patch.object(run_mod, "get_user_allowed_orgs", return_value=[1]):
        for rows in (first, second):
            cube.open = rows
            results.append(run_mod.run_bot("b1"))
    return saved, fixed, results


def test_aged_out_invoice_stays_open_and_paid_one_is_fixed(cube):
    first = [invoice(1, days(-29), 20000), invoice(2, days(-5), 500)]
    # Invoice 1 is now 31 days overdue (still open); invoice 2 was paid.
    second = [invoice(1, days(-31), 20000)]
    saved, fixed, results = run_twice(cube, first, second)
    assert fixed == [open_docs_scan.doc_key("receivables", 1, 2)]
    assert saved[open_docs_scan.doc_key("receivables", 1, 1)]["state"] in ("new", "seen")
    summary = results[1]["summary"]
    assert summary["position"]["kind"] == "receivables" and summary["return_position"] is None
    assert summary["headline"].startswith("Checked ") and "bills" not in summary["headline"]


def test_customer_below_threshold_is_fixed(cube):
    first = [invoice(1, days(-100), 20000)]
    second = [invoice(1, days(-100), 2000)]
    saved, fixed, _ = run_twice(cube, first, second)
    assert fixed == [open_docs_scan.customer_key(1, "Arjun")]


def test_engine_without_confirm_fixed_is_unchanged():
    assert not hasattr(agents.get("vat").engine, "confirm_fixed")
    assert not hasattr(agents.get("ap").engine, "confirm_fixed")


def test_confirm_fixed_keeps_everything_open_when_cube_fails(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cube down")
    monkeypatch.setattr(open_docs_scan.cube_client, "load", boom)
    findings = [{"key": "a", "rule": "invoice_overdue", "org_id": 1, "detail": {"bills": [{"id": 9}]}},
                {"key": "b", "rule": "customer_overdue", "org_id": 1, "detail": {}}]
    assert ENGINE.confirm_fixed(findings, org_ids=[1], subject="t", deadline=1e12) == {"b"}


# --- monitor and inbox --------------------------------------------------------------------------------------------

def test_monitor_runs_only_document_watches_on_invoice_saved():
    bot = ar_bot(("invoice_overdue", "customer_overdue", "duplicate_invoice"))
    with patch.object(store, "list_active_bots_for_org", return_value=[bot]), \
         patch.object(store, "get_findings", return_value=[]), \
         patch.object(monitor, "get_user_allowed_orgs", return_value=[1]), \
         patch.object(ar_engine.vat_review_tool, "run", return_value={"groups": [], "findings": []}) as review, \
         patch.object(open_docs_scan, "due_items") as due, \
         patch.object(open_docs_scan, "customer_items") as customers:
        monitor.handle_document_event("invoice.saved", 1, [5])
        assert review.call_count == 1 and review.call_args[0][0]["side"] == "sales"
        due.assert_not_called()
        customers.assert_not_called()
        review.reset_mock()
        monitor.handle_document_event("bill.saved", 1, [5])
        review.assert_not_called()


def test_inbox_ar_stake_comes_from_the_position_once():
    bots = [{"id": "b1", "name": "A", "agent": "ar", "org_ids": [1]},
            {"id": "b2", "name": "B", "agent": "ar", "org_ids": [1]}]
    pos = {"kind": "receivables", "currency": "AED", "overdue": 5000.0}
    runs = [{"bot_id": "b1", "started_at": "2026-10-30", "summary": {"position": pos}},
            {"bot_id": "b2", "started_at": "2026-10-30", "summary": {"position": pos}}]
    findings = [{"bot_id": "b1", "key": "k", "rule": "customer_overdue", "state": "new", "amount": 5000.0,
                 "detail": {"side": "sales", "bills": [], "currency": "AED"}}]
    out = inbox.build(findings, runs, bots)
    assert out["at_stake_by_agent"]["ar"] == [{"currency": "AED", "amount": 5000.0}]
    assert out["findings"][0]["watch_id"] == "customer_overdue"
