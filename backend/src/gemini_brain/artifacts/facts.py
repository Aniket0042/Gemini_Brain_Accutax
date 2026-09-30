"""FactSheet: every figure a report may legitimately state, computed in code.

A narrative sentence may only quote a number that appears here. The sheet
holds the report's own values (KPIs, chart points, table cells) plus the
figures an analyst derives from them: totals, averages, extremes, shares of
total, cumulative top-N shares, period-over-period changes, and differences
and ratios between headline figures. Each fact carries an id and the ids of
its inputs, so a verified sentence can be traced back to the data.

`extract_figures` finds the numbers a sentence states (with the precision the
writer used), and `FactSheet.match` decides whether one of them is backed by
a fact, within that precision.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from decimal import Decimal
from itertools import combinations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gemini_brain.artifacts import theme
from gemini_brain.artifacts.insights import KPI_CHART_TITLE
from gemini_brain.artifacts.ir import ReportDocument, infer_unit, to_decimal

_HUNDRED = Decimal(100)
#: Row cap for per-row share facts; totals and top-N shares still use all rows.
_SHARE_ROW_CAP = 500
_TOP_N_CAP = 12


@dataclass(frozen=True)
class Fact:
    id: str
    value: Decimal
    unit: str  # money | count | percent | days | number
    inputs: Tuple[str, ...] = ()


#: Labels too generic to tie a figure to one subject ("other", "total", the
#: month "May", which is also a verb).
_GENERIC_SUBJECTS = frozenset({"other", "total", "amount", "value", "count", "net", "may", "item", "items"})


@dataclass
class FactSheet:
    facts: List[Fact] = field(default_factory=list)
    #: Names the report knows (KPIs, measures, categories, columns, rows),
    #: lower-cased. A sentence naming one ties its figures to that subject.
    subjects: List[str] = field(default_factory=list)
    #: The subset that are categories (customers, months, line items) and never
    #: also a measure — a sentence naming one is about that category's figures.
    category_subjects: set = field(default_factory=set)
    measure_subjects: set = field(default_factory=set)
    #: subject -> the strings that name it in a sentence (itself included).
    aliases: Dict[str, set] = field(default_factory=dict)
    #: The report ranks only the top N of a longer list, so no figure in it is
    #: a share of the whole business.
    partial: bool = False

    def __post_init__(self) -> None:
        self._index()

    def add_subject(self, label: Any, *, category: bool = False) -> None:
        text = re.sub(r"[_\s]+", " ", str(label or "")).strip().lower()
        if len(text) < 3 or text in _GENERIC_SUBJECTS or re.fullmatch(r"[\d.,\s%-]+", text):
            return
        self.subjects.append(text)
        (self.category_subjects if category else self.measure_subjects).add(text)
        aliases = self.aliases.setdefault(text, {text})
        aliases |= _time_aliases(text)
        # "Expense" is named by "expenses", "Customers" by "customer".
        if len(text) >= 4 and not _time_aliases(text):
            aliases.add(text[:-1] if text.endswith("s") else text + "s")
        # "Apex Retail Trading LLC" is named by "Apex Retail Trading".
        stripped = _LEGAL_SUFFIX.sub("", text).strip(" ,.")
        if category and stripped != text and len(stripped) >= 3:
            aliases.add(stripped)
        words = text.split()
        if not category and len(words) > 1:
            # "Net Profit" is named by "profit", "VAT Amount" by "VAT",
            # "Total Revenue" by "revenue".
            if len(words[-1]) >= 4 and words[-1] not in _GENERIC_SUBJECTS:
                aliases.add(words[-1])
            if words[0] in _ACRONYM_WORDS:
                aliases.add(words[0])
            if words[0] == "total":
                aliases.add(" ".join(words[1:]))

    def scope(self, sentence: str) -> Optional[set]:
        """Indices of the facts a sentence can be backed by, or None when it
        names no subject the report knows (then any fact may back it).

        A fact qualifies when:
          1. its id is about at least one subject the sentence names (by name
             or alias: "profit" names Net Profit, "March" names Mar 2026);
          2. every non-period category it is tied to — a customer, a line
             item — is named too (one of two for a leader/runner-up fact); its
             periods must include one the sentence names, unless the sentence
             names no period at all ("fell 10% over the period");
          3. if the sentence names a category, the fact involves one of them —
             an aggregate (a total, a top-3 share) cannot back "Payroll …".
        So Cost of Goods' 30% share of revenue cannot back "Payroll was 30% of
        revenue", while "income declined 10% over the period" still can.
        """
        text = f" {re.sub(r'[_\s]+', ' ', sentence.lower())} "
        if text in self._scopes:
            return self._scopes[text]  # None: names no subject; a set: the facts that may back it
        named = {sub for sub, aliases in self.aliases.items() if any(_mentioned(a, text) for a in aliases)}
        if not named:
            self._scopes[text] = None
            return None
        periods_named = any(_time_aliases(n) for n in named)
        named_categories = {n for n in named if n in self.category_subjects and n not in self.measure_subjects}

        def is_named(cat: str) -> bool:
            return cat in named or any(_mentioned(a, text) for a in (self.aliases.get(cat) or {cat} | _time_aliases(cat)))

        cached = set()
        for i, key in enumerate(self._keys):
            if not any(n in key for n in named):
                continue
            cats = self._categories[i]
            periods = [c for c in cats if _time_aliases(c)]
            others = [c for c in cats if c not in periods]
            others_named = [is_named(c) for c in others]
            if others and not (any(others_named) if "|" in key else all(others_named)):
                continue
            if periods and periods_named and not any(is_named(c) for c in periods):
                continue
            if named_categories and not any(is_named(c) for c in cats):
                continue
            cached.add(i)
        self._scopes[text] = cached
        return cached

    @property
    def change_ids(self) -> set:
        """Facts that are changes between periods (their id has "a->b")."""
        return {i for i, key in enumerate(self._keys) if "->" in key}

    def add(self, fact_id: str, value: Optional[Decimal], unit: str, inputs: Iterable[str] = ()) -> None:
        if value is None:
            return
        self.facts.append(Fact(fact_id, value, unit, tuple(inputs)))

    def _index(self) -> None:
        self._keys = [re.sub(r"[_\s]+", " ", f.id.lower()) for f in self.facts]
        self._categories = [_categories(key) for key in self._keys]
        self._scopes: Dict[str, Optional[set]] = {}
        self._pct: List[Tuple[float, int]] = []
        self._abs: List[Tuple[float, int]] = []
        for i, f in enumerate(self.facts):
            pool = self._pct if f.unit == "percent" else self._abs
            pool.append((float(f.value), i))
            if f.value < 0:
                pool.append((float(-f.value), i))  # "a decline of 12%" states 12
        self._pct.sort()
        self._abs.sort()

    def freeze(self) -> "FactSheet":
        self._index()
        return self

    def match(self, lo: Decimal, hi: Decimal, *, percent: bool, among: Optional[set] = None) -> Optional[Fact]:
        """A fact whose value lies in [lo, hi], from the percent or the absolute
        pool — restricted to `among` (fact indices) when given."""
        pool = self._pct if percent else self._abs
        i = bisect.bisect_left(pool, (float(lo), -1))
        while i < len(pool) and pool[i][0] <= float(hi):
            if among is None or pool[i][1] in among:
                return self.facts[pool[i][1]]
            i += 1
        return None

    def __len__(self) -> int:
        return len(self.facts)


def _mentioned(label: str, text: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(label)}(?![a-z0-9])", text) is not None


_ACRONYM_WORDS = frozenset({"vat", "gst", "tds", "cogs", "ebitda", "ar", "ap"})
#: Company-form suffixes a writer often leaves off a name.
_LEGAL_SUFFIX = re.compile(
    r"[\s,]+(?:l\.?l\.?c\.?|ltd\.?|limited|inc\.?|co\.?|corp\.?|plc|pjsc|psc|fze|fzco|fz[\s-]?llc|llp|gmbh|s\.?a\.?)$",
    re.IGNORECASE,
)
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august",
           "september", "october", "november", "december")


def _time_aliases(label: str) -> set:
    """Ways a sentence may name a period label: "mar 2026" is also "march",
    "march 2026", "mar"; "q1 2026" is also "q1". Empty for non-periods."""
    text = label.strip().lower()
    for i, month in enumerate(_MONTHS):
        m = re.fullmatch(rf"({month[:3]}[a-z]*)\.?(?:[\s'-]+(\d{{2,4}}))?", text)
        if m:
            year = m.group(2)
            names = {month, month[:3], "sept"} if i == 8 else {month, month[:3]}
            return names | ({f"{n} {year}" for n in names} if year else set())
    m = re.fullmatch(r"(q[1-4]|h[12])\s*[-']?\s*(\d{2,4})?", text)
    if m:
        return {m.group(1)} | ({f"{m.group(1)} {m.group(2)}"} if m.group(2) else set())
    if re.fullmatch(r"(19|20)\d{2}(-\d{1,2}(-\d{1,2})?)?", text):
        return {text}
    return set()


def _categories(key: str) -> List[str]:
    """Category names a fact id is tied to: bracketed parts, except measure
    names (kpi[...] headline figures and .m[...] pairs of measures)."""
    out: List[str] = []
    for m in re.finditer(r"(kpi|\.m)?\[([^\]]*)\]", key):
        if m.group(1):
            continue  # names measures, not categories
        out += [part.strip() for part in re.split(r"->|\||/|-(?=[a-z])", m.group(2)) if part.strip()]
    return [c for c in out if len(c) >= 2 and not re.fullmatch(r"[\d.,\s]+", c)]


# ── building the sheet ───────────────────────────────────────────────────────

def _pct(part: Decimal, whole: Decimal) -> Optional[Decimal]:
    return None if not whole else part / whole * _HUNDRED


def _add_distribution(sheet: FactSheet, prefix: str, labelled: Sequence[Tuple[str, Decimal]], unit: str) -> None:
    """Totals, extremes, average, shares and cumulative top-N shares of one measure."""
    vals = [v for _, v in labelled]
    if not vals:
        return
    ids = [f"{prefix}[{lbl}]" for lbl, _ in labelled]
    total = sum(vals, Decimal(0))
    sheet.add(f"{prefix}.total", total, unit, ids)
    sheet.add(f"{prefix}.max", max(vals), unit, ids)
    sheet.add(f"{prefix}.min", min(vals), unit, ids)
    sheet.add(f"{prefix}.average", total / len(vals), unit, ids)
    sheet.add(f"{prefix}.count", Decimal(len(vals)), "count", ids)
    if total > 0 and all(v >= 0 for v in vals):
        if len(labelled) <= _SHARE_ROW_CAP:
            for (lbl, v), fid in zip(labelled, ids):
                sheet.add(f"{prefix}[{lbl}].share", _pct(v, total), "percent", (fid, f"{prefix}.total"))
        # "Other" is a remainder, never a ranked item ("the top 3 make up …").
        ranked_items = sorted(((lbl, v) for lbl, v in labelled if lbl.strip().lower() != "other"),
                              key=lambda p: p[1], reverse=True)
        ranked = [v for _, v in ranked_items]
        running = Decimal(0)
        for k, v in enumerate(ranked[:_TOP_N_CAP], start=1):
            running += v
            sheet.add(f"{prefix}.top{k}", running, unit, (f"{prefix}.total",))
            sheet.add(f"{prefix}.top{k}.share", _pct(running, total), "percent", (f"{prefix}.total",))
            rest = total - running
            sheet.add(f"{prefix}.rest_after_top{k}", rest, unit, (f"{prefix}.total",))
            sheet.add(f"{prefix}.rest_after_top{k}.share", _pct(rest, total), "percent", (f"{prefix}.total",))
        if len(ranked) >= 2 and ranked[1]:
            # Both names in the id, so "A is ahead of B by …" is a fact about either.
            pair = f"[{ranked_items[0][0]}|{ranked_items[1][0]}]"
            sheet.add(f"{prefix}.lead_over_second{pair}", ranked[0] - ranked[1], unit)
            sheet.add(f"{prefix}.lead_over_second.pct{pair}", _pct(ranked[0] - ranked[1], ranked[1]), "percent")
            # "Four and a half times the next customer": the leader as a multiple of the runner-up.
            sheet.add(f"{prefix}.lead_ratio{pair}", _pct(ranked[0], ranked[1]), "percent")


def _add_changes(sheet: FactSheet, prefix: str, labelled: Sequence[Tuple[str, Optional[Decimal]]], unit: str) -> None:
    """Period-over-period and first-to-last change for a time series."""
    points = [(lbl, v) for lbl, v in labelled if v is not None]
    for (a_lbl, a), (b_lbl, b) in zip(points, points[1:]):
        fid = f"{prefix}[{a_lbl}->{b_lbl}]"
        sheet.add(f"{fid}.change", b - a, unit)
        sheet.add(f"{fid}.change_pct", _pct(b - a, abs(a)) if a else None, "percent")
        sheet.add(f"{fid}.ratio", _pct(b, a) if a else None, "percent")  # "doubled" = 200%
    if len(points) >= 3:
        (f_lbl, first), (l_lbl, last) = points[0], points[-1]
        sheet.add(f"{prefix}[{f_lbl}->{l_lbl}].change", last - first, unit)
        sheet.add(f"{prefix}[{f_lbl}->{l_lbl}].change_pct", _pct(last - first, abs(first)) if first else None, "percent")
        sheet.add(f"{prefix}[{f_lbl}->{l_lbl}].ratio", _pct(last, first) if first else None, "percent")


def _add_pairs(sheet: FactSheet, prefix: str, named: Sequence[Tuple[str, Decimal, str]], *, measures: bool = True) -> None:
    """Differences and ratios between headline figures of the same unit.

    `measures` marks the paired names as measures (ids "prefix.m[a-b]") rather
    than categories, so subject scoping does not treat "Net Sales" as a
    customer that must be named."""
    if measures and prefix != "kpi":
        prefix = f"{prefix}.m"
    for (a_name, a, unit_a), (b_name, b, unit_b) in combinations(named, 2):
        # "Average invoice of AED 1,265": a money figure per counted item.
        for (m_name, m, m_unit), (c_name, c, c_unit) in (((a_name, a, unit_a), (b_name, b, unit_b)),
                                                         ((b_name, b, unit_b), (a_name, a, unit_a))):
            if m_unit == "money" and c_unit == "count" and c:
                sheet.add(f"{prefix}[{m_name}/{c_name}]", m / c, "money",
                          (f"{prefix}[{m_name}]", f"{prefix}[{c_name}]"))
        if unit_a != unit_b or unit_a == "percent":
            continue
        ids = (f"{prefix}[{a_name}]", f"{prefix}[{b_name}]")
        sheet.add(f"{prefix}[{a_name}-{b_name}]", a - b, unit_a, ids)
        sheet.add(f"{prefix}[{a_name}/{b_name}]", _pct(a, b), "percent", ids)
        sheet.add(f"{prefix}[{b_name}/{a_name}]", _pct(b, a), "percent", ids)
        # Income + expenses (or their shares of that sum) is arithmetic without
        # meaning, so it can never back a sentence ("total flows of AED 2.51M").
        if not theme.is_opposing([a_name, b_name]):
            sheet.add(f"{prefix}[{a_name}+{b_name}]", a + b, unit_a, ids)


def _numeric_columns(rows: Sequence[Dict]) -> List[str]:
    if not rows:
        return []
    keys = [k for k in rows[0] if not (k == "id" or str(k).endswith("_id"))]
    out = []
    for k in keys:
        sample = [r.get(k) for r in rows[:50]]
        nums = [to_decimal(v) for v in sample if not isinstance(v, str) or re.fullmatch(r"-?[\d,]+(\.\d+)?", v.strip())]
        if nums and sum(n is not None for n in nums) >= max(1, len(sample) // 2):
            out.append(k)
    return out


def _label_column(rows: Sequence[Dict], numeric: Sequence[str]) -> Optional[str]:
    for k in rows[0] if rows else []:
        if k not in numeric and not (k == "id" or str(k).endswith("_id")):
            return k
    return None


def build_factsheet(doc: ReportDocument) -> FactSheet:
    from gemini_brain.artifacts.report_spec import _is_time_axis

    sheet = FactSheet()

    kpis = [(m.label, m.value, m.unit) for m in doc.kpis if m.value is not None]
    for label, value, unit in kpis:
        sheet.add(f"kpi[{label}]", value, unit)
        sheet.add_subject(label)
    _add_pairs(sheet, "kpi", kpis)

    for ci, chart in enumerate(doc.charts):
        timeish = _is_time_axis(chart.categories)
        totals = []
        for label in chart.categories:
            sheet.add_subject(label, category=True)
        for s in chart.series:
            sheet.add_subject(s.name)
        for s in chart.series:
            prefix = f"chart{ci}.{s.name}"
            labelled = [(c, v) for c, v in zip(chart.categories, s.values) if v is not None]
            for c, v in labelled:
                sheet.add(f"{prefix}[{c}]", v, chart.unit)
            # Totals and shares only where the points are parts of one whole —
            # not across unrelated headline figures, not across income vs expenses.
            if chart.title != KPI_CHART_TITLE and not theme.is_opposing(chart.categories):
                _add_distribution(sheet, prefix, labelled, chart.unit)
            if timeish:
                _add_changes(sheet, prefix, list(zip(chart.categories, s.values)), chart.unit)
            if labelled:
                totals.append((s.name, sum((v for _, v in labelled), Decimal(0)), chart.unit))
        _add_pairs(sheet, f"chart{ci}.totals", totals)
        if len(chart.series) == 1 and len(chart.categories) == 2:
            # "Revenue is AED 400 above Expenses". Only for a two-figure chart:
            # pairing every point of a longer series adds sums and ratios that
            # would let unrelated wrong numbers match by accident.
            point = [(c, v, chart.unit) for c, v in zip(chart.categories, chart.series[0].values) if v is not None]
            _add_pairs(sheet, f"chart{ci}.points", point, measures=False)
        combined = sum((t for _, t, _ in totals), Decimal(0))
        if len(totals) >= 2 and combined > 0 and all(t >= 0 for _, t, _ in totals):
            sheet.add(f"chart{ci}.combined", combined, chart.unit)
            for name, t, _ in totals:
                sheet.add(f"chart{ci}.{name}.share_of_combined", _pct(t, combined), "percent")
        if chart.chart_type == "waterfall":
            steps = [v for i, v in enumerate(chart.series[0].values) if i not in chart.total_indices and v is not None]
            sheet.add(f"chart{ci}.decreases", sum((v for v in steps if v < 0), Decimal(0)), chart.unit)
            sheet.add(f"chart{ci}.increases", sum((v for v in steps if v > 0), Decimal(0)), chart.unit)
        if len(chart.series) >= 2:
            for cat_i, cat in enumerate(chart.categories):
                point = [(s.name, s.values[cat_i], chart.unit) for s in chart.series if s.values[cat_i] is not None]
                _add_pairs(sheet, f"chart{ci}[{cat}]", point)

    for ti, table in enumerate(doc.tables):
        rows = table.raw_rows or []
        sheet.add(f"table{ti}.rows", Decimal(table.total_rows or len(rows)), "count")
        numeric = _numeric_columns(rows)
        label_key = _label_column(rows, numeric)
        for col in numeric:
            sheet.add_subject(col)
        if label_key:
            for r in rows[:_SHARE_ROW_CAP]:
                sheet.add_subject(r.get(label_key), category=True)
        for col in numeric:
            unit = infer_unit(col)
            prefix = f"table{ti}.{col}"
            labelled = []
            for ri, r in enumerate(rows):
                v = to_decimal(r.get(col))
                if v is None:
                    continue
                lbl = str(r.get(label_key)) if label_key else str(ri)
                labelled.append((lbl, v))
                sheet.add(f"{prefix}[{lbl}]", v, unit)
            _add_distribution(sheet, prefix, labelled, unit)
            if label_key and _is_time_axis([lbl for lbl, _ in labelled[:8]]):
                _add_changes(sheet, prefix, labelled, unit)
            # "Payroll is 24% of revenue": each row as a share of each headline figure.
            if unit == "money" and len(labelled) <= _SHARE_ROW_CAP:
                for k_label, k_value, k_unit in kpis:
                    if k_unit == "money" and k_value:
                        for lbl, v in labelled:
                            sheet.add(f"{prefix}[{lbl}]/kpi[{k_label}]", _pct(v, k_value), "percent")

    p = doc.meta.provenance
    if p and p.row_count is not None:
        sheet.add("source.rows", Decimal(p.row_count), "count")
    sheet.partial = any(c.ranked_subset for c in doc.charts)
    return sheet.freeze()


# ── figures stated in text ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Figure:
    text: str
    value: Decimal
    lo: Decimal
    hi: Decimal
    percent: bool
    start: int = 0
    #: "Doubled", "tripled": a change over time, so only period changes back it.
    change: bool = False


_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATES = re.compile(
    r"\b\d{4}-\d{1,2}(?:-\d{1,2})?(?:T[\d:.]+Z?)?\b"
    r"|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"
    rf"|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}(?:,?\s+\d{{4}})?"
    rf"|\b{_MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+\d{{4}})?"
    rf"|\b{_MONTH}\s+'?\d{{2,4}}\b"
    r"|\b(?:Q[1-4]|H[12]|FY)\s*[-']?\s*\d{2,4}\b"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?\b",
    re.IGNORECASE,
)
#: A span of time ("90 days", "12-month") names the window, not a result.
_DURATION = re.compile(r"^\s*-?\s*(?:day|week|month|year|quarter|hour|minute)s?\b", re.IGNORECASE)
_NUMBER = re.compile(
    r"(?<![\w.\-/#])"
    r"(?P<cur>(?:AED|USD|INR|EUR|GBP|SAR|Dhs?\.?|[$€£₹])\s?)?"
    r"(?P<sign>[-−])?"
    r"(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>(?:k|m|mn|bn|b|million|thousand|billion)\b))?"
    r"(?P<pct>\s?(?:%|per\s?cent\b|percent\b|pct\b|percentage points?\b|pp\b))?"
    r"(?![\w/])",
    re.IGNORECASE,
)
_SCALE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000,
          "b": 1_000_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000}
_APPROX = re.compile(r"(?:about|around|approximately|approx\.?|roughly|nearly|almost|close to|~|some)\s*$", re.IGNORECASE)
_OVER = re.compile(r"(?:over|more than|above|exceeding|in excess of|upwards of|at least)\s*$", re.IGNORECASE)
_UNDER = re.compile(r"(?:under|less than|below|up to|at most)\s*$", re.IGNORECASE)


def _precision(num: str) -> Decimal:
    """Half a unit of the last digit the writer committed to.

    "220,500.00" -> 0.005; "25%" -> 0.5; "220,000" -> 5,000 (trailing zeros
    in a whole number >= 1,000 read as rounding, as a person would).
    """
    digits = num.replace(",", "")
    if "." in digits:
        return Decimal(5) / (Decimal(10) ** (len(digits.split(".")[1]) + 1))
    stripped = digits.rstrip("0")
    zeros = len(digits) - len(stripped) if int(digits) >= 1000 and stripped else 0
    rounding = Decimal(5) * (Decimal(10) ** zeros) / Decimal(10)
    if zeros:
        # A bare round figure is read as rounded, but only by up to 0.5% of
        # itself: "AED 220,000" for 220,500 passes, "AED 120,000" for any
        # figure within ±5,000 does not ("about …" widens it separately).
        rounding = min(rounding, max(Decimal("0.5"), Decimal(int(digits)) * Decimal("0.005")))
    return rounding


_WORD_NUMBERS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "twice": 2, "double": 2, "doubled": 2, "doubles": 2, "triple": 3, "tripled": 3, "triples": 3,
    "quadruple": 4, "quadrupled": 4, "half": 0.5, "a third": 1 / 3, "one third": 1 / 3,
    "a quarter": 0.25, "one quarter": 0.25,
}
#: Multiples written in words: "six times", "2.5x", "twice", "doubled", "half".
_MULTIPLE = re.compile(
    r"\b(?:(?P<word>twice|doubled?|doubles|tripled?|triples|quadrupled?|half|a third|one third|a quarter|one quarter)"
    r"|(?P<n>\d+(?:\.\d+)?|two|three|four|five|six|seven|eight|nine|ten)\s*(?:times\b|x\b|-?fold\b))",
    re.IGNORECASE,
)


def _multiples(text: str) -> List[Figure]:
    """A stated multiple is a ratio claim, checked against ratio facts (as %).

    People round multiples ("nearly five times" for 4.6), so each is accepted
    within 10%; "about"/"nearly" widen that further via the qualifier rules.
    """
    out = []
    for m in _MULTIPLE.finditer(text):
        token = (m.group("word") or m.group("n")).lower()
        mult = _WORD_NUMBERS.get(token)
        if mult is None:
            try:
                mult = float(token)
            except ValueError:
                continue
        value = Decimal(str(mult)) * 100
        lo, hi = value * Decimal("0.9"), value * Decimal("1.1")
        before = text[: m.start()]
        if _APPROX.search(before):
            lo, hi = value * Decimal("0.85"), value * Decimal("1.15")
        elif _OVER.search(before):  # "more than half"
            lo, hi = value, value * Decimal("1.25")
        elif _UNDER.search(before):  # "less than a third"
            lo, hi = value * Decimal("0.75"), value
        change = token in ("doubled", "doubles", "tripled", "triples", "quadrupled")
        out.append(Figure(m.group(0).strip(), value, lo, hi, True, m.start(), change))
    return out


def extract_figures(sentence: str) -> List[Figure]:
    """The figures a sentence states, each with the range it is true within.

    Dates, years, times, durations ("90 days"), ids and small bare integers
    (ranks and counts like "top 3", "5 customers") are not figures. Multiples
    in words ("six times", "doubled", "half") are, as ratios.
    """
    text = _DATES.sub(lambda m: " " * len(m.group(0)), sentence)
    out: List[Figure] = _multiples(text)
    text = _MULTIPLE.sub(lambda m: " " * len(m.group(0)), text)
    for m in _NUMBER.finditer(text):
        num, cur, suf, pct = m.group("num"), m.group("cur"), m.group("suf"), m.group("pct")
        plain = num.replace(",", "")
        value = Decimal(plain)
        bare = not (cur or suf or pct or "," in num or "." in num)
        if bare and (value <= 31 or 1900 <= value <= 2100 or _DURATION.match(text[m.end():])):
            continue
        if not bare and not (cur or suf or pct) and _DURATION.match(text[m.end():]):
            continue
        scale = Decimal(_SCALE[suf.lower()]) if suf else Decimal(1)
        value *= scale
        tol = _precision(num) * scale
        lo, hi = value - tol, value + tol
        before = text[: m.start()]
        if _APPROX.search(before):
            lo, hi = min(lo, value * Decimal("0.95")), max(hi, value * Decimal("1.05"))
        elif _OVER.search(before):
            lo, hi = value, max(hi, value * Decimal("1.25"))
        elif _UNDER.search(before):
            lo, hi = min(lo, value * Decimal("0.75")), value
        out.append(Figure(m.group(0).strip(), value, lo, hi, bool(pct), m.start()))
    return out
