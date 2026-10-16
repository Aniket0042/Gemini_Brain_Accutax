"""The daily pilot report (scripts/pilot_report.py)."""
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
pr = importlib.import_module("pilot_report")

NOW = dt.datetime.now(dt.timezone.utc)


def ts(hours_ago):
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat(timespec="seconds")


def write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n", encoding="utf-8")
    return path


def test_lines_are_read_by_time_and_user(tmp_path):
    path = write(tmp_path / "a.jsonl", [{"ts": ts(1), "user_id": 1}, {"ts": ts(30), "user_id": 1}, {"ts": ts(2), "user_id": 2}])
    since = NOW - dt.timedelta(days=1)
    assert len(pr.read_lines(path, since, None)) == 2
    assert [r["user_id"] for r in pr.read_lines(path, since, {2})] == [2]
    assert pr.read_lines(tmp_path / "missing.jsonl", since, None) == [] and pr.read_lines(None, since, None) == []


def test_the_summary_counts_and_lists_what_needs_reading():
    answers = [
        {"ts": ts(1), "user_id": 1, "question": "q1", "route": "law", "cost_usd": 0.02, "elapsed_s": 2.0, "status": "ok"},
        {"ts": ts(1), "user_id": 2, "question": "q2", "route": "tools", "cost_usd": 0.06, "elapsed_s": 12.0, "status": "ok",
         "documents": [{"id": "f"}], "tool_calls": [{"name": "review_vat"}, {"name": "list_documents", "error": "x"}],
         "verification": {"grounded": False}},
        {"ts": ts(1), "user_id": 2, "question": "q3", "route": "tools", "cost_usd": 0.04, "elapsed_s": 40.0, "status": "deadline"},
    ]
    events = [{"kind": "vat_review"}, {"kind": "injection_in_file"}, {"kind": "canary_hit"},
              {"kind": "vat_kb_refresh", "ts": ts(3), "ok": True, "new_documents": 2}]
    s = pr.summary(answers, events)
    assert s["answers"] == 3 and s["users"] == 2 and s["with_files"] == 1 and s["routes"] == {"law": 1, "tools": 2}
    assert s["tools_used"] == {"review_vat": 1, "list_documents": 1} and s["cost_usd"]["total"] == 0.12
    assert s["seconds"]["median"] == 12.0 and s["not_grounded"] == 1 and s["vat_reviews"] == 1
    assert s["guard_events"] == {"injection_in_file": 1, "canary_hit": 1}
    assert s["knowledge_base_refreshes"][0]["new_documents"] == 2
    assert [(n["question"], n["why"]) for n in s["need_reading"]] == [
        ("q2", "figures not grounded, a tool error"), ("q3", "status deadline")]


def test_an_empty_day_is_a_valid_report():
    s = pr.summary([], [])
    assert s["answers"] == 0 and s["cost_usd"] == {"total": 0, "median": 0} and s["seconds"]["p95"] is None
