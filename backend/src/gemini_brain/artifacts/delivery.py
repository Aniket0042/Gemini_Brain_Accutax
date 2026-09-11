"""Detect whether the user asked for a chart, a downloadable file, or both.

Orthogonal to the 7-type data intent classifier: routing still picks the
data source; this only decides how the retrieved payload is presented.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

FILE_PDF = re.compile(r"\b(pdf|as a pdf)\b", re.IGNORECASE)
FILE_DOCX = re.compile(
    r"\b(docx|word\s+doc(?:ument)?|(?:^|\s)docs?(?:\s|$)|as a word)\b",
    re.IGNORECASE,
)
FILE_PPTX = re.compile(
    r"\b(pptx?|powerpoint|slides?|presentation)\b",
    re.IGNORECASE,
)
FILE_XLSX = re.compile(
    r"\b(xlsx|xls\b|excel|spreadsheet|workbook)\b",
    re.IGNORECASE,
)
FILE_CSV = re.compile(r"\bcsv\b", re.IGNORECASE)
EXPORT_GENERIC = re.compile(
    r"\b(export|download|send me|give me (?:a |the )?file|as a file)\b",
    re.IGNORECASE,
)

CHART_OF_ACCOUNTS = re.compile(r"\bchart of accounts\b", re.IGNORECASE)
CHART_WORDS = re.compile(
    r"\b(charts?|graphs?|plotted|plots?|visuali[sz]e|visuali[sz]ation|"
    r"bar charts?|pie charts?|line charts?|area charts?|trend(?:line)?s?)\b",
    re.IGNORECASE,
)
BAR_HINT = re.compile(r"\bbar(?:\s+chart)?\b", re.IGNORECASE)
LINE_HINT = re.compile(r"\b(?:line(?:\s+chart)?|trendlines?)\b", re.IGNORECASE)
AREA_HINT = re.compile(r"\barea(?:\s+chart)?\b", re.IGNORECASE)
PIE_HINT = re.compile(r"\bpie(?:\s+chart)?\b", re.IGNORECASE)

FILE_PRIORITY = (
    ("csv", FILE_CSV),
    ("xlsx", FILE_XLSX),
    ("pptx", FILE_PPTX),
    ("pdf", FILE_PDF),
    ("docx", FILE_DOCX),
)


@dataclass(frozen=True)
class Delivery:
    """Presentation request parsed from the user query."""

    mode: str  # none | chart | file | both
    format: Optional[str] = None  # pdf | docx | pptx | xlsx | csv
    chart_hint: Optional[str] = None  # bar | line | area | pie

    @property
    def wants_chart(self) -> bool:
        return self.mode in ("chart", "both")

    @property
    def wants_file(self) -> bool:
        return self.mode in ("file", "both")


def _chart_hint(text: str) -> Optional[str]:
    if PIE_HINT.search(text):
        return "pie"
    if BAR_HINT.search(text):
        return "bar"
    if AREA_HINT.search(text):
        return "area"
    if LINE_HINT.search(text):
        return "line"
    return None


def _file_format(text: str) -> Optional[str]:
    for name, pattern in FILE_PRIORITY:
        if pattern.search(text):
            return name
    if EXPORT_GENERIC.search(text):
        return "xlsx"
    return None


def detect_delivery(query: str) -> Delivery:
    """Return the delivery mode for `query`. Fast, no LLM."""
    raw = (query or "").strip()
    if not raw:
        return Delivery(mode="none")

    # "chart of accounts" is an accounting report, not a visualisation.
    stripped = CHART_OF_ACCOUNTS.sub(" ", raw)
    file_fmt = _file_format(raw)
    wants_chart = bool(CHART_WORDS.search(stripped))
    hint = _chart_hint(stripped) if wants_chart else None

    wants_report = _wants_financial_report(stripped)

    # A P&L/balance-sheet *file* export can still include a chart in the
    # artifact. A plain "show me the P&L statement" is not a graph request —
    # treating it as one made the model invent a "User: can you provide the
    # graph?" follow-up and render a chart nobody asked for.
    if file_fmt and (wants_chart or wants_report):
        return Delivery(mode="both", format=file_fmt, chart_hint=hint or ("bar" if wants_report else None))
    if file_fmt:
        return Delivery(mode="file", format=file_fmt, chart_hint=hint)
    if wants_chart:
        return Delivery(mode="chart", chart_hint=hint)
    return Delivery(mode="none")


def _wants_financial_report(text: str) -> bool:
    has_report = bool(re.search(r"\b(reports?|statements?)\b", text, re.IGNORECASE))
    has_fin = bool(re.search(
        r"\b(profit\s*(?:and|&)\s*loss|p\s*&\s*l|p&l|income statement|"
        r"balance sheet|cash[- ]flow)\b",
        text,
        re.IGNORECASE,
    ))
    return has_report and has_fin
