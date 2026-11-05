"""Phase 1 of invoices and quotes by chat: totals as the form computes them, customer and item matching, the
draft_sales_document tool, the save (confirm) and the card routes. Accutax runs on an httpx mock transport."""
import json

import httpx
import pytest
from fastapi import HTTPException

from gemini_brain.accutax_api.client import AccutaxClient, AccutaxCredential
from gemini_brain.actions import pending
from gemini_brain.agent import prompt, tools
from gemini_brain.api import action_routes
from gemini_brain.api.auth import CurrentUser
from gemini_brain.config.settings import settings
from gemini_brain.invoicing import confirm, draft, lookups, totals
from gemini_brain.semantic.catalog import ToolInputError

def _fake_jwt(org):
    """Accutax's own tokens name an organization; only that claim is read."""
    import base64

    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'HS256'})}.{part({'userId': 501, 'organization_id': org})}.sig"


TOKEN = _fake_jwt(24)


# ── totals: parity with InvoiceLineItems.jsx calculateLineAmount ──

def js_round2(n):
    """What the browser does: Math.round(n * 100) / 100 (Math.round = floor(x + 0.5))."""
    import math
    return math.floor(n * 100 + 0.5) / 100


@pytest.mark.parametrize("qty,price,pct,amt,rate,net,tax,line", [
    (40, 300, 0, 0, 5, 12000.0, 600.0, 12600.0),
    (45, 300, 10, 0, 5, 12150.0, 607.5, 12757.5),
    (3, 33.33, 0, 0, 5, 99.99, 5.0, 104.99),        # 4.9995 rounds up like the browser
    (1, 1.005, 0, 0, 0, 1.0, 0.0, 1.0),            # float 1.005*100 = 100.49999: the browser gives 1.00
    (7, 12.5, 15, 2.25, 5, 72.13, 3.61, 75.74),    # 72.125 rounds up in the browser too
    (3, 0.1, 0, 0, 5, 0.3, 0.02, 0.32),
    (1, 2.675, 0, 0, 5, 2.68, 0.13, 2.81),
    (2, 100, 0, 0, None, 200.0, 0.0, 200.0),       # no tax rate on the line
])
def test_line_totals_match_form(qty, price, pct, amt, rate, net, tax, line):
    t = totals.line_totals(totals.Line(qty, price, pct, amt, rate))
    assert (t.net, t.tax, t.line_total) == (net, tax, line)


def test_document_totals_match_form():
    lines = [totals.line_totals(totals.Line(40, 300, 0, 0, 5)), totals.line_totals(totals.Line(3, 33.33, 0, 0, 5))]
    doc = totals.document_totals(lines)
    gross, tax = 12600.0 + 104.99, 600.0 + 5.0
    assert doc.total == js_round2(gross) and doc.vat == js_round2(tax) and doc.subtotal == js_round2(gross - tax)


@pytest.mark.parametrize("line", [totals.Line(0, 10), totals.Line(1, -1), totals.Line(1, 10, 120),
                                  totals.Line(1, 10, 0, 11), totals.Line(1, 10, 0, 0, 150)])
def test_bad_lines_refused(line):
    with pytest.raises(totals.TotalsError):
        totals.line_totals(line)


# ── matching ────────────────────────────────────────────────

POOL = [lookups.Customer(1, "Al Noor Trading LLC", trn="100234567800003"),
        lookups.Customer(2, "Desert Rose General Trading"), lookups.Customer(3, "Desert Rose Interiors"),
        lookups.Customer(4, "", organization_name="Gulf Print House")]


@pytest.mark.parametrize("text,expect", [("al noor trading", 1), ("Al Noor", 1), ("AL-NOOR TRADING L.L.C.", 1),
                                         ("gulf print house", 4), ("Desert Rose Interiors", 3)])
def test_customer_found(text, expect):
    assert lookups.match_customer(text, POOL).found.id == expect


def test_customer_ambiguous_and_missing():
    m = lookups.match_customer("Desert Rose", POOL)
    assert m.found is None and {c.id for c in m.candidates} == {2, 3}
    m = lookups.match_customer("Zenith Labs", POOL)
    assert m.found is None and not m.candidates
    assert lookups.match_customer("", POOL, customer_id=3).found.id == 3
    assert lookups.match_customer("x", POOL, customer_id=99).found is None


def test_item_exact_only():
    pool = [lookups.Item(10, "Consulting", 300.0, 400, 7), lookups.Item(11, "Consulting - senior", 450.0, 400, 7)]
    assert lookups.match_item("consulting", pool).id == 10
    assert lookups.match_item("consult", pool) is None


RATES = [lookups.TaxRate(7, "VAT 5%", 5.0, "standard", "sales", False),
         lookups.TaxRate(8, "Zero rated", 0.0, "zero_rated", "sales", False),
         lookups.TaxRate(9, "Exempt", 0.0, "exempt", "exempt", False),
         lookups.TaxRate(12, "Input VAT", 5.0, "standard", "purchase", True)]


def test_pick_rate():
    assert lookups.pick_rate(RATES).id == 7            # purchase default ignored for sales
    assert lookups.pick_rate(RATES, "zero_rated").id == 8
    assert lookups.pick_rate(RATES, "exempt").id == 9
    assert lookups.pick_rate(RATES, "out_of_scope") is None
    assert lookups.pick_rate(RATES, None, 8).id == 8   # the item's own rate
    assert lookups.pick_rate([]) is None


def test_unwrap_body_errors():
    assert lookups.unwrap({"error": False, "code": 200, "data": {"id": 1}}) == {"id": 1}
    with pytest.raises(lookups.AccutaxApiError) as e:
        lookups.unwrap({"error": False, "code": 400, "message": "organization_id or user_id is required"})
    assert e.value.kind == "validation" and "organization_id" in e.value.user_message
    with pytest.raises(lookups.AccutaxApiError) as e:
        lookups.unwrap({"error": True, "code": 403, "message": "no"})
    assert e.value.kind == "permission"


# ── a fake Accutax ──────────────────────────────────────────

CONTACTS = [{"id": 1, "name": "Al Noor Trading LLC", "trn_number": "100234567800003", "email": "ap@alnoor.ae",
             "currency": "AED", "contact_type_id": 4},
            {"id": 2, "name": "Desert Rose General Trading", "contact_type_id": 4},
            {"id": 3, "name": "Desert Rose Interiors", "contact_type_id": 4}]
ITEMS = [{"id": 10, "name": "Consulting", "unit_price": "300.00", "revenue_account_id": 400, "revenue_tax_rate_id": 7}]
TAX = [{"id": 7, "name": "VAT 5%", "tax_rate": 5, "tax_type": "standard", "rate_type": "sales", "is_active": True,
        "is_default": True},
       {"id": 8, "name": "Zero", "tax_rate": 0, "tax_type": "zero_rated", "rate_type": "sales", "is_active": True}]


class FakeAccutax:
    def __init__(self):
        self.calls = []
        self.bodies = []
        self.tax_rates = None    # override the tax rates (a list of dicts)
        self.list_rows = []      # income/list_filter rows (dicts)
        self.records = {}        # income/find: id -> record
        self.create_response = lambda body: httpx.Response(200, json={"error": False, "code": 200, "data": {
            "id": 881, "invoice_number": body["invoice_number"]}})

    def handler(self, request):
        path = request.url.path
        self.calls.append((request.method, path, dict(request.url.params), request.headers.get("authorization")))
        ok = lambda data: httpx.Response(200, json={"error": False, "code": 200, "data": data})
        if path == "/contact/list_filter":
            return ok({"data": CONTACTS, "totalCount": len(CONTACTS)})
        if path == "/item/list":
            return ok(ITEMS)
        if path == "/tax-rate/list":
            # Accutax pages this list 10 at a time unless pageSize is sent (a limit parameter is ignored)
            size = int(request.url.params.get("pageSize", 10))
            return ok((self.tax_rates if self.tax_rates is not None else TAX)[:size])
        if path.startswith("/income/invoice_setting/"):
            return ok({"suggested_invoice_number": "INV-1048", "due_date_days_after": 30, "custom_columns": []})
        if path == "/income/document-number/QUOTE":
            return ok({"prefix": "QUO-", "next_number": 12, "suggested_number": "QUO-0012"})
        if path == "/income/create":
            return self.create_response(json.loads(request.content))
        if path == "/income/list_filter":
            q = request.url.params
            rows = [r for r in self.list_rows if r["income_type"] == q.get("income_type")
                    and (not q.get("number") or q["number"].lower() in r["number"].lower())
                    and (not q.get("contact_name") or q["contact_name"].lower() in r["contact_name"].lower())]
            return ok({"data": rows, "pagination": {"totalItems": len(rows)}})
        if path == "/income/find":
            record = self.records.get(int(request.url.params["income_id"]))
            if record is None:
                return httpx.Response(200, json={"error": False, "code": 400, "message": "Cannot find income", "data": {}})
            return ok(record)
        if path == "/income/update":
            self.bodies.append(json.loads(request.content))
            return ok({"income_id": self.bodies[-1]["income_id"]})
        if path.startswith("/income/finalise/"):
            self.finalised = getattr(self, "finalised", []) + [int(path.rsplit("/", 1)[1])]
            return getattr(self, "finalise_response", lambda: ok({"income_id": self.finalised[-1]}))()
        if path == "/contact/create":
            body = json.loads(request.content)
            self.bodies.append(body)
            # the contacts table's NOT NULL columns, which Accutax's create code leaves unset when a block omits them
            missing = [k for k in ("image_url", "last_name", "phone_number") if body["base"].get(k) is None]
            missing += [k for k in ("trn_number", "website") if (body.get("personal") or {}).get(k) is None]
            if missing:
                return httpx.Response(500, json={"message": f"null value in column {missing[0]} violates not-null constraint"})
            return httpx.Response(201, json={"id": 77, "name": body["base"]["name"]})
        return httpx.Response(404, json={"message": "nope"})

    def client(self, credential, org):
        return AccutaxClient(credential, org, base_url="http://accutax.test", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def accutax(monkeypatch, tmp_path):
    fake = FakeAccutax()
    monkeypatch.setattr(draft, "client_for", fake.client)
    monkeypatch.setattr(confirm, "client_for", fake.client)
    from gemini_brain.invoicing import customer as customer_tool, duplicate
    monkeypatch.setattr(duplicate, "client_for", fake.client)
    monkeypatch.setattr(customer_tool, "client_for", fake.client)
    monkeypatch.setattr(settings, "pending_action_dir", str(tmp_path / "pending"))
    monkeypatch.setattr(settings, "agent_audit_log", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(settings, "accutax_app_url", "https://app.accutax.test")
    monkeypatch.setattr(settings, "ai_write_actions_enabled", True)
    monkeypatch.setattr(settings, "ai_write_users", "")
    monkeypatch.setattr(settings, "ai_write_tools", "")
    monkeypatch.setattr(draft, "_today", lambda: __import__("datetime").date(2026, 11, 5))
    pending.reset_dir_cache()
    yield fake
    pending.reset_dir_cache()


def _ctx(orgs=(24,), session="s1"):
    return tools.ToolContext(organization_ids=list(orgs), subject="t", deadline=1e18, user_id=501, session_id=session,
                             accutax=AccutaxCredential(TOKEN), write_actions=True)


ASK = {"document_type": "invoice", "customer": "Al Noor Trading",
       "lines": [{"description": "Consulting", "quantity": 40, "unit_price": 300}]}


# ── the tool ────────────────────────────────────────────────

def test_draft_ready_and_card(accutax):
    ctx = _ctx()
    out, ok = tools.execute("draft_sales_document", ASK, ctx)
    assert ok and out["status"] == "draft_ready"
    assert (out["subtotal"], out["vat"], out["total"]) == ("12,000.00", "600.00", "12,600.00")
    assert out["suggested_number"] == "INV-1048" and out["due_date"] == "2026-12-05"
    text = json.dumps(out)
    assert "100234567800003" not in text and "ap@alnoor.ae" not in text   # no TRN or email to the model
    assert len(ctx.cards) == 1 and ctx.cards[0]["type"] == "sales_document_draft"
    action = pending.get_for(out["draft_id"], user_id=501)
    line = action.payload["line_items"][0]
    assert action.payload["contact_id"] == 1 and action.payload["is_draft"] is True
    assert (line["items_id"], line["account_id"], line["tax_rate_id"]) == (10, 400, 7)
    assert (line["tax_amount"], line["line_amount"]) == (600.0, 12000.0)    # the net: Accutax adds the tax itself
    assert action.view["customer"]["trn"] == "100234567800003"         # the card may show it
    assert not any(c[0] == "POST" for c in accutax.calls)              # preparing never writes
    assert all(c[3] == f"Bearer {TOKEN}" for c in accutax.calls)


def test_quote_number_and_price_from_item(accutax):
    out, ok = tools.execute("draft_sales_document", {
        "document_type": "quote", "customer": "Al Noor", "lines": [{"description": "consulting", "quantity": 2}]},
        _ctx())
    assert ok and out["document_type"] == "Quote" and out["suggested_number"] == "QUO-0012"
    assert out["total"] == "630.00"


def test_ambiguous_customer_prepares_nothing(accutax):
    ctx = _ctx()
    out, ok = tools.execute("draft_sales_document", {**ASK, "customer": "Desert Rose"}, ctx)
    assert ok and out["status"] == "choose_customer"
    assert {c["customer_id"] for c in out["candidates"]} == {2, 3}
    assert not ctx.cards
    out, ok = tools.execute("draft_sales_document", {**ASK, "customer": "Desert Rose", "customer_id": 3}, ctx)
    assert ok and out["status"] == "draft_ready" and out["customer"] == "Desert Rose Interiors"


def test_unknown_customer_and_missing_price(accutax):
    out, _ = tools.execute("draft_sales_document", {**ASK, "customer": "Zenith Labs"}, _ctx())
    assert out["status"] == "customer_not_found"
    out, ok = tools.execute("draft_sales_document", {**ASK, "lines": [{"description": "Design work", "quantity": 1}]},
                            _ctx())
    assert not ok and "price" in out["error"]


def test_several_orgs_need_one(accutax):
    out, ok = tools.execute("draft_sales_document", ASK, _ctx(orgs=(24, 25)))
    assert not ok and "organization" in out["error"].lower()
    out, ok = tools.execute("draft_sales_document", {**ASK, "organization_id": 26}, _ctx(orgs=(24, 25)))
    assert not ok and "not selected" in out["error"]
    out, ok = tools.execute("draft_sales_document", {**ASK, "organization_id": 25}, _ctx(orgs=(24, 25)))
    assert ok and pending.get_for(out["draft_id"], user_id=501).organization_id == 25


def test_change_earlier_draft_withdraws_it(accutax):
    ctx = _ctx()
    first, _ = tools.execute("draft_sales_document", ASK, ctx)
    second, ok = tools.execute("draft_sales_document", {
        **ASK, "changes_earlier_draft": True,
        "lines": [{"description": "Consulting", "quantity": 45, "unit_price": 300, "discount_percent": 10}]}, _ctx())
    assert ok and second["total"] == "12,757.50"
    assert pending.get_for(first["draft_id"], user_id=501).status == "discarded"
    assert pending.get_for(second["draft_id"], user_id=501).status == "pending"


def test_zero_rated_warns(accutax):
    out, ok = tools.execute("draft_sales_document", {**ASK, "lines": [
        {"description": "Consulting", "quantity": 1, "unit_price": 1000, "vat": "zero_rated"}]}, _ctx())
    assert ok and out["vat"] == "0.00" and any("qualifies" in w for w in out["warnings"])


def test_tool_not_offered_without_writes(accutax, monkeypatch):
    ctx = _ctx()
    ctx.write_actions = False
    out, ok = tools.execute("draft_sales_document", ASK, ctx)
    assert not ok and "not available" in out["error"]
    monkeypatch.setattr(settings, "ai_write_actions_enabled", False)
    assert not tools.writes_offered(_ctx())


def test_prompt_rules_only_with_writes():
    base = dict(org_meta={24: {"name": "Org"}}, organization_ids=[24], today=__import__("datetime").date(2026, 11, 5),
                timezone="Asia/Dubai")
    assert "Save as draft" in prompt.system_prompt(**base, writes=True)
    assert "Save as draft" not in prompt.system_prompt(**base)


# ── confirm ─────────────────────────────────────────────────

def _prepared(accutax):
    out, _ = tools.execute("draft_sales_document", ASK, _ctx())
    return out["draft_id"]


def test_confirm_saves_once(accutax):
    aid = _prepared(accutax)
    done = confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN), expected_version=1)
    assert done.status == "done"
    assert done.result == {"record_id": 881, "number": "INV-1048", "url": "https://app.accutax.test/income/details/881"}
    posts = [c for c in accutax.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][2] == {"sync": "1"}
    with pytest.raises(pending.ActionStateError):
        confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))
    assert len([c for c in accutax.calls if c[0] == "POST"]) == 1


def test_confirm_refused_can_retry(accutax):
    aid = _prepared(accutax)
    accutax.create_response = lambda body: httpx.Response(400, json={"message": ["invoice_number already exists"]})
    back = confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))
    assert back.status == "pending" and "already exists" in back.error["message"]
    accutax.create_response = lambda body: httpx.Response(200, json={"error": False, "code": 200,
                                                                     "data": {"id": 9, "invoice_number": "INV-1049"}})
    assert confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN)).status == "done"


def test_confirm_timeout_blocks_second_save(accutax):
    aid = _prepared(accutax)

    def slow(body):
        raise httpx.ReadTimeout("slow")
    accutax.create_response = slow
    out = confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))
    assert out.status == "failed" and "Check Accutax" in out.error["message"]
    with pytest.raises(pending.ActionStateError):
        confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))


def test_confirm_error_in_body(accutax):
    aid = _prepared(accutax)
    accutax.create_response = lambda body: httpx.Response(200, json={"error": True, "code": 403, "message": "No"})
    out = confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))
    assert out.status == "pending" and out.error["kind"] == "permission"


def test_audit_has_no_customer_or_amount(accutax, tmp_path):
    aid = _prepared(accutax)
    confirm.confirm(aid, user_id=501, organization_id=24, credential=AccutaxCredential(TOKEN))
    log = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    steps = [json.loads(l)["step"] for l in log.splitlines()]
    assert steps == ["prepared", "confirm_started", "confirmed"]
    assert "Al Noor" not in log and "12600" not in log and TOKEN not in log


# ── routes ──────────────────────────────────────────────────

def _user(uid=501, orgs=(24,), token=TOKEN):
    return CurrentUser(user_id=uid, email="x@test.com", allowed_org_ids=list(orgs), accutax_token=token)


def test_routes_owner_only(accutax):
    aid = _prepared(accutax)
    assert action_routes.get_action(aid, _user())["block"]["status"] == "pending"
    for user in (_user(uid=777), _user(orgs=(25,))):
        with pytest.raises(HTTPException) as e:
            action_routes.get_action(aid, user)
        assert e.value.status_code == 404
    with pytest.raises(HTTPException) as e:
        action_routes.get_action("../etc", _user())
    assert e.value.status_code == 404


def test_route_confirm_and_discard(accutax):
    import asyncio
    aid = _prepared(accutax)
    with pytest.raises(HTTPException) as e:
        asyncio.run(action_routes.confirm_action(aid, action_routes.ConfirmRequest(version=1), _user(token="")))
    assert e.value.status_code == 401
    with pytest.raises(HTTPException) as e:
        asyncio.run(action_routes.confirm_action(aid, action_routes.ConfirmRequest(version=5), _user()))
    assert e.value.status_code == 409
    saved = asyncio.run(action_routes.confirm_action(aid, action_routes.ConfirmRequest(version=1), _user()))
    assert saved["block"]["status"] == "done" and saved["block"]["result"]["number"] == "INV-1048"
    with pytest.raises(HTTPException) as e:
        action_routes.discard_action(aid, _user())
    assert e.value.status_code == 409
    other = _prepared(accutax)
    assert action_routes.discard_action(other, _user())["block"]["status"] == "discarded"


def test_route_confirm_switched_off(accutax, monkeypatch):
    import asyncio
    aid = _prepared(accutax)
    monkeypatch.setattr(settings, "ai_write_actions_enabled", False)
    with pytest.raises(HTTPException) as e:
        asyncio.run(action_routes.confirm_action(aid, action_routes.ConfirmRequest(), _user()))
    assert e.value.status_code == 403
