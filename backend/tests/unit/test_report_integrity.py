"""Phase 3: reconciliation checks and narrative grounding."""
import random
from decimal import Decimal

import pytest

from gemini_brain.artifacts.attach import attach_delivery
from gemini_brain.artifacts.facts import build_factsheet, extract_figures
from gemini_brain.artifacts.generate import render_md
from gemini_brain.artifacts.integrity import ground_text, reconcile, verify_document
from gemini_brain.artifacts.ir import Provenance, ReportDocument
from gemini_brain.artifacts.report_spec import build_report_spec
from gemini_brain.observability.metrics import METRICS

CUSTOMERS = [
    {"customer_name": "Apex Retail", "sales": 220500},
    {"customer_name": "Falcon Energy", "sales": 191310},
    {"customer_name": "Al Habtoor", "sales": 181755},
    {"customer_name": "Marina Foods", "sales": 96400},
    {"customer_name": "Desert Labs", "sales": 72210},
]
TOTAL = sum(r["sales"] for r in CUSTOMERS)  # 762,175

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
    "monthly": [
        {"month": "April", "revenue": 52400, "expenses": 34100},
        {"month": "May", "revenue": 58200, "expenses": 36800},
        {"month": "June", "revenue": 63700, "expenses": 43760},
    ],
}


def _doc(data, query="report", **kw):
    return ReportDocument.from_spec(build_report_spec(data, query, **kw), query=query)


def _facts(data, query="report", **kw):
    return build_factsheet(_doc(data, query, **kw))


# ── figure extraction ────────────────────────────────────────────────────────

def test_dates_years_ranks_and_durations_are_not_figures():
    s = "In Q2 2026, on 31 Mar 2026 and over the last 90 days, the top 3 customers in 2025 stood out."
    assert extract_figures(s) == []


def test_figure_precision_follows_how_it_was_written():
    [f] = extract_figures("Revenue reached AED 1.2M.")
    assert f.lo == Decimal("1150000") and f.hi == Decimal("1250000")
    [f] = extract_figures("Sales were 220,000.")  # a round figure: rounded by at most 0.5% of itself
    assert f.lo == Decimal("218900") and f.hi == Decimal("221100")
    [f] = extract_figures("Margin was 34.2%.")
    assert f.percent and f.lo == Decimal("34.15")


# ── grounding: true statements survive ───────────────────────────────────────

@pytest.mark.parametrize("sentence", [
    "Apex Retail leads with AED 220,500.00 in sales.",
    "Apex Retail leads with AED 220.5K.",
    "Apex Retail brought in about AED 220,000.",
    "Apex Retail accounts for 29% of sales.",
    "The top 3 customers make up 77.9% of sales.",
    "Total sales were AED 762,175.",
    "Apex Retail is ahead of Falcon Energy by AED 29,190.",
    "Average sales per customer were AED 152,435.",
    "Desert Labs is the smallest at 72,210.",
    "The remaining two customers contribute 22% of sales.",
])
def test_true_statements_are_kept(sentence):
    facts = _facts(CUSTOMERS, "sales by customer")
    g = ground_text(sentence, facts)
    assert g.removed == [], (sentence, g.removed)


def test_pnl_statements_are_kept():
    facts = _facts(PNL, "P&L")
    text = (
        "Net profit was AED 59,640 on revenue of AED 174,300, a margin of 34.2%.\n"
        "- Payroll was 24.4% of revenue.\n"
        "- Revenue grew 21.6% from April to June.\n"
        "- Expenses rose by AED 2,700 from April to May."
    )
    g = ground_text(text, facts)
    assert g.removed == []
    assert g.checked == g.verified == 6


# ── grounding: planted wrong numbers are removed ────────────────────────────

@pytest.mark.parametrize("sentence", [
    "Apex Retail made AED 250,000 in sales.",
    "The top 3 customers make up 90% of sales.",
    "Total sales were AED 1.5M.",
    "Sales grew 40% year on year.",
    "Falcon Energy contributed 31.4% of sales.",
])
def test_wrong_statements_are_removed(sentence):
    facts = _facts(CUSTOMERS, "sales by customer")
    g = ground_text(sentence, facts)
    assert g.removed == [sentence.strip()] and g.text == ""


def test_only_the_bad_sentence_goes_and_structure_survives():
    facts = _facts(CUSTOMERS, "sales by customer")
    text = (
        "## Highlights\n"
        "Apex Retail leads with AED 220,500. It is 40% bigger than everyone else combined.\n"
        "- Falcon Energy follows with AED 191,310.\n"
        "## Risks\n"
        "- Desert Labs fell 55% this quarter.\n"
    )
    g = ground_text(text, facts)
    assert "## Highlights" in g.text
    assert "Apex Retail leads with AED 220,500." in g.text
    assert "40% bigger" not in g.text
    assert "- Falcon Energy follows with AED 191,310." in g.text
    assert "## Risks" not in g.text  # heading dropped with its only bullet
    assert len(g.removed) == 2


def test_no_false_removals_on_random_true_statements():
    """Any figure taken from the FactSheet and written the way a person would stays."""
    rng = random.Random(7)
    for _ in range(40):
        rows = [{"customer_name": f"Client {chr(65 + i)}", "sales": rng.randint(1_000, 900_000)} for i in range(rng.randint(2, 11))]
        doc = _doc(rows, "sales by customer")
        facts = build_factsheet(doc)
        total = sum(r["sales"] for r in rows)
        top = max(rows, key=lambda r: r["sales"])
        share = Decimal(top["sales"]) / Decimal(total) * 100
        sentences = [
            f"{top['customer_name']} leads with AED {top['sales']:,}.",
            f"{top['customer_name']} holds {share:.1f}% of sales.",
            f"Total sales were AED {total:,.2f}.",
            f"Total sales were AED {total / 1_000_000:.2f}M." if total >= 1_000_000 else f"Total sales were AED {total / 1000:.1f}K.",
        ]
        for s in sentences:
            assert ground_text(s, facts).removed == [], (s, rows)


# ── reconciliation ───────────────────────────────────────────────────────────

def _failed(doc):
    return [c for c in reconcile(doc) if not c.passed]


@pytest.mark.parametrize("data,query,hint", [
    (CUSTOMERS, "sales by customer", "bar"),
    (CUSTOMERS, "pie of sales by customer", "pie"),
    ([{"customer_name": f"C{i}", "sales": 1000 * (20 - i)} for i in range(15)], "pie of sales by customer", "pie"),
    (PNL, "P&L", "bar"),
    ({"graphData": {"labels": ["Jan 2026", "Feb 2026"], "incomeValues": [100, 300], "expenseValues": [50, 50]}}, "trend", "line"),
])
def test_normal_reports_raise_no_false_alarms(data, query, hint):
    doc = _doc(data, query, chart_hint=hint)
    assert _failed(doc) == []


def test_pnl_runs_every_reconciliation():
    names = {c.name for c in reconcile(_doc(PNL, "P&L"))}
    assert "Revenue − Expenses = Net Profit" in names
    assert any("Line Items items" in n for n in names)
    assert any("Monthly Breakdown" in n for n in names)


def test_net_profit_that_does_not_add_up_is_flagged():
    data = dict(PNL, net_profit=70000)
    failed = _failed(_doc(data, "P&L"))
    assert any("Revenue − Expenses" in c.name for c in failed)
    msg = next(c.message for c in failed if "Revenue − Expenses" in c.name)
    assert "AED 70,000.00" in msg and "AED 59,640.00" in msg


def test_subtotal_counted_as_line_item_is_flagged():
    data = dict(PNL, expenses={"total": 114660, "line_items": [
        {"name": "Cost of Goods", "amount": 52290},
        {"name": "Payroll", "amount": 42600},
        {"name": "Overheads", "amount": 17430},
        {"name": "Marketing", "amount": 2340},
        {"name": "Subtotal", "amount": 114660},
    ]})
    failed = _failed(_doc(data, "P&L"))
    assert any("line items add up to AED 229,320.00" in c.message for c in failed)


def test_monthly_table_that_disagrees_with_total_is_flagged():
    data = dict(PNL, revenue={"total": 180000, "line_items": [{"name": "Services", "amount": 180000}]})
    failed = _failed(_doc(data, "P&L"))
    assert any("Monthly Breakdown table adds up to AED 174,300.00" in c.message for c in failed)


def test_chart_point_that_disagrees_with_its_table_is_flagged():
    spec = build_report_spec(CUSTOMERS, "sales by customer", chart_hint="bar")
    spec["charts"][0]["series"][0]["data"][1] = 199999.0  # a corrupted point
    failed = _failed(ReportDocument.from_spec(spec))
    assert any("Falcon Energy at AED 199,999.00" in c.message and "AED 191,310.00" in c.message for c in failed)


def test_missing_rows_are_flagged():
    doc = ReportDocument.from_spec(
        build_report_spec(CUSTOMERS, "sales by customer"),
        provenance=Provenance(tier="live_api", row_count=40),
    )
    failed = _failed(doc)
    assert any("returned 40 records, but the report holds 5" in c.message for c in failed)


# ── end to end ───────────────────────────────────────────────────────────────

def test_attach_removes_bad_narrative_and_explains_it_everywhere(monkeypatch):
    """A narrator sentence that cites a real fact but states a wrong figure is
    still removed by grounding, and the report says so."""
    import json

    from gemini_brain.artifacts import narrator

    def fake_model(system, user):
        return json.dumps({
            "headline": "Apex Retail leads with AED 220,500.00 in sales [F1].",
            "summary": ["The top 3 customers make up 77.9% of sales [F1, F2]."],
            "drivers": ["Desert Labs grew 300% this year [F5]."],
            "risks": [],
            "actions": ["Review the customer mix [F1]."],
        })

    monkeypatch.setattr(narrator, "_model_call", fake_model)
    METRICS.reset()
    out = attach_delivery("sales by customer as markdown", CUSTOMERS, [])
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    assert "Desert Labs grew" not in spec["narrative"]
    assert "77.9%" in spec["narrative"]
    assert spec["narrative_parts"]["origin"] == "report"
    assert spec["integrity"]["sentences_removed"] == 1
    assert any("1 statement removed from the summary" in line for line in spec["methodology"])
    md = render_md(spec).decode("utf-8")
    assert "Desert Labs grew" not in md
    snap = METRICS.snapshot()
    assert snap["report_sentences_removed"] == 1
    assert snap["report_numbers_checked"] == 3 and snap["report_numbers_verified"] == 2


def test_failed_check_becomes_a_warning_in_the_report():
    data = dict(PNL, net_profit=70000)
    out = attach_delivery("P&L report as pdf", data, [])
    spec = next(b for b in out if b["type"] == "canvas")["spec"]
    warnings = [c for c in spec["callouts"] if c["tone"] == "warning"]
    assert any("confirm before relying on the net figure" in w["text"] for w in warnings)
    assert any(line.startswith("Consistency checks:") and "see the warnings" in line for line in spec["methodology"])


def test_unverifiable_insight_is_dropped():
    doc = ReportDocument.from_spec(
        dict(build_report_spec(CUSTOMERS, "sales"), narrative="Apex Retail leads with AED 220,500.",
             insight="Sales doubled to AED 2M."),
    )
    out = verify_document(doc).to_spec()
    assert out["insight"] is None
    assert out["narrative"] == "Apex Retail leads with AED 220,500."


def test_average_per_counted_item_is_a_fact():
    facts = _facts({"total_income": 1612654.5, "invoice_count": 1275}, "totals")
    g = ground_text("That is an average of AED 1,264.83 per invoice across 1,275 invoices.", facts)
    assert g.removed == [] and g.verified == 2


# ── top-N reports: shares are of the N shown, never of the whole business ────

_TOP5_ROWS = [
    {"customer_name": n, "sales": v}
    for n, v in (("Arjun", 1250000), ("Jyoti", 1150000), ("Pavani", 904400), ("Lakshit", 837700),
                 ("Nilima", 697200), ("Ravi", 400000), ("Sana", 300000))
]


def _top5_doc():
    from tests.report_golden.harness import _offline_attach
    from gemini_brain.artifacts.ir import ReportDocument

    spec = _offline_attach("Top 5 customers by revenue please as a chart", _TOP5_ROWS)
    return spec, ReportDocument.from_spec(spec)


def test_top_n_report_says_shares_are_of_the_n_shown():
    spec, _ = _top5_doc()
    assert spec["title"] == "Top 5 Customers by Revenue"
    chart = spec["charts"][0]
    assert chart["categories"] == ["Arjun", "Jyoti", "Pavani", "Lakshit", "Nilima"]
    assert chart["ranked_subset"] == 5
    assert "of the 5 shown" in chart["takeaway"]
    assert "of the total" not in chart["takeaway"]


def test_top_n_report_removes_claims_about_the_whole_business():
    from gemini_brain.artifacts.facts import build_factsheet

    _, doc = _top5_doc()
    facts = build_factsheet(doc)
    removed = [
        "The top three customers generate 68.3% of total revenue.",
        "High concentration risk with just five customers generating all revenue.",
        "Arjun leads with 25.8% of sales.",
    ]
    kept = ["Arjun accounts for 25.8% of the top 5.", "The top three make up 68.3% of the 5 shown."]
    for text in removed:
        assert not ground_text(text, facts).text, text
    for text in kept:
        assert ground_text(text, facts).text, text
