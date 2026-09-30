"""Compact layouts for multi-org list answers: per-org summary, merged top-N, overlap."""
from __future__ import annotations

import pytest

from gemini_brain.orchestrator.multi_org import OrgRun, merge_org_results
from gemini_brain.orchestrator.multi_org_present import (
    MERGED,
    OVERLAP,
    PER_ORG,
    build_presentation,
    choose_shape,
    extract_rows,
    is_list_question,
)


def _vendors(*pairs):
    return [{"vendor": name, "outstanding": amount, "bill_count": 1} for name, amount in pairs]


def _run(oid, name, rows, currency="AED", as_report=False, status="ok"):
    if status == "failed":
        return OrgRun(oid, name, currency, result=None)
    results = [{"summary": {"n": len(rows)}, "bills": rows}] if as_report else rows
    return OrgRun(oid, name, currency, result={"status": status, "results": results,
                                               "blocks": [{"type": "table", "rows": rows}]})


# ── Shape choice ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("question, shape", [
    ("List vendors with overdue bills in each organization", PER_ORG),
    ("Which vendors are good for both organizations?", OVERLAP),
    ("Vendors common to all organizations", OVERLAP),
    ("Top 10 customers across all organizations", MERGED),
    ("Show top customers overall", MERGED),
])
def test_shape_from_question(question, shape):
    assert choose_shape(question) == shape


@pytest.mark.parametrize("question, is_list", [
    ("List vendors with overdue bills", True),
    ("Which vendors are good for both organizations?", True),
    ("Top 5 customers", True),
    ("Show me the P&L statement", False),
    ("Compare the balance sheet", False),
])
def test_list_question_detection(question, is_list):
    assert is_list_question(question) is is_list


# ── Row extraction ───────────────────────────────────────────────────────────

def test_rows_from_plain_list_and_from_report_payload():
    rows = _vendors(("A", 1))
    assert extract_rows({"results": rows}) == rows
    assert extract_rows({"results": [{"summary": {}, "bills": rows}]}) == rows
    assert extract_rows({"results": [{"summary": {"total": 5}}]}) == []
    assert extract_rows({"results": ["text"]}) is None


# ── Per-org layout ───────────────────────────────────────────────────────────

def test_per_org_shows_summary_then_open_sections_with_top_rows_only():
    many = _vendors(*[(f"V{i}", i * 10.0) for i in range(12)])
    runs = [_run(1, "Org A", many), _run(2, "Org B", _vendors(("X", 5.0)), as_report=True)]
    pres = build_presentation("List vendors with overdue bills in each organization", runs)
    summary, group = pres["blocks"]
    assert summary["type"] == "table"
    assert summary["rows"][0] == {"organization": "Org A", "items": 12, "amount": 660.0}
    assert group["type"] == "collapsible_group" and group["default_open"] is True
    assert group["sections_open"] is True
    section_a = group["sections"][0]
    assert section_a["subtitle"] == "12 items, showing top 5"
    table = section_a["blocks"][0]
    assert [r["vendor"] for r in table["rows"]] == ["V11", "V10", "V9", "V8", "V7"]
    assert table["truncated"] is True and table["total_rows"] == 12


def test_per_org_prompt_has_counts_and_top_names_not_raw_rows():
    runs = [_run(1, "Org A", _vendors(("Big", 900.0), ("Small", 1.0))), _run(2, "Org B", [], status="empty")]
    prompt = build_presentation("list vendors", runs)["prompt"]
    assert "Org A: 2 items, total 901.00 AED. Top 2: Big 900.00; Small 1.00" in prompt
    assert "Org B: no rows" in prompt
    assert '"outstanding"' not in prompt


def test_failed_org_is_named_in_its_section():
    runs = [_run(1, "Org A", _vendors(("V", 1.0))), _run(2, "Org B", [], status="failed")]
    group = build_presentation("list vendors", runs)["blocks"][1]
    assert group["sections"][1]["subtitle"] == "could not be retrieved"


# ── Merged layout ────────────────────────────────────────────────────────────

def test_merged_is_one_ranked_table_with_organization_column():
    runs = [_run(1, "Org A", _vendors(("V1", 50.0), ("V2", 5.0))), _run(2, "Org B", _vendors(("V3", 70.0)))]
    pres = build_presentation("Top vendors across all organizations", runs)
    assert pres["shape"] == MERGED and len(pres["blocks"]) == 1
    rows = pres["blocks"][0]["rows"]
    assert [(r["organization"], r["vendor"]) for r in rows] == [("Org B", "V3"), ("Org A", "V1"), ("Org A", "V2")]


def test_merged_falls_back_to_per_org_when_currencies_differ():
    runs = [_run(1, "Org A", _vendors(("V1", 50.0)), "AED"), _run(2, "Org B", _vendors(("V3", 70.0)), "USD")]
    assert build_presentation("Top vendors across all organizations", runs)["shape"] == PER_ORG


# ── Overlap layout ───────────────────────────────────────────────────────────

def test_overlap_lists_only_shared_items_case_insensitively():
    runs = [
        _run(1, "Org A", _vendors(("Acme", 10.0), ("Solo A", 1.0))),
        _run(2, "Org B", _vendors(("ACME", 20.0), ("Solo B", 2.0))),
    ]
    pres = build_presentation("Which vendors are good for both organizations?", runs)
    assert pres["shape"] == OVERLAP
    rows = pres["blocks"][0]["rows"]
    assert rows == [{"item": "Acme", "organizations": 2, "Org A": 10.0, "Org B": 20.0}]
    assert "Acme: in Org A, Org B" in pres["prompt"]


def test_overlap_with_nothing_shared_says_so():
    runs = [_run(1, "Org A", _vendors(("A", 1.0))), _run(2, "Org B", _vendors(("B", 1.0)))]
    pres = build_presentation("vendors used by both", runs)
    assert pres["blocks"] == []
    assert "No item appears in more than one organization." in pres["prompt"]


# ── Merge wiring ─────────────────────────────────────────────────────────────

class _Summary:
    prompts = []

    def _call_llm(self, system, user_text, **kwargs):
        _Summary.prompts.append(user_text)
        return "- ok", 1, 1


def test_list_question_uses_presentation_and_short_prompt():
    runs = [_run(1, "Org A", _vendors(("V", 1.0))), _run(2, "Org B", _vendors(("W", 2.0)))]
    env = merge_org_results("List vendors with overdue bills", runs, _Summary())
    assert [b["type"] for b in env["blocks"]] == ["table", "collapsible_group"]
    assert env["routing_info"]["layout"] == PER_ORG
    assert "naming the specific items and amounts" in _Summary.prompts[-1]
    assert len(env["results"]) == 2  # every tagged row is still returned


def test_other_question_keeps_org_blocks_collapsed():
    runs = [_run(1, "Org A", _vendors(("V", 1.0))), _run(2, "Org B", _vendors(("W", 2.0)))]
    env = merge_org_results("Compare the balance sheet", runs, _Summary())
    assert [b["type"] for b in env["blocks"]] == ["collapsible_group"]
    assert env["routing_info"]["layout"] == "collapsed"
    assert "Do not reproduce them" in _Summary.prompts[-1]


# ── Richer per-org lists ─────────────────────────────────────────────────────

@pytest.mark.parametrize("question, shown", [
    ("Show top 5 customers for each organization", 5),
    ("Show top 3 customers for each organization", 3),
    ("Show top 10 customers for each organization", 10),
    ("List customers for each organization", 5),
])
def test_rows_shown_follow_the_number_asked(question, shown):
    runs = [_run(1, "Org A", _vendors(*[(f"V{i}", float(i)) for i in range(12)]))]
    pres = build_presentation(question, runs)
    table = pres["blocks"][1]["sections"][0]["blocks"][0]
    assert len(table["rows"]) == shown
    assert f"Top {shown}:" in pres["prompt"]


def test_all_top_items_reach_the_model_not_just_three():
    runs = [_run(1, "Org A", _vendors(*[(f"V{i}", float(i)) for i in range(8)]))]
    prompt = build_presentation("Show top 5 vendors for each organization", runs)["prompt"]
    assert all(f"V{i}" in prompt for i in (7, 6, 5, 4, 3))


def test_org_that_fell_back_shows_its_own_answer_not_no_data():
    fallback = OrgRun(2, "Org B", "AED", result={
        "status": "ok", "answer": "Org B owes Acme AED 900.", "results": [],
        "blocks": [], "routing_info": {"path": "db_fallback"}})
    pres = build_presentation("List vendors with overdue bills", [_run(1, "Org A", _vendors(("V", 1.0))), fallback])
    section = pres["blocks"][1]["sections"][1]
    assert section["blocks"][0] == {"type": "markdown", "text": "Org B owes Acme AED 900."}
    assert "Org B: Org B owes Acme AED 900." in pres["prompt"]
