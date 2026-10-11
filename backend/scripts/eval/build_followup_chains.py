"""
build_followup_chains.py — Write tests/data/followup_chains.json, the follow-up set for session context.

Plan: docs/CONTEXT_MODELS_REPORTS_PLAN.md, step 1.1. Each chain is a short conversation replayed in one
thread by scripts/eval/replay_real_questions.py --cases tests/data/followup_chains.json. Turn ids are the
chain id plus a letter (f04a, f04b, ...). Edit the chains here and re-run; do not edit the JSON by hand.

For each turn:
  carry    acceptable tool calls; any one must match a successful call of the turn.
           {"tool": None} means the turn may be answered from earlier results without a call.
           "*" matches any value (a measure, a filter operator or filter values).
           not_group_by / not_filters: members that must not appear (context that should not carry over).
  ref      reference Cube queries for the figure screen (same shape as real_questions.json).
  clarify  the agent should ask a question instead of querying.
  file     "file" or "chart": the turn should get one, even when the follow-up does not say so again.

Usage (from backend/):
  python scripts/eval/build_followup_chains.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

OUT = Path(__file__).resolve().parents[2] / "tests" / "data" / "followup_chains.json"

ALL = list(range(24, 34))
THREE = [24, 27, 29]
#: Seed names: org 24 is Org1, org 27 is Org4.
ORG1 = "Agriculture & Forestry_User1_Org1"
ORG4 = "Professional & Consulting Services_User1_Org4"


def qm(view: str, measures: List[str], period: Any = None, **kw: Any) -> Dict[str, Any]:
    call: Dict[str, Any] = {"tool": "query_metrics", "view": view, "measures": measures}
    if period is not None:
        call["period"] = period
    call.update(kw)
    return call


def ld(kind: str, **kw: Any) -> Dict[str, Any]:
    return {"tool": "list_documents", "type": kind, **kw}


def ref(view: str, measures: List[str], period: Any = None, **kw: Any) -> Dict[str, Any]:
    spec: Dict[str, Any] = {"view": view, "measures": measures}
    if period is not None:
        spec["period"] = period
    spec.update(kw)
    return spec


NO_CALL = {"tool": None}

#: (chain id, category, orgs, source, turns)
#: NO_CALL is accepted where the earlier answer already holds the figures (seen in the 11 Oct baseline).
CHAINS = [
    ("f01", "period", [27], "hand", [
        dict(q="What were our net sales this year?", carry=[qm("sales", ["net_sales"], "ytd")],
             ref=[ref("sales", ["net_sales"], "ytd")]),
        dict(q="and last quarter?", carry=[qm("sales", ["net_sales"], "last_quarter")],
             ref=[ref("sales", ["net_sales"], "last_quarter")]),
        dict(q="and last month?", carry=[qm("sales", ["net_sales"], "last_month")],
             ref=[ref("sales", ["net_sales"], "last_month")]),
    ]),
    ("f02", "period", [27], "hand", [
        dict(q="Show revenue by month this year", carry=[qm("pnl", ["revenue"], "ytd", granularity="month")],
             ref=[ref("pnl", ["revenue"], "ytd", granularity="month")]),
        dict(q="same for last year", carry=[qm("pnl", ["revenue"], "last_year", granularity="month")],
             ref=[ref("pnl", ["revenue"], "last_year", granularity="month")]),
    ]),
    ("f03", "period", THREE, "hand", [
        dict(q="Compare net profit last quarter", carry=[qm("pnl", ["net_profit"], "last_quarter")],
             ref=[ref("pnl", ["net_profit"], "last_quarter")]),
        dict(q="what about this month?", carry=[qm("pnl", ["net_profit"], "this_month")],
             ref=[ref("pnl", ["net_profit"], "this_month")]),
        dict(q="and year to date?", carry=[qm("pnl", ["net_profit"], "ytd")],
             ref=[ref("pnl", ["net_profit"], "ytd")]),
    ]),
    ("f04", "breakdown", [27], "hand", [
        dict(q="Net sales this year", carry=[qm("sales", ["net_sales"], "ytd")],
             ref=[ref("sales", ["net_sales"], "ytd")]),
        dict(q="break that down by customer", carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=25)]),
        dict(q="only the top 5",
             carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"], limit=5), NO_CALL],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=5)]),
    ]),
    ("f05", "breakdown", [27], "hand", [
        dict(q="How much did we spend on purchases this year?", carry=[qm("purchases", ["net_purchases"], "ytd")],
             ref=[ref("purchases", ["net_purchases"], "ytd")]),
        dict(q="split it by expense category",
             carry=[qm("purchases", ["net_purchases"], "ytd", group_by=["expense_category"])],
             ref=[ref("purchases", ["net_purchases"], "ytd", group_by=["expense_category"])]),
        dict(q="by vendor instead",
             carry=[qm("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"], not_group_by=["expense_category"])],
             ref=[ref("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"], order=["net_purchases", "desc"],
                      limit=25)]),
    ]),
    ("f06", "breakdown", [24], "hand", [
        dict(q="Revenue by month this year", carry=[qm("pnl", ["revenue"], "ytd", granularity="month")],
             ref=[ref("pnl", ["revenue"], "ytd", granularity="month")]),
        dict(q="make it quarterly", carry=[qm("pnl", ["revenue"], "ytd", granularity="quarter")],
             ref=[ref("pnl", ["revenue"], "ytd", granularity="quarter")]),
    ]),
    ("f07", "breakdown", [27], "hand", [
        dict(q="What are our outstanding receivables?", carry=[qm("receivables", ["outstanding"])],
             ref=[ref("receivables", ["outstanding"])]),
        dict(q="how much of that is overdue?", carry=[qm("receivables", ["overdue_amount"]), NO_CALL],
             ref=[ref("receivables", ["overdue_amount", "overdue_count"])]),
        dict(q="show it by aging bucket", carry=[qm("receivables", ["*"], group_by=["aging_bucket"])],
             ref=[ref("receivables", ["outstanding"], group_by=["aging_bucket"])]),
    ]),
    ("f08", "entity", [27], "hand", [
        dict(q="Who are our top 5 customers by sales this year?",
             carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=5)]),
        dict(q="how much does the top one still owe us?",
             carry=[qm("receivables", ["outstanding"], filters=[["customer_name", "*", "*"]]),
                    ld("open_receivables", filters=[["customer_name", "*", "*"]])],
             note="Filtered on the top customer of turn a, by name."),
        dict(q="list their open invoices", carry=[ld("open_receivables", filters=[["customer_name", "*", "*"]])],
             note="Same customer as turn b."),
    ]),
    ("f09", "entity", [27], "hand", [
        dict(q="Top 5 vendors by purchases this year",
             carry=[qm("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"])],
             ref=[ref("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"], order=["net_purchases", "desc"],
                      limit=5)]),
        dict(q="show me the bills from the second one", carry=[ld("bills", filters=[["vendor_name", "*", "*"]])],
             note="Bills this year from the second vendor of turn a, by name."),
    ]),
    ("f10", "entity", ALL, "hand", [
        dict(q="Rank the organizations by revenue this year", carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd", order=["revenue", "desc"])]),
        dict(q="show the monthly revenue for the last one",
             carry=[qm("pnl", ["revenue"], "ytd", granularity="month", filters=[["organization_name", "*", "*"]]),
                    qm("pnl", ["revenue"], "ytd", granularity="month")],
             note="Monthly revenue for the lowest-ranked organization of turn a only."),
    ]),
    ("f11", "share", [27], "hand", [
        dict(q="What was revenue this year?", carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd")]),
        dict(q="and net profit?", carry=[qm("pnl", ["net_profit"], "ytd")],
             ref=[ref("pnl", ["net_profit"], "ytd")]),
        dict(q="so what is the margin?", carry=[qm("pnl", ["net_margin_pct"], "ytd"), NO_CALL],
             ref=[ref("pnl", ["net_margin_pct"], "ytd")], note="Net margin this year, same basis as turns a and b."),
    ]),
    ("f12", "share", [27], "hand", [
        dict(q="Top 5 customers by net sales this year",
             carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=5)]),
        dict(q="what share of total sales is that?",
             carry=[qm("sales", ["net_sales"], "ytd", not_group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd")], note="Top-5 total as a percent of all net sales this year."),
    ]),
    ("f13", "share", THREE, "hand", [
        dict(q="Compare outstanding receivables", carry=[qm("receivables", ["outstanding"])],
             ref=[ref("receivables", ["outstanding"])]),
        dict(q="how much of that is overdue?", carry=[qm("receivables", ["overdue_amount"]), NO_CALL],
             ref=[ref("receivables", ["overdue_amount"])]),
    ]),
    ("f14", "scope", ALL, "real r080-r084", [
        dict(q="Compare revenue this year", carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd")]),
        dict(q="and last quarter ?", carry=[qm("pnl", ["revenue"], "last_quarter")],
             ref=[ref("pnl", ["revenue"], "last_quarter")]),
        dict(q="now only show the top 2", carry=[qm("pnl", ["revenue"], "last_quarter", limit=2), NO_CALL],
             ref=[ref("pnl", ["revenue"], "last_quarter", order=["revenue", "desc"], limit=2)]),
        dict(q="why is the second one so low?",
             carry=[qm("pnl", ["*"], "last_quarter"), qm("sales", ["*"], "last_quarter")],
             note="Explains the second organization of turn c for last quarter, from figures."),
        dict(q=f"drop  {ORG1} and redo",
             carry=[qm("pnl", ["revenue"], "last_quarter", filters=[["organization_name", "notEquals", "*"]]),
                    qm("pnl", ["revenue"], "last_quarter"), NO_CALL],
             ref=[ref("pnl", ["revenue"], "last_quarter", order=["revenue", "desc"],
                      filters=[["organization_name", "notEquals", [ORG1]]])],
             note="Top 2 by last-quarter revenue without Org1."),
    ]),
    ("f15", "period", [24, 27, 29, 31, 33], "real r006-r007", [
        dict(q="Compare revenue this year", carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd")]),
        dict(q="what about last quarter", carry=[qm("pnl", ["revenue"], "last_quarter")],
             ref=[ref("pnl", ["revenue"], "last_quarter")]),
    ]),
    ("f16", "repair", ALL, "real r034-r035", [
        dict(q="rank the top 5 orgnanization by thier revenue and also tell me the overdue invoices",
             carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd", order=["revenue", "desc"], limit=5),
                  ref("receivables", ["overdue_amount", "overdue_count"])]),
        dict(q="what about revenue you are not showing the revenue", carry=[qm("pnl", ["revenue"], "ytd"), NO_CALL],
             ref=[ref("pnl", ["revenue"], "ytd", order=["revenue", "desc"], limit=5)],
             note="Top 5 by revenue, with the revenue figures."),
    ]),
    ("f17", "repair", ALL, "real r038-r039", [
        dict(q="rank top 5 organization based on their revenue and also show me the overdue invoices",
             carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd", order=["revenue", "desc"], limit=5),
                  ref("receivables", ["overdue_amount", "overdue_count"])]),
        dict(q="show me the ooverdue invoices count", carry=[qm("receivables", ["overdue_count"]), NO_CALL],
             ref=[ref("receivables", ["overdue_count"])], note="For the same top 5 organizations."),
    ]),
    ("f18", "scope", THREE, "hand", [
        dict(q="Compare net sales this year", carry=[qm("sales", ["net_sales"], "ytd")],
             ref=[ref("sales", ["net_sales"], "ytd")]),
        dict(q=f"just {ORG4} now, by month",
             carry=[qm("sales", ["net_sales"], "ytd", granularity="month", filters=[["organization_name", "*", "*"]]),
                    qm("sales", ["net_sales"], "ytd", granularity="month")],
             ref=[ref("sales", ["net_sales"], "ytd", granularity="month", orgs=[27])], note="Org4 (org 27) only."),
    ]),
    ("f19", "file", [27], "hand", [
        dict(q="Download revenue by month this year as Excel", carry=[qm("pnl", ["revenue"], "ytd", granularity="month")],
             ref=[ref("pnl", ["revenue"], "ytd", granularity="month")], file="file"),
        dict(q="add net profit to it", carry=[qm("pnl", ["revenue", "net_profit"], "ytd", granularity="month")],
             ref=[ref("pnl", ["revenue", "net_profit"], "ytd", granularity="month")], file="file",
             note="A new Excel file with both measures."),
    ]),
    ("f20", "file", [24], "hand", [
        dict(q="Chart net sales by month this year", carry=[qm("sales", ["net_sales"], "ytd", granularity="month")],
             ref=[ref("sales", ["net_sales"], "ytd", granularity="month")], file="chart"),
        dict(q="same for last year", carry=[qm("sales", ["net_sales"], "last_year", granularity="month")],
             ref=[ref("sales", ["net_sales"], "last_year", granularity="month")], file="chart"),
    ]),
    ("f21", "clarify", [27], "hand", [
        dict(q="Net sales this year by customer", carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=25)]),
        dict(q="what about the other one?", clarify=True,
             note="Unclear: turn a has no 'other one'. Ask which customer or figure."),
    ]),
    ("f22", "clarify", THREE, "hand", [
        dict(q="Compare net profit this year", carry=[qm("pnl", ["net_profit"], "ytd")],
             ref=[ref("pnl", ["net_profit"], "ytd")]),
        dict(q="fix it", clarify=True, note="Unclear request. Ask what to change."),
    ]),
    ("f23", "switch", [27], "hand", [
        dict(q="Overdue receivables by customer",
             carry=[qm("receivables", ["overdue_amount"], group_by=["customer_name"])],
             ref=[ref("receivables", ["overdue_amount"], group_by=["customer_name"], order=["overdue_amount", "desc"],
                      limit=25)]),
        dict(q="and payables?", carry=[qm("payables", ["overdue_amount"], group_by=["vendor_name"])],
             ref=[ref("payables", ["overdue_amount"], group_by=["vendor_name"], order=["overdue_amount", "desc"],
                      limit=25)],
             note="Overdue payables by vendor, same shape as turn a."),
    ]),
    ("f24", "switch", [27], "hand", [
        dict(q="Net sales by month this year", carry=[qm("sales", ["net_sales"], "ytd", granularity="month")],
             ref=[ref("sales", ["net_sales"], "ytd", granularity="month")]),
        dict(q="What is the standard VAT rate in the UAE?", kind="kb", note="Law route; 5%."),
        dict(q="ok, and the monthly sales for last year?",
             carry=[qm("sales", ["net_sales"], "last_year", granularity="month")],
             ref=[ref("sales", ["net_sales"], "last_year", granularity="month")],
             note="Back to turn a after the law question."),
    ]),
    ("f25", "switch", [27], "hand", [
        dict(q="Top vendors by purchases this year",
             carry=[qm("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"])],
             ref=[ref("purchases", ["net_purchases"], "ytd", group_by=["vendor_name"], order=["net_purchases", "desc"],
                      limit=10)]),
        dict(q="and the customers?", carry=[qm("sales", ["net_sales"], "ytd", group_by=["customer_name"])],
             ref=[ref("sales", ["net_sales"], "ytd", group_by=["customer_name"], order=["net_sales", "desc"], limit=10)]),
    ]),
    ("f26", "long", [27], "hand", [
        dict(q="What was our revenue last quarter?", carry=[qm("pnl", ["revenue"], "last_quarter")],
             ref=[ref("pnl", ["revenue"], "last_quarter")]),
        dict(q="What are our outstanding payables?", carry=[qm("payables", ["outstanding"])],
             ref=[ref("payables", ["outstanding"])]),
        dict(q="How many invoices did we issue this month?", carry=[qm("sales", ["invoice_count"], "this_month")],
             ref=[ref("sales", ["invoice_count"], "this_month")]),
        dict(q="What is our cash balance?", carry=[qm("balance_sheet", ["cash_and_bank"])],
             ref=[ref("balance_sheet", ["cash_and_bank"], as_of="today")]),
        dict(q="Total expenses this year?", carry=[qm("pnl", ["total_expenses"], "ytd")],
             ref=[ref("pnl", ["total_expenses"], "ytd")]),
        dict(q="go back to the revenue figure from my first question and compare it with the quarter before",
             carry=[qm("pnl", ["revenue"])], ref=[ref("pnl", ["revenue"], "last_quarter")],
             note="Turn a is outside a 6-message window. Last quarter's revenue against the quarter before it."),
    ]),
    ("f27", "long", ALL, "hand", [
        dict(q="Compare gross margin this year", carry=[qm("pnl", ["gross_margin_pct"], "ytd")],
             ref=[ref("pnl", ["gross_margin_pct"], "ytd")]),
        dict(q="only those above 30%",
             carry=[qm("pnl", ["gross_margin_pct"], "ytd", filters=[["gross_margin_pct", "gt", ["30"]]]), NO_CALL],
             ref=[ref("pnl", ["gross_margin_pct"], "ytd", filters=[["gross_margin_pct", "gt", ["30"]]])]),
        dict(q="and what was their revenue?", carry=[qm("pnl", ["revenue"], "ytd"), NO_CALL],
             note="Revenue this year for the organizations kept in turn b only."),
        dict(q="now the same for last year",
             carry=[qm("pnl", ["gross_margin_pct"], "last_year"), qm("pnl", ["revenue"], "last_year")],
             note="Gross margin above 30% and their revenue, for last year."),
    ]),
    # Real threads (chat history, 27 Sep - 10 Oct 2026) where a new, standalone question follows: the earlier
    # turn's filters, period or breakdown must not leak into it.
    ("f28", "independent", ALL, "real thread 29 Sep (r085-r087)", [
        dict(q="Vendors common to all orgs paid more than 100,000 AED",
             carry=[qm("purchases", ["net_purchases"], group_by=["vendor_name"])]),
        dict(q="Top 10 largest bills overall",
             carry=[ld("bills", limit=10, not_filters=["net_purchases", "vendor_name"])],
             note="All bills by amount; not only vendors above 100,000."),
        dict(q="List every unpaid invoice older than 180 days per org",
             carry=[qm("receivables", ["*"], filters=[["days_overdue", "gt", ["180"]]], not_filters=["vendor_name"]),
                    ld("open_receivables", filters=[["days_overdue", "gt", ["180"]]]),
                    qm("receivables", ["*"], filters=[["document_date", "beforeDate", "*"]])],
             ref=[ref("receivables", ["outstanding", "open_count"], filters=[["days_overdue", "gt", ["180"]]])]),
    ]),
    ("f29", "independent", [27], "real thread 10 Oct", [
        dict(q="What is revenue this year?", carry=[qm("pnl", ["revenue"], "ytd")],
             ref=[ref("pnl", ["revenue"], "ytd")]),
        dict(q="List the last 5 invoices", carry=[ld("sales_invoices", limit=5)],
             note="The 5 most recent sales invoices."),
    ]),
    ("f30", "independent", ALL, "real thread 29 Sep (r065-r067)", [
        dict(q="Which business should I invest more in based on growth and margin?",
             carry=[qm("pnl", ["*"], "ytd")]),
        dict(q="Summarize each company in 3 bullets", carry=[qm("pnl", ["*"]), qm("balance_sheet", ["*"])],
             note="From figures, per company; the old path answered with the app guide."),
        dict(q="Are any of my companies in trouble?",
             carry=[qm("pnl", ["*"]), qm("balance_sheet", ["*"]), qm("receivables", ["*"]), NO_CALL],
             note="From figures (losses, cash, overdue); the old path answered with the app guide."),
    ]),
    ("f31", "independent", ALL, "real thread 29 Sep (r044-r045)", [
        dict(q="revenue by month for each organization", carry=[qm("pnl", ["revenue"], granularity="month")],
             ref=[ref("pnl", ["revenue"], "ytd", granularity="month")]),
        dict(q="Quarterly profit trend", carry=[qm("pnl", ["net_profit"], granularity="quarter")],
             ref=[ref("pnl", ["net_profit"], "ytd", granularity="quarter")]),
    ]),
    ("f32", "independent", ALL, "real thread 29 Sep (r072)", [
        dict(q="What is VAT for each organization?", carry=[qm("vat", ["*"])],
             ref=[ref("vat", ["output_vat", "input_vat", "net_vat_payable"], "ytd")]),
        dict(q="What is VAT?", carry=[NO_CALL],
             note="Explains what VAT is (or asks); the old path repeated the figures for another period."),
    ]),
]


def build() -> Dict[str, Any]:
    cases = []
    for chain, category, orgs, source, turns in CHAINS:
        ids: List[str] = []
        for i, turn in enumerate(turns):
            tid = f"{chain}{'abcdefgh'[i]}"
            expect: Dict[str, Any] = {}
            for key in ("ref", "carry", "file", "note"):
                if turn.get(key):
                    expect[key] = turn[key]
            if turn.get("clarify"):
                expect["clarify"] = True
            if turn.get("kind") == "kb":
                expect["kb"] = True
            cases.append({"id": tid, "chain": chain, "category": category, "source": source, "orgs": orgs,
                          "question": turn["q"], "kind": turn.get("kind") or ("opener" if i == 0 else "followup"),
                          "context": list(ids), "expect": expect})
            ids.append(tid)
    return {
        "description": (
            "Follow-up chains for session context (docs/CONTEXT_MODELS_REPORTS_PLAN.md, step 1.1). Each chain "
            "is replayed in one thread; 'context' lists its earlier turns. 'expect.carry': acceptable tool calls "
            "(one must match a successful call; tool null = may answer without a call; '*' matches anything). "
            "'expect.ref': reference Cube queries for the figure screen. 'expect.clarify': the agent should ask, "
            "not query. 'expect.file': the turn should get a file or chart. 'source': 'hand' or the real-question "
            "ids the chain replays. Generated by scripts/eval/build_followup_chains.py; edit that, not this file."
        ),
        "cases": cases,
    }


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    data = build()
    out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    chains = {c["chain"] for c in data["cases"]}
    follow = sum(c["kind"] != "opener" for c in data["cases"])
    print(f"{out}: {len(chains)} chains, {len(data['cases'])} turns, {follow} follow-ups")
    return 0


if __name__ == "__main__":
    sys.exit(main())
