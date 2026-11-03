"""The bots Inbox page (bots/inbox.py): VAT at stake per check, links into Accutax, counts and the weekly fixed list."""
from __future__ import annotations

import datetime as dt

import pytest

from gemini_brain.bots import inbox

NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=dt.timezone.utc)


def bill(id_, amount, vat, number="B-1"):
    return {"id": id_, "number": number, "date": "2026-10-14", "party": "Gulf Agri Supplies", "amount": amount, "vat": vat}


@pytest.fixture(autouse=True)
def app_url(monkeypatch):
    monkeypatch.setattr(inbox.links.settings, "accutax_app_url", "https://app.example/")


def test_a_duplicate_puts_the_copies_vat_at_stake():
    d = {"bills": [bill(1, 12000, 600), bill(2, 12000, 600), bill(3, 12000, 600)]}
    assert inbox.vat_at_stake("same_total_near_date", d) == 1200.0


def test_wrong_rate_missing_vat_and_vat_without_amount():
    assert inbox.vat_at_stake("vat_rate_not_standard", {"bills": [bill(1, 3450, 517.5)]}) == 345.0  # 15% vs 5%
    assert inbox.vat_at_stake("vat_zero", {"bills": [bill(1, 4200, 0)]}) == 210.0
    assert inbox.vat_at_stake("vat_without_amount", {"bills": [bill(1, 0, 1120)]}) == 1120.0
    assert inbox.vat_at_stake("missing_supplier", {"bills": [bill(1, 640, 32)]}) == 0.0
    assert inbox.vat_at_stake("same_number", {}) == 0.0


def test_bills_link_to_their_page_in_accutax():
    assert inbox.bill_url("purchases", 11) == "https://app.example/expenses/edit-expense/11"
    assert inbox.bill_url("sales", "12.0") == "https://app.example/income/details/12"
    assert inbox.bill_url("sales", None) is None


def test_no_link_without_an_app_url(monkeypatch):
    monkeypatch.setattr(inbox.links.settings, "accutax_app_url", "")
    assert inbox.bill_url("purchases", 11) is None


def finding(key, state, rule="same_number", bot="b1", updated="2026-10-29T08:00:00+00:00", bills=None, side="purchases"):
    return {"bot_id": bot, "bot_name": f"Bot {bot}", "key": key, "org_id": 101, "rule": rule, "state": state,
            "amount": 600.0, "updated_at": updated, "found_at": updated,
            "detail": {"side": side, "currency": "AED", "bills": bills if bills is not None else [bill(1, 100, 5), bill(2, 100, 5)]}}


def test_the_inbox_counts_stake_and_fixed_this_week():
    findings = [
        finding("a", "new"), finding("b", "seen", bot="b2"), finding("c", "dismissed"),
        finding("d", "fixed", updated="2026-10-28T08:00:00+00:00"),
        finding("e", "fixed", updated="2026-10-01T08:00:00+00:00"),  # older than a week: not listed
    ]
    bots = [{"id": "b1", "name": "VAT Watch"}, {"id": "b2", "name": "Return Reminder"}, {"id": "b3", "name": "Idle"}]
    out = inbox.build(findings, runs=[], bots=bots, approvals=[{"id": "x"}], now=NOW)
    assert out["counts"] == {"all": 2, "new": 1, "seen": 1, "fixed": 2, "dismissed": 1}
    assert out["unread_count"] == 2  # one new finding and one approval
    assert out["bots"] == [{"id": "b1", "name": "VAT Watch", "open": 1}, {"id": "b2", "name": "Return Reminder", "open": 1},
                           {"id": "b3", "name": "Idle", "open": 0}]
    assert out["at_stake"] == [{"currency": "AED", "amount": 10.0}]  # the copies' VAT, open findings only
    assert [f["key"] for f in out["fixed_this_week"]] == ["d"]


def test_each_finding_says_its_check_and_links_its_bills():
    out = inbox.build([finding("a", "new", rule="vat_zero", side="sales", bills=[bill(7, 4200, 0, "SI-1088")])],
                      runs=[], bots=[], now=NOW)
    f = out["findings"][0]
    assert f["watch_id"] == "sales_vat_wrong" and f["side"] == "sales"
    assert f["rule_text"] == "no VAT on a bill with an amount"
    assert f["detail"]["bills"][0]["url"] == "https://app.example/income/details/7"
    assert f["at_stake"] == 210.0
