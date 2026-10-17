"""PII plan phase 2: placeholders before the model, real values back after it, with Bedrock faked."""
import json

import pytest

from gemini_brain.agent import loop
from gemini_brain.privacy import dictionary, masking, policy, sentinel
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault

ORGS = [24, 25]
META = {24: {"name": "Zenith Foods Org", "currency": "AED"}, 25: {"name": "Delta Builders Org", "currency": "AED"}}
SUPPLIER = "Al Noor Trading L.L.C"
TRN = "100234567800003"


def _names(*extra):
    entries = [dictionary.Entry(SUPPLIER, "SUPPLIER", policy.normalize_name(SUPPLIER)),
               dictionary.Entry("Al Futtaim Motors LLC", "SUPPLIER", "al futtaim motors")]
    entries += [dictionary.Entry(n, "ORG", policy.normalize_name(n)) for n in extra]
    return dictionary.Dictionary(entries)


# ── Vault ────────────────────────────────────────────────────────────────────

def test_one_value_one_placeholder_in_any_spelling():
    v = Vault()
    assert v.token("SUPPLIER", "Al Noor Trading LLC") == "[SUPPLIER_1]"
    assert v.token("CUSTOMER", "AL NOOR TRADING L.L.C") == "[SUPPLIER_1]"      # one party keeps its first kind
    assert v.token("TRN", "100 2345 6780 0003") == v.token("TRN", TRN) == "[TRN_1]"
    assert v.token("SUPPLIER", "Other Co Ltd") == "[SUPPLIER_2]"
    assert v.counts() == {"SUPPLIER": 2, "TRN": 1}


@pytest.mark.parametrize("written", ["[SUPPLIER_1]", "[Supplier_1]", "[SUPPLIER 1]", "SUPPLIER_1", "SUPPLIER-1",
                                     r"\[SUPPLIER_1\]"])
def test_rehydrate_accepts_usual_rewrites(written):
    v = Vault()
    v.token("SUPPLIER", "Al Noor Trading LLC")
    assert v.rehydrate(f"Pay {written} today.") == "Pay Al Noor Trading LLC today."


def test_rehydrate_leaves_words_and_counts_unknown_placeholders():
    v = Vault()
    v.token("SUPPLIER", "Al Noor Trading LLC")
    assert v.rehydrate("Supplier-1 in prose stays; [SUPPLIER_9] is unknown.") == \
        "Supplier-1 in prose stays; [SUPPLIER_9] is unknown."
    assert v.unresolved == 1
    assert v.rehydrate_json({"filters": [{"values": ["[SUPPLIER_1]"]}], "limit": 5}) == \
        {"filters": [{"values": ["Al Noor Trading LLC"]}], "limit": 5}


# ── Scrubber ─────────────────────────────────────────────────────────────────

def test_text_masks_names_and_identifiers_and_partial_names_only_when_asked():
    s = Scrubber(Vault(), _names())
    out = s.text(f"Is TRN {TRN} on AL NOOR TRADING LLC's bills valid? Mail a.b@alnoor.ae, ask futtaim.")
    assert out == "Is TRN [TRN_1] on [SUPPLIER_1]'s bills valid? Mail [EMAIL_1], ask futtaim."
    assert s.text("bills from futtaim", partial=True) == "bills from [SUPPLIER_2]"
    assert s.vault.rehydrate("[SUPPLIER_2]") == "Al Futtaim Motors LLC"           # back to the books' spelling


def test_json_follows_the_field_policy():
    s = Scrubber(Vault(), _names())
    row = {"purchases.vendor_name": SUPPLIER, "vendor_trn": int(TRN), "net": "18450.00", "vat": 922.5,
           "document_number": "INV-BULK-26-20241216180347-114231", "document_date": "2026-09-12",
           "line_description": f"Advance via {SUPPLIER}", "payee": "DEWA", "customer_name": "-",
           "organization_name": "Zenith Foods Org", "item_name": "Al Noor special"}
    out = s.json({"rows": [row], "row_count": 1})
    assert out["rows"][0] == {
        "purchases.vendor_name": "[SUPPLIER_1]", "vendor_trn": "[TRN_1]", "net": "18450.00", "vat": 922.5,
        "document_number": "INV-BULK-26-20241216180347-114231", "document_date": "2026-09-12",
        "line_description": "Advance via [SUPPLIER_1]", "payee": "DEWA", "customer_name": "-",
        "organization_name": "[ORG_1]", "item_name": "Al Noor special"}
    assert out["row_count"] == 1 and row["purchases.vendor_name"] == SUPPLIER          # the original is untouched


def test_message_masks_text_tool_inputs_and_results_but_not_files():
    s = Scrubber(Vault(), _names())
    pdf = {"document": {"format": "pdf", "name": "bill", "source": {"bytes": b"%PDF"}}}
    msg = {"role": "user", "content": [
        {"text": f"About {SUPPLIER}"}, pdf,
        {"toolResult": {"toolUseId": "t", "content": [{"json": {"vendor_name": SUPPLIER}}, {"text": f"TRN {TRN}"}]}}]}
    out = s.message(msg)
    assert out["content"][0]["text"] == "About [SUPPLIER_1]"
    assert out["content"][1] == pdf
    assert out["content"][2]["toolResult"]["content"] == [{"json": {"vendor_name": "[SUPPLIER_1]"}}, {"text": "TRN [TRN_1]"}]
    assert msg["content"][0]["text"] == f"About {SUPPLIER}"


# ── The agent loop ───────────────────────────────────────────────────────────

class FakeAdapter:
    script, step, sent = [], 0, []

    def __init__(self, model_id, label=""):
        self.model_id, self.calls = model_id, 0

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        FakeAdapter.sent.append({"system": system, "messages": json.loads(json.dumps(messages, default=str))})
        step = FakeAdapter.script[min(FakeAdapter.step, len(FakeAdapter.script) - 1)]
        FakeAdapter.step += 1
        self.calls += 1
        return step

    def get_token_usage(self):
        return {"llm_calls": self.calls, "cost_usd": 0.0}


def _tool_use(name, params):
    return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": name, "input": params}}]}}}


def _final(text):
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


ROWS = {"rows": [{"organization_id": 24, "purchases.vendor_name": SUPPLIER, "purchases.vendor_trn": TRN,
                  "net": "18450.00", "document_number": "BILL-2041"}], "row_count": 1, "type": "bills"}


@pytest.fixture
def agent(monkeypatch):
    FakeAdapter.step, FakeAdapter.sent = 0, []
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "_no_cache", set())
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop.law, "answer", lambda question, messages, brief=False: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "list_documents"}}])
    monkeypatch.setattr(dictionary, "for_orgs", lambda orgs, **kw: _names(*kw.get("org_names") or ()))
    ran = []

    def fake_execute(name, params, ctx):
        ran.append(params)
        return json.loads(json.dumps(ROWS)), True

    monkeypatch.setattr(loop.tools, "execute", fake_execute)
    return ran


def _all_sent_text():
    return json.dumps(FakeAdapter.sent, ensure_ascii=False)


def test_on_sends_placeholders_and_answers_with_real_values(monkeypatch, agent):
    monkeypatch.setattr(loop.settings, "pii_mode", "on")
    FakeAdapter.script = [
        _tool_use("list_documents", {"type": "bills", "filters": [
            {"member": "purchases.vendor_name", "operator": "equals", "values": ["[SUPPLIER_1]"]}]}),
        _final("[SUPPLIER_1] has bill BILL-2041 for AED 18,450.00 at [ORG_1]; its TRN [TRN_1] has 15 digits.")]
    history = [{"role": "user", "content": "Who is our biggest supplier?"},
               {"role": "assistant", "content": f"{SUPPLIER} at Zenith Foods Org."}]
    result = loop.run_agent(f"Bills from al noor trading llc? Mail me at a.b@x.ae", ORGS, META, history=history)

    sent = _all_sent_text()
    for raw in ("Al Noor", "al noor", "AL NOOR", "Zenith Foods", "Delta Builders", TRN, "a.b@x.ae"):
        assert raw not in sent
    assert "[SUPPLIER_1]" in sent and "[ORG_1]" in sent and "Privacy placeholders" in json.dumps(FakeAdapter.sent[0]["system"])
    assert "18450.00" in sent and "BILL-2041" in sent                                  # figures stay visible (D1)

    assert agent[0]["filters"][0]["values"] == [SUPPLIER]                              # the tool ran on the real name
    assert result.tool_calls[0]["input"]["filters"][0]["values"] == [SUPPLIER]
    assert result.data[0]["result"]["rows"][0]["purchases.vendor_name"] == SUPPLIER    # files and charts: real data
    assert result.answer == (f"{SUPPLIER} has bill BILL-2041 for AED 18,450.00 at Zenith Foods Org; its TRN {TRN} "
                             "has 15 digits.")
    assert result.verification["grounded"] is True
    assert result.privacy["mode"] == "on" and result.privacy["unresolved"] == 0
    assert result.privacy["hidden"]["SUPPLIER"] == 1 and result.privacy["hidden"]["ORG"] == 2


def test_on_rehydrates_the_law_route(monkeypatch, agent):
    monkeypatch.setattr(loop.settings, "pii_mode", "on")
    seen = {}

    def law_answer(question, messages, brief=False):
        seen["messages"] = json.dumps(messages)
        return loop.law.LawAnswer(answer="[ORG_1] must register: the threshold is AED 375,000.")

    monkeypatch.setattr(loop.law, "answer", law_answer)
    result = loop.run_agent("Must Zenith Foods Org register for VAT?", ORGS, META)
    assert "Zenith" not in seen["messages"] and "[ORG_1]" in seen["messages"]
    assert result.route == "law" and result.answer.startswith("Zenith Foods Org must register")


def test_shadow_sends_originals_and_reports_the_masked_version(monkeypatch, agent, tmp_path):
    monkeypatch.setattr(loop.settings, "pii_mode", "shadow")
    log = tmp_path / "sentinel.jsonl"
    monkeypatch.setattr(sentinel.settings, "pii_sentinel", "report")
    monkeypatch.setattr(sentinel.settings, "pii_sentinel_log", str(log))
    monkeypatch.setattr(sentinel.dictionary, "warm", lambda *a, **k: None)
    FakeAdapter.script = [_tool_use("list_documents", {"type": "bills"}), _final(f"{SUPPLIER} has one bill.")]
    result = loop.run_agent("Bills from Al Noor Trading?", ORGS, META)
    assert "Al Noor Trading" in _all_sent_text() and "Privacy placeholders" not in json.dumps(FakeAdapter.sent[0]["system"])
    assert result.answer == f"{SUPPLIER} has one bill." and result.privacy["mode"] == "shadow"
    masked = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [e["purpose"] for e in masked] == ["agent:masked", "agent:masked"]
    assert all("PARTY_NAME" not in e["found"] and "ORG_NAME" not in e["found"] for e in masked)


def test_off_changes_nothing_and_loads_no_names(monkeypatch, agent):
    monkeypatch.setattr(loop.settings, "pii_mode", "off")
    monkeypatch.setattr(dictionary, "for_orgs", lambda *a, **k: pytest.fail("no dictionary when off"))
    FakeAdapter.script = [_final(f"{SUPPLIER} is your supplier.")]
    result = loop.run_agent(f"Who is {SUPPLIER}?", ORGS, META)
    assert SUPPLIER in _all_sent_text() and result.privacy == {}


def test_without_the_contact_names_masking_still_runs_on_fields_and_patterns(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cube down")

    monkeypatch.setattr(dictionary, "for_orgs", boom)
    m = masking.Masking.start("on", ORGS, META, subject="t")
    assert m is not None and m.sends_masked
    [out] = m.outbound([{"role": "user", "content": [{"text": f"Zenith Foods Org and TRN {TRN}"}]}])
    assert out["content"][0]["text"] == "[ORG_1] and TRN [TRN_1]"                  # org names still known
    assert m.scrubber.json({"vendor_name": SUPPLIER}) == {"vendor_name": "[SUPPLIER_1]"}
