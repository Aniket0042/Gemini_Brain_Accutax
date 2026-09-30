"""Multi-org answers written from computed figures, and zeros that only mean "nothing recorded".

Cases come from the role-based manual test run (CFO, CA, CEO, follow-ups, auditor),
where a model-written summary contradicted its own table and empty ledgers were
ranked as real zeros.
"""
from __future__ import annotations

import re

from gemini_brain.orchestrator.multi_org import OrgRun, _org_prompt_section, merge_org_results
from gemini_brain.orchestrator.multi_org_metrics import (
    BY_KEY,
    NOTHING_RECORDED,
    build_comparison,
    build_multi_comparison,
    computed_answer,
)


def _org(oid: int, summary: dict | None, currency: str = "AED", status: str = "ok", **payload) -> OrgRun:
    result = None if status == "failed" else {"status": status, "results": [{"summary": summary or {}, **payload}]}
    return OrgRun(oid, f"Org{oid}", currency, result=result)


class _NoModel:
    def _call_llm(self, *args, **kwargs):
        raise AssertionError("a computed comparison must not call the model")


# ── Computed answer: every statement matches the table ───────────────────────

def test_negative_count_matches_the_table():
    # CEO "Compare net worth": the summary said 6 of 10 negative; the table had 8.
    equity = [62932377, 43285387, -2501959, -2519801, -2631397, -3007813, -3164437, -6697591, -7632539, -9396867]
    runs = [_org(i + 1, {"total_equity": v}, assets=[1], liabilities=[1]) for i, v in enumerate(equity)]
    answer = computed_answer(build_comparison(BY_KEY["total_equity"], runs, "compare net worth"))
    assert "Negative for 8 of 10" in answer
    assert "Highest: Org1, 62,932,377.00 AED." in answer
    assert "Lowest: Org10, -9,396,867.00 AED." in answer


def test_percentage_gap_is_computed_not_guessed():
    # CFO/CEO margin: the summary gave a 41.40 pp gap between 100.00% and 89.63%.
    runs = [_org(1, {"profit_margin_pct": 100.0}), _org(2, {"profit_margin_pct": 89.63}),
            _org(3, {"profit_margin_pct": -1.27})]
    answer = computed_answer(build_comparison(BY_KEY["profit_margin"], runs, "compare margin"))
    assert "Gap between the highest and the second (Org2): 10.37 percentage points." in answer
    assert "Lowest: Org3, -1.27%." in answer


def test_answer_names_only_selected_organizations():
    # CEO "invest based on margin": the summary named Org11, which was not selected.
    runs = [_org(i, {"total_income": 100.0 * i, "invoice_count": 3}) for i in range(1, 11)]
    answer = computed_answer(build_comparison(BY_KEY["revenue"], runs, "compare revenue"))
    named = set(re.findall(r"Org\d+", answer))
    assert named <= {f"Org{i}" for i in range(1, 11)}


def test_missing_orgs_are_stated_from_statuses_only():
    runs = [_org(1, {"total_income": 500.0, "invoice_count": 2}),
            _org(2, {"total_income": 300.0, "invoice_count": 1}),
            _org(3, None, status="failed")]
    answer = computed_answer(build_comparison(BY_KEY["revenue"], runs, "compare revenue"))
    assert "Could not be retrieved: Org3." in answer
    assert "Org1" not in answer.split("Could not be retrieved")[1]


def test_lowest_first_when_asked():
    runs = [_org(1, {"total_expenses": 900.0, "bill_count": 4}), _org(2, {"total_expenses": 100.0, "bill_count": 1})]
    answer = computed_answer(build_comparison(BY_KEY["expenses"], runs, "rank by expenses, lowest first"))
    assert answer.index("Lowest: Org2") < answer.index("Highest: Org1")


def test_mixed_currencies_are_not_ranked_in_words():
    runs = [_org(1, {"total_income": 100.0, "invoice_count": 1}, currency="AED"),
            _org(2, {"total_income": 900.0, "invoice_count": 1}, currency="USD")]
    answer = computed_answer(build_comparison(BY_KEY["revenue"], runs, "compare revenue"))
    assert "different currencies (AED, USD)" in answer
    assert "Highest" not in answer


def test_unknown_currency_is_named_not_called_different():
    runs = [_org(1, {"total_income": 100.0, "invoice_count": 1}, currency=""),
            _org(2, {"total_income": 900.0, "invoice_count": 1})]
    answer = computed_answer(build_comparison(BY_KEY["revenue"], runs, "compare revenue"))
    assert "The currency is unknown for Org1" in answer


def test_multi_metric_states_each_column_and_gaps_in_it():
    runs = [_org(1, {"total_income": 500.0, "invoice_count": 2, "overdue_amount": 0.0}),
            _org(2, {"total_income": 300.0, "invoice_count": 2})]
    comp = build_multi_comparison([BY_KEY["revenue"], BY_KEY["overdue_receivables"]], runs, "compare", {})
    answer = computed_answer(comp)
    assert "Total revenue: Highest: Org1, 500.00 AED." in answer
    assert "Overdue receivables not available for: Org2." in answer


def test_merge_writes_computed_answer_without_model_call():
    runs = [_org(i, {"total_income": 1000.0 * i, "invoice_count": 5}) for i in range(1, 11)]
    comp = build_comparison(BY_KEY["revenue"], runs, "compare revenue")
    env = merge_org_results("compare revenue", runs, _NoModel(), comparison=comp)
    assert env["token_usage"]["llm_calls"] == 0
    assert env["answer"].startswith("**Total revenue**")
    # Gaps, shares and the combined total all trace back to the comparison.
    assert env["verification"]["unmatched"] == []


# ── Zero that means "nothing recorded" ───────────────────────────────────────

def test_vat_zero_with_no_vat_lines_is_nothing_recorded_not_ranked():
    # CA "Compare VAT payable": 8 orgs with no VAT lines were ranked 3rd-10th at 0.
    runs = [
        _org(1, {"net_vat_payable": 356234.17}, output_vat_by_month=[{"month": "2026-01"}], input_vat_by_month=[]),
        _org(2, {"net_vat_payable": 0.0}, output_vat_by_month=[], input_vat_by_month=[]),
    ]
    comp = build_comparison(BY_KEY["vat_payable"], runs, "compare vat")
    assert [r["organization"] for r in comp["rows"]] == ["Org1"]
    assert comp["missing"] == [{"organization": "Org2", "organization_id": 2, "reason": NOTHING_RECORDED}]
    assert "Nothing recorded: Org2." in computed_answer(comp)


def test_vat_zero_with_vat_lines_is_a_real_zero():
    runs = [_org(1, {"net_vat_payable": 0.0}, output_vat_by_month=[{"month": "2026-01"}],
                 input_vat_by_month=[{"month": "2026-01"}])]
    comp = build_comparison(BY_KEY["vat_payable"], runs, "compare vat")
    assert comp["rows"][0]["value"] == 0.0


def test_revenue_zero_without_invoices_is_nothing_recorded():
    runs = [_org(1, {"total_income": 0.0, "invoice_count": 0}), _org(2, {"total_income": 50.0, "invoice_count": 1})]
    comp = build_comparison(BY_KEY["revenue"], runs, "compare revenue")
    assert [m["reason"] for m in comp["missing"]] == [NOTHING_RECORDED]


def test_zero_overdue_receivables_stay_a_real_zero():
    # CEO: Org1 and Org2 had no overdue invoices. That is an answer, not a gap.
    runs = [_org(1, {"overdue_amount": 0.0, "open_count": 0}), _org(2, {"overdue_amount": 10.0, "open_count": 2})]
    comp = build_comparison(BY_KEY["overdue_receivables"], runs, "most overdue")
    assert {r["organization"]: r["value"] for r in comp["rows"]} == {"Org1": 0.0, "Org2": 10.0}


def test_zero_without_evidence_keys_is_kept():
    runs = [_org(1, {"total_income": 0.0})]
    assert build_comparison(BY_KEY["revenue"], runs, "revenue")["rows"][0]["value"] == 0.0


def test_empty_balance_sheet_is_nothing_recorded():
    runs = [_org(1, {"total_assets": 0.0}, assets=[], liabilities=[]),
            _org(2, {"total_assets": 0.0}, assets=[{"account": "Bank"}], liabilities=[])]
    comp = build_comparison(BY_KEY["total_assets"], runs, "compare assets")
    assert [r["organization"] for r in comp["rows"]] == ["Org2"]


# ── Model-written answers (lists, collapsed layouts) ─────────────────────────

class _Model:
    def __init__(self, answer: str):
        self.answer = answer
        self.prompts = []

    def _call_llm(self, system, user_text, **kwargs):
        self.prompts.append(user_text)
        return self.answer, 10, 5


def _list_run(oid: int, status: str = "ok") -> OrgRun:
    if status == "failed":
        return OrgRun(oid, f"Org{oid}", "AED")
    rows = [] if status == "empty" else [{"vendor": "V", "total": 10.0}]
    return OrgRun(oid, f"Org{oid}", "AED", result={"status": status, "answer": "", "results": rows, "blocks": []})


def test_empty_org_prompt_forbids_stating_zero():
    # CFO combined cash: an empty source was written up as "AED 0" for every org.
    section = _org_prompt_section(_list_run(1, status="empty"))
    assert "never state 0" in section


def test_false_no_data_line_is_removed_and_real_gaps_restated():
    model = _Model("- Org1 has 10 AED.\n- No data was provided for Org1 and Org2.\n- Org3 is fine.")
    runs = [_list_run(1), _list_run(2, status="empty"), _list_run(3), _list_run(4, status="failed")]
    env = merge_org_results("summary of each org", runs, model)
    assert "No data was provided for Org1" not in env["answer"]
    assert "- Org1 has 10 AED." in env["answer"]
    assert "_No records found: Org2._" in env["answer"]
    assert "_Could not be retrieved: Org4._" in env["answer"]


def test_org1_name_does_not_match_inside_org10():
    model = _Model("- Org10 has no records.")
    runs = [_list_run(1), _list_run(10, status="empty")]
    env = merge_org_results("summary of each org", runs, model)
    assert "- Org10 has no records." in env["answer"]


def test_all_lines_removed_falls_back_to_per_org_answers():
    model = _Model("No data for Org1.")
    env = merge_org_results("summary of each org", [_list_run(1), _list_run(2)], model)
    assert "### Org1" in env["answer"]
