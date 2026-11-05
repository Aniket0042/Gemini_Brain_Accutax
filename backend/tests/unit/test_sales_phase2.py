"""Phase 2 of invoices and quotes by chat: copy and convert, change a draft already saved, create a customer.
Accutax is the fake of test_sales_draft (an httpx mock transport)."""
import json

import pytest

from gemini_brain.accutax_api.client import AccutaxCredential
from gemini_brain.actions import pending
from gemini_brain.agent import tools
from gemini_brain.invoicing import confirm
from tests.unit.test_sales_draft import ASK, TOKEN, _ctx, accutax  # noqa: F401  (accutax is a fixture)


def _quote_record(**over):
    rec = {"id": 55, "invoice_number": "QUO-0012", "income_type": "QUOTE", "organization_id": 24, "contact_id": 1,
           "is_draft": False, "invoice_date": "2026-10-20T00:00:00.000Z", "due_date": "2026-11-04",
           "additional_notes": "Net 30", "terms_and_conditions": "", "items": [
               {"items_id": 10, "description": "Consulting", "quantity": 2, "unit_price": 300, "discount_percent": 0,
                "discount_amount": 0, "tax_rate_id": 7, "tax_amount": 30, "line_amount": 630, "account_id": 400},
               {"items_id": None, "description": "Setup", "quantity": 1, "unit_price": 100, "discount_percent": 0,
                "discount_amount": 0, "tax_rate_id": 7, "tax_amount": 5, "line_amount": 105}]}
    rec.update(over)
    return rec


def _quote_row(**over):
    row = {"id": 55, "number": "QUO-0012", "income_type": "QUOTE", "contact_name": "Al Noor Trading LLC",
           "date": "2026-10-20", "is_draft": False, "total_value": 770.0, "converted_to_number": None}
    row.update(over)
    return row


def _with_quote(fake, **over):
    fake.list_rows = [_quote_row()]
    fake.records = {55: _quote_record(**over)}


COPY = {"target_type": "invoice", "source_type": "quote", "source_number": "QUO-0012"}
CRED = AccutaxCredential(TOKEN)


def test_convert_quote_to_invoice(accutax):
    _with_quote(accutax)
    out, ok = tools.execute("copy_sales_document", COPY, _ctx())
    assert ok and out["status"] == "draft_ready" and out["linked_to_source"] is True
    assert (out["subtotal"], out["vat"], out["total"]) == ("700.00", "35.00", "735.00")
    action = pending.get_for(out["draft_id"], user_id=501)
    p = action.payload
    assert p["income_type"] == "INVOICE" and p["reference_id"] == 55 and p["contact_id"] == 1 and p["is_draft"] is True
    assert p["invoice_number"] == "INV-1048" and p["additional_notes"] == "Net 30"
    assert [(l["items_id"], l["line_amount"], l["tax_amount"]) for l in p["line_items"]] == [(10, 600, 30), (None, 100, 5)]
    assert action.view["copied_from"] == {"number": "QUO-0012", "type": "Quote", "linked": True}
    assert not any(c[0] == "POST" for c in accutax.calls)
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "done"


def test_copy_of_a_line_saved_the_forms_way_stores_the_net(accutax):
    # the quote's lines hold net + VAT in line_amount (630 = 600 + 30); the copy must hold the net (600)
    _with_quote(accutax)
    out, _ = tools.execute("copy_sales_document", COPY, _ctx())
    lines = pending.get_for(out["draft_id"], user_id=501).payload["line_items"]
    assert [l["line_amount"] for l in lines] == [600.0, 100.0] and [l["tax_amount"] for l in lines] == [30, 5]
    assert out["total"] == "735.00"
    # lines saved the assistant's way (the net already) copy the same
    accutax.records[55]["items"][0]["line_amount"] = 600
    accutax.records[55]["items"][1]["line_amount"] = 100
    accutax.list_rows = [_quote_row(total_value=735.0)]
    out, _ = tools.execute("copy_sales_document", COPY, _ctx())
    assert out["total"] == "735.00"


def test_convert_already_converted(accutax):
    _with_quote(accutax)
    accutax.list_rows = [_quote_row(converted_to_number="INV-1040")]
    out, ok = tools.execute("copy_sales_document", COPY, _ctx())
    assert ok and out["status"] == "already_converted" and out["converted_to"] == "INV-1040"
    out, ok = tools.execute("copy_sales_document", {**COPY, "link_to_source": False}, _ctx())
    assert out["status"] == "draft_ready"
    assert "reference_id" not in pending.get_for(out["draft_id"], user_id=501).payload


def test_copy_refused_when_a_line_cannot_be_read(accutax):
    _with_quote(accutax)
    accutax.list_rows = [_quote_row(total_value=1000.0)]   # the list sums a line that find left out
    out, ok = tools.execute("copy_sales_document", COPY, _ctx())
    assert ok and out["status"] == "cannot_copy"


def test_copy_never_reads_another_organizations_record(accutax):
    _with_quote(accutax, organization_id=99)
    out, ok = tools.execute("copy_sales_document", COPY, _ctx())
    assert ok and out["status"] == "source_not_found"
    assert "99" not in json.dumps(out)


def test_copy_source_missing_or_ambiguous(accutax):
    out, _ = tools.execute("copy_sales_document", COPY, _ctx())
    assert out["status"] == "source_not_found"
    accutax.list_rows = [_quote_row(), _quote_row(id=56, contact_name="Desert Rose")]
    out, _ = tools.execute("copy_sales_document", COPY, _ctx())
    assert out["status"] == "choose_source" and len(out["candidates"]) == 2


def test_repeat_last_invoice_of_customer(accutax):
    accutax.list_rows = [
        {"id": 90, "number": "INV-1030", "income_type": "INVOICE", "contact_name": "Al Noor Trading LLC",
         "date": "2026-10-30", "is_draft": False, "total_value": 770.0, "converted_to_number": None},
        {"id": 80, "number": "INV-1020", "income_type": "INVOICE", "contact_name": "Al Noor Trading LLC",
         "date": "2026-09-30", "is_draft": False, "total_value": 1.0, "converted_to_number": None}]
    accutax.records = {90: _quote_record(id=90, invoice_number="INV-1030", income_type="INVOICE")}
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "customer": "Al Noor"}, _ctx())
    assert ok and out["status"] == "draft_ready" and out["copied_from"] == "INV-1030"
    assert out["linked_to_source"] is False
    assert "reference_id" not in pending.get_for(out["draft_id"], user_id=501).payload


def test_invoice_cannot_become_quote(accutax):
    out, ok = tools.execute("copy_sales_document", {"target_type": "quote", "source_type": "invoice",
                                                    "source_number": "INV-1"}, _ctx())
    assert not ok and "cannot be turned into a quote" in out["error"]
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice"}, _ctx())
    assert not ok and "source_number" in out["error"]


# ── change a draft that is already saved ────────────────────

def _saved(fake):
    first, _ = tools.execute("draft_sales_document", ASK, _ctx())
    confirm.confirm(first["draft_id"], user_id=501, organization_id=24, credential=CRED)
    fake.records[881] = {"id": 881, "invoice_number": "INV-1048", "income_type": "INVOICE", "organization_id": 24,
                         "is_draft": True, "items": []}
    return first["draft_id"]


EDIT = {**ASK, "changes_earlier_draft": True,
        "lines": [{"description": "Consulting", "quantity": 45, "unit_price": 300, "discount_percent": 10}]}


def test_change_saved_draft_updates_in_place(accutax):
    saved_id = _saved(accutax)
    out, ok = tools.execute("draft_sales_document", EDIT, _ctx())
    assert ok and out["mode"] == "change_saved_draft" and out["total"] == "12,757.50"
    assert out["suggested_number"] == "INV-1048"
    action = pending.get_for(out["draft_id"], user_id=501)
    assert action.kind == "sales_document_update" and action.view["mode"] == "update"
    assert action.payload["income_id"] == 881 and "reference_id" not in action.payload
    assert pending.get_for(saved_id, user_id=501).status == "done"      # untouched until the click
    assert not accutax.bodies
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "done" and done.result["updated"] is True and done.result["record_id"] == 881
    put = accutax.bodies[-1]
    assert put["income_id"] == 881 and put["is_draft"] is True and put["line_items"][0]["line_amount"] == 12150.0
    assert len([c for c in accutax.calls if c[0] == "POST"]) == 1       # no second invoice was created
    again = {**EDIT, "lines": [{"description": "Consulting", "quantity": 1, "unit_price": 10}]}
    out2, _ = tools.execute("draft_sales_document", again, _ctx())
    assert pending.get_for(out2["draft_id"], user_id=501).payload["income_id"] == 881


def test_saved_draft_no_longer_draft_is_not_changed(accutax):
    _saved(accutax)
    out, _ = tools.execute("draft_sales_document", EDIT, _ctx())
    accutax.records[881]["is_draft"] = False
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "failed" and "no longer a draft" in done.error["message"]
    assert not accutax.bodies


def test_saved_draft_in_another_org_is_not_changed(accutax):
    _saved(accutax)
    out, _ = tools.execute("draft_sales_document", EDIT, _ctx())
    accutax.records[881]["organization_id"] = 99
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "failed" and not accutax.bodies


def test_saved_draft_type_cannot_change(accutax):
    _saved(accutax)
    out, ok = tools.execute("draft_sales_document", {**EDIT, "document_type": "quote"}, _ctx())
    assert not ok and "cannot change" in out["error"]


# ── create customer ─────────────────────────────────────────

def test_create_customer_flow(accutax):
    ctx = _ctx()
    args = {"name": "Zenith Labs", "email": "ap@zenith.ae", "trn": "100 2345 6780 0003"}
    out, ok = tools.execute("create_customer", args, ctx)
    assert ok and out["status"] == "customer_ready" and out["has_trn"] is True
    assert ctx.cards[0]["type"] == "customer_draft" and ctx.cards[0]["text"] == "New customer Zenith Labs"
    assert "ap@zenith.ae" not in json.dumps(out) and "100234567800003" not in json.dumps(out)
    assert not accutax.bodies
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "done" and done.result == {"customer_id": 77, "name": "Zenith Labs"}
    body = accutax.bodies[-1]
    assert body["base"]["contact_type_id"] == 4 and body["base"]["organization_id"] == 24
    assert body["base"]["name"] == "Zenith Labs" and body["personal"]["trn_number"] == "100234567800003"
    assert body["base"]["image_url"] == "" and body["base"]["last_name"] == "" and body["personal"]["website"] == "N/A"


def test_create_customer_without_trn_or_phone_sends_placeholders(accutax):
    ctx = _ctx()
    out, _ = tools.execute("create_customer", {"name": "Zenith Labs", "email": "rosetest@gmail.com"}, ctx)
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "done"                    # the fake answers 500 when a NOT NULL column would be left empty
    body = accutax.bodies[-1]
    assert body["personal"]["trn_number"] == "N/A" and body["base"]["phone_number"] == ""


def test_create_customer_checks(accutax):
    out, _ = tools.execute("create_customer", {"name": "Zenith Labs"}, _ctx())
    assert out["status"] == "email_needed"
    out, ok = tools.execute("create_customer", {"name": "Zenith Labs", "email": "nope"}, _ctx())
    assert not ok and "email" in out["error"]
    out, ok = tools.execute("create_customer", {"name": "Zenith", "email": "a@b.ae", "trn": "123"}, _ctx())
    assert not ok and "15 digits" in out["error"]
    out, _ = tools.execute("create_customer", {"name": "al noor trading", "email": "a@b.ae"}, _ctx())
    assert out["status"] == "customer_exists"
    out, _ = tools.execute("create_customer", {"name": "Desert Rose", "email": "a@b.ae"}, _ctx())
    assert out["status"] == "similar_customers" and len(out["candidates"]) == 2
    args = {"name": "Desert Rose", "email": "a@b.ae", "create_anyway": True}
    out, _ = tools.execute("create_customer", args, _ctx())
    assert out["status"] == "customer_ready"


def test_all_write_tools_gated_and_offered(accutax):
    off = _ctx()
    off.write_actions = False
    for name in tools.WRITE_RUNNERS:
        out, ok = tools.execute(name, {}, off)
        assert not ok and "not available" in out["error"]
    offered = [s["toolSpec"]["name"] for s in tools.specs(_ctx()) if s["toolSpec"]["name"] in tools.WRITE_RUNNERS]
    assert sorted(offered) == ["copy_sales_document", "create_customer", "draft_sales_document"]
