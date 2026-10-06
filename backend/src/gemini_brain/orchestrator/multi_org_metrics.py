"""
multi_org_metrics.py — Comparable metrics for multi-organization questions.

A question such as "which organization has the highest cash balance" or
"rank the organizations by expenses" names one figure per organization. For
those, the source of the figure is fixed per metric (an org-filtered SQL
report) instead of being chosen by a model for each question, and the
comparison itself (ranking, shares, gaps, totals) is computed here in code.
The model only writes a short summary of the computed table; it never has to
read raw rows or add numbers up.

Three shapes are computed here:
- one metric per org ("compare revenue"): a ranked table and a bar chart;
- several metrics per org ("compare revenue, expenses and cash", "balance
  sheet", "P&L"): one table with a column per metric;
- one metric over time per org ("revenue by month"): periods x organizations,
  and a line per organization.

Questions that ask for lists or breakdowns by customer, vendor, category...
are deliberately not matched: they are not figures per org.
"""
from __future__ import annotations

import datetime
import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Pattern, Tuple

from gemini_brain.router import dates
from gemini_brain.utils.ranking import extract_direction_from_text

CURRENCY = "currency"
COUNT = "count"
PERCENT = "percent"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    report: str                 # rpt_ key in reports/definitions.py
    field: str                  # key in the report's `summary`
    unit: str                   # CURRENCY, COUNT or PERCENT
    pattern: Pattern
    period_based: bool          # True: start/end window; False: balance as of today
    currency_field: Optional[str] = None  # summary key holding the value's own currency, if any
    params: Tuple[Tuple[str, str], ...] = ()  # fixed report parameters, e.g. (("base", "income"),)
    extra_fields: Tuple[Tuple[str, str], ...] = ()  # (summary key, column label) shown beside the value
    #: Summary keys (counts) or payload keys (row lists) that show the report found activity.
    #: A value of 0 with all of them zero or empty means nothing was recorded, not a real 0.
    evidence: Tuple[str, ...] = ()

    @property
    def fetch_key(self) -> str:
        """Identifies the report call: metrics with the same key share one fetch."""
        return self.report + "".join(f":{v}" for _, v in self.params)


def _p(expr: str) -> Pattern:
    return re.compile(expr, re.IGNORECASE)


_GROWTH = (r"grow(?:th|s|n|ing)?|grew|increase[sd]?|decrease[sd]?|decline[sd]?|change[sd]?|yoy|year[\s-]+over[\s-]+year"
           # "this year vs last year" is a growth comparison, not one window.
           r"|(?:vs\.?|versus|against|compared\s+(?:to|with))\s+(?:the\s+)?(?:last|previous|prior|same\s+period\s+last)"
           r"\s+(?:year|quarter|month|period)")


def _growth_pattern(base_words: str) -> Pattern:
    """The base metric and a growth word close together, in either order."""
    return _p(rf"\b(?:{base_words})\b[\w\s,&]{{0,24}}?\b(?:{_GROWTH})\b"
              rf"|\b(?:{_GROWTH})\b[\w\s,&]{{0,24}}?\b(?:{base_words})\b")


_REVENUE_WORDS = r"revenues?|sales|turnover|income(?!\s+statement)"
_EXPENSE_WORDS = r"expenses?|spend(?:ing)?|expenditures?|costs?"
_PROFIT_WORDS = r"profits?|net\s+income|earnings"

#: Matched in this order, and an earlier match hides later ones on the same
#: words: "revenue growth" is growth, not revenue; "overdue receivables" is not
#: also "receivables"; "profit margin" is not also "profit".
METRICS: List[Metric] = [
    Metric("revenue_growth", "Revenue growth", "rpt_period_growth", "growth_pct", PERCENT,
           _growth_pattern(_REVENUE_WORDS), period_based=True, params=(("base", "income"),),
           extra_fields=(("current", "Current"), ("previous", "Previous"))),
    Metric("expense_growth", "Expense growth", "rpt_period_growth", "growth_pct", PERCENT,
           _growth_pattern(_EXPENSE_WORDS), period_based=True, params=(("base", "expenses"),),
           extra_fields=(("current", "Current"), ("previous", "Previous"))),
    Metric("profit_growth", "Profit growth", "rpt_period_growth", "growth_pct", PERCENT,
           _growth_pattern(_PROFIT_WORDS), period_based=True, params=(("base", "profit"),),
           extra_fields=(("current", "Current"), ("previous", "Previous"))),
    Metric("profit_margin", "Profit margin", "rpt_profit_summary", "profit_margin_pct", PERCENT,
           _p(r"\b(?:net\s+|profit\s+)?margins?\b|\bprofit\s+(?:percentage|ratio)\b"),
           period_based=True),
    Metric("cash_balance", "Cash balance", "rpt_cash_balance", "total_balance", CURRENCY,
           _p(r"\bcash\b(?!\s*flow)|\bbank\s+balances?\b|\bmoney\s+in\s+(?:the\s+)?bank\b"),
           period_based=False, currency_field="account_currency", evidence=("account_count",)),
    Metric("total_assets", "Total assets", "rpt_balance_sheet", "total_assets", CURRENCY,
           _p(r"\b(?:total\s+)?assets\b"), period_based=False, evidence=("assets", "liabilities")),
    Metric("total_liabilities", "Total liabilities", "rpt_balance_sheet", "total_liabilities", CURRENCY,
           _p(r"\b(?:total\s+)?liabilit(?:y|ies)\b"), period_based=False, evidence=("assets", "liabilities")),
    Metric("total_equity", "Total equity", "rpt_balance_sheet", "total_equity", CURRENCY,
           _p(r"\b(?:total\s+|owners?'?\s+|shareholders?'?\s+)?equity\b|\bnet\s+worth\b"), period_based=False,
           evidence=("assets", "liabilities")),
    Metric("net_profit", "Net profit", "rpt_profit_summary", "net_profit", CURRENCY,
           _p(r"\bprofit(?:s|ability|able)?\b|\bnet\s+income\b|\bbottom\s+line\b"
              r"|\b(?:net\s+)?loss(?:es)?\b(?!\s+statement)|\bloss[\s-]+making\b|\blost\s+money\b"),
           period_based=True),
    Metric("input_vat", "Input VAT", "rpt_vat_input_output", "total_input_vat", CURRENCY,
           _p(r"\binput\s+vat\b|\bvat\s+(?:on\s+)?(?:purchases|bills|expenses|paid)\b|\brecoverable\s+vat\b"),
           period_based=True, evidence=("input_vat_by_month",)),
    Metric("output_vat", "Output VAT", "rpt_vat_input_output", "total_output_vat", CURRENCY,
           _p(r"\boutput\s+vat\b|\bvat\s+(?:on\s+)?(?:sales|invoices|collected|charged)\b"),
           period_based=True, evidence=("output_vat_by_month",)),
    Metric("vat_payable", "Net VAT payable", "rpt_vat_input_output", "net_vat_payable", CURRENCY,
           _p(r"\bvat\b|\btax\s+(?:payable|liability|due)\b"),
           period_based=True, evidence=("output_vat_by_month", "input_vat_by_month")),
    Metric("overdue_receivables", "Overdue receivables", "rpt_receivables_outstanding", "overdue_amount", CURRENCY,
           _p(r"\boverdue\s+(?:invoices?|receivables?)\b|\b(?:invoices?|receivables?)\s+(?:that\s+are\s+)?(?:overdue|past\s+due)\b|\bpast\s+due\s+invoices?\b"),
           period_based=False),
    Metric("overdue_payables", "Overdue payables", "rpt_payables_outstanding", "overdue_amount", CURRENCY,
           _p(r"\boverdue\s+(?:bills?|payables?)\b|\b(?:bills?|payables?)\s+(?:that\s+are\s+)?(?:overdue|past\s+due)\b|\bpast\s+due\s+bills?\b"),
           period_based=False),
    Metric("receivables", "Outstanding receivables", "rpt_receivables_outstanding", "outstanding", CURRENCY,
           _p(r"\breceivables?\b|\bowed\s+to\s+(?:us|them|it)\b|\bowes?\s+(?:us|them)\b|\b(?:outstanding|unpaid)\s+invoices?\b|\bdebtors?\b"),
           period_based=False),
    Metric("payables", "Outstanding payables", "rpt_payables_outstanding", "outstanding", CURRENCY,
           # "VAT payable" / "tax payable" is a tax liability, not money owed to suppliers.
           _p(r"(?<!vat\s)(?<!tax\s)\bpayables?\b|\b(?:we|they)\s+owe\b|\b(?:outstanding|unpaid)\s+bills?\b|\bcreditors?\b"),
           period_based=False),
    Metric("expenses", "Total expenses", "rpt_expense_total", "total_expenses", CURRENCY,
           _p(r"\bexpenses?\b|\bspend(?:ing|s)?\b|\bexpenditures?\b"),
           period_based=True, evidence=("bill_count",)),
    Metric("revenue", "Total revenue", "rpt_income_total", "total_income", CURRENCY,
           _p(r"\brevenues?\b|\bsales\b|\bturnover\b|\bincome\b(?!\s+statement)|\bearn(?:ed|ings|s)?\b"),
           period_based=True, evidence=("invoice_count",)),
]

BY_KEY = {m.key: m for m in METRICS}


@dataclass(frozen=True)
class Unsupported:
    """A figure the ledger cannot produce. Its words are hidden from metric matching."""
    label: str
    pattern: Pattern
    reason: str
    closest: Tuple[str, ...] = ()  # metric keys worth offering instead


#: Figures users ask for that no report can compute. Without this list the
#: nearest metric answered under the asked name: "gross margin" came back as
#: net margin, "fixed assets" as total assets, "other income" as total revenue,
#: "cost of sales" as revenue, and EBITDA and cash flow were made up.
UNSUPPORTED: List[Unsupported] = [
    Unsupported("EBITDA / operating profit",
                _p(r"\bebitda\b|\bebit\b|\boperating\s+(?:profit|income|margin)\b"),
                "expenses are not split into depreciation, amortisation, interest and tax",
                ("net_profit",)),
    Unsupported("Gross profit / gross margin", _p(r"\bgross\s+(?:profit|margin)s?\b"),
                "there is no cost-of-sales split, so gross profit cannot be separated from net profit",
                ("profit_margin", "net_profit")),
    Unsupported("Cost of sales", _p(r"\bcost\s+of\s+(?:sales|goods(?:\s+sold)?|revenue)\b|\bcogs\b"),
                "expenses are not classified as cost of sales", ("expenses",)),
    Unsupported("Working capital and liquidity ratios",
                _p(r"\bworking\s+capital\b|\b(?:current|quick|acid[\s-]+test|liquidity)\s+ratios?\b"),
                "assets and liabilities are not classified as current or non-current",
                ("cash_balance", "receivables", "payables")),
    Unsupported("Current / non-current / fixed balances",
                _p(r"\b(?:non[\s-]*)?current\s+(?:assets|liabilit(?:y|ies))\b|\bfixed\s+assets\b"
                   r"|\b(?:long|short)[\s-]+term\s+(?:debt|loans?|liabilit(?:y|ies)|assets)\b"),
                "assets and liabilities are not classified as current, non-current or fixed",
                ("total_assets", "total_liabilities")),
    Unsupported("Cash flow", _p(r"\bcash[\s-]*flows?\b|\bburn\s+rate\b|\bfree\s+cash\b"),
                "there is no cash flow statement; only the cash balance on a date is available",
                ("cash_balance",)),
    Unsupported("Other income", _p(r"\b(?:other|non[\s-]*operating|miscellaneous)\s+income\b"),
                "income is not split by category", ("revenue",)),
    Unsupported("Corporate / income tax",
                _p(r"\b(?:corporate|income|corporation)\s+tax(?:\s+(?:payable|liabilit(?:y|ies)|expense|due))?\b"),
                "corporate tax is not recorded in the ledger; only VAT is", ("vat_payable",)),
    Unsupported("Collection and payment days, stock turnover",
                _p(r"\bdso\b|\bdpo\b|\bdays\s+(?:sales|payables?)\s+outstanding\b|\b(?:debtor|creditor)\s+days\b"
                   r"|\binventory\s+turnover\b|\bstock\s+turnover\b"),
                "there is no report for it yet", ("receivables", "payables")),
    Unsupported("Payroll and headcount",
                _p(r"\bpayroll\b|\bheadcount\b|\bemployees?\b|\bsalar(?:y|ies)\b|\bstaff\s+costs?\b"),
                "payroll and headcount are not recorded", ("expenses",)),
]


def unsupported_terms(question: str) -> List[Unsupported]:
    """The figures in the question that cannot be computed, in the order of UNSUPPORTED."""
    return [u for u in UNSUPPORTED if u.pattern.search(question or "")]


def _mask_unsupported(question: str) -> str:
    """The question with unsupported phrases blanked, so "fixed assets" is not read as "assets"."""
    for u in UNSUPPORTED:
        question = u.pattern.sub(lambda m: " " * len(m.group(0)), question)
    return question


def unsupported_note(terms: List[Unsupported]) -> List[str]:
    """One line per figure that cannot be computed, with what is available instead."""
    lines = []
    for u in terms:
        closest = ", ".join(BY_KEY[k].label.lower() for k in u.closest if k in BY_KEY)
        lines.append(f"{u.label} is not available: {u.reason}."
                     + (f" Closest available: {closest}." if closest else ""))
    return lines


#: Open questions about how the organizations are doing ("which company is
#: performing best", "are any of my companies in trouble", "summarize each
#: company"). They name no figure, and were answered with generic advice or a
#: how-to guide instead of data. They get a fixed scorecard.
_SCORECARD_WORDS = _p(
    r"\bperform(?:s|ing|ance)?\b|\bdoing\b|\bhealth(?:y)?\b|\btrouble\b|\bstruggl\w*|\bat\s+risk\b|\brisky?\b"
    r"|\boverview\b|\bsummar(?:y|ise|ize|ies)\b|\bsnapshot\b|\bscorecard\b|\bbest\b|\bworst\b"
    r"|\bstrong(?:est)?\b|\bweak(?:est)?\b|\binvest\w*"
)
_SCORECARD_SUBJECT = _p(
    r"\bcompan(?:y|ies)\b|\bbusiness(?:es)?\b|\bentit(?:y|ies)\b|\borg(?:ani[sz]ation)?s?\b|\bportfolio\b"
    r"|\bgroup\b|\bsubsidiar(?:y|ies)\b|\bventures?\b|\boverall\b"
)
_HOW_ARE_WE_DOING = _p(r"\bhow\s+(?:are|is)\s+(?:we|things|business|our|my)\b.*\bdoing\b")
#: How-to and setup phrasing is left to the guide ("how do I set up a company profile").
_SCORECARD_NOT = _p(
    r"^\s*how\s+(?:do|can|to|should)\b|\bbest\s+(?:way|practice)s?\b"
    r"|\b(?:create|set\s*up|setup|configure|add|delete|edit|record|upload|profile|settings?)\b"
)

#: The scorecard: size, profitability, growth, liquidity, collections and solvency.
SCORECARD_METRICS = ("revenue", "net_profit", "profit_margin", "revenue_growth", "cash_balance",
                     "overdue_receivables", "total_equity")


def is_scorecard_question(question: str) -> bool:
    """An open question about how the organizations are doing that names no figure."""
    q = question or ""
    if _SCORECARD_NOT.search(q) or match_metrics(q) or unsupported_terms(q) or _LIST_OR_BREAKDOWN.search(q):
        return False
    return bool(_HOW_ARE_WE_DOING.search(q) or (_SCORECARD_WORDS.search(q) and _SCORECARD_SUBJECT.search(q)))

#: Statements named as a whole: each is a fixed set of metrics.
METRIC_GROUPS: List[Tuple[Pattern, List[str]]] = [
    (_p(r"\bbalance\s+sheets?\b|\bfinancial\s+position\b"), ["total_assets", "total_liabilities", "total_equity"]),
    (_p(r"\bp\s*&\s*l\b|\bprofit\s+(?:and|&)\s+loss\b|\bincome\s+statements?\b"),
     ["revenue", "expenses", "net_profit", "profit_margin"]),
]

#: A plan that lands on one of these reports asks for that metric, whatever the
#: wording ("What is the total revenue…" routed by the fast router to
#: rpt_income_total is the revenue comparison). Reports that carry two metrics
#: map to the broader one.
METRIC_FOR_REPORT: Dict[str, str] = {
    "rpt_income_total": "revenue",
    "rpt_expense_total": "expenses",
    "rpt_profit_summary": "net_profit",
    "rpt_receivables_outstanding": "receivables",
    "rpt_payables_outstanding": "payables",
    "rpt_cash_balance": "cash_balance",
    "rpt_vat_input_output": "vat_payable",
}

#: Questions that want rows, not figures per organization.
_LIST_OR_BREAKDOWN = _p(
    r"\b(list|details?|breakdown|itemi[sz]e|each\s+(invoice|bill|customer|vendor))\b"
    r"|\bshow\s+(me\s+)?all\b"
    r"|\b(largest|biggest|smallest|highest|lowest|latest|recent)\s+\d*\s*(invoices?|bills?|customers?|vendors?|suppliers?|clients?|items?|products?|transactions?|payments?)\b"
    r"|\btop\s+\d*\s*(customers?|vendors?|suppliers?|clients?|items?|products?)\b"
    r"|\bwhich\s+(customers?|vendors?|suppliers?|clients?|items?|products?)\b"
    r"|\bby\s+(customer|vendor|supplier|client|month|quarter|week|category|item|product|project)\b"
    r"|\bper\s+(customer|vendor|supplier|client|month|quarter|week|category|item|product|project)\b"
)

#: Definitions and how-to questions ("What is VAT?") name a metric but want no figures.
_CONCEPT_OPENING = _p(
    r"^\s*(what\s+(is|are|does)|explain|define|how\s+(do|does|can|to|should)|why\s+(is|are|do|does))\b"
)
_CONCEPT_ALWAYS = _p(r"\bdifference\s+between\b|\bmeaning\s+of\b|\bwhat\s+does\s+.+\s+mean\b")
#: Words that make "What is the total revenue this year" a data question, not a definition.
_DATA_CUES = _p(
    r"\b(total|each|every|organi[sz]ations?|orgs?|compan(y|ies)|compare|comparison|rank|highest|lowest"
    r"|most|least|this|last|current|previous|year|quarter|month|ytd|our|their|amount|figures?"
    r"|entit(y|ies)|combined|consolidated|group|subsidiar(y|ies)|across|position|balances?)\b"
    r"|\b(19|20)\d{2}\b"
)


def is_concept_question(question: str) -> bool:
    """A definition or how-to question ("What is VAT?"), which wants no figures."""
    q = question or ""
    if _CONCEPT_ALWAYS.search(q):
        return True
    return bool(_CONCEPT_OPENING.search(q)) and not _DATA_CUES.search(q)


def _raw_matches(question: str) -> List[Metric]:
    """Every metric the question names, in the order they appear in it.

    Metrics are tried in METRICS order; a match is dropped when its words
    overlap an earlier (more specific) match.
    """
    taken: List[Tuple[int, int]] = []
    found: List[Tuple[int, Metric]] = []
    for metric in METRICS:
        m = metric.pattern.search(question)
        if not m:
            continue
        span = m.span()
        if any(span[0] < end and start < span[1] for start, end in taken):
            continue
        taken.append(span)
        found.append((span[0], metric))
    return [metric for _, metric in sorted(found, key=lambda x: x[0])]


_GROWTH_ANY = _p(rf"\b(?:{_GROWTH})\b")
_GROWTH_FOR_BASE = {"revenue": "revenue_growth", "expenses": "expense_growth", "net_profit": "profit_growth"}


def _growth_matches(question: str) -> List[Metric]:
    """With a growth word, each base figure named becomes its growth metric.

    "revenue and profit growth" is two growth figures; matching the phrase
    alone would find only the first.
    """
    if not _GROWTH_ANY.search(question):
        return []
    bases = [m for m in _raw_matches(_GROWTH_ANY.sub(" ", question)) if m.key in _GROWTH_FOR_BASE]
    return [BY_KEY[_GROWTH_FOR_BASE[m.key]] for m in bases]


#: Plain listing words. With organizations and a condition ("list the organizations
#: making a loss") the rows wanted are organizations, so it is a figure per org.
_GENERIC_LIST = _p(r"\blist\b|\bshow\s+(me\s+)?all\b")
#: Organizations as the thing listed: "list (all) the organizations", "which of the companies".
#: "List overdue bills ... in each org" lists bills, not organizations.
_ORGS_LISTED = _p(
    r"\b(list|show\s+(me\s+)?|which|what)\s+(of\s+)?(all\s+)?(the\s+|my\s+|our\s+)?"
    r"(organi[sz]ations?|orgs?|compan(y|ies)|entit(y|ies)|businesses|subsidiar(y|ies))\b"
)


def _organizations_with_condition(question: str) -> bool:
    """"List the organizations with margin above 20%": a list of organizations, filtered on a figure."""
    from gemini_brain.orchestrator.multi_org_conditions import has_condition

    return (bool(_ORGS_LISTED.search(question)) and has_condition(question)
            and not _LIST_OR_BREAKDOWN.search(_GENERIC_LIST.sub(" ", question)))


def match_metrics(question: str) -> List[Metric]:
    """The figures per organization a question asks for; [] when it wants something else.

    Unsupported figures ("gross margin", "fixed assets") are blanked first so
    their words never select a different metric.
    """
    if not question or is_concept_question(question):
        return []
    if _LIST_OR_BREAKDOWN.search(question) and not _organizations_with_condition(question):
        return []
    question = _mask_unsupported(question)
    for pattern, keys in METRIC_GROUPS:
        if pattern.search(question):
            return [BY_KEY[k] for k in keys]
    growth = _growth_matches(question)
    if growth:
        other = [m for m in _raw_matches(question)
                 if m.key not in _GROWTH_FOR_BASE and m.key not in _GROWTH_FOR_BASE.values()]
        return growth + other
    found = _raw_matches(question)
    if _BARE_GROWTH.search(question):
        # "growth and margin", "compare growth": growth with no figure named is revenue growth.
        found = [BY_KEY["revenue_growth"]] + found
    if not found:
        # "Which organizations are loss-making": net profit, named only by its sign.
        from gemini_brain.orchestrator.multi_org_conditions import implied_metric

        implied = implied_metric(question)
        found = [implied] if implied is not None else []
    return found


#: "growth" as a noun on its own; verbs like "change" or "increase" are left alone.
_BARE_GROWTH = _p(r"\bgrowth\b")


def match_metric(question: str) -> Optional[Metric]:
    """The first figure a question asks for, or None."""
    metrics = match_metrics(question)
    return metrics[0] if metrics else None


#: Metrics that can be shown over time, per organization.
SERIES_METRICS = ("revenue", "expenses", "net_profit")
_SERIES_WORDS = _p(
    r"\b(by|per|each|every)\s+(month|quarter)\b|\bmonthly\b|\bquarterly\b"
    r"|\bmonth[\s-]+(by|on|over)[\s-]+month\b|\bquarter[\s-]+(by|on|over)[\s-]+quarter\b"
    r"|\btrends?\b|\bover\s+time\b"
)
_QUARTER_WORDS = _p(r"\bquarter(ly|s)?\b")


def match_series(question: str) -> Optional[Tuple[Metric, str]]:
    """(metric, grain) for "revenue by month"-style questions, or None."""
    found = match_series_metrics(question)
    return (found[0][0], found[1]) if found else None


#: A statement asked for over time: every figure of it that has a series.
_PNL = _p(r"\bp\s*&\s*l\b|\bprofit\s+(?:and|&)\s+loss\b|\bincome\s+statements?\b")


def match_series_metrics(question: str) -> Optional[Tuple[List[Metric], str]]:
    """([metrics], grain) for figures over time, or None.

    "P&L by month" is revenue, expenses and net profit per month; it used to
    come back as net profit alone (the first figure the words matched).
    """
    if not question or not _SERIES_WORDS.search(question) or is_concept_question(question):
        return None
    grain = "quarter" if _QUARTER_WORDS.search(question) and not re.search(r"\bmonth", question, re.I) else "month"
    masked = _mask_unsupported(question)
    if _PNL.search(masked):
        return [BY_KEY[k] for k in SERIES_METRICS], grain
    metrics = [m for m in _raw_matches(masked) if m.key in SERIES_METRICS]
    return (metrics, grain) if metrics else None


def _window(question: str) -> Tuple[str, str]:
    from gemini_brain.orchestrator.multi_org_dates import resolve_window

    start, end = resolve_window(question)
    return start.isoformat(), end.isoformat()


def _growth_basis(question: str) -> str:
    """Compare with the same dates last year unless the question asks for the previous period."""
    q = question or ""
    if re.search(r"\b(previous|prior|preceding|last)\s+(period|month|quarter)\b|\bmonth[\s-]on[\s-]month\b"
                 r"|\bquarter[\s-]on[\s-]quarter\b|\bmom\b|\bqoq\b", q, re.I):
        return "period"
    return "year"


_QOQ = re.compile(r"\bquarter[\s-]+(?:on|over)[\s-]+quarter\b|\bqoq\b|\b(?:previous|prior|last)\s+quarter\b", re.I)
_MOM = re.compile(r"\bmonth[\s-]+(?:on|over)[\s-]+month\b|\bmom\b|\b(?:previous|prior|last)\s+month\b", re.I)


def _growth_window_question(metric: Metric, question: str) -> str:
    """For a growth figure with no period of its own, the period its comparison implies.

    "Revenue growth quarter on quarter" compared the year so far with the
    stretch before it; it means this quarter against the previous one.
    """
    from gemini_brain.orchestrator.multi_org_dates import period_phrase

    if metric.report != "rpt_period_growth":
        return question
    phrase = period_phrase(question)
    compares_to_previous = phrase and re.fullmatch(r"(?:previous|prior|last)\s+(?:quarter|month)", phrase.strip(), re.I)
    if phrase and not compares_to_previous:
        return question
    if _QOQ.search(question):
        return "this quarter"
    if _MOM.search(question):
        return "this month"
    return question


def metric_selection(metric: Metric, question: str) -> Dict[str, Any]:
    """The data selection that fetches `metric` for one organization."""
    if metric.period_based:
        start, end = _window(_growth_window_question(metric, question))
        query_params: Dict[str, Any] = {"start_date": start, "end_date": end}
    else:
        query_params = {"as_of_date": dates.today().isoformat()}
    query_params.update(dict(metric.params))
    if metric.report == "rpt_period_growth":
        query_params["basis"] = _growth_basis(question)
    return {
        "endpoint": metric.report,
        "key": metric.fetch_key,
        "path_params": {},
        "query_params": query_params,
        "reason": f"Metric: {metric.key}",
    }


def metrics_selections(metrics: List[Metric], question: str) -> List[Dict[str, Any]]:
    """One selection per distinct report call the metrics need."""
    seen: Dict[str, Dict[str, Any]] = {}
    for metric in metrics:
        seen.setdefault(metric.fetch_key, metric_selection(metric, question))
    return list(seen.values())


_SERIES_BASE = {"revenue": "income", "expenses": "expenses", "net_profit": "profit"}


def series_key(metric: Metric, grain: str) -> str:
    """The payload key of one metric's series fetch."""
    return f"rpt_metric_series:{_SERIES_BASE[metric.key]}:{grain}"


def series_selection(metric: Metric, grain: str, question: str) -> Dict[str, Any]:
    """The per-period data for `metric` over the question's window."""
    start, end = _window(question)
    base = _SERIES_BASE[metric.key]
    return {
        "endpoint": "rpt_metric_series",
        "key": series_key(metric, grain),
        "path_params": {},
        "query_params": {"start_date": start, "end_date": end, "base": base, "grain": grain},
        "reason": f"Series: {metric.key} by {grain}",
    }


#: Per-contact totals for "which vendors/customers do the orgs share" questions.
CONTACT_TOTALS_REPORTS = {"vendor": "rpt_vendor_totals", "customer": "rpt_customer_totals"}
_VENDOR_WORDS = _p(r"\b(vendors?|suppliers?|payees?)\b")
_CUSTOMER_WORDS = _p(r"\b(customers?|clients?|buyers?)\b")


def contact_totals_selection(question: str) -> Optional[Dict[str, Any]]:
    """Org-filtered per-contact totals for a shared-vendors/customers question.

    Names are matched across organizations, so the period defaults to all
    history rather than this year: a vendor both orgs used last year is still
    shared. A period in the question narrows it.
    """
    if _VENDOR_WORDS.search(question or ""):
        report = CONTACT_TOTALS_REPORTS["vendor"]
    elif _CUSTOMER_WORDS.search(question or ""):
        report = CONTACT_TOTALS_REPORTS["customer"]
    else:
        return None
    from gemini_brain.orchestrator.multi_org_dates import period_phrase

    if period_phrase(question or ""):
        start, end = _window(question)
    else:
        start, end = "2000-01-01", dates.today().isoformat()
    return {
        "endpoint": report,
        "path_params": {},
        "query_params": {"start_date": start, "end_date": end, "limit": 500},
        "reason": "Shared contacts: per-contact totals",
    }


# ── Reading figures from fetched results ─────────────────────────────────────

def _payload_for(metric: Metric, result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The report payload holding `metric` in one org's fetched result."""
    payloads = (result or {}).get("payloads") or {}
    if metric.fetch_key in payloads:
        payload = payloads[metric.fetch_key]
        return payload if isinstance(payload, dict) else None
    if payloads:
        return None  # several reports were fetched and this metric's is missing
    for row in (result or {}).get("results") or []:
        if isinstance(row, dict) and isinstance(row.get("summary"), dict):
            return row
    return None


def _summary_number(payload: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    value = ((payload or {}).get("summary") or {}).get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def extract_value(metric: Metric, result: Optional[Dict[str, Any]]) -> Optional[float]:
    """The metric's figure from one org's fetched result; None when absent."""
    return _summary_number(_payload_for(metric, result), metric.field)


#: Reason given for a 0 that only reflects an empty ledger.
NOTHING_RECORDED = "nothing recorded"


def nothing_recorded(metric: Metric, result: Optional[Dict[str, Any]]) -> bool:
    """Whether the report found no activity at all behind the metric's figure.

    A report run over an organization with no invoices (or no VAT lines, no
    cash accounts...) still returns 0, and that 0 used to be ranked as the
    lowest figure. Evidence keys that are absent prove nothing either way.
    """
    if not metric.evidence:
        return False
    payload = _payload_for(metric, result)
    if not isinstance(payload, dict):
        return False
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    found = [summary[k] if k in summary else payload[k] for k in metric.evidence if k in summary or k in payload]
    return bool(found) and not any(found)


def metric_value(metric: Metric, run: Any) -> Tuple[Optional[float], str]:
    """(figure, reason it is missing) for one org; the reason is "" when there is a figure."""
    if not run.answered:
        return None, "could not be retrieved"
    value = extract_value(metric, run.result)
    if value is None:
        return None, "no data"
    if value == 0 and nothing_recorded(metric, run.result):
        return None, NOTHING_RECORDED
    return value, ""


def _value_currency(metric: Metric, run: Any) -> str:
    """Currency of the figure: the org's currency, or the account currency the report states."""
    if metric.unit != CURRENCY:
        return ""
    if metric.currency_field:
        own = ((_payload_for(metric, run.result) or {}).get("summary") or {}).get(metric.currency_field)
        if own:
            return str(own)
    return run.currency or ""


def period_label(query_params: Optional[Dict[str, Any]]) -> str:
    """Human wording of the period a figure covers, e.g. "1 Jan 2026 – 28 Sep 2026"."""
    qp = query_params or {}

    def fmt(value: Any) -> str:
        try:
            day = datetime.date.fromisoformat(str(value))
        except (ValueError, TypeError):
            return str(value)
        return f"{day.day} {day.strftime('%b %Y')}"

    if qp.get("start_date") and qp.get("end_date"):
        label = f"{fmt(qp['start_date'])} – {fmt(qp['end_date'])}"
        if qp.get("basis"):
            label += " vs " + ("the same period last year" if qp["basis"] == "year" else "the previous period")
        return label
    if qp.get("as_of_date"):
        return f"as of {fmt(qp['as_of_date'])}"
    return ""


def _unit_suffix(unit: str, currency: Optional[str]) -> str:
    if unit == PERCENT:
        return "%"
    return f" {currency}" if currency else ""


def _fmt(value: Optional[float], unit: str, currency: Optional[str] = None) -> str:
    if value is None:
        return "n/a"
    return f"{value:,.2f}{_unit_suffix(unit, currency)}"


# ── One metric ───────────────────────────────────────────────────────────────

def build_comparison(
    metric: Metric,
    runs: List[Any],
    question: str,
    period: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Rank the organizations on one metric, in code.

    `runs` are OrgRun objects (see multi_org.py). An org without a value is
    listed under `missing` with its reason, never given a guessed figure.
    Shares and the total appear only when every figure is in one currency;
    percentages are compared directly and never totalled.
    """
    ascending = sort_ascending(question)
    rows: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    for run in runs:
        value, reason = metric_value(metric, run)
        if value is None:
            if reason == NOTHING_RECORDED:
                pass
            elif run.answered and metric.key == "cash_balance":
                reason = "no cash or bank postings in the ledger (or accounts in several currencies)"
            elif run.answered and metric.unit == PERCENT:
                reason = "no base figure to compute a percentage from"
            missing.append({"organization": run.name, "organization_id": run.org_id, "reason": reason})
            continue
        row = {
            "organization": run.name,
            "organization_id": run.org_id,
            "currency": _value_currency(metric, run),
            "value": round(value, 2),
        }
        payload = _payload_for(metric, run.result)
        for key, _label in metric.extra_fields:
            row[key] = _summary_number(payload, key)
        if metric.extra_fields:
            row["extra_currency"] = run.currency or ""
        rows.append(row)

    currencies = {r["currency"] for r in rows}
    comparable = metric.unit in (COUNT, PERCENT) or (len(currencies) == 1 and "" not in currencies)
    total = round(sum(r["value"] for r in rows), 2) if comparable and rows and metric.unit != PERCENT else None
    # Figures in different currencies are not ranked: an order between them
    # would claim a comparison the numbers cannot support.
    if comparable:
        rows.sort(key=lambda r: r["value"], reverse=not ascending)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank if comparable else None
        # A share is an org's part of the combined total; it means nothing when
        # the total or any figure is negative (net losses, overdrawn accounts).
        if total and total > 0 and all(r["value"] >= 0 for r in rows):
            row["share_pct"] = round(100.0 * row["value"] / total, 1)

    comparison: Dict[str, Any] = {
        "kind": "metric",
        "metric": metric.key,
        "label": metric.label,
        "period": period_label(period),
        "unit": metric.unit,
        "order": "ascending" if ascending else "descending",
        "comparable": comparable,
        "currency": next(iter(currencies)) if comparable and metric.unit == CURRENCY and rows else None,
        "extra_fields": [list(f) for f in metric.extra_fields],
        "rows": rows,
        "missing": missing,
        "total": total,
    }
    if len(rows) >= 2 and comparable:
        comparison["spread"] = round(rows[0]["value"] - rows[-1]["value"], 2)
    return comparison


def comparison_block(comparison: Dict[str, Any]) -> Dict[str, Any]:
    """The computed ranking as a table block, shown above everything else."""
    unit = comparison.get("unit")
    currency = comparison.get("currency")
    value_label = comparison["label"] + (" (%)" if unit == PERCENT else f" ({currency})" if currency else "")
    columns = [
        {"key": "rank", "label": "#", "align": "right"},
        {"key": "organization", "label": "Organization", "align": "left"},
        {"key": "value", "label": value_label, "align": "right"},
    ]
    if not comparison.get("comparable"):
        columns.insert(2, {"key": "currency", "label": "Currency", "align": "left"})
        columns = [c for c in columns if c["key"] != "rank"]
    extra = comparison.get("extra_fields") or []
    for key, label in extra:
        cur = next((r.get("extra_currency") for r in comparison["rows"] if r.get("extra_currency")), "")
        columns.append({"key": key, "label": label + (f" ({cur})" if cur else ""), "align": "right"})
    if any("share_pct" in r for r in comparison["rows"]):
        columns.append({"key": "share_pct", "label": "% of total", "align": "right"})
    keys = ["rank", "organization", "currency", "value", "share_pct"] + [k for k, _ in extra]
    rows = [{k: r.get(k) for k in keys} for r in comparison["rows"]]
    title = comparison["label"] + (f", {comparison['period']}" if comparison.get("period") else "")
    caption = title
    if any("share_pct" in r for r in comparison["rows"]):
        caption += " · % of total: each organization's part of the selected organizations' combined total"
    return {
        "type": "table",
        "title": title,
        # TableBlock shows `period` as the table's caption.
        "period": caption,
        "columns": columns,
        "rows": rows,
        "total_rows": len(rows),
        "truncated": False,
    }


def comparison_chart(comparison: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A horizontal bar chart of the ranking, or None when a chart would mislead.

    Drawn only for figures in one unit (bars of different currencies on one
    axis would compare the incomparable), with at least two organizations and
    at least one non-zero value.
    """
    rows = comparison.get("rows") or []
    if not comparison.get("comparable") or len(rows) < 2 or not any(r["value"] for r in rows):
        return None
    unit = comparison.get("unit")
    currency = comparison.get("currency")
    series_name = comparison["label"] + (" (%)" if unit == PERCENT else f" ({currency})" if currency else "")
    return {
        "type": "chart",
        "chart_type": "hbar",
        "title": comparison["label"],
        "caption": comparison.get("period") or None,
        "categories": [r["organization"] for r in rows],
        "series": [{"name": series_name, "data": [r["value"] for r in rows]}],
        "x_label": series_name,
    }


def comparison_prompt(comparison: Dict[str, Any]) -> str:
    """The computed table in the form the summary call reads. Authoritative."""
    period = f", {comparison['period']}" if comparison.get("period") else ""
    unit = comparison.get("unit")
    lines = [
        f"Computed comparison of {comparison['label']}{period} (authoritative; do not recompute). "
        "State this period in the answer."
    ]
    for r in comparison["rows"]:
        share = f", {r['share_pct']}% of total" if "share_pct" in r else ""
        prefix = f"{r['rank']}. " if r.get("rank") else "- "
        extra = "".join(
            f", {label.lower()} {_fmt(r.get(key), CURRENCY, r.get('extra_currency'))}"
            for key, label in comparison.get("extra_fields") or []
        )
        lines.append(f"{prefix}{r['organization']}: {_fmt(r['value'], unit, r.get('currency'))}{share}{extra}")
    if comparison.get("total") is not None:
        lines.append(f"Total: {comparison['total']:,.2f} {comparison.get('currency') or ''}".rstrip())
    if not comparison.get("comparable"):
        lines.append("The figures are in different or unknown currencies: do not add or rank them as one unit.")
    for m in comparison["missing"]:
        lines.append(f"Missing: {m['organization']} ({m['reason']})")
    return "\n".join(lines)


# ── Several metrics ──────────────────────────────────────────────────────────

_ASCENDING = re.compile(r"\b(?:lowest|smallest|least|bottom|worst)\s+(?:first|to\s+(?:highest|largest))\b|\bascending\b",
                        re.IGNORECASE)
_DESCENDING = re.compile(r"\b(?:highest|largest|biggest|most|best|top)\s+(?:first|to\s+(?:lowest|smallest))\b"
                         r"|\bdescending\b", re.IGNORECASE)


def sort_ascending(question: str) -> bool:
    """Lowest first? An explicit "lowest first" wins over a "most" elsewhere in the question.

    "Which entity burns the most cash? Rank by expenses, lowest first" was
    sorted highest first because of "most".
    """
    if _ASCENDING.search(question or ""):
        return True
    if _DESCENDING.search(question or ""):
        return False
    return extract_direction_from_text(question)


_RANK_BY = re.compile(r"\b(?:rank(?:ed|ing)?|sort(?:ed)?|order(?:ed)?|arrange[d]?)\b[^.?!]{0,30}?\bby\s+([^.?!,;]{1,50})",
                      re.IGNORECASE)


def ranking_metric(metrics: List[Metric], question: str) -> str:
    """The metric to order rows by: the one after "rank/sort by", else the first one asked for.

    "Which entity burns the most cash? Rank by expenses, lowest first" was
    ordered by cash, the first figure mentioned.
    """
    keys = [m.key for m in metrics]
    m = _RANK_BY.search(question or "")
    if m:
        named = [x.key for x in _raw_matches(m.group(1))]
        growth = [x.key for x in _growth_matches(m.group(1))]
        for key in growth + named:
            if key in keys:
                return key
    return keys[0]


def build_multi_comparison(
    metrics: List[Metric],
    runs: List[Any],
    question: str,
    periods: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    """One row per organization, one column per metric, ordered by the first metric.

    `periods` maps metric key to its selection's query params. A cell with no
    figure is None; an org with no figure at all is listed under `missing`.
    """
    ascending = sort_ascending(question)
    columns = []
    for metric in metrics:
        cur = {_value_currency(metric, run) for run in runs if metric_value(metric, run)[0] is not None}
        comparable = metric.unit in (COUNT, PERCENT) or (len(cur) == 1 and "" not in cur)
        columns.append({
            "metric": metric.key,
            "label": metric.label,
            "unit": metric.unit,
            "currency": next(iter(cur)) if metric.unit == CURRENCY and comparable and cur else None,
            "comparable": comparable,
            "period": period_label(periods.get(metric.key)),
        })

    rows: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    for run in runs:
        found = {m.key: metric_value(m, run) for m in metrics}
        values = {k: v for k, (v, _reason) in found.items()}
        if all(v is None for v in values.values()):
            reasons = {reason for _v, reason in found.values()}
            missing.append({"organization": run.name, "organization_id": run.org_id,
                            "reason": reasons.pop() if len(reasons) == 1 else "no data"})
            continue
        rows.append({
            "organization": run.name,
            "organization_id": run.org_id,
            "values": {k: (round(v, 2) if v is not None else None) for k, v in values.items()},
            "currencies": {m.key: _value_currency(m, run) for m in metrics},
        })

    lead = ranking_metric(metrics, question)
    lead_column = next(c for c in columns if c["metric"] == lead)
    if lead_column["comparable"]:
        sign = 1 if ascending else -1
        rows.sort(key=lambda r: (r["values"][lead] is None, sign * (r["values"][lead] or 0)))
    period_set = {c["period"] for c in columns if c["period"]}
    return {
        "kind": "multi_metric",
        "lead": lead,
        "metrics": [m.key for m in metrics],
        "label": ", ".join(m.label for m in metrics),
        "columns": columns,
        "period": " / ".join(sorted(period_set)) if period_set else "",
        "order": "ascending" if ascending else "descending",
        "rows": rows,
        "missing": missing,
    }


def multi_block(comparison: Dict[str, Any]) -> Dict[str, Any]:
    columns = [{"key": "organization", "label": "Organization", "align": "left"}]
    for c in comparison["columns"]:
        suffix = " (%)" if c["unit"] == PERCENT else f" ({c['currency']})" if c["currency"] else ""
        columns.append({"key": c["metric"], "label": c["label"] + suffix, "align": "right"})
    rows = [{"organization": r["organization"], **r["values"]} for r in comparison["rows"]]
    title = "Comparison" + (f", {comparison['period']}" if comparison.get("period") else "")
    return {"type": "table", "title": title, "period": title, "columns": columns, "rows": rows,
            "total_rows": len(rows), "truncated": False}


def multi_chart(comparison: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Grouped bars (one group per org, one bar per metric) when every metric shares one unit."""
    cols = comparison["columns"]
    units = {(c["unit"], c["currency"]) for c in cols}
    rows = comparison["rows"]
    if len(units) != 1 or not all(c["comparable"] for c in cols) or len(rows) < 1:
        return None
    unit, currency = next(iter(units))
    series = [{"name": c["label"], "data": [r["values"].get(c["metric"]) or 0 for r in rows]} for c in cols]
    if not any(any(s["data"]) for s in series):
        return None
    return {
        "type": "chart",
        "chart_type": "bar",
        "title": comparison["label"] + (" (%)" if unit == PERCENT else f" ({currency})" if currency else ""),
        "caption": comparison.get("period") or None,
        "categories": [r["organization"] for r in rows],
        "series": series,
    }


def multi_prompt(comparison: Dict[str, Any]) -> str:
    lines = [f"Computed comparison of {comparison['label']} (authoritative; do not recompute). "
             "State the period(s) in the answer."]
    for c in comparison["columns"]:
        if c["period"]:
            lines.append(f"{c['label']}: {c['period']}")
        if not c["comparable"]:
            lines.append(f"{c['label']}: organizations report in different currencies; do not rank or add them.")
    for r in comparison["rows"]:
        cells = "; ".join(
            f"{c['label']} {_fmt(r['values'].get(c['metric']), c['unit'], r['currencies'].get(c['metric']))}"
            for c in comparison["columns"]
        )
        lines.append(f"- {r['organization']}: {cells}")
    for m in comparison["missing"]:
        lines.append(f"Missing: {m['organization']} ({m['reason']})")
    return "\n".join(lines)


# ── One metric over time ─────────────────────────────────────────────────────

def build_series(
    metric: Metric,
    grain: str,
    runs: List[Any],
    period: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Periods x organizations for one metric, with a total per organization."""
    per_org: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []
    period_order: List[str] = []
    key = series_key(metric, grain)
    for run in runs:
        payload = None
        if run.answered:
            payloads = (run.result or {}).get("payloads") or {}
            if key in payloads:
                payload = payloads[key]  # one of several series fetched per org
            elif len(payloads) == 1:
                payload = next(iter(payloads.values()))
            elif not payloads:
                payload = next(
                    (r for r in (run.result or {}).get("results") or [] if isinstance(r, dict) and "series" in r), None)
        points = (payload or {}).get("series") if isinstance(payload, dict) else None
        if not points:
            missing.append({"organization": run.name, "organization_id": run.org_id,
                            "reason": "could not be retrieved" if not run.answered else "no data"})
            continue
        values = {p["period"]: p["value"] for p in points}
        for p in points:
            if p["period"] not in period_order:
                period_order.append(p["period"])
        per_org.append({"organization": run.name, "organization_id": run.org_id, "currency": run.currency or "",
                        "values": values, "total": round(sum(values.values()), 2)})
    currencies = {o["currency"] for o in per_org}
    comparable = len(currencies) == 1 and "" not in currencies
    return {
        "kind": "series",
        "metric": metric.key,
        "label": metric.label,
        "grain": grain,
        "period": period_label(period),
        "periods": period_order,
        "comparable": comparable,
        "currency": next(iter(currencies)) if comparable and per_org else None,
        "organizations": per_org,
        "missing": missing,
    }


def build_series_set(
    metrics: List[Metric],
    grain: str,
    runs: List[Any],
    period: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Several figures over time ("P&L by month"): one series per figure."""
    return {"kind": "series_set", "grain": grain, "period": period_label(period),
            "items": [build_series(m, grain, runs, period) for m in metrics]}


def series_block(series: Dict[str, Any]) -> Dict[str, Any]:
    orgs = series["organizations"]
    columns = [{"key": "period", "label": series["grain"].capitalize(), "align": "left"}]
    columns += [{"key": f"org_{o['organization_id']}", "label": o["organization"], "align": "right"} for o in orgs]
    rows = [{"period": p, **{f"org_{o['organization_id']}": o["values"].get(p) for o in orgs}} for p in series["periods"]]
    rows.append({"period": "Total", **{f"org_{o['organization_id']}": o["total"] for o in orgs}})
    cur = f" ({series['currency']})" if series.get("currency") else ""
    title = f"{series['label']} by {series['grain']}{cur}" + (f", {series['period']}" if series.get("period") else "")
    return {"type": "table", "title": title, "period": title, "columns": columns, "rows": rows,
            "total_rows": len(rows), "truncated": False}


def series_chart(series: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """One line per organization, only when every organization reports in one currency."""
    orgs = series["organizations"]
    if not series.get("comparable") or not orgs or len(series["periods"]) < 2:
        return None
    cur = f" ({series['currency']})" if series.get("currency") else ""
    return {
        "type": "chart",
        "chart_type": "line",
        "title": f"{series['label']} by {series['grain']}{cur}",
        "caption": series.get("period") or None,
        "categories": series["periods"],
        "series": [{"name": o["organization"], "data": [o["values"].get(p) or 0 for p in series["periods"]]} for o in orgs],
    }


def series_prompt(series: Dict[str, Any]) -> str:
    lines = [f"Computed {series['label'].lower()} by {series['grain']}, {series.get('period') or ''} "
             "(authoritative; do not recompute). State the period in the answer."]
    if not series.get("comparable"):
        lines.append("Organizations report in different currencies: do not add or rank them as one unit.")
    for o in series["organizations"]:
        points = ", ".join(f"{p} {o['values'].get(p, 0):,.0f}" for p in series["periods"])
        best = max(series["periods"], key=lambda p: o["values"].get(p, 0)) if series["periods"] else ""
        lines.append(f"- {o['organization']} ({o['currency']}): total {o['total']:,.2f}; best {series['grain']} {best}; {points}")
    for m in series["missing"]:
        lines.append(f"Missing: {m['organization']} ({m['reason']})")
    return "\n".join(lines)


# ── The written answer ───────────────────────────────────────────────────────
# Every statement is derived from the computed comparison, so the answer can
# never disagree with the table under it. A model-written summary used to
# misstate gaps, leaders, "no data" orgs and negative counts, and once named an
# organization that was not selected.

#: Names listed in one sentence before the rest are counted instead.
_MAX_NAMES = 5


def _names(items: List[str]) -> str:
    if len(items) <= _MAX_NAMES:
        return ", ".join(items)
    return ", ".join(items[:_MAX_NAMES]) + f" and {len(items) - _MAX_NAMES} more"


def _diff(value: float, unit: str, currency: Optional[str]) -> str:
    if unit == PERCENT:
        return f"{value:,.2f} percentage points"
    return _fmt(value, unit, currency)


def _missing_lines(missing: List[Dict[str, Any]]) -> List[str]:
    """One line per reason, e.g. "Nothing recorded: A, B"."""
    by_reason: Dict[str, List[str]] = {}
    for m in missing:
        by_reason.setdefault(m["reason"], []).append(m["organization"])
    return [f"{reason[0].upper()}{reason[1:]}: {_names(orgs)}." for reason, orgs in by_reason.items()]


def _heading(label: str, period: str, compared: int) -> str:
    return f"**{label}**" + (f", {period}" if period else "") + f" · {compared} organization{'s' if compared != 1 else ''}"


def _ranked(entries: List[Tuple[str, float]], unit: str, currency: Optional[str], ascending: bool) -> List[str]:
    """Highest, lowest, gaps and negatives for (organization, value) pairs in one unit."""
    ordered = sorted(entries, key=lambda e: e[1], reverse=True)
    (top, top_v), (low, low_v) = ordered[0], ordered[-1]
    lines = [f"Highest: {top}, {_fmt(top_v, unit, currency)}."]
    if len(ordered) > 1:
        lines.append(f"Lowest: {low}, {_fmt(low_v, unit, currency)}.")
        if ascending:
            lines.reverse()
    if len(ordered) > 2:
        second, second_v = ordered[1]
        lines.append(f"Gap between the highest and the second ({second}): {_diff(top_v - second_v, unit, currency)}.")
    if len(ordered) > 1:
        lines.append(f"Gap between the highest and the lowest: {_diff(top_v - low_v, unit, currency)}.")
    negative = [name for name, v in ordered if v < 0]
    if negative:
        lines.append(f"Negative for {len(negative)} of {len(ordered)}: {_names(negative)}.")
    return lines


def _metric_answer(c: Dict[str, Any]) -> List[str]:
    rows, unit = c["rows"], c["unit"]
    lines: List[str] = []
    if rows and c.get("comparable"):
        currency = c.get("currency")
        lines += _ranked([(r["organization"], r["value"]) for r in rows], unit, currency, c.get("order") == "ascending")
        top = max(rows, key=lambda r: r["value"])
        if "share_pct" in top:
            after = next(i for i, line in enumerate(lines) if line.startswith("Highest")) + 1
            lines.insert(after, f"{top['organization']} holds {top['share_pct']}% of the combined total.")
        if c.get("total") is not None:
            scope = f" of all {c['shown']['of']} organizations" if c.get("shown") else ""
            lines.append(f"Combined total{scope}: {_fmt(c['total'], unit, currency)}.")
    elif rows:
        unknown = [r["organization"] for r in rows if not r.get("currency")]
        currencies = sorted({r["currency"] for r in rows if r.get("currency")})
        if unknown:
            lines.append(f"The currency is unknown for {_names(unknown)}, so the figures are not ranked "
                         "or added. Each figure is in the table.")
        else:
            lines.append(f"The figures are in different currencies ({', '.join(currencies)}), "
                         "so they are not ranked or added. Each figure is in the table.")
    else:
        lines.append(f"No organization has a {c['label'].lower()} figure for this period.")
    return lines + _missing_lines(c["missing"])


def _multi_answer(c: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    lead = next((col for col in c["columns"] if col["metric"] == c.get("lead")), None)
    if lead is not None and lead["comparable"]:
        lines.append(f"Ordered by {lead['label'].lower()}, "
                     f"{'lowest' if c.get('order') == 'ascending' else 'highest'} first.")
    for col in c["columns"]:
        entries = [(r["organization"], r["values"][col["metric"]]) for r in c["rows"]
                   if r["values"].get(col["metric"]) is not None]
        absent = [r["organization"] for r in c["rows"] if r["values"].get(col["metric"]) is None]
        if not entries:
            lines.append(f"{col['label']}: no figures.")
            continue
        if col["comparable"]:
            facts = _ranked(entries, col["unit"], col["currency"], c.get("order") == "ascending")
            ranked = [f for f in facts if f.startswith(("Highest", "Lowest", "Negative"))]
            lines.append(f"{col['label']}: " + " ".join(ranked))
        else:
            lines.append(f"{col['label']}: in different currencies, not ranked.")
        if absent:
            lines.append(f"{col['label']} not available for: {_names(absent)}.")
    signs = _warning_signs(c)
    if signs:
        lines.append("Warning signs: " + "; ".join(signs) + ".")
    return lines + _missing_lines(c["missing"])


#: Figures that point to trouble when below zero, and how to say so.
_WARNINGS = (("net_profit", "net loss"), ("cash_balance", "negative cash"),
             ("total_equity", "negative equity"), ("revenue_growth", "revenue down"))


def _warning_signs(c: Dict[str, Any]) -> List[str]:
    """Each organization's negative profit, cash, equity or revenue growth, from the table."""
    shown = set(c.get("metrics") or [])
    signs = []
    for r in c["rows"]:
        found = [f"{text} {abs(r['values'][key]):,.2f}%" if key == "revenue_growth" else text
                 for key, text in _WARNINGS
                 if key in shown and r["values"].get(key) is not None and r["values"][key] < 0]
        if found:
            signs.append(f"{r['organization']} ({', '.join(found)})")
    return signs


def _series_answer(c: Dict[str, Any]) -> List[str]:
    orgs = c["organizations"]
    lines: List[str] = []
    if orgs and c.get("comparable"):
        totals = [(o["organization"], o["total"]) for o in orgs]
        facts = _ranked(totals, CURRENCY, c.get("currency"), False)
        lines += [f.replace("Highest:", "Highest total:").replace("Lowest:", "Lowest total:") for f in facts
                  if f.startswith(("Highest", "Lowest", "Negative"))]
    elif orgs:
        lines.append("Organizations report in different currencies, so their totals are not ranked.")
    for o in orgs[:10]:
        if c["periods"]:
            best = max(c["periods"], key=lambda p: o["values"].get(p, 0))
            lines.append(f"{o['organization']}: best {c['grain']} {best} "
                         f"({_fmt(o['values'].get(best, 0), CURRENCY, o['currency'])}).")
    return lines + _missing_lines(c["missing"])


def limit_rows(comparison: Dict[str, Any], question: str) -> Dict[str, Any]:
    """Keep only the top or bottom N organizations the question asks for.

    "Rank top 5 organizations by revenue" and "now only show the top 2" used
    to show every organization. Totals and shares keep covering all of them.
    Figures that cannot be ranked (mixed currencies) are never cut.
    """
    from gemini_brain.orchestrator.multi_org_followup import LIMIT

    m = LIMIT.search(question or "")
    if not m:
        return comparison
    n, bottom = int(m.group(2)), m.group(1).lower() in ("bottom", "lowest", "worst", "smallest")
    kind = comparison.get("kind", "metric")
    if kind == "series_set":
        return {**comparison, "items": [limit_rows(item, question) for item in comparison["items"]]}
    if kind == "series":
        field_name, value = "organizations", (lambda r: r["total"])
        rankable = comparison.get("comparable")
    elif kind == "multi_metric":
        lead = comparison.get("lead") or comparison["metrics"][0]
        field_name, value = "rows", (lambda r: r["values"].get(lead))
        rankable = next(c for c in comparison["columns"] if c["metric"] == lead)["comparable"]
    else:
        field_name, value = "rows", (lambda r: r["value"])
        rankable = comparison.get("comparable")
    items = comparison[field_name]
    if not rankable or n < 1 or n >= len(items):
        return comparison
    ranked = sorted((r for r in items if value(r) is not None), key=value, reverse=True)
    chosen = {id(r) for r in (ranked[-n:] if bottom else ranked[:n])}
    limited = dict(comparison)
    limited[field_name] = [r for r in items if id(r) in chosen]
    limited["shown"] = {"end": "bottom" if bottom else "top", "n": n, "of": len(items)}
    return limited


def computed_answer(comparison: Dict[str, Any]) -> str:
    """The answer for a computed comparison, written entirely from its figures."""
    kind = comparison.get("kind", "metric")
    if kind == "series_set":
        items = comparison["items"]
        compared = max((len(i["organizations"]) + len(i["missing"]) for i in items), default=0)
        label = ", ".join(i["label"] for i in items) + f" by {comparison['grain']}"
        parts = [_heading(label, comparison.get("period") or "", compared)]
        for item in items:
            parts.append(f"**{item['label']}**\n" + "\n".join(f"- {line}" for line in _series_answer(item)))
        parts += [f"- {note}" for note in comparison.get("condition_notes") or []]
        return "\n\n".join(parts)
    if kind == "series":
        compared = len(comparison["organizations"]) + len(comparison["missing"])
        label = f"{comparison['label']} by {comparison['grain']}"
        body = _series_answer(comparison)
    else:
        compared = len(comparison["rows"]) + len(comparison["missing"])
        label = comparison["label"]
        body = _multi_answer(comparison) if kind == "multi_metric" else _metric_answer(comparison)
    heading = _heading(label, comparison.get("period") or "", compared)
    condition = comparison.get("condition")
    if condition:
        # See multi_org_conditions.apply_conditions: rows are the orgs that meet it.
        text = condition["text"][0].lower() + condition["text"][1:]
        heading = (heading.rsplit(" · ", 1)[0]
                   + f" · {condition['matched']} of {condition['of']} organizations with {text}")
        if not comparison["rows"]:
            body = [f"No organization has {text}."] + _missing_lines(comparison["missing"])
    shown = comparison.get("shown")
    if shown:
        heading = (heading if condition else heading.rsplit(" · ", 1)[0]) + \
            f" · {shown['end']} {shown['n']} of {shown['of']}" + ("" if condition else " organizations")
    body = list(body) + list(comparison.get("condition_notes") or [])
    return heading + "\n\n" + "\n".join(f"- {line}" for line in body)


# ── Dispatch ─────────────────────────────────────────────────────────────────

def computed_view(comparison: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], str, List[Dict[str, Any]]]:
    """(blocks, summary prompt, result rows) for any computed comparison."""
    kind = comparison.get("kind", "metric")
    if kind == "series_set":
        # A table and a line chart per figure, in statement order.
        blocks: List[Dict[str, Any]] = []
        prompts: List[str] = []
        rows: List[Dict[str, Any]] = []
        for item in comparison["items"]:
            item_blocks, item_prompt, item_rows = computed_view(item)
            blocks += item_blocks
            prompts.append(item_prompt)
            rows += [{"metric": item["metric"], **r} for r in item_rows]
        return blocks, "\n\n".join(prompts), rows
    if kind == "series":
        blocks = [series_block(comparison)]
        chart = series_chart(comparison)
        rows = [{"organization": o["organization"], "organization_id": o["organization_id"],
                 "total": o["total"], **{p: o["values"].get(p) for p in comparison["periods"]}}
                for o in comparison["organizations"]]
        return blocks + ([chart] if chart else []), series_prompt(comparison), rows
    if kind == "multi_metric":
        blocks = [multi_block(comparison)]
        chart = multi_chart(comparison)
        rows = [{"organization": r["organization"], "organization_id": r["organization_id"], **r["values"]}
                for r in comparison["rows"]]
        return blocks + ([chart] if chart else []), multi_prompt(comparison), rows
    blocks = [comparison_block(comparison)]
    chart = comparison_chart(comparison)
    return blocks + ([chart] if chart else []), comparison_prompt(comparison), [dict(r) for r in comparison["rows"]]


def selection_key(selection: Dict[str, Any]) -> str:
    """The payload key a fetched selection is stored under."""
    return selection.get("key") or selection.get("endpoint") or json.dumps(selection, sort_keys=True, default=str)
