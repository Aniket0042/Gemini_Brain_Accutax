"""The PII sentinel (PII plan phase 0): reports what personal data a model call carries, never blocks or changes it,
and never writes the values themselves."""
import importlib.util
import json
from pathlib import Path

import pytest

from gemini_brain.privacy import sentinel
from gemini_brain.reasoning import bedrock_client

_REPORT = Path(__file__).resolve().parents[2] / "scripts" / "pii_leak_report.py"

RAW = ("Al Noor Trading LLC", "100234567800003", "ahmed.k@alnoor.ae", "050 123 4567", "Acme Foods Org",
       "784-1990-1234567-1", "AE070331234567890123456")


@pytest.fixture
def log_file(monkeypatch, tmp_path):
    path = tmp_path / "pii_sentinel.jsonl"
    monkeypatch.setattr(sentinel.settings, "pii_sentinel", "report")
    monkeypatch.setattr(sentinel.settings, "pii_sentinel_log", str(path))
    sentinel.note_request(session_id="s1", organization_ids=[24], org_names=["Acme Foods Org"])
    return path


def _payload():
    system = [{"text": "You answer for these organizations:\n- id 24: Acme Foods Org"}, {"cachePoint": {"type": "default"}}]
    messages = [
        {"role": "user", "content": [{"text": "Bills from Al Noor last month? Call me on 050 123 4567."}]},
        {"role": "assistant", "content": [{"text": "Al Noor Trading LLC has 2 bills."}]},
        {"role": "user", "content": [{"text": "Is TRN 100234567800003 valid? Ask ahmed.k@alnoor.ae, EID 784-1990-1234567-1, "
                                              "IBAN AE070331234567890123456"}]},
        {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "review_vat",
                                                        "input": {"vendor_name": "Al Noor Trading LLC"}}}]},
        {"role": "user", "content": [{"toolResult": {"toolUseId": "t1", "content": [{"json": {"rows": [
            {"purchases.vendor_name": "Al Noor Trading LLC", "vendor_trn": 100234567800003, "net": 18450.0,
             "vat": 922.5, "bill_number": "BILL-2041", "document_date": "2026-09-12",
             "line_description": "Advance to staff", "organization_name": "Acme Foods Org"},
            {"purchases.vendor_name": "-", "vendor_trn": None, "net": 120.0, "bill_number": "INV-BULK-26-20241216180347-114231"},
        ]}}]}}]},
        {"role": "user", "content": [{"document": {"format": "pdf", "name": "bill", "source": {"bytes": b"%PDF-1.4 x" * 10}}},
                                     {"text": "Record this document."}]},
    ]
    return system, messages


def test_off_by_default_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(sentinel.settings, "pii_sentinel", "off")
    monkeypatch.setattr(sentinel.settings, "pii_sentinel_log", str(tmp_path / "x.jsonl"))
    system, messages = _payload()
    assert sentinel.inspect(model_id="m", label="agent", purpose="agent", system=system, messages=messages) is None
    assert not (tmp_path / "x.jsonl").exists()


def test_finds_each_kind_where_it_was_sent(log_file):
    system, messages = _payload()
    entry = sentinel.inspect(model_id="m", label="agent", purpose="agent", system=system, messages=messages)
    found, where = entry["found"], entry["where"]
    assert {"PARTY_NAME", "ORG_NAME", "TRN", "EMAIL", "PHONE", "EMIRATES_ID", "IBAN"} <= set(found)
    assert where["tool_result"]["PARTY_NAME"] == 1 and where["tool_result"]["TRN"] == 1    # by their keys; "-" skipped
    assert where["tool_input"]["PARTY_NAME"] == 1
    assert where["assistant_text"]["PARTY_NAME"] == 1                                      # echoed in an earlier answer
    assert where["system"]["ORG_NAME"] == 1                                                # noted org name in the prompt
    assert where["user_text"]["TRN"] == 1 and where["user_text"]["EMAIL"] == 1
    assert entry["distinct"]["PARTY_NAME"] == 1 and entry["distinct"]["TRN"] == 1
    assert entry["files"] == 1 and entry["file_bytes"] == 100
    assert entry["free_text_fields"] == 1
    assert entry["session_id"] == "s1" and entry["organization_ids"] == [24] and entry["purpose"] == "agent"


def test_log_never_holds_raw_values(log_file):
    system, messages = _payload()
    sentinel.inspect(model_id="m", label="agent", purpose="agent", system=system, messages=messages)
    text = log_file.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 1
    for value in RAW:
        assert value not in text and value.lower() not in text.lower()
    assert "A• N••• T•••••• L••" in text


def test_figures_dates_and_document_numbers_are_not_flagged():
    text = ("Net AED 18,450.00 and 1002345678000.50; bill INV-BULK-26-20241216180347-114231; "
            "journal JE-24-2026-00000773 on 2026-09-12; 15 digits 200234567800003")
    result = sentinel.scan("", [{"role": "user", "content": [{"text": text}]}])
    assert result["found"] == {}


@pytest.mark.parametrize("text,kind,expected", [
    ("IBAN AE070331234567890123456", "IBAN", 1),
    ("IBAN AE07 0331 2345 6789 0123 456", "IBAN", 1),
    ("IBAN AE080331234567890123456", "IBAN", 0),       # wrong check digits
    ("card 4111 1111 1111 1111", "CARD", 1),
    ("card 4111 1111 1111 1112", "CARD", 0),           # fails Luhn
    ("call +971 50 123 4567 or 00971501234567", "PHONE", 2),
    ("EID 784199012345671", "EMIRATES_ID", 1),
])
def test_patterns_use_their_check_digits(text, kind, expected):
    result = sentinel.scan("", [{"role": "user", "content": [{"text": text}]}])
    assert result["found"].get(kind, 0) == expected


def test_key_kinds():
    assert sentinel.key_kind("purchases.vendor_name") == "PARTY_NAME"
    assert sentinel.key_kind("buyer_tax_id") == "TRN"
    assert sentinel.key_kind("vendor_address") == "ADDRESS"
    assert sentinel.key_kind("journal_description") == "FREE_TEXT"
    assert sentinel.key_kind("item_name") is None and sentinel.key_kind("account_name") is None


class _FakeClient:
    def __init__(self):
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return {"output": {"message": {"role": "assistant", "content": [{"text": "ok"}]}}, "usage": {}}


def test_every_adapter_call_is_inspected_and_sent_unchanged(monkeypatch, log_file):
    client = _FakeClient()
    monkeypatch.setattr(bedrock_client, "get_bedrock_client", lambda *a, **k: client)
    system, messages = _payload()
    adapter = bedrock_client.BedrockAdapter("test-model", label="agent")
    adapter.converse_with_tools(system, messages, [], purpose="agent")
    adapter.converse("narrate", [{"role": "user", "content": [{"text": "Top customer Al Noor Trading LLC"}]}],
                     purpose="report_narration")
    bedrock_client.converse("sys", [{"role": "user", "content": [{"text": "mail ahmed.k@alnoor.ae"}]}],
                            model_id="test-model", purpose="module")
    entries = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()]
    assert [e["purpose"] for e in entries] == ["agent", "report_narration", "module"]
    assert client.calls[0]["messages"] is messages                     # report only: the payload is not touched
    assert entries[2]["found"] == {"EMAIL": 1}


def test_a_failing_scan_never_breaks_the_call(monkeypatch, log_file):
    client = _FakeClient()
    monkeypatch.setattr(bedrock_client, "get_bedrock_client", lambda *a, **k: client)

    def boom(*a, **k):
        raise RuntimeError("scan bug")

    monkeypatch.setattr(sentinel, "scan", boom)
    out = bedrock_client.BedrockAdapter("test-model").converse("s", [{"role": "user", "content": [{"text": "hi"}]}])
    assert out == "ok" and len(client.calls) == 1


def _report_module():
    spec = importlib.util.spec_from_file_location("pii_leak_report", _REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_leak_report_summarises_without_values(log_file):
    system, messages = _payload()
    sentinel.inspect(model_id="m", label="agent", purpose="agent", system=system, messages=messages)
    sentinel.inspect(model_id="m", label="agent", purpose="agent", system=system, messages=messages)
    sentinel.inspect(model_id="m", label="h", purpose="health", system="ping", messages=[
        {"role": "user", "content": [{"text": "Reply OK"}]}])
    report = _report_module()
    summary = report.summarise(report.load(log_file))
    assert summary["calls"] == 3 and summary["calls_with_pii"] == 2 and summary["sessions"] == 1
    assert summary["by_kind"]["PARTY_NAME"]["distinct"] == 1          # the same supplier across both calls
    assert summary["by_site"]["agent"]["files"] == 2
    text = report.markdown(summary)
    assert "2 of 3 model calls" in text and "| agent |" in text
    for value in RAW:
        assert value not in text
