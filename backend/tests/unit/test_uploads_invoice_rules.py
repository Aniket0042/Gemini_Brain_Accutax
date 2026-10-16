"""What a UAE tax invoice must show (uploads/invoice_rules.py), and the AED value of a bill (uploads/fx.py)."""
import json

import pytest

from gemini_brain.config.settings import settings
from gemini_brain.uploads import blocks, checks, context, fx, invoice_rules
from gemini_brain.uploads.extract import DocumentExtract, ExtractItem

TRN = "100123456700003"


def full(**kw):
    base = dict(document_type="tax_invoice", document_title="Tax Invoice", vendor_name="Acme LLC", vendor_address="Dubai, UAE",
                vendor_tax_id=TRN, buyer_name="Northwind LLC", buyer_address="Sharjah, UAE", buyer_tax_id="100998877600003",
                document_number="INV-1", date="2026-09-01", currency="AED", subtotal=100.0, tax_total=5.0, tax_rate_percent=5.0,
                grand_total=105.0, items=[ExtractItem("Goods", 1, 100.0, 100.0)])
    base.update(kw)
    return DocumentExtract(**base)


def req(ex):
    return invoice_rules.required_items(ex)


# --- a full tax invoice (Article 59, clause 1) ----------------------------------------------------------------------

def test_a_complete_tax_invoice_passes():
    c = req(full())
    assert c.status == "pass" and c.detail.startswith("Everything a tax invoice must show is there.")


@pytest.mark.parametrize("change,words", [
    ({"document_title": "Invoice"}, 'the words "Tax Invoice"'),
    ({"document_title": None}, 'the words "Tax Invoice"'),
    ({"vendor_address": None}, "the supplier's address"),
    ({"vendor_name": "  "}, "the supplier's name"),
    ({"buyer_name": None}, "the recipient's name"),
    ({"buyer_address": None}, "the recipient's address"),
    ({"document_number": None}, "a sequential or unique invoice number"),
    ({"items": []}, "a description of the goods or services"),
    ({"items": [ExtractItem(None, 1, 10.0, 10.0)]}, "a description on every line"),
    ({"items": [ExtractItem("Goods", None, 10.0, 10.0)]}, "the unit price, quantity and amount on every line"),
    ({"tax_rate_percent": None, "tax_total": None}, "the rate of tax"),
    ({"grand_total": None}, "the total amount payable"),
    ({"tax_total": None}, "the tax amount"),
])
def test_each_missing_item_of_a_tax_invoice_is_named(change, words):
    c = req(full(**change))
    assert c.status == "fail" and words in c.detail and "and the document does not" in c.detail


def test_the_arabic_title_counts():
    assert req(full(document_title="فاتورة ضريبية")).status == "pass"


def test_a_missing_recipient_trn_is_to_confirm_not_a_fault():
    c = req(full(buyer_tax_id=None))
    assert c.status == "pass" and "To confirm: the recipient's TRN" in c.detail


def test_the_suppliers_trn_and_date_are_left_to_their_own_checks():
    c = req(full(vendor_tax_id=None, date=None))
    assert c.status == "pass"  # tax_id and date_valid report those two


def test_a_bill_in_another_currency_from_a_uae_supplier_needs_amounts_in_aed():
    c = req(full(currency="USD"))
    assert c.status == "fail" and "the total and the tax in AED" in c.detail
    assert req(full(currency="USD", total_aed=385.6, tax_aed=18.4)).status == "pass"


def test_a_foreign_bill_from_a_foreign_supplier_is_not_judged_by_uae_rules():
    c = req(full(currency="MYR", vendor_tax_id="001006288896"))
    assert c.status == "na" and "do not apply" in c.detail


# --- a simplified tax invoice (Article 59, clause 2) and a receipt -------------------------------------------------

def simple(**kw):
    base = dict(document_type="simplified_tax_invoice", buyer_name=None, buyer_address=None, buyer_tax_id=None,
                document_number=None, items=[ExtractItem("Goods", None, None, 100.0)])
    base.update(kw)
    return full(**base)


def test_a_simplified_invoice_needs_less():
    c = req(simple())
    assert c.status == "pass" and "simplified tax invoice" in c.detail


def test_a_simplified_invoice_still_needs_the_words_a_description_and_amounts():
    c = req(simple(document_title="Receipt", items=[], grand_total=None, tax_total=None))
    assert c.status == "fail"
    for words in ('the words "Tax Invoice"', "a description of the goods or services", "the total amount", "the tax amount"):
        assert words in c.detail


def test_a_receipt_is_not_a_tax_invoice():
    c = req(full(document_type="receipt", document_title="Receipt", buyer_name=None, buyer_address=None))
    assert c.status == "fail" and "This is a receipt, not a tax invoice" in c.detail and 'the words "Tax Invoice"' in c.detail


def test_a_reverse_charge_statement_rules_out_a_simplified_invoice():
    c = req(simple(reverse_charge_statement=True))
    assert c.status == "fail" and "reverse charge" in c.detail and "full tax invoice is needed" in c.detail


# --- the AED 10,000 limit -----------------------------------------------------------------------------------------

def test_a_registered_recipient_allows_a_simplified_invoice_only_up_to_10000():
    ok = invoice_rules.simplified_limit(simple(buyer_tax_id="100998877600003", grand_total=9_999.0))
    over = invoice_rules.simplified_limit(simple(buyer_tax_id="100998877600003", grand_total=10_001.0))
    assert ok.status == "pass" and over.status == "fail" and "10,000" in over.detail and "10,001.00" in over.detail


def test_without_a_recipient_trn_the_limit_is_not_a_fault():
    assert invoice_rules.simplified_limit(simple(grand_total=500.0)).status == "pass"
    c = invoice_rules.simplified_limit(simple(grand_total=25_000.0))
    assert c.status == "na" and "not registered" in c.detail


def test_the_limit_is_only_judged_for_simplified_invoices_in_aed_terms():
    assert invoice_rules.simplified_limit(full()) is None
    assert invoice_rules.simplified_limit(simple(currency="USD", total_aed=None)).status == "na"


# --- a tax credit note (Article 60) -------------------------------------------------------------------------------

def credit(**kw):
    base = dict(document_title="Tax Credit Note", document_number="CN-1", credit_reason="Goods returned",
                original_invoice_reference="INV-1", items=[ExtractItem("Goods", 1, 100.0, 100.0)])
    base.update(kw)
    return full(**base)


def test_a_complete_credit_note_passes():
    c = req(credit())
    assert c.status == "pass" and "tax credit note" in c.detail


def test_a_credit_note_needs_its_words_its_reason_and_the_vat_credited():
    c = req(credit(document_title="Credit Note", credit_reason=None, tax_total=None))
    assert c.status == "fail"
    for words in ('the words "Tax Credit Note"', "a brief explanation of why it was issued", "the VAT amount credited"):
        assert words in c.detail


def test_the_invoice_a_credit_note_corrects_is_to_confirm():
    c = req(credit(original_invoice_reference=None))
    assert c.status == "pass" and "To confirm: which tax invoice it corrects" in c.detail


# --- blocked input VAT (Article 53): hints only ---------------------------------------------------------------------

def test_a_restaurant_bill_gets_an_entertainment_hint():
    c = invoice_rules.claim_hint(full(vendor_name="Blue Nile Restaurant"))
    assert c.status == "na" and "entertainment" in c.detail and "Confirm who it was for" in c.detail


def test_a_vehicle_cost_gets_a_vehicle_hint():
    c = invoice_rules.claim_hint(full(items=[ExtractItem("Car rental, 3 days", 1, 300.0, 300.0)]))
    assert "motor vehicle" in c.detail and "personal use" in c.detail


def test_no_hint_for_ordinary_goods_or_when_no_vat_is_charged():
    assert invoice_rules.claim_hint(full()) is None
    assert invoice_rules.claim_hint(full(vendor_name="Blue Nile Restaurant", tax_total=0.0)) is None


def test_a_hint_never_counts_as_a_problem():
    ex = full(vendor_name="Blue Nile Restaurant")
    names = {c.name: c.status for c in checks.run_checks(ex)}
    assert names["claim_hint"] == "na" and "claim_hint" not in {c.name for c in checks.failed(checks.run_checks(ex))}


# --- wiring ----------------------------------------------------------------------------------------------------------

def test_the_checks_run_with_the_new_ones_and_have_plain_labels():
    got = {c.name: c for c in checks.run_checks(full())}
    assert "required_items" in got and got["required_items"].label == "Items a tax invoice must show"
    assert "simplified_limit" not in got and "claim_hint" not in got


def test_the_new_fields_survive_a_round_trip_and_odd_values():
    ex = DocumentExtract.from_dict({**full().to_dict(), "supply_date": "2026-08-30", "discount_total": "5,00", "exchange_rate": "3.6725",
                                    "total_aed": "385.60", "reverse_charge_statement": "yes", "credit_reason": " x "})
    assert ex.supply_date == "2026-08-30" and ex.exchange_rate == 3.6725 and ex.total_aed == 385.6
    assert ex.reverse_charge_statement is None and ex.credit_reason == "x"  # only a real true or false is kept
    assert DocumentExtract.from_dict(DocumentExtract().to_dict()).document_title is None


# --- the AED value ---------------------------------------------------------------------------------------------------

@pytest.fixture
def rates(tmp_path, monkeypatch):
    path = tmp_path / "rates.json"
    path.write_text(json.dumps({"source": "Central Bank of the UAE", "rates": {
        "USD": {"rate": 3.6725, "as_of": "fixed rate"}, "EUR": {"rate": 4.3, "as_of": "2026-10-14"}, "BAD": {"rate": "x"}}}),
        encoding="utf-8")
    monkeypatch.setattr(settings, "aed_rates_file", str(path))
    fx._load.cache_clear()
    yield
    fx._load.cache_clear()


def test_an_amount_is_worth_its_aed_at_the_rate_with_its_date(rates):
    conv = fx.to_aed(1000.0, "eur")
    assert conv["aed"] == 4300.0 and conv["rate"] == 4.3 and conv["as_of"] == "2026-10-14"
    assert fx.sentence(conv) == "AED 4,300.00 (EUR 1,000.00 at 4.3, Central Bank of the UAE, 2026-10-14)"


def test_a_currency_without_a_rate_is_never_guessed(rates):
    assert fx.to_aed(100.0, "GBP") is None and fx.to_aed(100.0, "BAD") is None and fx.sentence(None) == ""


def test_aed_and_missing_values_are_not_converted(rates):
    assert fx.to_aed(100.0, "AED") is None and fx.to_aed(None, "USD") is None and fx.to_aed(100.0, None) is None


def test_the_file_that_ships_holds_only_the_official_fixed_usd_rate():
    fx._load.cache_clear()
    data = json.loads(fx._DEFAULT_FILE.read_text(encoding="utf-8"))
    assert list(data["rates"]) == ["USD"] and data["rates"]["USD"]["rate"] == 3.6725
    assert fx.to_aed(100.0, "USD")["aed"] == 367.25


def test_a_missing_rates_file_converts_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "aed_rates_file", str(tmp_path / "nope.json"))
    fx._load.cache_clear()
    assert fx.to_aed(100.0, "USD") is None
    fx._load.cache_clear()


def test_the_card_and_the_prompt_show_the_aed_value_with_its_rate(rates):
    from types import SimpleNamespace
    ex = full(currency="EUR", grand_total=1000.0, subtotal=952.38, tax_total=47.62)
    rec = SimpleNamespace(id="f1", filename="b.pdf", kind="pdf", pages=1, size_bytes=10, checks_failed=0)
    doc = context.AttachedDocument(rec, {"extract": ex.to_dict(), "checks": [], "source": "claude"})
    row = next(r for r in blocks.documents_block([doc])["items"][0]["rows"] if r["label"] == "Total in AED")
    assert "AED 4,300.00" in row["value"] and "Central Bank of the UAE" in row["value"]
    assert "worth at the Central Bank of the UAE rate: AED 4,300.00" in context.render(doc)


def test_a_printed_aed_total_is_used_as_printed(rates):
    from types import SimpleNamespace
    ex = full(currency="EUR", grand_total=1000.0, total_aed=4321.0)
    rec = SimpleNamespace(id="f1", filename="b.pdf", kind="pdf", pages=1, size_bytes=10, checks_failed=0)
    doc = context.AttachedDocument(rec, {"extract": ex.to_dict(), "checks": [], "source": "claude"})
    row = next(r for r in blocks.documents_block([doc])["items"][0]["rows"] if r["label"] == "Total in AED")
    assert row["value"].endswith("(printed on the bill)") and "4,321.00" in row["value"]
