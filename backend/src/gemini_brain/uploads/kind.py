"""What kind of document a file is, guessed from its words, for files read without a model.

Word, Excel and CSV files, and PDFs with a text layer that are not bills, are read as text, so no model says what they
are. The chat still wants to offer fitting questions, so the kind is guessed here from the file name and its text: a
contract, a budget, a statement of account, or "other". A guess only picks which suggested questions are offered; no
figure and no answer depends on it.
"""
from __future__ import annotations

import re

CONTRACT_WORDS = ("agreement", "payment terms", "governing law", "the parties", "hereinafter", "terms and conditions",
                  "indemnif", "termination", "confidential", "in witness", "signed for", "liability", "late payment fee")
BUDGET_LABELS = ("revenue", "sales", "cost of sales", "cost of goods", "salaries", "payroll", "rent", "expenses", "marketing",
                 "utilities", "gross profit", "net profit", "operating costs", "capex")
PERIOD = re.compile(r"\b(q[1-4]|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|"
                    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|fy ?\d{2,4}|20\d\d)\b", re.I)


def _distinct(words, text: str) -> int:
    return sum(1 for w in words if w in text)


def guess_type(text: str, name: str = "", kind: str = "") -> str:
    """'budget', 'statement', 'contract' or 'other'."""
    low = (text or "").lower()
    label = (name or "").lower()
    sheet = kind in ("xlsx", "csv")
    if re.search(r"statement of account|account statement", low) or ("opening balance" in low and "closing balance" in low):
        return "statement"
    if re.search(r"budget|forecast|projection", label) or (sheet and (
            re.search(r"\bbudget\b|\bforecast\b", low) or
            (_distinct(BUDGET_LABELS, low) >= 2 and len(PERIOD.findall(low)) >= 2))):
        return "budget"
    if _distinct(CONTRACT_WORDS, low) >= 2:
        return "contract"
    if not sheet and re.search(r"\bbudget\b", low) and len(PERIOD.findall(low)) >= 2 and _distinct(BUDGET_LABELS, low) >= 2:
        return "budget"
    return "other"
