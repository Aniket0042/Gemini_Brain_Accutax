"""Usage ledger (docs/ORG_TOKEN_LIMITS_STRATEGY.md, step 1): one line per piece of AI spend, owner charged."""
from __future__ import annotations

import concurrent.futures
import contextvars
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from gemini_brain.agent import loop, preview
from gemini_brain.reasoning import bedrock_client
from gemini_brain.uploads import reader
from gemini_brain.usage import ledger

USAGE = {"inputTokens": 1000, "outputTokens": 200}


@pytest.fixture
def ledger_file(monkeypatch, tmp_path):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setattr(ledger.settings, "usage_ledger", str(path))
    # $0.01 a call, whatever the model; owners: org 24 and 25 belong to user 7, org 30 to user 9.
    monkeypatch.setattr(ledger, "call_cost_usd", lambda *a, **k: 0.01)
    monkeypatch.setattr(ledger, "owners_of", lambda orgs: {o: {24: 7, 25: 7, 30: 9}.get(o) for o in orgs})
    return path


def lines(path: Path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_off_by_default_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(ledger.settings, "usage_ledger", "")
    with ledger.scope("agent_answer", user_id=7, organization_ids=[24]):
        ledger.add_call("m", "agent", "agent", USAGE)
    assert not list(tmp_path.iterdir())


def test_scope_writes_one_line_with_calls_from_other_threads(ledger_file):
    with ledger.scope("agent_answer", user_id=7, organization_ids=[24, 25], session_id="s1") as spend:
        ledger.add_call("model-a", "agent", "agent", USAGE)
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            pool.submit(contextvars.copy_context().run, ledger.add_call, "model-b", "narrator", "narrate", USAGE).result()
            s = ledger.current()

            def worker():
                with ledger.attach(s):
                    ledger.add_call("model-b", "x", "y", USAGE)
            pool.submit(worker).result()
        spend.status = "partial"
    [line] = lines(ledger_file)
    assert line["kind"] == "agent_answer" and line["status"] == "partial" and line["session_id"] == "s1"
    assert line["calls"] == 3 and line["input_tokens"] == 3000 and line["output_tokens"] == 600
    assert line["models"]["model-b"]["calls"] == 2
    assert line["credits"] == 30.0 and line["cost_usd"] == 0.03
    assert line["owners"] == {"24": 7, "25": 7} and line["relationship"] == {"24": "owner", "25": "owner"}
    assert line["charge"] == {"7": 30.0}
    assert line["reserved_credits"] == ledger.RESERVE_CREDITS


def test_question_across_two_owners_is_split_evenly(ledger_file):
    with ledger.scope("agent_answer", user_id=50, organization_ids=[24, 25, 30]):
        ledger.add_call("m", "agent", "agent", USAGE)
        ledger.add_call("m", "agent", "agent", USAGE)
    [line] = lines(ledger_file)
    assert line["charge"] == {"7": 10.0, "9": 10.0}
    assert set(line["relationship"].values()) == {"other"}


def test_split_charge_unknown_and_none():
    assert ledger.split_charge(9, [None, 4]) == {"unknown": 4.5, "4": 4.5}
    assert ledger.split_charge(9, []) == {"none": 9}


def test_call_outside_a_scope_is_written_alone(ledger_file):
    ledger.add_call("m", "report narrator", "narrate", USAGE)
    [line] = lines(ledger_file)
    assert line["kind"] == "unscoped" and line["calls"] == 1 and line["charge"] == {"none": 10.0}


def test_call_with_no_tokens_is_not_counted(ledger_file):
    with ledger.scope("agent_answer", user_id=7, organization_ids=[24]):
        ledger.add_call("m", "agent", "agent", {})
    [line] = lines(ledger_file)
    assert line["calls"] == 0 and line["credits"] == 0


def test_error_inside_scope_is_recorded_and_raised(ledger_file):
    with pytest.raises(ValueError):
        with ledger.scope("agent_answer", user_id=7, organization_ids=[24]):
            ledger.add_call("m", "agent", "agent", USAGE)
            raise ValueError("boom")
    [line] = lines(ledger_file)
    assert line["status"] == "error" and line["credits"] == 10.0


def test_metering_never_breaks_a_call(ledger_file, monkeypatch):
    monkeypatch.setattr(ledger, "_call", lambda *a: 1 / 0)
    ledger.add_call("m", "agent", "agent", USAGE)  # no exception
    assert lines(ledger_file) == []


def test_adapter_calls_are_metered(ledger_file):
    fake = SimpleNamespace(model_id="model-a", label="agent")
    with ledger.scope("agent_answer", user_id=7, organization_ids=[24]):
        bedrock_client.BedrockAdapter._record_trace(fake, "agent", USAGE, 0.0)
        bedrock_client._record_module_trace("model-b", "sql_agent", USAGE, 0.0)
    [line] = lines(ledger_file)
    assert set(line["models"]) == {"model-a", "model-b"}


def test_agent_answer_is_one_line_with_its_status(ledger_file, monkeypatch):
    def run_agent(*a, **k):
        ledger.add_call("m", "agent", "agent", USAGE)
        return loop.AgentResult(answer="Revenue AED 5.", status="ok")
    monkeypatch.setattr(loop, "run_agent", run_agent)
    response = preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=7)
    [line] = lines(ledger_file)
    assert line["kind"] == "agent_answer" and line["status"] == response["status"] == "ok"
    assert line["user_id"] == 7 and line["organization_ids"] == [24] and line["calls"] == 1


def test_file_read_is_one_line_with_its_status(ledger_file, monkeypatch):
    rec = SimpleNamespace(id="u1", user_id=7, organization_ids=[30], session_id="s9")
    monkeypatch.setattr(reader.store, "get", lambda upload_id: rec)

    def read(r, adapter):
        ledger.add_call("m", "upload-reader", "extract", USAGE)
        return "ready"
    monkeypatch.setattr(reader, "_read_and_record", read)
    reader.read_now("u1")
    [line] = lines(ledger_file)
    assert line["kind"] == "document_read" and line["status"] == "ready" and line["charge"] == {"9": 10.0}


def test_the_report_summarises_the_ledger(ledger_file):
    for user, orgs in ((7, [24]), (7, [24]), (50, [24, 30])):
        with ledger.scope("agent_answer", user_id=user, organization_ids=orgs):
            ledger.add_call("m", "agent", "agent", USAGE)
    spec = importlib.util.spec_from_file_location(
        "usage_report", Path(__file__).resolve().parents[2] / "scripts" / "usage_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    s = report.summarise(report.load(ledger_file))
    assert s["lines"] == 3 and s["credits"] == 30.0 and s["multi_owner_lines"] == 1
    assert sorted(s["question"]) == [10.0, 10.0, 10.0]
    month = next(iter(s["owner_month"]))[1]
    assert s["owner_month"][("7", month)] == 25.0 and s["owner_month"][("9", month)] == 5.0
    assert "One question" in report.report(s)


def test_health_check_is_its_own_internal_line(ledger_file, monkeypatch):
    from gemini_brain.api import routes
    monkeypatch.setattr(routes, "check_all_models_and_services",
                        lambda test_prompt: ledger.add_call("m", "health", "ping", USAGE) or {"ok": True})
    assert routes._health_check("OK") == {"ok": True}
    [line] = lines(ledger_file)
    assert line["kind"] == "health_check" and line["charge"] == {"none": 10.0}
