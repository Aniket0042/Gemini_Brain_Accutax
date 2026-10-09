"""The agent answer log: one masked, complete JSON line per answer; never breaks an answer."""
import json

import pytest

from gemini_brain.agent import answer_log, loop, preview


@pytest.fixture
def log_file(monkeypatch, tmp_path):
    path = tmp_path / "answers.jsonl"
    monkeypatch.setattr(answer_log.settings, "agent_answer_log", str(path))
    return path


def _result():
    return loop.AgentResult(
        answer="Revenue AED 5,809,352.", status="ok",
        tool_calls=[{"name": "query_metrics", "input": {"view": "pnl"}, "ok": True, "ms": 210, "rows": 1}],
        usage={"input_tokens": 1200, "output_tokens": 80, "cost_usd": 0.02, "llm_calls": 2},
        verification={"grounded": True, "checked": 1, "matched": 1, "unmatched": []})


def test_each_answer_is_one_masked_line(monkeypatch, log_file):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: _result())
    preview.answer("Revenue for john.doe@example.com this year?", [24, 25], lambda: {}, session_id=None, user_id=7)
    [line] = log_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(line)
    assert "john.doe@example.com" not in entry["question"]
    assert entry["user_id"] == 7 and entry["organization_ids"] == [24, 25]
    assert entry["route"] == "tools" and entry["status"] == "ok" and entry["notice"] is None
    assert entry["tool_calls"] == [{"name": "query_metrics", "input": {"view": "pnl"}, "ok": True, "ms": 210,
                                    "rows": 1, "error": None}]
    assert entry["verification"]["grounded"] is True and entry["cost_usd"] == 0.02
    assert entry["answer"].startswith("Revenue AED") and entry["elapsed_s"] is not None


def test_failures_are_logged_with_their_notice(monkeypatch, log_file):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(answer="x", status="deadline"))
    preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=7)
    entry = json.loads(log_file.read_text(encoding="utf-8"))
    assert entry["status"] == "degraded" and entry["notice"] == "UPSTREAM_TIMEOUT"


def test_off_by_default_and_never_breaks_the_answer(monkeypatch, tmp_path):
    monkeypatch.setattr(answer_log.settings, "agent_answer_log", "")
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: _result())
    assert preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=7)["status"] == "ok"
    monkeypatch.setattr(answer_log.settings, "agent_answer_log", str(tmp_path / "x" / "y.jsonl"))
    monkeypatch.setattr(answer_log, "_write", lambda entry: 1 / 0)
    assert preview.answer("Revenue?", [24], lambda: {}, session_id=None, user_id=7)["status"] == "ok"


def test_the_log_rotates(monkeypatch, log_file):
    monkeypatch.setattr(answer_log, "MAX_BYTES", 10)
    for _ in range(3):
        answer_log._write({"question": "q" * 20})
    assert log_file.exists() and log_file.with_suffix(".jsonl.1").exists() and log_file.with_suffix(".jsonl.2").exists()


def test_the_report_summarises_the_log(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "agent_log_report", Path(__file__).resolve().parents[2] / "scripts" / "agent_log_report.py")
    report = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(report)
    path = tmp_path / "a.jsonl"
    rows = [
        {"ts": "2026-10-09T10:00:00+00:00", "user_id": 1, "question": "Revenue?", "status": "ok", "route": "tools",
         "elapsed_s": 6.0, "cost_usd": 0.02, "output_tokens": 90, "verification": {"grounded": True},
         "tool_calls": [{"name": "query_metrics", "input": {"view": "pnl"}, "ok": True}]},
        {"ts": "2026-10-09T10:05:00+00:00", "user_id": 2, "question": "Cash forecast?", "status": "degraded",
         "notice": "UPSTREAM_TIMEOUT", "route": "tools", "elapsed_s": 45.0, "cost_usd": 0.05, "output_tokens": 10,
         "verification": {"grounded": False, "unmatched": ["99"]},
         "tool_calls": [{"name": "cash_forecast", "input": {}, "ok": False, "error": "Figures are temporarily unavailable."}]},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    text = report.report(report.load(path, None))
    assert "Answers: 2   users: 2" in text and "p95 45.0 s" in text and "$0.07 total" in text
    assert "1 with unverified figures" in text and "failed 1" in text and "degraded/UPSTREAM_TIMEOUT" in text
