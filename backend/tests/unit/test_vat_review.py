"""VAT review: duplicate bills (vat_review.duplicates, scan, tool)."""
import datetime as dt
from types import SimpleNamespace

import pytest

from gemini_brain.agent import links, tools
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.vat_review import duplicates as dup
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review import tool


def bill(number, vendor="Acme Trading LLC", date="2026-03-01", net=1000.0, vat=50.0, status="ACCEPTED", id_=None):
    return {"document_number": number, "document_id": id_ or number, "document_date": date, "document_type": "BILL",
            "status": status, "vendor_name": vendor, "net_purchases": net, "input_vat": vat, "organization_name": "Org A",
            "currency": "AED"}


def rules(groups):
    return [g["rule"] for g in groups]


# --- keys ---------------------------------------------------------------------------------------------------------

def test_numbers_compare_without_punctuation_case_or_leading_zeros():
    assert dup.number_key("INV-0012") == dup.number_key("inv12") == dup.number_key("INV 012")
    assert dup.number_key("INV-10") != dup.number_key("INV-1")
    assert dup.number_key(None) == ""


def test_supplier_names_compare_without_legal_suffix_or_punctuation():
    assert dup.vendor_key("Acme Trading L.L.C.") == dup.vendor_key("ACME")
    assert dup.vendor_key("Zayan FZCO") == "zayan"


# --- rule 1: same supplier, same number ---------------------------------------------------------------------------

def test_same_supplier_and_number_is_a_sure_duplicate():
    groups = dup.find_duplicates([bill("B-1"), bill("b1", date="2026-03-20"), bill("B-2", net=777.0)])
    assert rules(groups) == ["same_number"]
    assert groups[0]["confidence"] == "high" and groups[0]["count"] == 2
    assert groups[0]["extra_amount"] == 1000.0


def test_same_number_from_different_suppliers_is_not_a_duplicate():
    assert dup.find_duplicates([bill("B-1", vendor="Acme"), bill("B-1", vendor="Zayan")]) == []


# --- rule 2: same supplier, same amount, close dates --------------------------------------------------------------

def test_same_supplier_and_amount_within_three_days_is_likely():
    groups = dup.find_duplicates([bill("A", date="2026-03-01"), bill("B", date="2026-03-04")])
    assert rules(groups) == ["same_total_near_date"] and groups[0]["confidence"] == "medium"


def test_four_days_apart_or_a_different_amount_is_not_flagged():
    assert dup.find_duplicates([bill("A", date="2026-03-01"), bill("B", date="2026-03-05")]) == []
    assert dup.find_duplicates([bill("A", net=1000.0), bill("B", net=1000.5)]) == []


def test_a_monthly_recurring_bill_is_not_flagged():
    assert dup.find_duplicates([bill(f"R{m}", date=f"2026-{m:02d}-01") for m in range(1, 7)]) == []


def test_a_group_stays_within_three_days_of_its_first_bill():
    groups = dup.find_duplicates([bill("A", date="2026-03-01"), bill("B", date="2026-03-03"), bill("C", date="2026-03-05")])
    assert len(groups) == 1 and [x["document_number"] for x in groups[0]["bills"]] == ["A", "B"]


def test_a_daily_run_of_the_same_amount_does_not_become_one_huge_group():
    groups = dup.find_duplicates([bill(f"D{i}", date=f"2026-03-{i:02d}") for i in range(1, 21)])
    assert groups and max(g["count"] for g in groups) <= 4


def test_a_same_number_pair_is_not_reported_twice():
    groups = dup.find_duplicates([bill("B-1", date="2026-03-01"), bill("B-1", date="2026-03-02")])
    assert rules(groups) == ["same_number"]


def test_bills_without_an_amount_are_only_checked_by_number():
    assert dup.find_duplicates([bill("A", net=0.0), bill("B", net=0.0)]) == []
    assert dup.find_duplicates([bill("A", net=None), bill("B", net=None)]) == []
    assert rules(dup.find_duplicates([bill("A", net=0.0), bill("A", net=0.0)])) == ["same_number"]


# --- rule 3: alike supplier names ----------------------------------------------------------------------------------

def test_alike_supplier_names_with_the_same_amount_and_date_are_possible():
    groups = dup.find_duplicates([bill("A", vendor="Al Noor Stationery"), bill("B", vendor="Al Noor Stationary")])
    assert rules(groups) == ["similar_vendor_same_day"] and groups[0]["confidence"] == "low"


def test_different_suppliers_with_the_same_amount_and_date_are_not_flagged():
    assert dup.find_duplicates([bill("A", vendor="Acme"), bill("B", vendor="Zayan")]) == []


# --- leaving out what is not a bill -------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["VOID", "Cancelled", "REJECTED", "DRAFT", "deleted"])
def test_voided_and_draft_bills_are_left_out(status):
    kept, skipped = dup.usable([bill("A"), bill("A", status=status)])
    assert len(kept) == 1 and skipped == 1


# --- order of the result ------------------------------------------------------------------------------------------

def test_sure_groups_come_first_then_the_larger_amount():
    groups = dup.find_duplicates([
        bill("S1", vendor="Small", net=10.0), bill("S1", vendor="Small", net=10.0, date="2026-03-09"),
        bill("L1", vendor="Large", net=9000.0, date="2026-05-01"), bill("L2", vendor="Large", net=9000.0, date="2026-05-02"),
        bill("M1", vendor="Mid", net=500.0), bill("M1", vendor="Mid", net=500.0, date="2026-04-01"),
    ])
    assert rules(groups) == ["same_number", "same_number", "same_total_near_date"]
    assert [g["vendor"] for g in groups[:2]] == ["Mid", "Small"] and [g["group"] for g in groups] == [1, 2, 3]


# --- scan ---------------------------------------------------------------------------------------------------------

def fake_cube(monkeypatch, all_bills):
    """A Cube that returns at most 500 rows for a date range, like the real one."""
    calls = []

    def load(query, *, organization_ids, subject, deadline):
        a, b = query["timeDimensions"][0]["dateRange"]
        rows = [{f"purchases.{k}": v for k, v in x.items()} for x in all_bills if a <= x["document_date"] <= b][:500]
        calls.append((a, b))
        return SimpleNamespace(rows=rows)

    monkeypatch.setattr(scan_mod.cube_client, "load", load)
    return calls


def many(n, start=dt.date(2026, 1, 1)):
    return [bill(f"N{i}", vendor=f"V{i}", date=(start + dt.timedelta(days=i % 300)).isoformat(), net=float(100 + i))
            for i in range(n)]


def test_a_scan_cuts_the_period_until_nothing_is_lost(monkeypatch):
    calls = fake_cube(monkeypatch, many(1700))
    result = scan_mod.scan_bills(1, dt.date(2026, 1, 1), dt.date(2026, 12, 31), subject="t", deadline=1e12)
    assert result.complete and len(result.bills) == 1700 and len(calls) > 1


def test_cube_decimals_never_reach_the_result(monkeypatch):
    """Cube sends ids and amounts as Decimal; the result goes to the model as JSON, which cannot hold them."""
    import json
    from decimal import Decimal

    def load(query, *, organization_ids, subject, deadline):
        return SimpleNamespace(rows=[{"purchases.document_number": "B-1", "purchases.document_id": Decimal("6853"),
                                      "purchases.document_date": "2026-03-01T00:00:00.000", "purchases.status": "PAID",
                                      "purchases.vendor_name": "Acme", "purchases.net_purchases": Decimal("1000.50"),
                                      "purchases.input_vat": Decimal("0")}] * 2)

    monkeypatch.setattr(scan_mod.cube_client, "load", load)
    out = tool.run({"mode": "duplicates", "period": {"start": "2026-03-01", "end": "2026-03-01"}},
                   organization_ids=[7], subject="t", deadline=1e12)
    assert out["groups_found"] == 1 and out["groups"][0]["bills"][0]["document_date"] == "2026-03-01"
    json.dumps(out)


def test_one_day_with_too_many_bills_makes_the_scan_incomplete(monkeypatch):
    fake_cube(monkeypatch, [bill(f"X{i}", date="2026-03-01", net=float(i + 1)) for i in range(600)])
    result = scan_mod.scan_bills(1, dt.date(2026, 3, 1), dt.date(2026, 3, 1), subject="t", deadline=1e12)
    assert not result.complete and "more bills" in result.why_incomplete


def test_a_scan_that_runs_out_of_time_says_so(monkeypatch):
    fake_cube(monkeypatch, many(10))
    result = scan_mod.scan_bills(1, dt.date(2026, 1, 1), dt.date(2026, 12, 31), subject="t", deadline=0.0)
    assert not result.complete and result.why_incomplete == "time ran out" and result.bills == []


# --- the tool -----------------------------------------------------------------------------------------------------

def run_tool(monkeypatch, bills, **params):
    fake_cube(monkeypatch, bills)
    return tool.run({"mode": "duplicates", "period": {"start": "2026-01-01", "end": "2026-12-31"}, **params},
                    organization_ids=[7], subject="t", deadline=1e12)


def test_the_tool_reports_groups_counts_and_rows(monkeypatch):
    out = run_tool(monkeypatch, [bill("B-1", id_="11"), bill("B-1", id_="12", date="2026-03-20"),
                                 bill("OK", vendor="Other", net=5.0)])
    assert out["bills_checked"] == 3 and out["groups_found"] == 1 and out["groups_by_rule"]["same_number"] == 1
    assert out["period"] == {"start": "2026-01-01", "end": "2026-12-31"} and out["organizations"][0]["complete"]
    assert out["row_count"] == 2 and out["rows"][0]["document_number"] == "B-1" and out["rows"][0]["kind"] == "duplicate"
    assert out["groups"][0]["organization_id"] == 7


def test_no_group_says_it_is_not_proof(monkeypatch):
    out = run_tool(monkeypatch, [bill("A"), bill("B", vendor="Other", net=5.0)])
    assert out["groups_found"] == 0 and any("does not prove" in n for n in out["notes"])


def test_the_tool_limits_the_groups_shown_and_says_so(monkeypatch):
    bills = []
    for i in range(5):
        bills += [bill(f"D{i}", vendor=f"V{i}", id_=f"a{i}"), bill(f"D{i}", vendor=f"V{i}", id_=f"b{i}", date="2026-04-01")]
    out = run_tool(monkeypatch, bills, limit=2)
    assert out["groups_found"] == 5 and len(out["groups"]) == 2 and any("first 2 of 5" in n for n in out["notes"])


def test_an_incomplete_scan_is_stated(monkeypatch):
    fake_cube(monkeypatch, [bill(f"X{i}", date="2026-03-01", net=float(i + 1)) for i in range(600)])
    out = tool.run({"mode": "duplicates", "period": {"start": "2026-03-01", "end": "2026-03-01"}},
                   organization_ids=[7], subject="t", deadline=1e12)
    assert out["organizations"][0]["complete"] is False and any("stopped early" in n for n in out["notes"])


def test_bad_input_is_a_tool_error():
    with pytest.raises(ToolInputError):
        tool.run({"mode": "everything"}, organization_ids=[7], subject="t", deadline=1e12)
    with pytest.raises(ToolInputError):
        tool.run({"mode": "duplicates", "limit": "many"}, organization_ids=[7], subject="t", deadline=1e12)
    with pytest.raises(ToolInputError):
        tool.run({"mode": "duplicates", "period": {"preset": "someday"}}, organization_ids=[7], subject="t", deadline=1e12)


def test_the_agent_runs_it_only_on_the_chats_organizations(monkeypatch):
    fake_cube(monkeypatch, [bill("B-1"), bill("B-1", date="2026-03-20")])
    ctx = tools.ToolContext(organization_ids=[7], subject="t", deadline=1e12)
    result, ok = tools.execute(
        "review_vat", {"mode": "duplicates", "period": {"start": "2026-01-01", "end": "2026-12-31"}}, ctx)
    assert ok and result["groups_found"] == 1
    result, ok = tools.execute("review_vat", {"mode": "duplicates", "organization_ids": [99]}, ctx)
    assert not ok and "not selected" in result["error"]


def test_bill_numbers_in_groups_become_links(monkeypatch):
    monkeypatch.setattr(links.settings, "accutax_app_url", "https://app.example")
    item = {"tool": "review_vat", "input": {}, "result": {"groups": [{"bills": [
        {"document_number": "B-100", "document_id": "42"}]}]}}
    found = links.document_links([item])
    assert list(found) == ["B-100"] and found["B-100"].startswith("https://app.example/")
