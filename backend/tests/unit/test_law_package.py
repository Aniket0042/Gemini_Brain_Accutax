"""Package 3: citations checked by code, Arabic law questions, the monthly knowledge-base refresh, the law evaluation."""
import importlib
import json
import sys
from pathlib import Path

import pytest

from gemini_brain.agent import law
from gemini_brain.config.settings import settings
from gemini_brain.vat_kb import citations

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path[:0] = [str(EVAL), str(SCRIPTS)]
lc = importlib.import_module("vat_law_cases")
refresh = importlib.import_module("vat_kb_refresh")


@pytest.fixture(autouse=True)
def audit_file(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setattr(settings, "agent_audit_log", str(path))
    return path


def lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# --- citations ----------------------------------------------------------------------------------------------------

def test_cited_reads_single_lists_and_ranges():
    assert citations.cited("Rate is 5% [1]. See [2, 3] and [4-6] and [7–8].") == {1, 2, 3, 4, 5, 6, 7, 8}
    assert citations.cited("No citation here, only AED [sic].") == set()


def test_a_citation_to_no_given_source_is_taken_out():
    text, report = citations.enforce("The rate is 5% [1]. Records are kept 5 years [9].", [1, 2])
    assert text == "The rate is 5% [1]. Records are kept 5 years." and report == {"cited": [1], "removed": [9], "uncited": False}


def test_a_list_keeps_its_known_numbers():
    text, report = citations.enforce("See [1, 7, 2].", [1, 2])
    assert text == "See [1, 2]." and report["removed"] == [7]


def test_an_answer_with_no_citation_is_reported_not_changed():
    text, report = citations.enforce("The rate is 5%.", [1, 2])
    assert text == "The rate is 5%." and report["uncited"] is True and report["removed"] == []


def test_a_good_answer_is_unchanged():
    text = "The rate is 5% [1], and zero for exports [2]."
    assert citations.enforce(text, [1, 2]) == (text, {"cited": [1, 2], "removed": [], "uncited": False})


# --- the law route ------------------------------------------------------------------------------------------------

def test_an_arabic_question_goes_to_the_tool_loop(monkeypatch):
    monkeypatch.setattr("gemini_brain.vat_kb.augment.augment", lambda *a, **k: pytest.fail("must not search in Arabic"))
    assert law.answer("ما هي نسبة ضريبة القيمة المضافة؟", []) is None


def test_the_law_route_takes_out_unknown_citations_and_logs_it(audit_file):
    blocks = [{"type": "fta_sources", "sources": [{"n": 1}, {"n": 2}]}]
    text, report = law._checked_citations("Rate 5% [1]. Threshold [5].", blocks)
    assert text == "Rate 5% [1]. Threshold." and report["removed"] == [5]
    [line] = lines(audit_file)
    assert line["kind"] == "law_answer_citations" and line["route"] == "law" and line["removed"] == [5]


def test_the_law_route_logs_an_uncited_answer(audit_file):
    law._checked_citations("The rate is 5%.", [{"type": "fta_sources", "sources": [{"n": 1}]}])
    assert lines(audit_file)[0]["uncited"] is True


def test_without_sources_nothing_is_checked(audit_file):
    assert law._checked_citations("Text [3].", []) == ("Text [3].", None) and lines(audit_file) == []


# --- the tool loop ------------------------------------------------------------------------------------------------

def test_the_tool_loop_takes_out_a_citation_the_search_did_not_give(monkeypatch, audit_file):
    from gemini_brain.agent import loop, tools

    class Adapter:
        step = 0

        def __init__(self, *a, **k):
            pass

        def converse_with_tools(self, system, messages, specs, **kw):
            Adapter.step += 1
            if Adapter.step == 1:
                return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
                    {"toolUse": {"toolUseId": "t1", "name": "search_vat_kb", "input": {"question": "rate"}}}]}}}
            return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [
                {"text": "The rate is 5% [1], see also [4]."}]}}}

        def get_token_usage(self):
            return {"llm_calls": Adapter.step, "cost_usd": 0.0}

    def execute(name, params, ctx):
        ctx.fta_numbers["https://fta/1"] = 1
        ctx.fta_sources.append({"n": 1, "title": "Law", "url": "https://fta/1"})
        return {"available": True, "confident": True, "sources_and_rules": "..."}, True

    monkeypatch.setattr(loop, "BedrockAdapter", Adapter)
    monkeypatch.setattr(loop, "redact_pii", lambda t: (t, {}))
    monkeypatch.setattr(loop.law, "answer", lambda *a, **k: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "search_vat_kb"}}])
    monkeypatch.setattr(loop.tools, "execute", execute)
    result = loop.run_agent("What is the VAT rate?", [24], {24: {"name": "O", "currency": "AED"}})
    assert result.answer == "The rate is 5% [1], see also." and result.blocks[0]["sources"][0]["n"] == 1
    assert lines(audit_file)[0]["removed"] == [4]


# --- the refresh ----------------------------------------------------------------------------------------------------

def test_a_failed_refresh_points_current_back_at_the_previous_build(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import index
    kb = tmp_path
    (kb / "builds" / "old").mkdir(parents=True)
    (kb / "builds" / "old" / "build_info.json").write_text(json.dumps({"chunks": 1000}), encoding="utf-8")
    (kb / "CURRENT").write_text("old", encoding="utf-8")

    def fake_build(kb_dir, embedder, keep_builds=3):
        new = kb_dir / "builds" / "new"
        new.mkdir()
        (new / "build_info.json").write_text(json.dumps({"chunks": 400}), encoding="utf-8")  # far fewer chunks
        (kb_dir / "CURRENT").write_text("new", encoding="utf-8")
        return new

    monkeypatch.setattr(index, "build", fake_build)
    monkeypatch.setattr("gemini_brain.vat_kb.embedder.BgeSmallEmbedder", lambda *a, **k: None)
    monkeypatch.setattr(refresh, "check_build", lambda kb_dir, prev: ["the new build has 400 chunks, under 90% of the previous 1000"])
    report = refresh.refresh(kb, crawl=False)
    assert report["ok"] is False and report["rolled_back_to"] == "old" and (kb / "CURRENT").read_text() == "old"
    assert json.loads((kb / "refresh_log.jsonl").read_text(encoding="utf-8").splitlines()[0])["rolled_back_to"] == "old"


def test_a_good_refresh_keeps_the_new_build(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import index
    kb = tmp_path
    (kb / "builds" / "old").mkdir(parents=True)
    (kb / "CURRENT").write_text("old", encoding="utf-8")

    def fake_build(kb_dir, embedder, keep_builds=3):
        new = kb_dir / "builds" / "new"
        new.mkdir()
        (kb_dir / "CURRENT").write_text("new", encoding="utf-8")
        return new

    monkeypatch.setattr(index, "build", fake_build)
    monkeypatch.setattr("gemini_brain.vat_kb.embedder.BgeSmallEmbedder", lambda *a, **k: None)
    monkeypatch.setattr(refresh, "check_build", lambda kb_dir, prev: [])
    report = refresh.refresh(kb, crawl=False)
    assert report["ok"] is True and report["in_use"] == "new" and "rolled_back_to" not in report


def test_the_check_wants_most_chunks_and_confident_answers(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import retrieval
    (tmp_path / "builds" / "b").mkdir(parents=True)
    (tmp_path / "builds" / "b" / "build_info.json").write_text(json.dumps({"chunks": 950}), encoding="utf-8")
    (tmp_path / "CURRENT").write_text("b", encoding="utf-8")

    class KB:
        def search(self, q):
            return type("R", (), {"confident": "zero-rated" not in q})()

    monkeypatch.setattr(retrieval, "load_knowledge_base", lambda d: KB())
    problems = refresh.check_build(tmp_path, 1000)
    assert problems == ["no confident match for: Which supplies are zero-rated?"]
    assert refresh.check_build(tmp_path, 2000)[0].startswith("the new build has 950 chunks")


# --- the law evaluation's judge -------------------------------------------------------------------------------------

def test_the_judge_compares_numbers_without_separators_and_wants_a_citation_and_sources():
    case = lc.case("t", "q", ["375,000"])
    blocks = [{"type": "fta_sources", "sources": [{"n": 1}]}]
    assert lc.judge(case, "Register above AED 375000 [1].", [], blocks)["ok"]
    assert not lc.judge(case, "Register above AED 375,000.", [], blocks)["ok"]
    assert not lc.judge(case, "Register above AED 375,000 [1].", [], [])["ok"]
    assert not lc.judge(case, "Register above AED 375,000 [1].", ["review_vat"], blocks)["ok"]


def test_an_arabic_case_needs_an_arabic_answer():
    case = lc.case("t", "q", ["5%"], arabic=True)
    blocks = [{"type": "fta_sources", "sources": [{"n": 1}]}]
    assert lc.judge(case, "نسبة الضريبة 5% [1].", [], blocks)["ok"]
    assert not lc.judge(case, "The rate is 5% [1].", [], blocks)["ok"]


def test_there_are_forty_cases_six_in_arabic():
    assert len(lc.CASES) == 40 and sum(c["arabic"] for c in lc.CASES) == 6
    assert len({c["id"] for c in lc.CASES}) == 40 and all(c["facts"] for c in lc.CASES)
