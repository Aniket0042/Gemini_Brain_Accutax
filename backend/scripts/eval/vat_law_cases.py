"""UAE VAT law questions for the evaluation (docs/VAT_AGENT_COMPLETION_PLAN.md, package 3), and how answers are judged.

Each case has a question and the facts a right answer states (any one of them is enough; numbers are compared without
their thousands separators). Every fact is a rule of the UAE VAT law or the FTA's guidance that the knowledge base holds;
`vat_law_eval.py facts` checks, for free, that the knowledge base's own search finds each English case's fact before any
model is asked. The six Arabic cases ask the same things in Arabic: the answer must be in Arabic and still cite.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List


def case(cid: str, question: str, facts: List[str], arabic: bool = False, tools_any: List[str] = (),
         cite: bool = True) -> Dict[str, Any]:
    """`tools_any`: tools the answer must have used; `cite`: the answer must cite an FTA source."""
    return {"id": cid, "question": question, "facts": facts, "arabic": arabic, "tools_any": list(tools_any), "cite": cite}


NOT_RECOVERABLE = ["not recoverable", "non-recoverable", "cannot recover", "can't recover", "cannot be recovered", "blocked",
                   "not be recovered", "no input tax", "cannot claim", "can't claim", "not deductible"]

CASES: List[Dict[str, Any]] = [
    case("rate", "What is the standard VAT rate in the UAE?", ["5%", "5 %", "five percent"]),
    case("register_mandatory", "At what level of taxable supplies must a business register for VAT?", ["375,000"]),
    case("register_voluntary", "Can a business register for VAT voluntarily, and from what amount?", ["187,500"]),
    case("return_deadline", "When must a VAT return be filed and the tax paid?", ["28 days", "28th day", "28 calendar days"]),
    case("invoice_timing", "Within how many days of a supply must a tax invoice be issued?", ["14 days", "14 calendar days"]),
    case("simplified", "When can a simplified tax invoice be issued?", ["10,000"]),
    case("credit_note", "What must a tax credit note show?", ["tax credit note"]),
    case("records", "How long must VAT records be kept?", ["5 years", "five years"]),
    case("exports", "Is VAT charged on exports of goods outside the GCC?", ["zero"]),
    case("new_residential", "Is the first sale of a new residential building subject to VAT?", ["zero", "three years", "3 years"]),
    case("residential_rent", "Is the rent of a residential apartment subject to VAT?", ["exempt"]),
    case("bare_land", "Is the sale of bare land subject to VAT?", ["exempt"]),
    case("passenger_transport", "Is local passenger transport subject to VAT?", ["exempt"]),
    case("financial_services", "Are margin-based financial services such as loan interest subject to VAT?", ["exempt"]),
    case("healthcare", "Is VAT charged on preventive healthcare services?", ["zero"]),
    case("education", "Is VAT charged on school tuition fees?", ["zero"]),
    case("gold", "What VAT rate applies to investment-grade gold?", ["zero", "99%"]),
    case("entertainment", "Can we recover input VAT on entertaining our customers?", NOT_RECOVERABLE),
    case("vehicles", "Can we recover input VAT on a car that is available for an employee's personal use?", NOT_RECOVERABLE),
    case("imported_services", "Who pays VAT on services bought from a supplier outside the UAE?", ["reverse charge"]),
    case("imported_goods", "How is VAT accounted for on goods imported into the UAE?", ["reverse charge", "customs"]),
    case("tax_period", "What is the standard tax period for VAT returns?", ["three months", "3 months", "quarter"]),
    case("bad_debt", "When can a business claim bad debt relief for VAT?", ["six months", "6 months"]),
    case("designated_zones", "Is a supply of goods between two designated zones subject to VAT?",
         ["outside the scope", "not subject", "not a supply", "no vat"]),
    case("profit_margin", "How does the VAT profit margin scheme work for second-hand goods?", ["profit margin"]),
    case("tax_group", "Who can form a VAT tax group?", ["related", "control"]),
    case("voluntary_disclosure", "What should a business do if it finds an error in a VAT return it already filed?",
         ["voluntary disclosure"]),
    case("exchange_rate", "Which exchange rate must be used for a tax invoice in a foreign currency?", ["central bank"]),
    case("tourists", "Can tourists get a refund of VAT they paid in the UAE?", ["scheme", "refund"]),
    case("deemed_supply", "What is a deemed supply for VAT?", ["consideration"]),
    case("date_of_supply", "When is the date of supply for goods?", ["transfer", "delivery", "delivered", "made available"]),
    case("unpaid_invoice", "Can input VAT be recovered if the supplier's invoice has not been paid?", ["six months", "6 months"]),
    case("einvoicing_asp", "What is an Accredited Service Provider in UAE e-invoicing?", ["accredited service provider"]),
    case("mixed_supplies", "How does a business that makes both taxable and exempt supplies recover input VAT?",
         ["apportion"]),
    # the VAT return position and penalties (package 5): the tools do the dates and the arithmetic
    case("return_last_quarter", "How much VAT do we owe for the last quarter, and when is it due?",
         ["28 october 2026", "2026-10-28", "october 28", "28 oct"], tools_any=["vat_return"], cite=False),
    case("return_next_deadline", "When is our next VAT return deadline?",
         ["28 october 2026", "2026-10-28", "october 28", "28 oct"], tools_any=["vat_return"], cite=False),
    case("penalty_late_payment", "We paid AED 12,000 of VAT due on 28 July 2026 on 16 October 2026. What is the late payment penalty?",
         ["420"], tools_any=["vat_penalty"]),
    case("penalty_late_return", "What is the penalty for filing a VAT return late for the first time?", ["1,000"]),
    case("ar_rate", "ما هي نسبة ضريبة القيمة المضافة في الإمارات؟", ["5%", "5 %", "٥"], arabic=True),
    case("ar_register", "ما هو حد التسجيل الإلزامي لضريبة القيمة المضافة؟", ["375,000", "٣٧٥"], arabic=True),
    case("ar_return", "متى يجب تقديم الإقرار الضريبي ودفع الضريبة؟", ["28", "٢٨"], arabic=True),
    case("ar_invoice", "خلال كم يوماً يجب إصدار الفاتورة الضريبية؟", ["14", "١٤"], arabic=True),
    case("ar_exports", "هل تخضع صادرات السلع خارج دول الخليج لضريبة القيمة المضافة؟", ["صفر", "الصفرية", "0%", "zero"], arabic=True),
    case("ar_records", "ما هي المدة التي يجب الاحتفاظ فيها بالسجلات الضريبية؟", ["5", "٥", "خمس"], arabic=True),
]

_CITE = re.compile(r"\[\d{1,3}(?:\s*[,–-]\s*\d{1,3})*\]")
_ARABIC = re.compile(r"[؀-ۿ]")
_LATIN = re.compile(r"[A-Za-z]")


def _norm(text: str) -> str:
    """Lower case, no thousands separators, ordinary spaces."""
    text = (text or "").lower().replace(" ", " ").replace(" ", " ")
    return re.sub(r"(?<=\d),(?=\d{3})", "", text)


def states_a_fact(answer: str, facts: List[str]) -> bool:
    text = _norm(answer)
    return any(_norm(f) in text for f in facts)


def mostly_arabic(answer: str) -> bool:
    arabic, latin = len(_ARABIC.findall(answer or "")), len(_LATIN.findall(answer or ""))
    return arabic > 0 and arabic >= latin


def judge(case_: Dict[str, Any], answer: str, tools: List[str], blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    checks = {
        "states the rule": states_a_fact(answer, case_["facts"]),
        "did not review the books": "review_vat" not in tools,
    }
    if case_.get("cite", True):
        checks["cites a source"] = bool(_CITE.search(answer or ""))
        checks["lists the cited sources"] = any(b.get("type") == "fta_sources" and b.get("sources") for b in blocks or [])
    for t in case_.get("tools_any") or []:
        checks[f"used {t}"] = t in tools
    if case_["arabic"]:
        checks["answers in Arabic"] = mostly_arabic(answer)
    return {"ok": all(checks.values()), "checks": checks}
