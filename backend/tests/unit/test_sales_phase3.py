"""Phase 3 of invoices and quotes by chat: finalising a draft invoice (posting it to the books).
Accutax is the fake of test_sales_draft (an httpx mock transport)."""
import base64
import json

import httpx
import pytest

from gemini_brain import actions
from gemini_brain.accutax_api.client import AccutaxCredential
from gemini_brain.actions import pending
from gemini_brain.agent import tools
from gemini_brain.config.settings import settings
from gemini_brain.invoicing import confirm
from tests.unit.test_sales_draft import ASK, accutax  # noqa: F401  (accutax is a fixture)


def jwt(org):
    """A token that names an organization, as Accutax's own tokens do (unsigned: only the claim is read)."""
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'userId': 501, 'organization_id': org})}.sig"


def ctx_for(org_in_token=24, orgs=(24,), session="s1"):
    return tools.ToolContext(organization_ids=list(orgs), subject="t", deadline=1e18, user_id=501, session_id=session,
                             accutax=AccutaxCredential(jwt(org_in_token) if org_in_token else "opaque-token"),
                             write_actions=True)


@pytest.fixture
def finalise_on(accutax, monkeypatch):
    from gemini_brain.invoicing import finalise
    monkeypatch.setattr(finalise, "client_for", accutax.client)
    monkeypatch.setattr(settings, "ai_write_tools", "finalise_sales_invoice")
    return accutax


def _record(**over):
    rec = {"id": 881, "invoice_number": "INV-1048", "income_type": "INVOICE", "organization_id": 24, "contact_id": 1,
           "is_draft": True, "invoice_date": "2026-11-05T00:00:00.000Z", "due_date": "2026-12-05", "items": [
               {"items_id": 10, "description": "Consulting", "quantity": 40, "unit_price": 300, "discount_percent": 0,
                "discount_amount": 0, "tax_rate_id": 7, "tax_amount": 600, "line_amount": 12600, "account_id": 400}]}
    rec.update(over)
    return rec


def _saved_invoice(fake, ctx):
    first, _ = tools.execute("draft_sales_document", ASK, ctx)
    confirm.confirm(first["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    fake.records[881] = _record()
    return first["draft_id"]


# ── switches ────────────────────────────────────────────────

def test_finalise_is_opt_in(accutax, monkeypatch):
    monkeypatch.setattr(settings, "ai_write_tools", "")
    assert actions.tool_enabled("draft_sales_document") and not actions.tool_enabled("finalise_sales_invoice")
    monkeypatch.setattr(settings, "ai_write_tools", "finalise_sales_invoice")
    assert actions.tool_enabled("finalise_sales_invoice")
    assert actions.tool_enabled("draft_sales_document")          # naming it does not switch the others off
    monkeypatch.setattr(settings, "ai_write_tools", "create_customer,finalise_sales_invoice")
    assert actions.tool_enabled("create_customer") and not actions.tool_enabled("draft_sales_document")


def test_finalise_not_offered_by_default(accutax, monkeypatch):
    monkeypatch.setattr(settings, "ai_write_tools", "")
    out, ok = tools.execute("finalise_sales_invoice", {}, ctx_for())
    assert not ok and "not available" in out["error"]
    assert "finalise_sales_invoice" not in [s["toolSpec"]["name"] for s in tools.specs(ctx_for())]


def test_token_organization_claim():
    assert AccutaxCredential(jwt(24)).organization_id() == 24
    assert AccutaxCredential("opaque").organization_id() is None
    assert AccutaxCredential("a.b.c").organization_id() is None


# ── prepare ─────────────────────────────────────────────────

def test_finalise_saved_invoice_of_this_chat(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, ok = tools.execute("finalise_sales_invoice", {}, ctx)
    assert ok and out["status"] == "finalise_ready" and out["invoice_number"] == "INV-1048"
    assert out["total"] == "12,600.00" and out["vat"] == "600.00" and out["customer"] == "Al Noor Trading LLC"
    assert ctx.cards[-1]["type"] == "finalise_draft"
    assert not getattr(finalise_on, "finalised", [])                      # preparing never posts


def test_finalise_confirm_posts_once(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    assert done.status == "done" and done.result["finalised"] is True and done.result["number"] == "INV-1048"
    assert finalise_on.finalised == [881]
    with pytest.raises(pending.ActionStateError):
        confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    assert finalise_on.finalised == [881]


def test_finalise_refused_when_token_is_on_another_organization(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    for token_org in (25, None):
        out, ok = tools.execute("finalise_sales_invoice", {}, ctx_for(org_in_token=token_org))
        assert ok and out["status"] == "finalise_unavailable"
    assert not getattr(finalise_on, "finalised", [])


def test_finalise_confirm_rechecks_token_organization(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=AccutaxCredential(jwt(25)))
    assert done.status == "failed" and "sign-in" in done.error["message"]
    assert not getattr(finalise_on, "finalised", [])


def test_finalise_needs_a_saved_invoice(finalise_on):
    out, ok = tools.execute("finalise_sales_invoice", {}, ctx_for())
    assert ok and out["status"] == "nothing_to_finalise"
    ctx = ctx_for()
    tools.execute("draft_sales_document", ASK, ctx)                       # prepared, not saved
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    assert out["status"] == "save_first"


def test_finalise_never_a_quote(finalise_on):
    ctx = ctx_for()
    first, _ = tools.execute("draft_sales_document", {**ASK, "document_type": "quote"}, ctx)
    confirm.confirm(first["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    assert out["status"] == "nothing_to_finalise"


def test_finalise_by_number_checks_the_record(finalise_on):
    finalise_on.list_rows = [{"id": 881, "number": "INV-1048", "income_type": "INVOICE", "contact_name": "Al Noor Trading LLC",
                              "date": "2026-11-05", "is_draft": True, "total_value": 13200.0, "converted_to_number": None}]
    finalise_on.records = {881: _record()}
    out, ok = tools.execute("finalise_sales_invoice", {"invoice_number": "INV-1048"}, ctx_for())
    assert ok and out["status"] == "finalise_ready"
    finalise_on.records[881] = _record(is_draft=False)
    out, _ = tools.execute("finalise_sales_invoice", {"invoice_number": "INV-1048"}, ctx_for())
    assert out["status"] == "not_a_draft"
    finalise_on.records[881] = _record(is_posted_to_accounting=True)
    out, _ = tools.execute("finalise_sales_invoice", {"invoice_number": "INV-1048"}, ctx_for())
    assert out["status"] == "not_a_draft"
    finalise_on.records[881] = _record(organization_id=99)
    out, _ = tools.execute("finalise_sales_invoice", {"invoice_number": "INV-1048"}, ctx_for())
    assert out["status"] == "source_not_found" and "99" not in json.dumps(out)
    out, _ = tools.execute("finalise_sales_invoice", {"invoice_number": "INV-9999"}, ctx_for())
    assert out["status"] == "source_not_found"


# ── confirm ─────────────────────────────────────────────────

def test_finalise_confirm_rereads_the_invoice(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    finalise_on.records[881]["is_draft"] = False                          # finalised in Accutax meanwhile
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    assert done.status == "failed" and "no longer a draft" in done.error["message"]
    assert not getattr(finalise_on, "finalised", [])


def test_finalise_server_error_is_outcome_unknown(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    finalise_on.finalise_response = lambda: httpx.Response(500, json={"message": "journal failed"})
    done = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    assert done.status == "failed" and "Check Accutax" in done.error["message"]
    with pytest.raises(pending.ActionStateError):                          # a second click cannot post twice
        confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)


def test_finalise_refusal_in_body_can_retry(finalise_on):
    ctx = ctx_for()
    _saved_invoice(finalise_on, ctx)
    out, _ = tools.execute("finalise_sales_invoice", {}, ctx)
    finalise_on.finalise_response = lambda: httpx.Response(200, json={"error": False, "code": 400,
                                                                      "message": "Invoice is already finalised.", "data": {}})
    back = confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=ctx.accutax)
    assert back.status == "pending" and "already finalised" in back.error["message"]
