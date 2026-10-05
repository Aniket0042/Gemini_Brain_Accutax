"""Phase 2 tests for the VAT knowledge base: the detector (R8), the answer prompt and Sources block
(R9-R11), the runner hook (R12), the off switch, shadow mode and every fallback."""
import time
from unittest.mock import MagicMock, patch

import pytest

from gemini_brain.config.settings import settings
from gemini_brain.vat_kb import augment as augment_mod
from gemini_brain.vat_kb.answer import VAT_ANSWER_RULES, prompt_block, sources_block
from gemini_brain.vat_kb.augment import augment
from gemini_brain.vat_kb.detector import is_vat_law_question
from gemini_brain.vat_kb.index import Chunk
from gemini_brain.vat_kb.retrieval import Hit, SearchResult

# ── R8: detector ──────────────────────────────────────────────────────────────
VAT_LAW = [
    "What changed in UAE VAT from 1 January 2026 under Federal Decree-Law No. 16 of 2025?",
    "What checks must a business carry out on its suppliers so its input tax is not denied?",
    "How do I value a deemed supply of services?",
    "We receive payment in USDT. How do we convert it to AED for VAT?",
    "A company leaves our VAT tax group mid-year. What output tax and input tax adjustments are needed?",
    "What are the current late payment penalties for VAT?",
    "A mainland company buys goods from another company in a designated zone and has them shipped to Dubai mainland. Who charges VAT, and when?",
    "We sell used cars that we bought from individuals. Can we use the profit margin scheme?",
    "A UAE national builds a villa and rents one floor out. Can they still claim the new-residence refund?",
    "We bought an entire business, including staff and stock. Do we charge or reclaim VAT on the transfer?",
    "Our employees get company mobile phones with unlimited data, and they also use them personally. Can we reclaim the input tax in full?",
    "Taxable supplies AED 6,000,000, exempt supplies AED 4,000,000, residual input tax AED 100,000. How much can we recover under the standard method?",
    "An invoice for AED 105,000 including VAT; the customer paid 40%. How much output tax can we adjust under bad debt relief?",
    "First sale of a residential building 2 years after completion, then a resale in year 5. What is the VAT treatment of each?",
    "Labour accommodation: residential (exempt) or serviced (standard-rated)? What decides it?",
    "A board member who is a natural person receives director fees. Who must register, and when is the date of supply?",
    "We export consultancy services to a Saudi client whose manager visits Dubai. Is the supply still zero-rated?",
    "We re-invoice courier fees to a client at cost. Is this a disbursement, or part of our supply?",
    "We sell smartphones to a VAT-registered reseller. Who accounts for the VAT?",
    "How long must we keep VAT records for general supplies, and for real estate?",
    "When does e-invoicing become mandatory for a business with AED 60 million revenue?",
    "What is the penalty for not issuing an e-invoice?",
    "What is the criteria for e-invoicing currently?",
    "What is the flow of e-invoicing in UAE?",
    "At what turnover must a business register for VAT?",
    "How long does a tourist have to claim back VAT?",
    "We buy scrap metal from a registered supplier for recycling. Who pays the VAT?",
    "Who can claim back the VAT spent on building and running a mosque?",
    "When can we issue a simplified tax invoice instead of a full one?",
    "A wholesaler sells gold jewellery to a VAT-registered retailer who will resell it. Who accounts for the VAT?",
    # A period with a law-only term is still a law question.
    "We found an error in last quarter's return that understated tax by AED 8,000. Voluntary disclosure, "
    "or correct it in the next return?",
    "Can we claim bad debt relief on last year's invoice?",
]
NOT_VAT_LAW = [
    "Show me my VAT payable for this quarter",
    "How much VAT did we collect last month?",
    "What is our VAT liability this year?",
    "List my invoices with VAT for March",
    "Which customers have overdue invoices?",
    "Show the balance sheet for March",
    "Give me the trial balance",
    "Top 5 customers by revenue",
    "How do I create a new invoice in Accutax?",
    "How do I generate a VAT return in Accutax?",
    "Where can I find the expense module?",
    "Where is the tax rates screen?",
    "How do I add a new user to my organisation?",
    "What is double-entry bookkeeping?",
    "Explain the difference between accounts payable and accounts receivable",
    "What is depreciation?",
    "What is EBITDA?",
    "Give me a business health check",
    "Forecast cash flow for the next three months",
    "What is the capital of France?",
    "Write me a poem about the sea",
    "How do I reset my password?",
    "Summarise this conversation",
    "How do I connect my bank account?",
    "What was our total revenue last quarter?",
    "Show my profit and loss",
    "Export the dashboard to PDF",
    "How do I reconcile my bank statement?",
    "What is working capital?",
    "Hello",
    "What VAT did we charge on invoice INV-1043?",
    "Can we recover the input tax on last quarter's purchases?",
    "Show our VAT payable last quarter",
    "How much penalty did we pay last quarter?",
    "What was in our VAT return last quarter?",
    "Show our voluntary disclosures from last year",
]


@pytest.mark.parametrize("question", VAT_LAW)
def test_detector_accepts_vat_law(question):
    assert is_vat_law_question(question)


@pytest.mark.parametrize("question", NOT_VAT_LAW)
def test_detector_rejects_other_questions(question):
    assert not is_vat_law_question(question)


# ── R9-R11: prompt and sources ────────────────────────────────────────────────
def _chunk(i, title, url, body, issue="Nov 04, 2025", effective=""):
    return Chunk(i, i, title, "VAT", "statute", issue, effective, url, f"{title} | issued {issue}\n{body}")


def _result(confident=True):
    hits = [
        Hit(_chunk(0, "Cabinet Decision No. 153 of 2025", "https://tax.gov.ae/a.pdf",
                   "The Recipient shall account for the Tax.", effective="14 January 2026"), 0.8, 30, 1),
        Hit(_chunk(1, "VATP047 Metal Scrap", "https://tax.gov.ae/b.pdf", "Declarations are required.", issue="NA"), 0.7, 20, 2),
        Hit(_chunk(2, "Cabinet Decision No. 153 of 2025", "https://tax.gov.ae/a.pdf",
                   "Zero-rated supplies are excluded.", effective="14 January 2026"), 0.6, 10, 3),
    ]
    return SearchResult("q", "q (metal scrap)", hits if confident else [], confident,
                        "" if confident else "weak match", 0.8, 30.0)


def test_prompt_block_numbers_sources_and_groups_chunks():
    block = prompt_block("We buy scrap metal for recycling. Who pays the VAT?", _result())
    assert VAT_ANSWER_RULES.strip() in block
    assert ("[1] Cabinet Decision No. 153 of 2025 | Law or decision | issued Nov 04, 2025 | "
            "effective 14 January 2026") in block
    assert "[2] VATP047 Metal Scrap" in block and "issued NA" not in block
    # both chunks of source 1 sit under its single heading, without repeating the header line
    one = block.split("[1] Cabinet")[1].split("[2] VATP047")[0]
    assert "The Recipient shall account" in one and "Zero-rated supplies are excluded" in one
    assert "CALCULATIONS (exact):" not in block


def test_prompt_block_adds_calculations():
    q = "An invoice for AED 105,000 including VAT; the customer paid 40%. Bad debt relief?"
    block = prompt_block(q, _result())
    assert "CALCULATIONS (exact):" in block and "AED 3,000.00" in block


def test_prompt_block_gives_monthly_rate_of_a_yearly_penalty():
    hits = [Hit(_chunk(1, "Cabinet Decision 129 of 2025", "https://tax.gov.ae/p.pdf",
                       "A monthly penalty of (14%) per annum, for each month or part thereof."), 0.8, 20, 1)]
    result = SearchResult("q", "q", hits, True, "", 0.8, 20.0)
    block = prompt_block("What is the late payment penalty?", result)
    assert "14% per annum charged monthly = 1.17% per month" in block
    assert 1.17 in sources_block("What is the late payment penalty?", result)["evidence_numbers"]


def test_answer_rules_ask_for_structure_and_no_self_introduction():
    assert "**Step 1 - " in VAT_ANSWER_RULES and "**Action:**" in VAT_ANSWER_RULES
    assert "**Documents to keep**" in VAT_ANSWER_RULES and "200 to 400 words" in VAT_ANSWER_RULES
    assert "not after every sub-bullet" in VAT_ANSWER_RULES and "never\n    cite CALCULATIONS" in VAT_ANSWER_RULES
    assert "introduce yourself" in VAT_ANSWER_RULES and "Let me explain" in VAT_ANSWER_RULES
    assert "no closing summary" in VAT_ANSWER_RULES
    assert "a previous rate, amount or penalty from guidance alone" in VAT_ANSWER_RULES


def test_sources_block_lists_documents_for_chips_and_list():
    block = sources_block("Who pays VAT on metal scrap?", _result())
    assert block["type"] == "fta_sources" and block["jurisdiction"] == "UAE"
    one, two = block["sources"]
    assert one["n"] == 1 and one["title"] == "Cabinet Decision No. 153 of 2025"
    assert one["url"] == "https://tax.gov.ae/a.pdf" and one["kind"] == "Law or decision"
    assert one["issued"] == "Nov 04, 2025" and one["effective"] == "14 January 2026"
    assert one["excerpt"].startswith("The Recipient shall account")      # best passage, header line removed
    assert two["issued"] == ""                                            # "NA" is not shown
    assert len(block["sources"]) == 2                                     # one entry per document
    # fallback for clients that do not know the block type yet
    assert "1. [Cabinet Decision No. 153 of 2025](https://tax.gov.ae/a.pdf) (issued Nov 04, 2025)" in block["text"]


def test_sources_block_carries_numbers_from_the_passages():
    block = sources_block("An invoice for AED 105,000 including VAT; the customer paid 40%. Bad debt relief?", _result())
    numbers = set(block["evidence_numbers"])
    assert {153.0, 2025.0, 14.0, 2026.0} <= numbers           # from the passages and headers
    assert 3000.0 in numbers and 63000.0 in numbers          # from the exact calculations


def test_long_excerpt_is_cut_at_a_word():
    from gemini_brain.vat_kb.answer import _excerpt
    text = "Title line" + chr(10) + "word " * 200
    out = _excerpt(text, limit=50)
    assert out.endswith(" …") and len(out) <= 52 and "Title line" not in out


# ── augment(): switch, question types, fallbacks, shadow ──────────────────────
QUESTION = "We buy scrap metal from a registered supplier for recycling. Who pays the VAT?"
SYSTEM = "BASE SYSTEM PROMPT"


@pytest.fixture
def kb_on(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", True)
    monkeypatch.setattr(settings, "vat_kb_shadow", False)
    monkeypatch.setattr(settings, "vat_kb_dir", "E:/not-used-in-tests")
    monkeypatch.setattr(settings, "vat_kb_model_id", "")
    monkeypatch.setattr(settings, "vat_kb_timeout_seconds", 1.0)


def _unchanged(v):
    return v.system == SYSTEM and v.model_id is None and v.blocks == []


def test_switch_off_changes_nothing_and_never_searches(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    monkeypatch.setattr(settings, "vat_kb_shadow", False)
    monkeypatch.setattr(settings, "vat_kb_dir", "E:/x")
    search = MagicMock()
    monkeypatch.setattr(augment_mod, "_search", search)
    v = augment(QUESTION, 6, SYSTEM)
    assert _unchanged(v) and v.trace_event is None
    search.assert_not_called()


def test_no_directory_changes_nothing(kb_on, monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_dir", "")
    assert _unchanged(augment(QUESTION, 6, SYSTEM))


@pytest.mark.parametrize("qtype", [3, 4, 5])
def test_other_question_types_change_nothing(kb_on, monkeypatch, qtype):
    search = MagicMock()
    monkeypatch.setattr(augment_mod, "_search", search)
    v = augment(QUESTION, qtype, SYSTEM)
    assert _unchanged(v) and v.trace_event is None
    search.assert_not_called()


def test_non_vat_question_changes_nothing(kb_on, monkeypatch):
    search = MagicMock()
    monkeypatch.setattr(augment_mod, "_search", search)
    assert _unchanged(augment("What is double-entry bookkeeping?", 6, SYSTEM))
    search.assert_not_called()


def test_confident_result_adds_sources_model_and_trace(kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    v = augment(QUESTION, 6, SYSTEM)
    assert v.system.startswith(SYSTEM) and "UAE VAT KNOWLEDGE BASE" in v.system
    assert v.model_id == settings.bedrock_model_id
    assert v.blocks and v.blocks[0]["type"] == "fta_sources"
    assert v.trace_event["status"] == "used" and v.trace_event["sources"][0] == "Cabinet Decision No. 153 of 2025"


def test_model_setting_overrides_default(kb_on, monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_model_id", "in.anthropic.claude-sonnet-5")
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    assert augment(QUESTION, 1, SYSTEM).model_id == "in.anthropic.claude-sonnet-5"


@pytest.mark.parametrize("search,reason", [
    (lambda q: None, "no knowledge-base build"),
    (lambda q: _result(confident=False), "weak match"),
])
def test_fallbacks_keep_todays_answer(kb_on, monkeypatch, search, reason):
    monkeypatch.setattr(augment_mod, "_search", search)
    v = augment(QUESTION, 6, SYSTEM)
    assert _unchanged(v)
    assert v.trace_event["status"] == "fallback" and reason in v.trace_event["reason"]


def test_search_error_keeps_todays_answer(kb_on, monkeypatch):
    def boom(q):
        raise RuntimeError("index file missing")
    monkeypatch.setattr(augment_mod, "_search", boom)
    v = augment(QUESTION, 6, SYSTEM)
    assert _unchanged(v)


def test_timeout_keeps_todays_answer(kb_on, monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_timeout_seconds", 0.1)
    monkeypatch.setattr(augment_mod, "_search", lambda q: time.sleep(1) or _result())
    started = time.perf_counter()
    v = augment(QUESTION, 6, SYSTEM)
    assert time.perf_counter() - started < 0.8
    assert _unchanged(v) and v.trace_event["reason"] == "timeout"


def test_shadow_mode_searches_in_background_without_changing_answer(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    monkeypatch.setattr(settings, "vat_kb_shadow", True)
    monkeypatch.setattr(settings, "vat_kb_dir", "E:/x")
    called = []
    monkeypatch.setattr(augment_mod, "_search", lambda q: called.append(q) or _result())
    v = augment(QUESTION, 6, SYSTEM)
    assert _unchanged(v) and v.trace_event is None
    for _ in range(50):
        if called:
            break
        time.sleep(0.02)
    assert called == [QUESTION]


def test_warm_up_does_nothing_when_off(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    monkeypatch.setattr(settings, "vat_kb_shadow", False)
    assert augment_mod.warm_up_in_background() is None


# ── Runner hook (R12): the knowledge-answer path end to end, with Bedrock mocked ──
def _runner():
    from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
    runner = GeminiBrainRunner(api_key="test-api-key")
    runner._call_llm = MagicMock(return_value=("The recipient accounts for the VAT [1].", 10, 5))
    return runner


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_switch_off_uses_default_model_and_no_sources(mock_intent, monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    monkeypatch.setattr(settings, "vat_kb_shadow", False)
    mock_intent.return_value = ({"type": 6, "reason": "concept"}, 10, 5)
    runner = _runner()
    res = runner.run("Who accounts for VAT on metal scrap sold between registrants?", organization_id=1)
    kwargs = runner._call_llm.call_args.kwargs
    assert "model_id" not in kwargs and kwargs["purpose"] == "direct_answer"
    assert "UAE VAT KNOWLEDGE BASE" not in runner._call_llm.call_args.args[0]
    assert not any(b.get("type") == "fta_sources" for b in res["blocks"])
    assert not any(e.get("step") == "vat_kb" for e in res["agent_trace"])


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_switch_on_answers_with_sources(mock_intent, kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    mock_intent.return_value = ({"type": 6, "reason": "concept"}, 10, 5)
    runner = _runner()
    res = runner.run("Who pays the VAT when we buy metal scrap from a registered supplier for recycling?",
                     organization_id=1)
    kwargs = runner._call_llm.call_args.kwargs
    assert kwargs["model_id"] == settings.bedrock_model_id
    assert "UAE VAT KNOWLEDGE BASE" in runner._call_llm.call_args.args[0]
    assert res["answer"].startswith("The recipient accounts")
    assert any(b.get("type") == "fta_sources" and b["sources"][0]["url"] == "https://tax.gov.ae/a.pdf"
               for b in res["blocks"])
    assert any(e.get("step") == "vat_kb" and e.get("status") == "used" for e in res["agent_trace"])


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_switch_on_with_broken_kb_answers_as_today(mock_intent, kb_on, monkeypatch):
    def boom(q):
        raise RuntimeError("corrupt build")
    monkeypatch.setattr(augment_mod, "_search", boom)
    mock_intent.return_value = ({"type": 6, "reason": "concept"}, 10, 5)
    runner = _runner()
    res = runner.run("Is the reverse charge applied to metal scrap supplies between registrants?", organization_id=1)
    assert "model_id" not in runner._call_llm.call_args.kwargs
    assert "UAE VAT KNOWLEDGE BASE" not in runner._call_llm.call_args.args[0]
    assert res["status"] == "ok" and res["answer"]


def test_app_guidance_type_is_allowed_for_law_questions(kb_on, monkeypatch):
    # the "How do I ..." pre-router labels "How do I value a deemed supply?" as type 2
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    assert augment("How do I value a deemed supply of services?", 2, SYSTEM).model_id


# ── reroute(): VAT law questions the classifier sent to a data path ───────────
BAD_DEBT = ("An invoice for AED 105,000 including VAT; the customer paid 40%. "
            "How much output tax can we adjust under bad debt relief?")


def test_reroute_off_by_default(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    search = MagicMock()
    monkeypatch.setattr(augment_mod, "_search", search)
    assert augment_mod.reroute(BAD_DEBT, 4, "llm") is None
    search.assert_not_called()


def test_reroute_law_question_from_data_type(kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    assert augment_mod.reroute(BAD_DEBT, 4, "llm") == 6
    assert augment_mod.reroute(BAD_DEBT, 3, "llm") == 6


@pytest.mark.parametrize("query,qtype,source", [
    (BAD_DEBT, 4, "fast"),                                    # rule-based fast router matched an endpoint
    (BAD_DEBT, 6, "llm"),                                     # already a knowledge answer
    ("Show me my VAT payable for this quarter", 4, "llm"),    # the user's own figures
    ("What was our total revenue last quarter?", 4, "llm"),   # not VAT law
])
def test_reroute_keeps_router_decision(kb_on, monkeypatch, query, qtype, source):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    assert augment_mod.reroute(query, qtype, source) is None


def test_reroute_needs_confident_match(kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result(confident=False))
    assert augment_mod.reroute(BAD_DEBT, 4, "llm") is None


def test_reroute_error_keeps_router_decision(kb_on, monkeypatch):
    def boom(q):
        raise RuntimeError("no index")
    monkeypatch.setattr(augment_mod, "_search", boom)
    assert augment_mod.reroute(BAD_DEBT, 4, "llm") is None


def test_rerouted_question_is_searched_once(kb_on, monkeypatch):
    calls = []
    monkeypatch.setattr(augment_mod, "_search", lambda q: calls.append(q) or _result())
    assert augment_mod.reroute(BAD_DEBT, 4, "llm") == 6
    assert augment(BAD_DEBT, 6, SYSTEM).model_id
    assert calls == [BAD_DEBT]


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_reroutes_vat_question_classified_as_data(mock_intent, kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    mock_intent.return_value = ({"type": 4, "reason": "has amounts"}, 10, 5)
    runner = _runner()
    q = ("An invoice for AED 210,000 including VAT; the customer paid 25%. "
         "How much output tax can we adjust under bad debt relief?")
    res = runner.run(q, organization_id=1, use_api=False)
    assert res["routing_info"]["path"] == "gemini_direct" and res["routing_info"]["type"] == 6
    assert runner._call_llm.call_args.kwargs["model_id"] == settings.bedrock_model_id
    assert any(e.get("step") == "vat_kb" and e.get("status") == "used" for e in res["agent_trace"])


# ── Multi-org planner: VAT law questions are answered once, not as a per-org VAT figure ──
CHANGED_2026 = "What changed in UAE VAT from 1 January 2026 under Federal Decree-Law No. 16 of 2025?"


def _plan(query):
    from gemini_brain.orchestrator import multi_org_plan
    runner = MagicMock()
    with patch.object(multi_org_plan, "fast_route", return_value=None), \
         patch.object(multi_org_plan, "_how_to_guide_section", return_value=None), \
         patch.object(multi_org_plan, "classify_intent", return_value=({"type": 4, "reason": "vat"}, 5, 2)), \
         patch.object(multi_org_plan, "select_endpoint", return_value=(None, 0, 0)):
        return multi_org_plan.plan_query(query, 5, runner, 1)


def test_multi_org_vat_law_question_is_answered_once_when_on(kb_on, monkeypatch):
    from gemini_brain.orchestrator.multi_org_plan import DIRECT
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    plan = _plan(CHANGED_2026)
    assert plan.kind == DIRECT and plan.source == "vat_kb"


def test_multi_org_planning_unchanged_when_off(monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    search = MagicMock()
    monkeypatch.setattr(augment_mod, "_search", search)
    plan = _plan(CHANGED_2026)
    assert plan is None or plan.source != "vat_kb"
    search.assert_not_called()


def test_multi_org_vat_figure_question_still_fetches_data(kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    plan = _plan("Show me my VAT payable for this quarter")
    assert plan is None or plan.source != "vat_kb"


def test_multi_org_weak_match_keeps_todays_planning(kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result(confident=False))
    plan = _plan(CHANGED_2026)
    assert plan is None or plan.source != "vat_kb"


@patch("gemini_brain.orchestrator.gemini_brain_runner._guide_link_block",
       return_value={"type": "action_button", "label": "Open in Accutax", "url": "http://x"})
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_drops_app_button_on_vat_answer(mock_intent, _guide, kb_on, monkeypatch):
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    mock_intent.return_value = ({"type": 6, "reason": "concept"}, 10, 5)
    res = _runner().run("Who accounts for VAT on metal scrap supplies between registrants?", organization_id=1)
    assert not any(b.get("type") == "action_button" for b in res["blocks"])


@patch("gemini_brain.orchestrator.gemini_brain_runner._guide_link_block",
       return_value={"type": "action_button", "label": "Open in Accutax", "url": "http://x"})
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_runner_keeps_app_button_when_off(mock_intent, _guide, monkeypatch):
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    mock_intent.return_value = ({"type": 1, "reason": "faq"}, 10, 5)
    res = _runner().run("Which screen shows the VAT settings?", organization_id=1)
    assert any(b.get("type") == "action_button" for b in res["blocks"])


@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_vat_answer_figures_count_as_grounded(mock_intent, kb_on, monkeypatch):
    # figures quoted from the FTA passages must not show up as "unverified"
    monkeypatch.setattr(augment_mod, "_search", lambda q: _result())
    mock_intent.return_value = ({"type": 6, "reason": "concept"}, 10, 5)
    runner = _runner()
    runner._call_llm = MagicMock(return_value=(
        "Under Cabinet Decision No. 153 of 2025 the recipient accounts for VAT from 14 January 2026 [1].", 10, 5))
    res = runner.run("Who accounts for VAT on metal scrap between registrants?", organization_id=1,
                     model="claude-sonnet", effort="exhaustive")
    verification = res.get("verification") or {}
    assert verification.get("unmatched", []) == []


def test_tidy_answer_drops_lead_in_and_uncited_closing_only():
    from gemini_brain.vat_kb.answer import tidy_answer
    body = "**Key points:**\n- **Rate:** 14% per annum [1]"
    assert tidy_answer("Let me explain the penalty.\n\n" + body) == body
    assert tidy_answer("Based on the latest rules, here are the requirements:\n\n" + body) == body
    assert tidy_answer(body + "\n\nThis is a significant change for businesses.") == body
    cited = "The penalty is 14% per annum [1].\n\n" + body + "\n\nThis replaced the old rules [2]."
    assert tidy_answer(cited) == cited                      # facts with citations are never removed


def test_vat_law_question_is_not_rewritten_in_a_multi_org_thread(monkeypatch):
    from gemini_brain.config.settings import settings
    from gemini_brain.orchestrator.multi_org import needs_rewrite
    monkeypatch.setattr(settings, "vat_kb_enabled", True)
    monkeypatch.setattr(settings, "vat_kb_dir", "E:/kb")
    # asked after a late-payment question, this was rewritten into the late-payment question again
    assert not needs_rewrite("How do I value a deemed supply of services?")
    assert needs_rewrite("and for exports?") and needs_rewrite("What about free zones?")
    monkeypatch.setattr(settings, "vat_kb_enabled", False)
    assert needs_rewrite("How do I value a deemed supply of services?")      # switch off: as before
