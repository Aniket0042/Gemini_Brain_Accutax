"""Copy and convert for documents saved in this chat: they are read by their Accutax id, not through the
organization's list (income/list_filter), which times out on big organizations (seen on the VM)."""
import json

from gemini_brain.accutax_api.client import AccutaxCredential
from gemini_brain.actions import pending
from gemini_brain.agent import tools
from gemini_brain.invoicing import confirm
from tests.unit.test_sales_draft import ASK, TOKEN, _ctx, accutax  # noqa: F401  (accutax is a fixture)

CRED = AccutaxCredential(TOKEN)
QUOTE_ASK = {**ASK, "document_type": "quote", "lines": [{"description": "Consulting", "quantity": 2, "unit_price": 300}]}


def _record(kind, record_id=881, number="QUO-0012", lines=1, **over):
    one = {"items_id": 10, "description": "Consulting", "quantity": 2, "unit_price": 300, "discount_percent": 0,
           "discount_amount": 0, "tax_rate_id": 7, "tax_amount": 30, "line_amount": 600, "account_id": 400}
    rec = {"id": record_id, "invoice_number": number, "income_type": kind, "organization_id": 24, "contact_id": 1,
           "is_draft": True, "invoice_date": "2026-11-05T00:00:00.000Z", "due_date": "2026-12-05",
           "items": [dict(one) for _ in range(lines)]}
    rec.update(over)
    return rec


def _saved_quote(fake, ctx):
    out, _ = tools.execute("draft_sales_document", QUOTE_ASK, ctx)
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert done.status == "done" and done.result["record_id"] == 881
    fake.records[881] = _record("QUOTE", number=done.result["number"])
    fake.list_fails = True                         # any use of the organization's list would fail
    return done


def test_convert_the_quote_made_in_this_chat_without_the_list(accutax):
    ctx = _ctx()
    _saved_quote(accutax, ctx)
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote"}, ctx)
    assert ok and out["status"] == "draft_ready" and out["linked_to_source"] is True
    assert out["copied_from"] == "QUO-0012" and out["total"] == "630.00"
    payload = pending.get_for(out["draft_id"], user_id=501).payload
    assert payload["income_type"] == "INVOICE" and payload["reference_id"] == 881
    assert payload["line_items"][0]["line_amount"] == 600.0 and payload["line_items"][0]["tax_amount"] == 30
    assert getattr(accutax, "list_calls", 0) == 0


def test_convert_by_number_or_customer_from_this_chat(accutax):
    ctx = _ctx()
    _saved_quote(accutax, ctx)
    for extra in ({"source_number": "quo-0012"}, {"customer": "Al Noor"}):
        out, _ = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote", **extra}, ctx)
        assert out["status"] == "draft_ready" and out["copied_from"] == "QUO-0012"
    assert getattr(accutax, "list_calls", 0) == 0


def test_repeat_the_invoice_saved_in_this_chat(accutax):
    ctx = _ctx()
    first, _ = tools.execute("draft_sales_document", ASK, ctx)
    done = confirm.confirm(first["draft_id"], user_id=501, organization_id=24, credential=CRED)
    accutax.records[881] = _record("INVOICE", number=done.result["number"], lines=1,
                                   items=[{"items_id": None, "description": "Consulting", "quantity": 40, "unit_price": 300,
                                           "discount_percent": 0, "discount_amount": 0, "tax_rate_id": 7, "tax_amount": 600,
                                           "line_amount": 12000}])
    accutax.list_fails = True
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "customer": "Al Noor Trading"}, ctx)
    assert ok and out["status"] == "draft_ready" and out["linked_to_source"] is False and out["total"] == "12,600.00"
    assert "reference_id" not in pending.get_for(out["draft_id"], user_id=501).payload
    assert getattr(accutax, "list_calls", 0) == 0


def test_already_converted_quote_from_this_chat(accutax):
    ctx = _ctx()
    _saved_quote(accutax, ctx)
    accutax.records[881]["converted_to_number"] = "INV-1040"
    out, _ = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote"}, ctx)
    assert out["status"] == "already_converted" and out["converted_to"] == "INV-1040"


def test_chat_copy_refused_when_a_line_is_missing(accutax):
    ctx = _ctx()
    _saved_quote(accutax, ctx)
    accutax.records[881] = _record("QUOTE", lines=2)                       # Accutax holds a line this chat never sent
    out, _ = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote"}, ctx)
    assert out["status"] == "cannot_copy"
    accutax.records[881] = _record("QUOTE", lines=1, organization_id=99)   # moved to another organization
    out, _ = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote"}, ctx)
    assert out["status"] == "source_not_found" and "99" not in json.dumps(out)


def test_nothing_saved_here_and_the_list_is_down(accutax):
    accutax.list_fails = True
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote",
                                                    "source_number": "QUO-0099"}, _ctx())
    assert ok and out["status"] == "list_unavailable" and "saved in this chat" in out["note"]
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "quote"}, _ctx())
    assert not ok and "saved in this chat" in out["error"]


def test_a_quote_saved_here_is_not_an_invoice(accutax):
    ctx = _ctx()
    _saved_quote(accutax, ctx)
    out, ok = tools.execute("copy_sales_document", {"target_type": "invoice", "source_type": "invoice"}, ctx)
    assert not ok and "No invoice was saved in this chat" in out["error"]
