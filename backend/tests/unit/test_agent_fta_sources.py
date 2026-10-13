"""The numbered FTA sources under a VAT answer the agent writes after its own search (the [1] [2] in the text)."""
import time
from types import SimpleNamespace

import pytest

from gemini_brain.agent import loop, tools
from gemini_brain.config.settings import settings


def _ctx(**kw):
    return tools.ToolContext(organization_ids=[24], subject="t", deadline=time.monotonic() + 30, **kw)


def _source(n, title="Guide"):
    return {"n": n, "title": f"{title} {n}", "url": f"https://tax.gov.ae/{n}.pdf", "kind": "Guide", "issued": "2024-01-01",
            "effective": "", "excerpt": "..."}


# ── the block under the answer ──

def test_only_the_documents_the_answer_cites_are_listed_under_their_numbers():
    ctx = _ctx()
    ctx.fta_sources = [_source(1), _source(2), _source(3), _source(4)]
    ctx.fta_evidence = [5.0, 375000.0, 5.0]
    block = tools.fta_block(ctx, "Input tax needs a tax invoice [1][3]. The TRN has 15 digits [4].")
    assert block["type"] == "fta_sources" and [s["n"] for s in block["sources"]] == [1, 3, 4]
    assert block["evidence_numbers"] == [5.0, 375000.0] and block["jurisdiction"] == "UAE"
    assert "3. [Guide 3](https://tax.gov.ae/3.pdf)" in block["text"]


def test_a_comma_list_of_citations_counts():
    ctx = _ctx()
    ctx.fta_sources = [_source(1), _source(2), _source(3)]
    assert [s["n"] for s in tools.fta_block(ctx, "Both apply [1, 3].")["sources"]] == [1, 3]


def test_no_block_when_nothing_is_cited_or_nothing_was_searched():
    ctx = _ctx()
    assert tools.fta_block(ctx, "Answer [1].") is None                      # no search was made
    ctx.fta_sources = [_source(1)]
    assert tools.fta_block(ctx, "The bill total is 525.00.") is None        # a search was made but nothing is cited
    assert tools.fta_block(ctx, "") is None
    assert tools.fta_block(ctx, "Cites [9].") is None                       # a number with no source


# ── one numbering across several searches ──

class _Chunk:
    def __init__(self, url):
        self.url, self.title = url, url


def _fake_kb(monkeypatch, searches):
    """Each call of search() gives the next of `searches` (a list of the urls it finds, best first)."""
    seen = {"calls": []}

    def prompt_block(question, result, numbers=None):
        seen["calls"].append(dict(numbers or {}))
        return "SOURCES: " + ", ".join(f"[{numbers[c.url]}]" for c in result.sources())

    def sources_block(question, result, numbers=None):
        return {"sources": [{"n": numbers[c.url], "title": c.title, "url": c.url} for c in result.sources()],
                "evidence_numbers": [5.0]}

    iterator = iter(searches)

    class KB:
        def search(self, question):
            chunks = [_Chunk(u) for u in next(iterator)]
            return SimpleNamespace(hits=chunks, confident=True, sources=lambda: chunks)

    monkeypatch.setattr(settings, "vat_kb_enabled", True)
    monkeypatch.setattr(settings, "vat_kb_dir", "x")
    import gemini_brain.vat_kb.answer as answer_mod
    import gemini_brain.vat_kb.retrieval as retrieval_mod
    monkeypatch.setattr(answer_mod, "prompt_block", prompt_block)
    monkeypatch.setattr(answer_mod, "sources_block", sources_block)
    monkeypatch.setattr(retrieval_mod, "load_knowledge_base", lambda d: KB())
    return seen


def test_two_searches_in_one_answer_share_one_numbering(monkeypatch):
    seen = _fake_kb(monkeypatch, [["a.pdf", "b.pdf"], ["b.pdf", "c.pdf"]])
    ctx = _ctx()
    first, _ = tools.execute("search_vat_kb", {"question": "q1"}, ctx)
    second, _ = tools.execute("search_vat_kb", {"question": "q2"}, ctx)
    assert first["sources_and_rules"] == "SOURCES: [1], [2]"
    assert second["sources_and_rules"] == "SOURCES: [2], [3]"        # b.pdf is [2] in both; c.pdf is the next number
    assert [(s["n"], s["url"]) for s in ctx.fta_sources] == [(1, "a.pdf"), (2, "b.pdf"), (3, "c.pdf")]
    assert ctx.fta_evidence == [5.0, 5.0]


def test_another_request_starts_from_one_again(monkeypatch):
    _fake_kb(monkeypatch, [["a.pdf"], ["z.pdf"]])
    tools.execute("search_vat_kb", {"question": "q"}, _ctx())
    other = _ctx()
    out, _ = tools.execute("search_vat_kb", {"question": "q"}, other)
    assert out["sources_and_rules"] == "SOURCES: [1]" and [s["url"] for s in other.fta_sources] == ["z.pdf"]


# ── what the user gets ──

class _Adapter:
    script = []
    model_id = "m"

    def __init__(self, model_id, label=""):
        self.model_id = model_id

    def converse_with_tools(self, system, messages, tools_, **kw):
        return _Adapter.script.pop(0)

    def get_token_usage(self):
        return {"llm_calls": 1, "cost_usd": 0.0}


def _say(text):
    return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": text}]}}}


def _search():
    return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
        {"toolUse": {"toolUseId": "t1", "name": "search_vat_kb", "input": {"question": "input tax"}}}]}}}


@pytest.fixture
def agent(monkeypatch):
    monkeypatch.setattr(loop, "BedrockAdapter", _Adapter)
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop.law, "answer", lambda *a, **k: None)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "search_vat_kb"}}])
    monkeypatch.setattr(loop, "_no_cache", set())
    _fake_kb(monkeypatch, [["a.pdf", "b.pdf"]])


def test_an_agent_answer_that_cites_its_search_carries_the_sources_block(agent):
    _Adapter.script = [_search(), _say("Input tax needs a valid tax invoice [1]. The TRN has 15 digits [2].")]
    result = loop.run_agent("Can I claim the VAT?", [24], {})
    assert [b["type"] for b in result.blocks] == ["fta_sources"]
    assert [s["url"] for s in result.blocks[0]["sources"]] == ["a.pdf", "b.pdf"]


def test_an_answer_that_cites_nothing_carries_no_block(agent):
    _Adapter.script = [_search(), _say("Looks fine, nothing to cite.")]
    assert loop.run_agent("Can I claim the VAT?", [24], {}).blocks == []


def test_an_answer_without_a_vat_search_carries_no_block(agent):
    _Adapter.script = [_say("The total is 525.00 [1].")]
    assert loop.run_agent("Read it", [24], {}).blocks == []
