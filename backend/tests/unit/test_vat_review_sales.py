"""VAT review of sales invoices: the same rules on the sales view, worded for customers and invoices."""
import json
import re
from types import SimpleNamespace

import pytest

from gemini_brain.agent import links
from gemini_brain.vat_review import blocks as vb
from gemini_brain.vat_review import duplicates as dup
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review import tool
from gemini_brain.vat_review.wording import for_side

TRN = "100123456700003"
SALES_NAMES = {"vendor_name": "customer_name", "net_purchases": "net_sales", "input_vat": "output_vat", "vendor_trn": "customer_trn"}


@pytest.fixture(autouse=True)
def _trn_field(monkeypatch):
    monkeypatch.setattr(tool, "_has_trn", lambda *a, **k: True)


def inv(number, customer="Acme Retail LLC", date="2026-03-01", net=1000.0, vat=50.0, trn=TRN, id_=None, status="ISSUED"):
    return {"document_number": number, "document_id": id_ or str(abs(hash(number + date)) % 90000 + 1000), "document_date": date,
            "document_type": "INVOICE", "status": status, "vendor_name": customer, "net_purchases": net, "input_vat": vat,
            "vendor_trn": trn, "organization_name": "Org S", "currency": "AED"}


def fake_sales_cube(monkeypatch, invoices):
    seen = []

    def load(query, *, organization_ids, subject, deadline):
        seen.append(query)
        a, b = query["timeDimensions"][0]["dateRange"]
        rows = [{(f"sales.{SALES_NAMES.get(k, k)}"): v for k, v in x.items()} for x in invoices if a <= x["document_date"] <= b]
        return SimpleNamespace(rows=rows[:500])

    monkeypatch.setattr(scan_mod.cube_client, "load", load)
    return seen


def review(monkeypatch, invoices, **params):
    fake_sales_cube(monkeypatch, invoices)
    return tool.run({"side": "sales", "period": {"start": "2026-01-01", "end": "2026-12-31"}, **params},
                    organization_ids=[7], subject="t", deadline=1e12)


#: A sales book where every rule fires once.
def book():
    clean = [inv(f"C{i}", customer=f"Customer {i}", net=1100.0 + 37 * i, vat=round((1100.0 + 37 * i) * 0.05, 2),
                 date=f"2026-02-{i + 1:02d}") for i in range(12)]
    regular = [inv(f"R{i}", customer="Regular Co", net=1000.0 + 10 * i + 0.5, vat=round((1000.0 + 10 * i + 0.5) * 0.05, 2),
                   date=f"2026-01-{i + 1:02d}") for i in range(8)]
    return clean + regular + [
        inv("INV-77", customer="First Customer", date="2026-03-02", net=300.0, vat=15.0),
        inv("INV-77", customer="Second Customer", date="2026-03-20", net=900.0, vat=45.0),     # same number, two customers
        inv("L1", customer="Likely Co", date="2026-03-10", net=1850.0, vat=92.5),
        inv("L2", customer="Likely Co", date="2026-03-12", net=1850.0, vat=92.5),
        inv("P1", customer="Zenith Stationery", date="2026-03-15", net=2310.0, vat=115.5),
        inv("P2", customer="Zenith Stationary", date="2026-03-15", net=2310.0, vat=115.5),
        inv("V1", customer="Rate Co", net=2000.0, vat=400.0, date="2026-03-21"),
        inv("S1", customer="Sign Co", net=1010.0, vat=-50.5, date="2026-03-22"),
        inv("N1", customer="No Amount Co", net=0.0, vat=25.0, date="2026-03-23"),
        inv("Z1", customer="Export Co", net=1510.0, vat=0.0, date="2026-03-24"),
        inv("M1", customer="", net=777.0, vat=38.85, date="2026-03-25"),
        inv("", customer="No Number Co", net=888.0, vat=44.4, date="2026-03-26"),
        inv("T1", customer="Short Trn Co", net=2020.0, vat=101.0, trn="123456789", date="2026-03-27"),
        inv("T2", customer="Unregistered Co", net=2030.0, vat=101.5, trn=None, date="2026-03-28"),
        inv("R9", customer="Regular Co", net=9500.5, vat=475.03, date="2026-04-06"),
    ]


# --- what is read ---------------------------------------------------------------------------------------------------

def test_a_sales_scan_reads_the_sales_view_and_uses_the_rules_names(monkeypatch):
    seen = fake_sales_cube(monkeypatch, [inv("A")])
    import datetime as dt
    scan = scan_mod.scan_bills(7, dt.date(2026, 1, 1), dt.date(2026, 12, 31), subject="t", deadline=1e12, with_trn=True, side="sales")
    q = seen[0]
    assert "sales.customer_name" in q["dimensions"] and "sales.customer_trn" in q["dimensions"]
    assert q["measures"] == ["sales.net_sales", "sales.output_vat"] and q["timeDimensions"][0]["dimension"] == "sales.document_date"
    bill = scan.bills[0]
    assert bill["vendor_name"] == "Acme Retail LLC" and bill["net_purchases"] == 1000.0 and bill["input_vat"] == 50.0
    assert bill["vendor_trn"] == TRN


def test_the_side_must_be_known(monkeypatch):
    from gemini_brain.semantic.catalog import ToolInputError
    with pytest.raises(ToolInputError):
        review(monkeypatch, [], side="payroll")


def test_the_trn_field_is_looked_for_on_the_sales_view(monkeypatch):
    seen = {}

    class View:
        dimensions = {"sales.customer_trn"}

    class Catalog:
        def view(self, name):
            seen["view"] = name
            return View()

    monkeypatch.undo()
    import gemini_brain.semantic.catalog as catalog
    monkeypatch.setattr(catalog, "get_catalog", lambda *a, **k: Catalog())
    assert tool._has_trn([7], "t", 1e12, "sales") is True and seen["view"] == "sales"
    assert tool._has_trn([7], "t", 1e12, "purchases") is False


# --- the rules on sales ---------------------------------------------------------------------------------------------

def test_the_same_invoice_number_for_two_customers_is_a_sure_duplicate():
    groups = dup.find_duplicates([inv("INV-77", customer="A"), inv("INV-77", customer="B", date="2026-03-20", net=5.0)], side="sales")
    assert [g["rule"] for g in groups] == ["same_number"] and groups[0]["why"] == "2 invoices carry the same number INV-77."
    assert dup.find_duplicates([inv("INV-77", customer="A"), inv("INV-77", customer="B", date="2026-03-20", net=5.0)]) == []


def test_a_customer_without_a_trn_is_fine_but_a_wrong_trn_is_not(monkeypatch):
    out = review(monkeypatch, book(), mode="anomalies", rules=["supplier_trn_invalid"])
    numbers = [f["bill"]["document_number"] for f in out["findings"]]
    assert numbers == ["T1"] and "customer's TRN has 9 digits" in out["findings"][0]["why"]


def test_every_rule_fires_on_the_sales_book(monkeypatch):
    out = review(monkeypatch, book(), limit=50)
    by_rule = {**out["groups_by_rule"], **out["findings_by_rule"]}
    for rule in ("same_number", "same_total_near_date", "similar_vendor_same_day", "vat_rate_not_standard", "vat_wrong_sign",
                 "vat_without_amount", "vat_zero", "missing_supplier", "missing_number", "large_for_supplier", "supplier_trn_invalid"):
        assert by_rule.get(rule), rule
    assert out["side"] == "sales"


_PURCHASE_WORDS = re.compile(r"\b(supplier|suppliers|bill|bills|purchases?|input VAT|claimed)\b", re.I)


def test_no_purchase_word_is_left_in_a_sales_review(monkeypatch):
    out = review(monkeypatch, book(), limit=50)
    texts = [g["why"] for g in out["groups"]] + [f["why"] for f in out["findings"]] + [p["text"] for p in out["patterns"]]
    texts += out["notes"] + list(out["rules"].values()) + [q["for"] for q in out["law_questions"]]
    [card] = vb.review_blocks([{"tool": "review_vat", "result": out}])
    texts += [i["title"] for i in card["items"]]
    left = [t for t in texts if _PURCHASE_WORDS.search(t)]
    assert left == []


def test_patterns_and_notes_are_worded_for_sales(monkeypatch):
    zero = [inv(f"Z{i}", customer=f"C{i}", net=1000.0 + 7 * i, vat=0.0) for i in range(20)]
    out = review(monkeypatch, zero, mode="anomalies")
    assert "20 of 20 invoices" in out["patterns"][0]["text"] and "output VAT is not being charged" in out["patterns"][0]["text"]
    out = review(monkeypatch, [inv("A"), inv("B", customer="Other", net=5.0)], mode="duplicates")
    assert any("No duplicate invoices" in n for n in out["notes"])


def test_wording_leaves_a_purchase_review_alone():
    text = "The same supplier has 2 bills with the document number X."
    assert for_side(text, "purchases") == text and for_side(None, "sales") is None


# --- what reaches the user -------------------------------------------------------------------------------------------

def test_rows_name_the_customer_and_output_vat(monkeypatch):
    out = review(monkeypatch, book(), mode="duplicates")
    row = out["rows"][0]
    assert "customer" in row and "output_vat" in row and "supplier" not in row and "input_vat" not in row


def test_the_card_says_sales_and_links_go_to_the_invoice(monkeypatch):
    monkeypatch.setattr(links.settings, "accutax_app_url", "https://app.example")
    out = review(monkeypatch, [inv("LIK-1001", id_="501"), inv("LIK-1002", id_="502", date="2026-03-02")], mode="duplicates")
    item = {"tool": "review_vat", "input": {"side": "sales"}, "result": out}
    [card] = vb.review_blocks([item])
    assert card["side"] == "sales" and card["items"][0]["title"] == "Same customer, same amount, days apart"
    assert links.document_links([item]) == {"LIK-1001": "https://app.example/income/details/501",
                                            "LIK-1002": "https://app.example/income/details/502"}
    assert json.dumps(card)
