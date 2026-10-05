"""Query expansion (R6): add the wording UAE tax legislation uses for common everyday terms.

The expanded text is used for searching only; the answer model sees the user's question as typed.
"""
from __future__ import annotations

import re

LEGAL_TERMS: list[tuple[str, str]] = [
    (r"\b(usdt|usdc|bitcoin|btc|ethereum|eth|crypto\w*|stablecoins?)\b", "digital currency"),
    (r"\bsmart ?phones?\b", "smart phones electronic devices"),
    (r"\b(late payment|paid late|pay late|overdue|delay(ed)? in pay\w*)\b",
     "failure to settle the Payable Tax administrative penalty"),
    (r"\bpenalt(y|ies)\b", "administrative penalties"),
    (r"\bfree ?zones?\b", "designated zone"),
    # Goods leaving a designated zone for the mainland: the 2021 rule (VATP027) on supplies consumed
    # outside the zone, customs import evidence and VAT paid on importation.
    (r"\b(designated|free) ?zones?\b.*\b(mainland|import\w*|ship\w*|deliver\w*|mov\w*|transport\w*)\b"
     r"|\b(mainland|import\w*|ship\w*|deliver\w*)\b.*\b(designated|free) ?zones?\b",
     "goods supplied in a designated zone consumed outside the designated zone importation customs evidence VAT paid outside the scope"),
    (r"\b(bought|buy|buying|acquired|acquire|sold|sell|selling)\s+(an|the|a|our|my)\s+(entire\s+|whole\s+)?business\b",
     "transfer of a business as a going concern"),
    (r"\b(re-?invoic\w*|recharg\w*|on-?charg\w*)\b", "disbursement reimbursement"),
    (r"\b(gold|jewel\w*|diamonds?)\b", "precious metals precious stones"),
    (r"\bscrap\b", "metal scrap"),
    (r"\be-?invoic\w*\b", "Electronic Invoicing System"),
    (r"\b(director'?s?\s+fees?|board member)\b", "Director Board of Directors natural person"),
    (r"\bTRN\b", "Tax Registration Number"),
    (r"\b(wrong|incorrect|exempt|zero-rated|standard-rated)\s+box\b|\b(mistake|error|errors|misreport\w*)\b.{0,40}\breturn\b",
     "error or omission in the Tax Return correction voluntary disclosure"),
    (r"\b(vat|tax)\s+group\b", "Tax Group"),
    # Supplier due diligence: FTA Decision No. 13 of 2026 (bank account check above AED 375,000,
    # exception below AED 10,000 unless the supplier exceeds AED 100,000).
    (r"\b(check\w*|verif\w*|due diligence|vet\w*)\b.{0,60}\bsuppliers?\b|\bsuppliers?\b.{0,60}\b(check\w*|verif\w*|due diligence)\b",
     "verification of the validity and integrity of the supplies before deduction of Input Tax; "
     "supplier bank account confirmation; exceptions Consideration less than AED 10,000"),
    (r"\b(flow|process|work|works|steps?|lifecycle)\b.*\be-?invoic|\be-?invoic\w*\b.*\b(flow|process|work|works|steps?|lifecycle)\b",
     "Issuer shall issue and transmit an Electronic Invoice to the Recipient; Recipient shall process; "
     "report to the Authority; Exchange and Reporting Obligation; Accredited Service Provider"),
    (r"\b(criteria|who must|mandatory|phases?|deadline)\b.*\be-?invoic|\be-?invoic\w*\b.*\b(criteria|who must|mandatory|phases?|deadline)\b",
     "implementation phases revenue threshold Accredited Service Provider"),
]
_COMPILED = [(re.compile(p, re.I), terms) for p, terms in LEGAL_TERMS]


def expand(question: str) -> str:
    extra = [terms for pattern, terms in _COMPILED if pattern.search(question)]
    return f"{question} ({'; '.join(extra)})" if extra else question
