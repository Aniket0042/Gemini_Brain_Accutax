"""PII plan phase 6: the strict sentinel hides what masking let through, and no model call can skip it."""
import ast
import json
import re
from pathlib import Path

import pytest

from gemini_brain.agent import loop
from gemini_brain.config.settings import settings
from gemini_brain.privacy import dictionary, masking, policy, sentinel, snapshots
from gemini_brain.reasoning import bedrock_client

SRC = Path(__file__).resolve().parents[2] / "src" / "gemini_brain"
GAMMA = "Gamma Traders"


# ── Guard: every model call goes through the sentinel ────────────────────────

#: Ways to reach a model without BedrockAdapter / the module helpers in bedrock_client.py.
_DIRECT = [
    (re.compile(r"""["']bedrock-runtime["']"""), "a Bedrock runtime client"),
    (re.compile(r"\.(converse|converse_stream|invoke_model|invoke_model_with_response_stream)\(\s*\*\*"), "a raw Bedrock call"),
    (re.compile(r"^\s*(import|from)\s+(anthropic|openai|google\.genai|google\.generativeai|mistralai|cohere)\b", re.M),
     "another model provider's SDK"),
]


def test_no_module_calls_a_model_except_through_bedrock_client():
    offenders = []
    for path in SRC.rglob("*.py"):
        if path.name == "bedrock_client.py":
            continue
        text = path.read_text(encoding="utf-8")
        for rx, what in _DIRECT:
            if rx.search(text):
                offenders.append(f"{path.relative_to(SRC)}: {what}")
    assert offenders == [], "Model calls must go through reasoning/bedrock_client.py (PII sentinel): " + "; ".join(offenders)


def test_every_call_path_in_bedrock_client_runs_the_sentinel_before_sending():
    tree = ast.parse((SRC / "reasoning" / "bedrock_client.py").read_text(encoding="utf-8"))
    functions = [(f.name, f) for f in tree.body if isinstance(f, ast.FunctionDef)]
    functions += [(f"{c.name}.{f.name}", f) for c in tree.body if isinstance(c, ast.ClassDef)
                  for f in c.body if isinstance(f, ast.FunctionDef)]
    sends = {}
    for name, node in functions:  # a nested _call() is part of the function that defines it
        body = ast.dump(node)
        if re.search(r"attr='(converse|converse_stream)'", body):
            sends[name] = "_inspect_outbound" in body
    assert set(sends) >= {"BedrockAdapter.converse", "BedrockAdapter.converse_stream",
                          "BedrockAdapter.converse_with_tools", "converse", "converse_tools"}, sends
    assert all(sends.values()), sends


# ── Strict mode ─────────────────────────────────────────────────────────────

@pytest.fixture
def strict(monkeypatch):
    monkeypatch.setattr(settings, "pii_sentinel", "strict")
    monkeypatch.setattr(settings, "pii_sentinel_log", "")
    yield
    masking._current.set(None)


def _miss(monkeypatch):
    """A masking that misses "Gamma Traders" while the sentinel knows it: what strict mode is for."""
    full = dictionary.Dictionary([dictionary.Entry(GAMMA, "SUPPLIER", policy.normalize_name(GAMMA))])
    monkeypatch.setattr(dictionary, "for_orgs",
                        lambda *a, fetch=False, **k: dictionary.Dictionary() if fetch else full)


def test_strict_hides_a_missed_value_in_a_copy_and_learns_it(monkeypatch, strict):
    _miss(monkeypatch)
    m = masking.Masking.start("on", [24], {}, subject="t")
    messages = [{"role": "user", "content": [{"text": f"Bills from {GAMMA}? Mail a.b@gamma.ae"}]},
                {"role": "user", "content": [{"toolResult": {"toolUseId": "t", "content": [{"json": {
                    "note": f"paid {GAMMA}", "net": "100.00"}}]}}]}]
    original = json.dumps(messages)
    system, sent, counts = sentinel.enforce("Answer for [ORG_1].", messages)
    text = json.dumps(sent)
    assert GAMMA not in text and "a.b@gamma.ae" not in text and "100.00" in text
    assert json.dumps(messages) == original                                    # the caller's payload is untouched
    assert counts == {"PARTY_NAME": 1, "EMAIL": 1} and m.sentinel_caught == 2
    assert m.rehydrate(re.search(r"\[PARTY_\d+\]", text).group(0)) == GAMMA
    assert GAMMA not in json.dumps(m.outbound(messages))                       # later calls hide it too


def test_report_mode_and_mode_off_change_nothing(monkeypatch, strict):
    _miss(monkeypatch)
    messages = [{"role": "user", "content": [{"text": f"Bills from {GAMMA}?"}]}]
    monkeypatch.setattr(settings, "pii_sentinel", "report")
    masking.Masking.start("on", [24], {}, subject="t")
    assert sentinel.enforce("s", messages)[1] is messages
    monkeypatch.setattr(settings, "pii_sentinel", "strict")
    masking.Masking.start("off", [24], {}, subject="t")                      # no masking: nothing to rehydrate with
    assert sentinel.enforce("s", messages)[1] is messages


def test_a_failing_check_never_stops_the_call(monkeypatch, strict):
    masking.Masking.start("on", [24], {}, subject="t")

    def boom(*a, **k):
        raise RuntimeError("scan bug")

    monkeypatch.setattr(sentinel, "leaked_values", boom)
    messages = [{"role": "user", "content": [{"text": "hi"}]}]
    system, sent = bedrock_client._inspect_outbound("m", "agent", "agent", "sys", messages)
    assert sent is messages and system == "sys"


class FakeAdapter:
    script, step, sent = [], 0, []

    def __init__(self, model_id, label=""):
        self.model_id, self.calls = model_id, 0

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        # What BedrockAdapter does first: the last check before the call leaves.
        system, messages = bedrock_client._inspect_outbound(self.model_id, "agent", purpose, system, messages)
        FakeAdapter.sent.append(json.dumps({"system": system, "messages": messages}, ensure_ascii=False))
        step = FakeAdapter.script[min(FakeAdapter.step, len(FakeAdapter.script) - 1)]
        FakeAdapter.step += 1
        self.calls += 1
        return step

    def get_token_usage(self):
        return {"llm_calls": self.calls, "cost_usd": 0.0}


def test_strict_in_the_agent_the_model_never_sees_it_and_the_user_still_does(monkeypatch, strict, tmp_path):
    _miss(monkeypatch)
    monkeypatch.setattr(settings, "pii_mode", "on")
    monkeypatch.setattr(settings, "pii_snapshots", "on")
    monkeypatch.setattr(settings, "pii_snapshot_sample_pct", 100)
    monkeypatch.setattr(settings, "pii_snapshot_users", "")
    monkeypatch.setattr(settings, "pii_snapshot_key", "")
    monkeypatch.setattr(settings, "pii_snapshot_dir", str(tmp_path))
    monkeypatch.setattr(masking.Masking, "learn", lambda self, texts: [])   # make the masking miss on purpose
    FakeAdapter.step, FakeAdapter.sent = 0, []
    FakeAdapter.script = [{"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
        {"text": "No bills from [PARTY_1] this year."}]}}}]
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop, "_no_cache", set())
    monkeypatch.setattr(loop.law, "answer", lambda q, m, brief=False: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [])
    result = loop.run_agent(f"Any bills from {GAMMA}?", [24], {24: {"name": "Org Twenty Four"}}, user_id=1)
    assert GAMMA not in FakeAdapter.sent[0] and "[PARTY_1]" in FakeAdapter.sent[0]
    assert result.answer == f"No bills from {GAMMA} this year."
    assert result.privacy["sentinel_caught"] == 1
    snap = snapshots.load(result.privacy["snapshot"], include_raw=False)
    assert GAMMA not in json.dumps(snap["calls"][0]["sent"])                  # the snapshot shows what really went
