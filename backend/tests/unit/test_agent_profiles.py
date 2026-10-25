"""Specialist agents (agent/profiles.py): the registry, the tool filter, the prompt, the cash card and the badge."""
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from gemini_brain.agent import cash_card, loop, preview, profiles, tools
from gemini_brain.agent.prompt import system_prompt
from gemini_brain.semantic.cube_client import CubeError

ORGS = [25]
META = {25: {"name": "Org Two", "currency": "AED"}}
KNOWN_TOOLS = set(tools.DATA_TOOLS) | {"search_vat_kb", "app_guide", "vat_penalty", "read_document"}


# ── Registry ─────────────────────────────────────────────────────────────────

def test_the_general_agent_is_the_default_and_keeps_every_tool():
    general = profiles.get(None)
    assert general.key == profiles.DEFAULT_KEY and general.tools is None and general.allows("query_metrics")


def test_specialists_only_name_tools_that_exist():
    for p in profiles.all_profiles():
        assert p.tools is None or p.tools <= KNOWN_TOOLS, p.key
        assert p.key == profiles.DEFAULT_KEY or (p.run_prompt and p.run_label), p.key


def test_unknown_agent_key():
    assert profiles.get(" VAT ").key == "vat"
    with pytest.raises(profiles.UnknownAgent):
        profiles.get("payroll")


def test_resolve_prefers_the_request_then_the_thread():
    assert profiles.resolve("cash", "vat").key == "cash"
    assert profiles.resolve(None, "vat").key == "vat"
    assert profiles.resolve(None, "retired-agent").key == profiles.DEFAULT_KEY
    with pytest.raises(profiles.UnknownAgent):
        profiles.resolve("retired-agent")


def test_the_reporting_agent_asks_for_both_files_and_only_reads_figures():
    from gemini_brain.artifacts.delivery import detect_delivery, named_formats
    reporting = profiles.get("reporting")
    assert reporting.tools == frozenset({"query_metrics", "list_documents"})
    delivery = detect_delivery(reporting.run_prompt)
    assert delivery.wants_file and delivery.wants_chart
    assert set(named_formats(reporting.run_prompt)) == {"pdf", "xlsx"}
    assert preview.attachment(reporting.run_prompt) == "XLSX and PDF files and chart"


def test_public_shape():
    public = profiles.get("vat").to_public()
    assert public["name"] == "VAT Agent" and "review_vat" in public["tools"] and public["default"] is False
    assert profiles.get(None).to_public()["default"] is True


# ── Tool filter ──────────────────────────────────────────────────────────────

def _ctx(key):
    return tools.ToolContext(organization_ids=list(ORGS), subject="test", deadline=time.monotonic() + 60,
                             allowed_tools=profiles.get(key).tools)


def test_specs_offer_only_the_agents_tools(monkeypatch):
    def no_cube(*a, **k):
        raise CubeError("down")
    monkeypatch.setattr(tools, "get_catalog", no_cube)
    assert [s["toolSpec"]["name"] for s in tools.specs(_ctx("vat"))] == ["search_vat_kb", "vat_penalty"]
    assert tools.specs(_ctx("cash")) == []
    assert len(tools.specs(_ctx("accutax"))) == 3


def test_a_tool_outside_the_agent_is_refused_and_never_runs(monkeypatch):
    monkeypatch.setattr(tools.query_metrics, "run", lambda *a, **k: pytest.fail("must not run"))
    result, ok = tools.execute("query_metrics", {"view": "pnl"}, _ctx("vat"))
    assert not ok and "not available to this agent" in result["error"]


# ── Prompt ───────────────────────────────────────────────────────────────────

def _prompt(profile=None):
    import datetime as dt
    return system_prompt(META, ORGS, dt.date(2026, 10, 25), "Asia/Dubai", profile=profile)


def test_the_general_prompt_is_unchanged_by_profiles():
    assert _prompt() == _prompt(profiles.get(None))
    assert _prompt().startswith("You are Accutax AI,") and "Your agent" not in _prompt()


def test_a_specialist_prompt_names_the_agent_and_adds_its_instructions():
    text = _prompt(profiles.get("cash"))
    assert text.startswith("You are the Cash Agent of Accutax AI")
    assert profiles.get("cash").instructions in text and "only the tools offered to you" in text


# ── Loop ─────────────────────────────────────────────────────────────────────

class FakeAdapter:
    systems = []

    def __init__(self, model_id, label=""):
        self.model_id = model_id

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        FakeAdapter.systems.append(system[0]["text"])
        return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": "Done."}]}}}

    def get_token_usage(self):
        return {"llm_calls": 1, "cost_usd": 0.0}


@pytest.fixture
def fake_loop(monkeypatch):
    FakeAdapter.systems = []
    seen = {}
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: seen.setdefault("ctx", ctx) and [])
    return seen


def test_an_agent_without_the_law_tool_never_takes_the_law_route(monkeypatch, fake_loop):
    monkeypatch.setattr(loop.law, "answer", lambda *a, **k: pytest.fail("law route must be skipped"))
    result = loop.run_agent("What is the VAT rate in the UAE?", ORGS, META, profile=profiles.get("cash"))
    assert result.route == "tools" and fake_loop["ctx"].allowed_tools == profiles.get("cash").tools
    assert FakeAdapter.systems[0].startswith("You are the Cash Agent")


def test_the_vat_agent_keeps_the_law_route(monkeypatch, fake_loop):
    monkeypatch.setattr(loop.law, "answer", lambda question, messages, brief=False: loop.law.LawAnswer(
        answer="5% [1].", usage={}))
    result = loop.run_agent("What is the VAT rate in the UAE?", ORGS, META, profile=profiles.get("vat"))
    assert result.route == "law"


# ── Cash card ────────────────────────────────────────────────────────────────

def _forecast(oid, name, closing):
    weeks = [{"week_start": start, "week_end": end, "closing_cash": str(c)} for (start, end), c in zip(
        (("2026-10-25", "2026-10-25"), ("2026-10-26", "2026-11-01"), ("2026-11-02", "2026-11-08")), closing)]
    return {"organization_id": oid, "organization_name": name, "currency": "AED", "opening_cash": "1000.00",
            "weeks": weeks, "closing_cash": str(closing[-1]), "lowest_closing_cash": str(min(closing)),
            "overdue_receivables": "250.00", "overdue_payables": "75.50"}


def test_cash_card_for_one_organization_has_tiles_and_a_line_chart():
    data = [{"tool": "cash_forecast", "input": {"weeks": 3},
             "result": {"period": "2026-10-26 to 2026-11-15 (weeks start on Monday)",
                        "organizations": [_forecast(25, "Org Two", [900, -50, 300])]}}]
    grid, chart = cash_card.cash_blocks(data)
    assert grid["type"] == "kpi_grid" and grid["period"] == "2026-10-26 to 2026-11-15"
    assert [i["label"] for i in grid["items"]][:3] == ["Cash today", "Lowest week", "Closing cash"]
    assert grid["items"][1]["value"] == "AED -50.00"
    assert chart["chart_type"] == "line" and chart["categories"] == ["19 Oct", "26 Oct", "02 Nov"]
    assert chart["series"] == [{"name": "Org Two", "data": [900.0, -50.0, 300.0]}]


def test_cash_card_for_several_organizations_is_one_chart_without_tiles():
    data = [{"tool": "cash_forecast", "result": {"organizations": [
        _forecast(25, "Org Two", [1, 2, 3]), _forecast(26, "Org Three", [4, 5, 6])]}}]
    blocks = cash_card.cash_blocks(data)
    assert [b["type"] for b in blocks] == ["chart"] and len(blocks[0]["series"]) == 2


def test_no_cash_card_without_a_forecast():
    assert cash_card.cash_blocks([{"tool": "query_metrics", "result": {"rows": []}}]) == []
    assert cash_card.cash_blocks([{"tool": "cash_forecast", "result": {"organizations": [{"weeks": []}]}}]) == []


# ── Badge, chip and request check ────────────────────────────────────────────

def test_badge_only_for_specialists():
    assert preview.agent_badge(profiles.get(None)) == []
    assert preview.agent_badge(profiles.get("ap")) == [
        {"type": "agent_badge", "key": "ap", "name": "AP / Bills Agent", "icon": "file-check"}]


def test_the_model_chip_names_the_specialist():
    model = SimpleNamespace(key="default", label="Sonnet 5")
    assert preview._policy(model, "tools", picker=False, profile=profiles.get("vat"))["model_label"] == "VAT Agent"
    assert preview._policy(model, "tools", picker=True, profile=profiles.get("vat"))["model_label"] == "VAT Agent · Sonnet 5"
    assert preview._policy(model, "tools", picker=False, profile=profiles.get(None))["model_label"] == preview.PRIMARY_LABEL


def test_an_unknown_agent_in_the_request_is_a_400():
    from gemini_brain.api import routes
    assert routes._agent_key(SimpleNamespace(agent=None)) is None
    assert routes._agent_key(SimpleNamespace(agent="Cash")) == "cash"
    with pytest.raises(HTTPException) as e:
        routes._agent_key(SimpleNamespace(agent="payroll"))
    assert e.value.status_code == 400
