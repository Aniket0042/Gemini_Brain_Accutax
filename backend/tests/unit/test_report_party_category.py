"""A supplier/customer report is by supplier/customer, never by the organization every row also carries.

The agent's rows come from query_metrics / list_documents with the auto dimensions
(organization_id, organization_name, currency) first; export_rows drops only the id.
"organization_name" holds "name", so it used to win the chart's category and every
bar, the brief and the template headline read "Construction & Real Estate_User1_Org2".
"""
import json

import pytest

from gemini_brain.agent.preview import export_rows
from gemini_brain.artifacts import narrator
from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.artifacts.insights import add_takeaways
from gemini_brain.artifacts.ir import ReportDocument
from gemini_brain.artifacts.narrator import build_brief, parse_narrative
from gemini_brain.artifacts.report_spec import build_report_spec

ORG = "Construction & Real Estate_User1_Org2"


def _agent_data(view, party_key, measure, parties):
    rows = [{f"{view}.organization_id": 25, f"{view}.organization_name": ORG, f"{view}.currency": "AED",
             f"{view}.{party_key}": name, f"{view}.{measure}": value} for name, value in parties]
    return [{"tool": "query_metrics", "input": {"view": view}, "result": {"rows": rows}}]


SUPPLIERS = _agent_data("purchases", "vendor_name", "gross_purchases", [
    ("Gulf Steel Trading", 71400), ("Emirates Cement", 52300), ("Al Noor Electricals", 33150),
    ("Dubai Timber Co", 18900),
])
CUSTOMERS = _agent_data("sales", "customer_name", "net_sales", [
    ("Marina Towers LLC", 120500), ("Palm Developments", 88210), ("Creek Holdings", 40100),
])
PARTIES = {
    "PDF report of my top suppliers by purchases this year": (SUPPLIERS, "Gulf Steel Trading"),
    "PDF report of my top customers by net sales this year": (CUSTOMERS, "Marina Towers LLC"),
}


def _spec(query, rows):
    return next(b for b in attach_delivery(query, rows, [], organization_id=25, org_name=ORG)
                if b["type"] == "canvas")["spec"]


@pytest.mark.parametrize("query", PARTIES)
def test_rows_keep_the_org_name_beside_the_party(query):
    data, _ = PARTIES[query]
    row = export_rows(data)[0]
    assert "organization_id" not in row
    assert row["organization_name"] == ORG  # the shape that caused the bug


@pytest.mark.parametrize("query", PARTIES)
def test_chart_category_is_the_party_not_the_org(query):
    data, top = PARTIES[query]
    rows = export_rows(data)
    chart = build_report_spec(rows, query)["charts"][0]
    assert ORG not in chart["categories"]
    assert chart["categories"][0] == top
    assert len(set(chart["categories"])) == len(rows)
    assert chart["x_label"] in ("Vendor", "Customer")


@pytest.mark.parametrize("query", PARTIES)
def test_brief_and_template_name_the_party(query):
    data, top = PARTIES[query]
    spec = _spec(query, export_rows(data))  # conftest makes the model unavailable: template narrative
    assert all(ORG not in c for chart in spec["charts"] for c in chart["categories"])
    headline = spec["narrative_parts"]["headline"]
    assert ORG not in headline and top in headline
    brief = build_brief(add_takeaways(ReportDocument.from_spec(build_report_spec(export_rows(data), query), query=query)))
    facts = brief.render()
    assert ORG not in facts and f"{top} is the largest" in facts


def test_org_name_labels_a_chart_only_when_nothing_else_can():
    rows = [{"organization_name": "Org A", "currency": "AED", "revenue": 100},
            {"organization_name": "Org B", "currency": "AED", "revenue": 300}]
    chart = build_report_spec(rows, "revenue by organization")["charts"][0]
    assert chart["categories"] == ["Org B", "Org A"]


def test_currency_is_never_the_category():
    rows = [{"currency": "AED", "account_name": "Rent", "amount": 500},
            {"currency": "AED", "account_name": "Fuel", "amount": 200}]
    assert build_report_spec(rows, "expenses by account")["charts"][0]["categories"] == ["Rent", "Fuel"]


# ── narrator output that used to be thrown away ─────────────────────────────

def _brief():
    query = "PDF report of my top suppliers by purchases this year"
    return build_brief(add_takeaways(ReportDocument.from_spec(build_report_spec(export_rows(SUPPLIERS), query),
                                                              query=query)))


def test_truncated_json_keeps_the_finished_parts():
    brief = _brief()
    full = json.dumps({
        "headline": "Gulf Steel Trading is the largest supplier [F1]",
        "summary": ["Gulf Steel Trading leads purchases [F1].", "Purchases are spread across four suppliers [F1]."],
        "drivers": ["Steel and cement make up most of the spend [F1]."],
    })
    cut = full[: full.index("make up") ]  # max tokens hit mid-sentence
    section = parse_narrative(cut, brief)
    assert section is not None
    assert section.headline == "Gulf Steel Trading is the largest supplier"
    assert len(section.summary) == 2 and section.drivers == []


def test_truncated_after_a_key_drops_the_key():
    brief = _brief()
    raw = '{"headline": "Gulf Steel Trading leads [F1]", "summary": ["Gulf Steel Trading leads [F1]."], "drivers"'
    section = parse_narrative(raw, brief)
    assert section.headline == "Gulf Steel Trading leads" and section.summary == ["Gulf Steel Trading leads."]


def test_prose_around_fenced_json_and_semicolon_citations():
    brief = _brief()
    raw = ('Here is the analysis:\n```json\n'
           + json.dumps({"headline": "Gulf Steel Trading leads", "summary": ["Gulf Steel Trading leads [F1; F2]."],
                         "drivers": [{"text": "Cement is second [F2]."}], "risks": [], "actions": []})
           + "\n```\nLet me know if you need more.")
    section = parse_narrative(raw, brief)
    assert section.summary == ["Gulf Steel Trading leads."]
    assert section.drivers == ["Cement is second."]


@pytest.mark.parametrize("raw,reason", [
    ("", "empty response"),
    ("I cannot write this report.", "no JSON object"),
    ('{"headline": "x", "summary": ["a" "b"]}', "invalid JSON"),
    ('{"headline": "x", "summary": ["a', "never closed"),
    ('{"headline": "x [F999]"}', "no cited sentence survived"),
])
def test_why_unusable_names_the_cause_without_quoting(raw, reason):
    why = narrator.why_unusable(raw)
    assert reason in why
    assert "Gulf" not in why


def test_failure_log_gives_the_cause(monkeypatch, caplog):
    monkeypatch.setattr(narrator, "_model_call", lambda s, u: "Sorry, the facts look inconsistent.")
    with caplog.at_level("WARNING", logger="gemini_brain.artifacts.narrator"):
        assert narrator._narrate_with_model(_brief(), "suppliers", 5.0) is None
    assert "no JSON object" in caplog.text and "inconsistent" not in caplog.text
