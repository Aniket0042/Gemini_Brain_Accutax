"""Found in the quote test on the VM: the model did not know a card had been saved, a slow customer list was fetched
on every call, and a slow organization list was retried until the turn ran out of time."""
import time

import httpx

from gemini_brain.accutax_api.client import AccutaxClient, AccutaxCredential
from gemini_brain.agent import prompt
from gemini_brain.invoicing import confirm, lookups, state
from gemini_brain.agent import tools
from tests.unit.test_sales_draft import ASK, TOKEN, _ctx, accutax  # noqa: F401  (accutax is a fixture)

CRED = AccutaxCredential(TOKEN)


# ── the cards of this chat, for the model ───────────────────

def test_state_is_empty_without_cards(accutax):
    assert state.describe(_ctx()) == ""


def test_state_tells_what_happened(accutax):
    ctx = _ctx()
    first, _ = tools.execute("draft_sales_document", {**ASK, "document_type": "quote"}, ctx)
    text = state.describe(ctx)
    assert "Quote (organization 24): prepared, waiting for the person to click its button" in text
    confirm.confirm(first["draft_id"], user_id=501, organization_id=24, credential=CRED)
    text = state.describe(ctx)
    assert "Quote (organization 24): saved in Accutax as a draft, number QUO-0012" in text
    second, _ = tools.execute("draft_sales_document", ASK, ctx)
    from gemini_brain.invoicing import confirm as c
    c.discard(second["draft_id"], user_id=501)
    text = state.describe(ctx)
    assert "Invoice (organization 24): withdrawn" in text
    # no names and no amounts reach the prompt
    assert "Al Noor" not in text and "12,600" not in text and "600" not in text.replace("organization 24", "")


def test_state_failed_save_says_check_accutax(accutax):
    ctx = _ctx()
    out, _ = tools.execute("draft_sales_document", ASK, ctx)
    accutax.create_response = lambda body: httpx.Response(504, json={"message": "gateway"})
    confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    assert "outcome unknown: the person must check Accutax" in state.describe(ctx)


def test_state_is_per_chat(accutax):
    tools.execute("draft_sales_document", ASK, _ctx(session="chat-a"))
    assert state.describe(_ctx(session="chat-b")) == ""


def test_prompt_carries_the_state_and_the_rate_rules():
    base = dict(org_meta={24: {"name": "Org"}}, organization_ids=[24], today=__import__("datetime").date(2026, 11, 5),
                timezone="Asia/Dubai", writes=True)
    text = prompt.system_prompt(**base, write_state="\nCards of this chat (what really happened):\n- Quote: saved\n")
    assert "- Quote: saved" in text and "never refuse" in text and "do not search the law" in text
    assert "Cards of this chat" not in prompt.system_prompt(**{**base, "writes": False}, write_state="x")


# ── speed ───────────────────────────────────────────────────

def test_customers_are_fetched_once_for_two_minutes(accutax):
    tools.execute("draft_sales_document", ASK, _ctx())
    tools.execute("draft_sales_document", ASK, _ctx())
    assert len([c for c in accutax.calls if c[1] == "/contact/list_filter"]) == 1
    lookups.reset_cache(24)
    tools.execute("draft_sales_document", ASK, _ctx())
    assert len([c for c in accutax.calls if c[1] == "/contact/list_filter"]) == 2


def test_a_created_customer_is_seen_at_once(accutax):
    ctx = _ctx()
    tools.execute("draft_sales_document", ASK, ctx)                     # fills the cache
    out, _ = tools.execute("create_customer", {"name": "Zenith Labs", "email": "a@zenith.ae"}, ctx)
    confirm.confirm(out["draft_id"], user_id=501, organization_id=24, credential=CRED)
    before = len([c for c in accutax.calls if c[1] == "/contact/list_filter"])
    tools.execute("draft_sales_document", ASK, ctx)
    assert len([c for c in accutax.calls if c[1] == "/contact/list_filter"]) == before + 1


def test_the_slow_document_list_is_tried_once_and_briefly():
    seen = []

    def handler(request):
        seen.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("slow", request=request)

    client = AccutaxClient(CRED, 24, base_url="http://accutax.test", transport=httpx.MockTransport(handler))
    from gemini_brain.invoicing import existing
    started = time.monotonic()
    try:
        existing.list_documents(client, 501, "QUOTE", number="QUO-1")
    except Exception as e:
        assert getattr(e, "kind", "") == "unavailable"
    assert len(seen) == 1                                               # no retry
    assert seen[0]["read"] == existing.LIST_TIMEOUT_SECONDS
    assert time.monotonic() - started < 2
