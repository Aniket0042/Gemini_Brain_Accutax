"""The figure check ignores dates and number lengths; the duplicate review adds up the amount counted twice itself.

Both come from the 18 answers the pilot report marked "not grounded" on 2026-10-15/16: 16 were false alarms on day
numbers in dates, a "15-digit TRN" and a receipt number; 2 summed ten duplicate groups wrongly (229,262 for 230,262).
"""
from types import SimpleNamespace

import pytest

from gemini_brain.policy.verifier import _figure_text, verify_answer, verify_attributed
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review import tool


@pytest.mark.parametrize("answer", [
    "For the quarter 1 Jul – 30 Sep 2026 the return is due 28 October 2026.",
    "Year to date (1 Jan–15 Oct 2026), net purchases were AED 2,554,678.",
    "I checked all bills posted in 2025 (Jan 1 – Dec 31).",
    "Due **28 Oct 2026**, then 28 Jan 2027.",
    "Bills dated 2026-10-17 to 2026-11-24 and 28/07/2026.",
    "Payment is due by the 28th day after the period.",
    "A UAE TRN has 15 digits; this one is not a 15-digit TRN.",
    "Strings of bills dated 13–16 Jul.",
])
def test_dates_and_number_lengths_are_not_checked_as_figures(answer):
    report = verify_answer(answer, {"net_purchases": 2554678.0})
    assert report.unmatched == [], report.unmatched


def test_an_amount_beside_a_date_is_still_checked():
    report = verify_answer("On 30 Sep 2026 the VAT was AED 7,777.", {"output_vat": 1250.0})
    assert report.unmatched == ["7,777"]


def test_a_number_inside_a_short_reference_counts_as_known():
    report = verify_answer("Receipt CS 67832 from Home Master.", {"document_number": "CS 67832"})
    assert report.grounded


def test_a_long_text_does_not_lend_its_numbers():
    text = "This sentence is a long description that happens to mention 67832 somewhere in its many words."
    assert not verify_answer("The figure is 67832.", {"description": text}).grounded


def test_the_attributed_check_ignores_dates_too():
    report = verify_attributed("Org A: VAT AED 500 for 1 Jul – 30 Sep 2026.", {"Org A": {"vat": 500.0}})
    assert report.grounded


def test_figure_text_keeps_amounts():
    assert "4,389.00" in _figure_text("AED 4,389.00 on 30 Sep 2026")


# --- the amount counted twice, added up by code -------------------------------------------------------------------

def bill(number, vendor, date, net, cur="AED"):
    return {"document_number": number, "document_id": number, "document_date": date, "document_type": "BILL",
            "status": "ACCEPTED", "vendor_name": vendor, "net_purchases": net, "input_vat": round(net * 0.05, 2),
            "organization_name": "Org", "currency": cur}


@pytest.fixture
def cube(monkeypatch):
    monkeypatch.setattr(tool, "_has_trn", lambda *a, **k: False)

    def make(bills):
        def load(query, *, organization_ids, subject, deadline):
            a, b = query["timeDimensions"][0]["dateRange"]
            return SimpleNamespace(rows=[{f"purchases.{k}": v for k, v in x.items()} for x in bills
                                         if a <= x["document_date"] <= b][:500])
        monkeypatch.setattr(scan_mod.cube_client, "load", load)
    return make


def test_the_review_gives_the_amount_counted_twice_for_all_and_for_the_shown_groups(cube):
    bills = []
    for i, amount in enumerate([29688.0, 24780.0, 24485.0]):
        bills += [bill(f"A{i}", f"Supplier {i}", "2026-03-01", amount), bill(f"B{i}", f"Supplier {i}", "2026-03-02", amount)]
    cube(bills)
    out = tool.run({"mode": "duplicates", "limit": 2, "period": {"start": "2026-01-01", "end": "2026-12-31"}},
                   organization_ids=[7], subject="t", deadline=1e12)
    assert out["amount_counted_twice"] == {"all_groups": {"AED": 78953.0}, "shown_groups": {"AED": 54468.0}}
    assert any("AED 78,953.00 would be counted twice across all 3 groups; the 2 shown add up to AED 54,468.00." in n
               for n in out["notes"])


def test_amounts_in_two_currencies_are_never_added_together(monkeypatch):
    """An organization's books are in its base currency; two organizations may have two currencies."""
    monkeypatch.setattr(tool, "_has_trn", lambda *a, **k: False)
    books = {7: [bill("A", "S1", "2026-03-01", 100.0), bill("B", "S1", "2026-03-02", 100.0)],
             8: [bill("C", "S2", "2026-03-01", 50.0, "USD"), bill("D", "S2", "2026-03-02", 50.0, "USD")]}

    def load(query, *, organization_ids, subject, deadline):
        a, b = query["timeDimensions"][0]["dateRange"]
        return SimpleNamespace(rows=[{f"purchases.{k}": v for k, v in x.items()} for x in books[organization_ids[0]]
                                     if a <= x["document_date"] <= b])

    monkeypatch.setattr(scan_mod.cube_client, "load", load)
    out = tool.run({"mode": "duplicates", "period": {"start": "2026-01-01", "end": "2026-12-31"}},
                   organization_ids=[7, 8], subject="t", deadline=1e12)
    assert out["amount_counted_twice"]["all_groups"] == {"AED": 100.0, "USD": 50.0}


def test_no_group_no_total_note(cube):
    cube([bill("A", "S1", "2026-03-01", 100.0)])
    out = tool.run({"mode": "duplicates", "period": {"start": "2026-01-01", "end": "2026-12-31"}},
                   organization_ids=[7], subject="t", deadline=1e12)
    assert out["amount_counted_twice"] == {"all_groups": {}, "shown_groups": {}}
    assert not any("counted twice" in n for n in out["notes"])
