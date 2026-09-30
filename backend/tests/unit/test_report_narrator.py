"""Phase 4: the report narrator — fact brief, cited JSON, fallbacks, grounding."""
import io
import json
import time

import pytest

from gemini_brain.artifacts import narrator
from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.artifacts.generate import render_docx, render_md
from gemini_brain.artifacts.insights import add_takeaways
from gemini_brain.artifacts.ir import ReportDocument
from gemini_brain.artifacts.narrator import build_brief, classify_report, parse_narrative
from gemini_brain.artifacts.report_spec import build_report_spec

MONTHLY = [
    {"month": "April", "revenue": 52400, "expenses": 34100},
    {"month": "May", "revenue": 58200, "expenses": 36800},
    {"month": "June", "revenue": 63700, "expenses": 43760},
]
PNL = {
    "statement": "profit_and_loss",
    "period": {"label": "Q2 2026"},
    "revenue": {"total": 174300, "line_items": [{"name": "Services", "amount": 174300}]},
    "expenses": {"total": 114660, "line_items": [
        {"name": "Cost of Goods", "amount": 52290},
        {"name": "Payroll", "amount": 42600},
        {"name": "Overheads", "amount": 17430},
        {"name": "Marketing", "amount": 2340},
    ]},
    "net_profit": 59640,
    "margin_pct": 34.2,
    "monthly": MONTHLY,
}
CUSTOMERS = [{"customer_name": n, "sales": v} for n, v in [
    ("Apex Retail", 420500), ("Falcon Energy", 91310), ("Al Habtoor", 81755),
    ("Marina Foods", 46400), ("Desert Labs", 32210),
]]


def _doc(data, query):
    return add_takeaways(ReportDocument.from_spec(build_report_spec(data, query), query=query))


def _spec(query, data):
    return next(b for b in attach_delivery(query, data, []) if b["type"] == "canvas")["spec"]


def _fake(payload):
    return lambda system, user: json.dumps(payload)


# ── the brief ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("data,query,kind", [
    (PNL, "P&L report", "pnl"),
    (CUSTOMERS, "sales by customer", "ranking"),
    ([{"month": m["month"], "revenue": m["revenue"]} for m in MONTHLY], "revenue trend", "trend"),
    ([{"customer_name": "A", "outstanding_amount": 100}, {"customer_name": "B", "outstanding_amount": 50}],
     "outstanding receivables", "aging"),
    ([{"month": "April", "vat_amount": 100}, {"month": "May", "vat_amount": 120}], "VAT by month", "tax"),
])
def test_report_kind(data, query, kind):
    assert classify_report(_doc(data, query)) == kind


def test_brief_states_figures_exactly_as_the_report_prints_them():
    brief = build_brief(_doc(PNL, "P&L report"))
    text = brief.render()
    assert "F1: Total Revenue: AED 174,300.00." in text
    assert "Profit bridge — Cost of Goods reduces profit by AED 52,290.00." in text
    assert "Revenue changed by 21.6% from April to June." in text
    assert [fid for fid, _ in brief.facts] == [f"F{i}" for i in range(1, len(brief.facts) + 1)]


def test_brief_includes_failed_checks_as_caveats():
    spec = _spec("P&L report as pdf", dict(PNL, net_profit=70000))
    assert any("does not reconcile" in r or "confirm before relying" in r
               for r in spec["narrative_parts"]["risks"])


# ── parsing the model's answer ───────────────────────────────────────────────

def test_citations_are_required_checked_and_stripped():
    brief = build_brief(_doc(CUSTOMERS, "sales by customer"))
    raw = json.dumps({
        "headline": "Apex Retail dominates sales [F1].",
        "summary": ["Apex Retail holds 62.6% of sales [F1, F2]", "This one cites a fact that does not exist [F999]."],
        "drivers": ["An uncited claim with no support."],
        "risks": [],
        "actions": ["Review exposure to Apex Retail [F2]."],
    })
    section = parse_narrative(raw, brief)
    assert section.headline == "Apex Retail dominates sales"
    assert section.summary == ["Apex Retail holds 62.6% of sales."]
    assert section.drivers == []
    assert section.actions == ["Review exposure to Apex Retail."]
    assert "[F" not in section.markdown


def test_unusable_model_output_gives_no_section():
    brief = build_brief(_doc(CUSTOMERS, "sales by customer"))
    assert parse_narrative("I'm sorry, I cannot help with that.", brief) is None


# ── choosing the narrative source ────────────────────────────────────────────

def test_model_narrative_is_used_and_marked(monkeypatch):
    monkeypatch.setattr(narrator, "_model_call", _fake({
        "headline": "Net profit of AED 59,640.00 on revenue of AED 174,300.00 [F1, F3]",
        "summary": ["The margin was 34.2% [F4]."],
        "drivers": ["Revenue changed by 21.6% from April to June [F10]."],
        "risks": ["Expenses changed by 28.3% from April to June, faster than revenue [F14, F10]."],
        "actions": ["Review Cost of Goods, the largest cost [F16]."],
    }))
    spec = _spec("P&L report as pdf", PNL)
    parts = spec["narrative_parts"]
    assert parts["origin"] == "report"
    assert parts["headline"] == "Net profit of AED 59,640.00 on revenue of AED 174,300.00"
    assert spec["insight"] == parts["headline"]
    assert spec["integrity"]["sentences_removed"] == 0
    assert any("written from the verified figures" in line for line in spec["methodology"])


def test_model_failure_falls_back_to_template():
    spec = _spec("P&L report as pdf", PNL)  # conftest makes the model unavailable
    parts = spec["narrative_parts"]
    assert parts["origin"] == "template"
    assert parts["headline"] == "Net Profit of AED 59,640.00 on revenue of AED 174,300.00, a 34.2% margin"
    assert parts["actions"] == ["Review Cost of Goods, the largest cost line, for savings."]


def test_slow_model_falls_back_within_the_timeout(monkeypatch):
    from gemini_brain.config.settings import settings

    def slow(system, user):
        time.sleep(3)
        return "{}"

    monkeypatch.setattr(narrator, "_model_call", slow)
    monkeypatch.setattr(settings, "report_narrative_timeout_seconds", 1.0)
    t0 = time.perf_counter()
    spec = _spec("sales by customer chart", CUSTOMERS)
    assert time.perf_counter() - t0 < 2.5
    assert spec["narrative_parts"]["origin"] == "template"


def test_template_mode_never_calls_the_model(monkeypatch):
    from gemini_brain.config.settings import settings

    calls = []
    monkeypatch.setattr(narrator, "_model_call", lambda s, u: calls.append(1) or "{}")
    monkeypatch.setattr(settings, "report_narrative_mode", "template")
    _spec("sales by customer chart", CUSTOMERS)
    assert calls == []


def test_template_flags_concentration():
    parts = _spec("sales by customer chart", CUSTOMERS)["narrative_parts"]
    assert parts["headline"] == "Apex Retail is the largest at AED 420.5K, 62.6% of the total"
    assert any("depend heavily on a single name" in r for r in parts["risks"])
    assert parts["drivers"][0] == "The top three make up 88.3%."


# ── grounding still has the last word ────────────────────────────────────────

def test_invented_figure_is_removed_from_its_part_only(monkeypatch):
    monkeypatch.setattr(narrator, "_model_call", _fake({
        "headline": "Apex Retail leads with 62.6% of sales [F2]",
        "summary": ["Apex Retail holds 62.6% of sales [F2].", "Sales will reach AED 2M next year [F2]."],
        "drivers": ["The top three make up 88.3% [F1]."],
        "risks": ["Apex Retail grew 45% this year [F2]."],
        "actions": ["Review exposure to Apex Retail [F2]."],
    }))
    spec = _spec("sales by customer chart", CUSTOMERS)
    parts = spec["narrative_parts"]
    assert parts["summary"] == ["Apex Retail holds 62.6% of sales."]
    assert parts["risks"] == []
    assert parts["drivers"] == ["The top three make up 88.3%."]
    assert spec["integrity"]["sentences_removed"] == 2


# ── every output carries the same analysis ───────────────────────────────────

def test_analysis_reaches_the_files():
    from docx import Document

    spec = _spec("P&L report as pdf", PNL)
    headline = spec["narrative_parts"]["headline"]
    md = render_md(spec).decode("utf-8")
    assert "## Executive summary" in md and f"**{headline}**" in md and "## Recommended actions" in md
    doc = Document(io.BytesIO(render_docx(spec)))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert headline in text and "recommended actions" in text.lower()


# ── multiples in words are figures too ───────────────────────────────────────

@pytest.mark.parametrize("sentence,removed", [
    ("Apex Retail generates nearly six times more sales than the second-largest customer.", True),
    ("Apex Retail sells about 4.6 times the second-largest customer.", False),
    ("Apex Retail sells nearly five times the next customer.", False),
    ("Apex Retail makes more than half of all sales.", False),
    ("Sales tripled.", True),
    ("Sales doubled.", True),
])
def test_multiples_in_words_are_checked(sentence, removed):
    from gemini_brain.artifacts.facts import build_factsheet
    from gemini_brain.artifacts.integrity import ground_text

    rows = CUSTOMERS + [{"customer_name": "Gulf Motors", "sales": 21000}, {"customer_name": "Nakheel Co", "sales": 9800}]
    facts = build_factsheet(_doc(rows, "sales by customer"))
    assert bool(ground_text(sentence, facts).removed) is removed


def test_missing_headline_is_filled_from_the_template(monkeypatch):
    monkeypatch.setattr(narrator, "_model_call", _fake({
        "summary": ["Apex Retail holds 62.6% of sales [F2]."],
        "drivers": [], "risks": [], "actions": [],
    }))
    parts = _spec("sales by customer chart", CUSTOMERS)["narrative_parts"]
    assert parts["origin"] == "report"
    assert parts["headline"] == "Apex Retail is the largest at AED 420.5K, 62.6% of the total"


def test_uncited_headline_is_kept_when_its_figures_check_out(monkeypatch):
    monkeypatch.setattr(narrator, "_model_call", _fake({
        "headline": "Apex Retail holds 62.6% of sales",
        "summary": ["Apex Retail holds 62.6% of sales [F2]."], "drivers": [], "risks": [], "actions": [],
    }))
    assert _spec("sales by customer chart", CUSTOMERS)["narrative_parts"]["headline"] == "Apex Retail holds 62.6% of sales"
