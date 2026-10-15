"""The VAT review evaluation harness (scripts/eval): the planted book, the judge, and the rules on the planted book."""
import importlib
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(EVAL))
vc = importlib.import_module("vat_review_cases")
ve = importlib.import_module("vat_review_eval")

from gemini_brain.vat_review import tool  # noqa: E402


# --- the planted book ---------------------------------------------------------------------------------------------

def test_every_planted_problem_is_found_and_no_clean_bill_is_flagged():
    report = ve.run_rules()
    assert [k for k, v in report["rules"].items() if v["missed"]] == []
    assert report["clean_flagged"] == [] and report["unplanned_rules"] == [] and report["patterns"] == []


def test_the_planted_book_is_stable_and_small_enough_to_judge_one_off_suppliers_inactive():
    book = vc.planted_book()
    assert book == vc.planted_book() and len(book) < 50
    assert len({b["document_id"] for b in book}) == len(book)


def test_the_tool_finds_the_planted_problems_through_the_scan(monkeypatch):
    """The whole tool, with the scan of the organization replaced by the planted book, as the agent evaluation does."""
    from gemini_brain.semantic import periods
    monkeypatch.setattr(periods, "today_in", lambda tz: __import__("datetime").date(2026, 10, 15))
    with vc.planted_cube(7):
        out = tool.run({"mode": "all", "limit": 50}, organization_ids=[7], subject="t", deadline=1e12)
    assert out["bills_checked"] == len(vc.planted_book()) - 1  # the future bill is looked at separately
    by_rule = {**out["groups_by_rule"], **out["findings_by_rule"]}
    assert by_rule == {"same_number": 1, "same_total_near_date": 1, "similar_vendor_same_day": 1, "vat_rate_not_standard": 2,
                       "vat_wrong_sign": 1, "vat_without_amount": 1, "vat_zero": 2, "supplier_trn_invalid": 2, "missing_supplier": 1,
                       "missing_number": 1, "future_date": 1, "large_for_supplier": 1}


def test_a_period_in_the_past_does_not_look_for_future_bills(monkeypatch):
    from gemini_brain.semantic import periods
    monkeypatch.setattr(periods, "today_in", lambda tz: __import__("datetime").date(2026, 10, 15))
    with vc.planted_cube(7):
        out = tool.run({"mode": "anomalies", "period": {"start": "2026-01-01", "end": "2026-06-30"}},
                       organization_ids=[7], subject="t", deadline=1e12)
    assert "future_date" in out["findings_by_rule"]  # the scan after the period end still sees the December bill


def test_another_organization_is_not_replaced(monkeypatch):
    from gemini_brain.semantic import cube_client
    seen = []
    monkeypatch.setattr(cube_client, "load", lambda query, **kw: seen.append(kw["organization_ids"]) or (_ for _ in ()).throw(RuntimeError("real")))
    with vc.planted_cube(7):
        with pytest.raises(RuntimeError):
            cube_client.load({"dimensions": ["purchases.document_id"], "limit": 500, "timeDimensions": [
                {"dimension": "purchases.document_date", "dateRange": ["2026-01-01", "2026-12-31"]}]},
                organization_ids=[8], subject="t", deadline=1e12)
    assert seen == [[8]]


# --- the judge ----------------------------------------------------------------------------------------------------

def card(*numbers):
    return {"type": "vat_review", "items": [{"bills": [{"number": n, "supplier": "S"} for n in numbers]}]}


def case(**kw):
    return vc.case("t", "q", **kw)


def test_the_judge_checks_tools_and_the_card():
    c = case(tools_any=["review_vat"], card_has=["A"], tools_none=["list_documents"])
    assert vc.judge(c, "ok", ["review_vat"], [card("A")])["ok"]
    assert not vc.judge(c, "ok", [], [card("A")])["ok"]
    assert not vc.judge(c, "ok", ["review_vat"], [card("B")])["ok"]
    assert not vc.judge(c, "ok", ["review_vat", "list_documents"], [card("A")])["ok"]


def test_a_card_item_without_a_number_counts_by_its_supplier():
    block = {"type": "vat_review", "items": [{"bills": [{"number": "", "supplier": "Planted No Number"}]}]}
    assert vc.card_numbers([block]) == ["Planted No Number"]


def test_the_judge_checks_words_and_blocks():
    c = case(any_of=["5%"], must_not=["trn is valid"], blocks=["artifact"])
    assert vc.judge(c, "UAE VAT is 5%", [], [{"type": "artifact"}])["ok"]
    assert not vc.judge(c, "UAE VAT is 7%", [], [{"type": "artifact"}])["ok"]
    assert not vc.judge(c, "5% and the TRN is valid", [], [{"type": "artifact"}])["ok"]
    assert not vc.judge(c, "5%", [], [])["ok"]


def test_a_review_answer_needs_a_hedge_no_rule_names_and_no_table():
    c = case(review=True)
    assert vc.judge(c, "Found 3. A person should confirm each.", [], [])["ok"]
    assert not vc.judge(c, "Found 3 duplicates.", [], [])["ok"]
    assert not vc.judge(c, "Please confirm. Rule: vat_rate_not_standard", [], [])["ok"]
    assert not vc.judge(c, "Please confirm.\n\n| Bill | VAT |\n|---|---|\n| A | 1 |", [], [])["ok"]


def test_cases_are_well_formed():
    ids = [c["id"] for c in vc.CASES]
    assert len(ids) == len(set(ids)) >= 20
    known = {n for names in vc.EXPECTED.values() for n in names}
    for c in vc.CASES:
        assert c["question"].strip() and c["org"]
        for n in c["card_has"]:
            assert n in known, f"{c['id']}: {n} is not a planted bill"
