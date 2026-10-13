"""Checks run in code on an extract, and which party is the organization."""
from datetime import date

import pytest

from gemini_brain.uploads.checks import failed, resolve_parties, run_checks
from gemini_brain.uploads.extract import DocumentExtract

TODAY = date(2026, 10, 12)


def _bill(**kw):
    base = {"document_type": "tax_invoice", "vendor_name": "Alex Adams Trading LLC", "vendor_tax_id": "100123456700003",
            "document_number": "152643", "date": "2026-09-01", "currency": "AED", "subtotal": 500, "tax_total": 25,
            "grand_total": 525, "items": [{"description": "a", "amount": 150}, {"description": "b", "amount": 240},
                                           {"description": "c", "amount": 110}]}
    base.update(kw)
    return DocumentExtract.from_dict(base)


def _by_name(ex):
    return {c.name: c for c in run_checks(ex, today=TODAY)}


def test_a_correct_bill_passes_every_check():
    assert {c.status for c in run_checks(_bill(), today=TODAY)} == {"pass"}


def test_documents_that_are_not_bills_get_no_checks():
    assert run_checks(DocumentExtract(document_type="contract", text="x"), today=TODAY) == []
    assert run_checks(DocumentExtract(document_type="statement"), today=TODAY) == []


def test_lines_that_do_not_add_up_fail_and_say_the_sums():
    c = _by_name(_bill(items=[{"amount": 150}, {"amount": 240}, {"amount": 100}]))["items_add_up"]
    assert c.status == "fail" and "490.00" in c.detail and "500.00" in c.detail


def test_lines_priced_with_tax_still_add_up():
    c = _by_name(_bill(items=[{"amount": 315}, {"amount": 210}]))["items_add_up"]
    assert c.status == "pass"  # 525 = the total, lines are tax-inclusive


def test_lines_without_amounts_cannot_be_judged():
    assert _by_name(_bill(items=[{"description": "x"}]))["items_add_up"].status == "na"
    assert _by_name(_bill(items=[]))["items_add_up"].status == "na"


def test_a_total_that_already_includes_the_tax_is_not_a_mismatch():
    # printed like the real receipts of phase 0: subtotal and total are the same figure, the tax is a part of it
    checks = _by_name(_bill(subtotal=15.9, tax_total=0.9, grand_total=15.9, items=[{"amount": 15.9}]))
    assert checks["total_adds_up"].status == "pass" and "includes the tax" in checks["total_adds_up"].detail
    assert _by_name(_bill(subtotal=105, tax_total=5, grand_total=105, items=[{"amount": 105}]))["vat_rate"].status == "pass"
    assert _by_name(_bill(subtotal=105, tax_total=9, grand_total=105, items=[{"amount": 105}]))["vat_rate"].status == "fail"


def test_subtotal_plus_tax_must_equal_the_total():
    assert _by_name(_bill(grand_total=530))["total_adds_up"].status == "fail"
    assert _by_name(_bill(grand_total=525.04))["total_adds_up"].status == "pass"  # rounded to 0.05
    assert _by_name(_bill(tax_total=None))["total_adds_up"].status == "na"


@pytest.mark.parametrize("subtotal,tax,status", [(1000, 50, "pass"), (1000, 52.4, "fail"), (1000, 0, "pass"), (999.99, 50, "pass")])
def test_vat_is_five_percent(subtotal, tax, status):
    c = _by_name(_bill(subtotal=subtotal, tax_total=tax, grand_total=subtotal + tax))["vat_rate"]
    assert c.status == status


def test_the_vat_failure_names_both_amounts():
    c = _by_name(_bill(subtotal=1000, tax_total=52.4, grand_total=1052.4))["vat_rate"]
    assert "52.40" in c.detail and "50.00" in c.detail


def test_uae_rules_do_not_apply_to_foreign_bills():
    checks = _by_name(_bill(currency="MYR", vendor_tax_id="001006288896", subtotal=13.3, tax_total=0.8, grand_total=14.1,
                            items=[{"amount": 5.2}, {"amount": 8.9}]))
    assert checks["vat_rate"].status == "na" and checks["tax_id"].status == "na"
    assert checks["total_adds_up"].status == "pass"


@pytest.mark.parametrize("tid,status", [("100123456700003", "pass"), ("1001 2345 6700 003", "pass"), ("TRN 100123456700003", "pass"),
                                         ("1001234567", "fail"), (None, "fail"), ("xxxxx", "na"), ("XXXXXXXXXXXXXXX", "na")])
def test_the_supplier_trn_has_15_digits(tid, status):
    assert _by_name(_bill(vendor_tax_id=tid))["tax_id"].status == status


def test_dates():
    assert _by_name(_bill(date="2026-10-13"))["date_valid"].status == "fail"
    assert _by_name(_bill(date=None))["date_valid"].status == "fail"
    assert _by_name(_bill(date="2026-10-12"))["date_valid"].status == "pass"


def test_a_missing_currency_is_flagged_but_treated_as_dirhams_for_vat():
    checks = _by_name(_bill(currency=None))
    assert checks["currency"].status == "fail" and checks["vat_rate"].status == "pass"


def test_failed_returns_only_failures():
    got = failed(run_checks(_bill(subtotal=1000, tax_total=52.4, grand_total=1052.4), today=TODAY))
    assert {c.name for c in got} == {"items_add_up", "vat_rate"}


# ── supplier against buyer ──

def test_the_buyer_being_us_makes_it_a_purchase():
    ex = DocumentExtract(vendor_name="Alex Adams Trading LLC", buyer_name="Company A Pty Limited", buyer_tax_id="100999999900003")
    got = resolve_parties(ex, own_names=["Company A Pty Ltd"], own_tax_ids=[])
    assert got["direction"] == "purchase" and got["supplier"] == "Alex Adams Trading LLC"


def test_the_vendor_being_us_makes_it_a_sale():
    ex = DocumentExtract(vendor_name="Company A Pty Limited", buyer_name="Victor Davis")
    assert resolve_parties(ex, own_names=["COMPANY A PTY LTD."])["direction"] == "sale"


def test_a_tax_id_match_beats_a_name_mismatch():
    ex = DocumentExtract(vendor_name="Supplier X", buyer_name="Some Other Name", buyer_tax_id="100 999 999 900 003")
    assert resolve_parties(ex, own_names=["Company A"], own_tax_ids=["100999999900003"])["direction"] == "purchase"


def test_neither_party_matching_leaves_it_unknown_and_says_so():
    got = resolve_parties(DocumentExtract(vendor_name="Alex Adams", buyer_name="Victor Davis"), own_names=["Company Z"])
    assert got["direction"] == "unknown" and "issuer" in got["basis"]


def test_two_unrelated_short_names_do_not_match():
    assert resolve_parties(DocumentExtract(vendor_name="Al Noor Trading", buyer_name="Al Safa Trading"),
                           own_names=["Al Madina Trading"])["direction"] == "unknown"


def test_the_organizations_name_in_the_address_line_counts():
    # the layout of the phase 0 sample: a person's name above the company's name and address
    ex = DocumentExtract(vendor_name="ALEX ADAMS", vendor_address="Company A Pty Limited, PO BOX: 12345, Maktoum Road, Dubai",
                         buyer_name="VICTOR DAVIS")
    assert resolve_parties(ex, own_names=["Company A Pty Limited"])["direction"] == "sale"


def test_an_address_does_not_make_a_short_or_partial_name_match():
    ex = DocumentExtract(vendor_name="Alex Adams", vendor_address="Al Noor Building, Dubai", buyer_name="Victor Davis")
    assert resolve_parties(ex, own_names=["Al Madina Trading"])["direction"] == "unknown"
    assert resolve_parties(ex, own_names=["A"])["direction"] == "unknown"


def test_without_the_organizations_name_nothing_is_compared():
    got = resolve_parties(DocumentExtract(vendor_name="Alex Adams", buyer_name="Victor Davis"), own_names=[], own_tax_ids=[])
    assert got["direction"] == "unknown" and "not available" in got["basis"]
