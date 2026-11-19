"""Fixes from the 19 Nov manual test of the hard VAT questions and traps: the sales review (no-VAT tax codes, VAT on
a normally zero-rated customer), law answers about the user's books and the VAT 201 boxes, the organization name on
the VAT 201 card, and "VAT 201" in the figure check."""
import datetime as dt

from gemini_brain.policy.verifier import _numbers_in
from gemini_brain.vat_kb.answer import VAT_ANSWER_RULES
from gemini_brain.vat_kb.detector import is_vat_law_question
from gemini_brain.vat_return.blocks import form201_blocks
from gemini_brain.vat_review import anomalies
from gemini_brain.vat_review.scan import _query

TODAY = dt.date(2026, 11, 19)


def inv(number, customer, net, vat, country="AE", coded=None):
    b = {"document_number": number, "document_id": number, "document_date": "2026-05-18", "document_type": "INVOICE",
         "status": "PAID", "vendor_name": customer, "net_purchases": net, "input_vat": vat,
         "vendor_country_code": country}
    if coded is not None:
        b["net_sales_coded_no_vat"] = coded
    return b


def rules(found):
    return sorted(f["rule"] for f in found)


# --- the sales review -----------------------------------------------------------------------------------------------

def test_a_sale_coded_zero_rated_is_not_a_no_vat_finding():
    found, patterns = anomalies.find_anomalies([inv("I-1", "Export Co", 1000.0, 0.0, coded=1000.0)], TODAY, side="sales")
    assert "vat_zero" not in rules(found) and not patterns


def test_a_sale_with_no_vat_and_no_tax_code_is_still_a_finding():
    invoices = [inv(f"S-{i}", f"Customer {i}", 1000.0, 50.0) for i in range(5)] + [inv("I-1", "Local Co", 1000.0, 0.0, coded=0.0)]
    found, _ = anomalies.find_anomalies(invoices, TODAY, side="sales")
    assert [f["bill"]["document_number"] for f in found if f["rule"] == "vat_zero"] == ["I-1"]


def test_vat_charged_to_a_customer_whose_other_invoices_carry_none():
    # The seed's planted defect: VAT on one invoice of a designated-zone customer (country AE) whose sales are zero-rated.
    invoices = [inv(f"Z-{i}", "Creek Projects LLC (OPC)", 1000.0 + i, 0.0, coded=1000.0 + i) for i in range(5)]
    invoices.append(inv("INV-2026-00677", "Creek Projects LLC (OPC)", 40086.88, 2004.34, coded=0.0))
    found, _ = anomalies.find_anomalies(invoices, TODAY, side="sales")
    hits = [f for f in found if f["rule"] == "vat_on_zero_rated_customer"]
    assert [f["bill"]["document_number"] for f in hits] == ["INV-2026-00677"]
    assert "5 of the customer's 5 other invoices" in hits[0]["why"]


def test_vat_charged_to_a_customer_outside_the_uae():
    found, _ = anomalies.find_anomalies([inv("I-9", "Gulf Buyer WLL", 1000.0, 50.0, country="BH")], TODAY, side="sales")
    hits = [f for f in found if f["rule"] == "vat_on_zero_rated_customer"]
    assert hits and "(BH)" in hits[0]["why"]


def test_an_ordinary_uae_customer_with_vat_is_not_flagged():
    invoices = [inv(f"S-{i}", "Local Co", 1000.0, 50.0) for i in range(6)]
    found, _ = anomalies.find_anomalies(invoices, TODAY, side="sales")
    assert "vat_on_zero_rated_customer" not in rules(found)


def test_purchases_never_get_the_sales_rule():
    found, _ = anomalies.find_anomalies([inv("B-1", "Overseas Ltd", 1000.0, 50.0, country="GB")], TODAY)
    assert "vat_on_zero_rated_customer" not in rules(found)


def test_the_sales_scan_asks_for_the_customer_country_and_the_no_vat_codes():
    q = _query(TODAY, TODAY, with_country=True, side="sales", with_rc=True)
    assert "sales.customer_country_code" in q["dimensions"]
    assert "sales.net_sales_coded_no_vat" in q["measures"]
    assert not any("reverse_charge" in m for m in q["measures"])


# --- law answers and routing ----------------------------------------------------------------------------------------

def test_questions_about_our_own_suppliers_and_returns_go_to_the_books():
    for q in ("Are any foreign suppliers charging us VAT that summit supplier claiming as input tax?",
              "Is any supplier charging us foreign VAT?", "When is Al noor next VAT return due?"):
        assert not is_vat_law_question(q), q
    for q in ("Can a foreign supplier charge us VAT?", "When is the next VAT return due for a quarterly filer?",
              "Are there any exceptions to the reverse charge for imported services?"):
        assert is_vat_law_question(q), q


def test_law_answers_know_box_9_and_never_speak_for_the_books():
    assert "Box 9 standard-rated expenses" in VAT_ANSWER_RULES and "Never describe\n    Box 9 as import" in VAT_ANSWER_RULES
    assert "no access to the user's books" in VAT_ANSWER_RULES


# --- the VAT 201 card and the figure check --------------------------------------------------------------------------

def test_each_organization_on_the_card_has_its_name():
    data = [{"tool": "vat_201_draft", "result": {"organizations": [
        {"organization_id": 10176, "organization_name": "Pixel Forge Technologies FZ-LLC", "boxes": []},
        {"organization_id": 10182, "organization_name": "Summit Advisory Partners LLC", "boxes": []}]}}]
    assert [o["organization_name"] for o in form201_blocks(data)[0]["organizations"]] == [
        "Pixel Forge Technologies FZ-LLC", "Summit Advisory Partners LLC"]


def test_the_form_name_201_is_not_a_figure():
    assert _numbers_in("I can show the VAT 201 draft, Form 201 and the 201 return: Box 14 is 17,370.66") == [
        ("17,370.66", 17370.66)]
