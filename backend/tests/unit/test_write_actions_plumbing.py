"""Phase 0 of invoices and quotes by chat: the Accutax client, the write switches, the pending store, the audit
line and the bearer's path from the route to the tool context. No network: httpx runs on a mock transport."""
import json
import threading

import httpx
import pytest

from gemini_brain import actions
from gemini_brain.accutax_api.client import AccutaxApiError, AccutaxClient, AccutaxCredential, client_for, json_of
from gemini_brain.actions import audit as action_audit
from gemini_brain.actions import pending
from gemini_brain.agent import preview, tools
from gemini_brain.api import routes
from gemini_brain.api.auth import CurrentUser
from gemini_brain.config.settings import settings

TOKEN = "secret-accutax-bearer-123"


# ── credential ──────────────────────────────────────────────

def test_credential_hides_token():
    cred = AccutaxCredential.of(f"Bearer {TOKEN}")
    assert cred.token == TOKEN
    assert TOKEN not in repr(cred) and TOKEN not in str(cred)
    assert AccutaxCredential.of("") is None and AccutaxCredential.of("  ") is None


def test_tool_context_repr_hides_token():
    ctx = tools.ToolContext(organization_ids=[24], subject="t", deadline=0.0, accutax=AccutaxCredential(TOKEN))
    assert TOKEN not in repr(ctx)


# ── client ──────────────────────────────────────────────────

def _client(handler, org=24):
    return AccutaxClient(AccutaxCredential(TOKEN), org, base_url="http://accutax.test",
                         transport=httpx.MockTransport(handler))


def test_client_sends_bearer_and_org():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["org"] = request.headers["x-organization-id"]
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"data": {"id": 9}})

    with _client(handler) as c:
        assert json_of(c.get("contact/list_filter", {"search": "Noor"})) == {"id": 9}
    assert seen == {"auth": f"Bearer {TOKEN}", "org": "24",
                    "url": "http://accutax.test/contact/list_filter?search=Noor"}


@pytest.mark.parametrize("status,kind", [(401, "auth"), (403, "permission"), (404, "not_found"),
                                         (400, "validation"), (422, "validation"), (409, "conflict"),
                                         (500, "error"), (503, "unavailable")])
def test_client_maps_status(status, kind):
    with _client(lambda r: httpx.Response(status, json={"message": ["contact_id must be a number"]})) as c:
        with pytest.raises(AccutaxApiError) as e:
            c.post("income/create", {})
    assert e.value.kind == kind
    if kind == "validation":
        assert "contact_id must be a number" in e.value.user_message
    if status >= 500:
        assert e.value.detail == ""  # a 5xx message never reaches the person


def test_get_retried_once_post_never():
    calls = {"GET": 0, "POST": 0}

    def handler(request):
        calls[request.method] += 1
        return httpx.Response(503)

    with _client(handler) as c:
        with pytest.raises(AccutaxApiError):
            c.get("item/list")
        with pytest.raises(AccutaxApiError):
            c.post("income/create", {})
    assert calls == {"GET": 2, "POST": 1}


def test_post_timeout_is_not_reported_as_unavailable():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with _client(handler) as c:
        with pytest.raises(AccutaxApiError) as e:
            c.post("income/create", {})
    assert e.value.kind == "error"  # outcome unknown: never "nothing was changed"


def test_client_without_bearer_refused():
    with pytest.raises(AccutaxApiError) as e:
        client_for(None, 24)
    assert e.value.kind == "auth"


# ── switches ────────────────────────────────────────────────

def test_write_switches(monkeypatch):
    monkeypatch.setattr(settings, "ai_write_actions_enabled", False)
    assert not actions.enabled_for(1, "a@x.com") and not actions.tool_enabled("draft_sales_document")
    monkeypatch.setattr(settings, "ai_write_actions_enabled", True)
    monkeypatch.setattr(settings, "ai_write_users", "")
    monkeypatch.setattr(settings, "ai_write_tools", "")
    assert actions.enabled_for(1) and actions.tool_enabled("draft_sales_document")
    assert not actions.tool_enabled("query_metrics")  # not a write tool
    monkeypatch.setattr(settings, "ai_write_users", "A@X.com, 7")
    assert actions.enabled_for(99, "a@x.com") and actions.enabled_for(7) and not actions.enabled_for(8, "b@x.com")
    monkeypatch.setattr(settings, "ai_write_tools", "draft_sales_document")
    assert actions.tool_enabled("draft_sales_document") and not actions.tool_enabled("convert_quote_to_invoice")


def test_write_tool_gate_in_tools(monkeypatch):
    monkeypatch.setattr(settings, "ai_write_actions_enabled", True)
    monkeypatch.setattr(settings, "ai_write_tools", "")
    off = tools.ToolContext(organization_ids=[24], subject="t", deadline=0.0)
    on = tools.ToolContext(organization_ids=[24], subject="t", deadline=0.0,
                           accutax=AccutaxCredential(TOKEN), write_actions=True)
    assert tools._write_allowed("search_vat_kb", off)
    assert not tools._write_allowed("draft_sales_document", off)
    assert tools._write_allowed("draft_sales_document", on)
    result, ok = tools.execute("draft_sales_document", {}, off)
    assert not ok and "not available" in result["error"]


# ── bearer path ─────────────────────────────────────────────

def test_write_access(monkeypatch):
    monkeypatch.setattr(settings, "ai_write_actions_enabled", True)
    monkeypatch.setattr(settings, "ai_write_users", "")
    got = preview._write_access(TOKEN, 501, "x@test.com")
    assert got["write_actions"] is True and got["accutax_credential"].token == TOKEN
    assert preview._write_access("", 501, "x@test.com") == {"accutax_credential": None, "write_actions": False}
    monkeypatch.setattr(settings, "ai_write_actions_enabled", False)
    assert preview._write_access(TOKEN, 501, "x@test.com")["write_actions"] is False


def test_write_identity_uses_accutax_bearer_only():
    user = CurrentUser(user_id=501, email="x@test.com", allowed_org_ids=[24], raw_token="our-jwt", accutax_token="")
    assert routes._write_identity(user) == {"accutax_token": "", "user_email": "x@test.com"}  # never our own JWT
    user.accutax_token = TOKEN
    assert routes._write_identity(user)["accutax_token"] == TOKEN
    assert routes._write_identity(user, external=True)["accutax_token"] == ""  # MCP: no writes yet


# ── pending store ───────────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "pending_action_dir", str(tmp_path))
    monkeypatch.setattr(settings, "pending_action_ttl_hours", 24)
    pending.reset_dir_cache()
    yield tmp_path
    pending.reset_dir_cache()


def _new(**kw):
    args = dict(user_id=501, organization_id=24, session_id="s1", payload={"income_type": "INVOICE"},
                view={"total": "12600.00"})
    args.update(kw)
    return pending.create("sales_document", **args)


def test_owner_only(store):
    a = _new()
    assert pending.valid_id(a.id)
    assert pending.get_for(a.id, user_id=501).payload == {"income_type": "INVOICE"}
    assert pending.get_for(a.id, user_id=777) is None
    assert pending.get_for(a.id, user_id=501, organization_id=25) is None
    assert pending.get_for("../../etc/passwd", user_id=501) is None
    assert "user_id" not in a.public() and "payload" not in a.public()


def test_update_and_stale_version(store):
    a = _new()
    b = pending.update(a.id, user_id=501, payload={"income_type": "QUOTE"}, expected_version=1)
    assert b.version == 2
    with pytest.raises(pending.ActionStateError) as e:
        pending.update(a.id, user_id=501, payload={}, expected_version=1)
    assert e.value.status == "stale"
    with pytest.raises(pending.ActionStateError):
        pending.update(a.id, user_id=777, payload={})


def test_claim_once_then_finish(store):
    a = _new()
    pending.claim(a.id, user_id=501, organization_id=24)
    with pytest.raises(pending.ActionStateError) as e:
        pending.claim(a.id, user_id=501, organization_id=24)
    assert e.value.status == "confirming"
    done = pending.finish(a.id, result={"income_id": 88, "number": "INV-1048"})
    assert done.status == "done" and done.public()["result"]["number"] == "INV-1048"
    with pytest.raises(pending.ActionStateError):
        pending.update(a.id, user_id=501, payload={})


def test_claim_other_org_refused(store):
    a = _new()
    with pytest.raises(pending.ActionStateError) as e:
        pending.claim(a.id, user_id=501, organization_id=25)
    assert e.value.status == "not_found"


def test_fail_retry_and_unknown(store):
    a = _new()
    pending.claim(a.id, user_id=501, organization_id=24)
    back = pending.fail(a.id, kind="validation", message="bad", retry=True)
    assert back.status == "pending" and back.error["kind"] == "validation"
    pending.claim(a.id, user_id=501, organization_id=24)
    stuck = pending.fail(a.id, kind="error", message="timed out", retry=False)
    assert stuck.status == "failed"
    with pytest.raises(pending.ActionStateError):
        pending.claim(a.id, user_id=501, organization_id=24)


def test_concurrent_claims_one_wins(store):
    a = _new()
    wins, losses = [], []

    def go():
        try:
            pending.claim(a.id, user_id=501, organization_id=24)
            wins.append(1)
        except pending.ActionStateError:
            losses.append(1)

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(wins) == 1 and len(losses) == 7


def test_expiry_and_sweep(store):
    a = _new(now=1000.0)
    late = 1000.0 + 25 * 3600
    assert pending.get_for(a.id, user_id=501).expired(late)
    with pytest.raises(pending.ActionStateError) as e:
        pending.claim(a.id, user_id=501, organization_id=24, now=late)
    assert e.value.status == "expired"
    assert pending.sweep(now=late) == [a.id]
    assert pending.get_for(a.id, user_id=501) is None


def test_discard(store):
    a = _new()
    assert pending.discard(a.id, user_id=501).status == "discarded"
    assert pending.discard(a.id, user_id=501).status == "discarded"  # twice is fine
    with pytest.raises(pending.ActionStateError):
        pending.claim(a.id, user_id=501, organization_id=24)


# ── audit ───────────────────────────────────────────────────

def test_audit_line_has_hash_not_payload(tmp_path, monkeypatch):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "agent_audit_log", str(log))
    payload = {"contact_name": "Al Noor Trading", "total": "12600.00"}
    action_audit.step("prepared", action_id="act_x", kind="sales_document", user_id=501, organization_id=24,
                      payload=payload, document_type="INVOICE")
    line = json.loads(log.read_text(encoding="utf-8").strip())
    assert line["kind"] == "write_action" and line["step"] == "prepared"
    assert line["payload_sha256"] == action_audit.payload_hash(payload)
    assert "Al Noor" not in log.read_text(encoding="utf-8") and "12600" not in log.read_text(encoding="utf-8")
