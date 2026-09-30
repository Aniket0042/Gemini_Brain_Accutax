"""Report integrity: reconcile the numbers, then ground the narrative.

Runs on a validated `ReportDocument` before anything is shown or exported.

1. Reconciliation — cross-checks between the report's own sections:
   chart points against the table rows they were drawn from (including the
   "Other" slice), headline totals against the rows that sum to them,
   Revenue − Expenses = Net Profit, and the table's row count against the
   source's. A failed check never blocks the report; it adds a visible
   warning that states both figures.

2. Narrative grounding — every figure the narrative states is matched against
   the FactSheet (facts.py) within the precision it was written with. A
   sentence containing any figure that cannot be matched is removed, and the
   report says how many were removed. The narrative can lose sentences; it
   can never gain an unbacked number.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Dict, List, Optional, Sequence

from gemini_brain.artifacts.facts import FactSheet, build_factsheet, extract_figures
from gemini_brain.artifacts.ir import (
    CalloutSection,
    ChartSection,
    MethodologySection,
    NarrativeSection,
    ReportDocument,
    TableSection,
    format_value,
    to_decimal,
)

logger = logging.getLogger("gemini_brain.artifacts.integrity")

_TOLERANCE = Decimal("0.01")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _close(a: Decimal, b: Decimal) -> bool:
    return abs(a - b) <= max(_TOLERANCE, abs(b) * Decimal("0.000001"))


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str = ""


# ── 1. reconciliation ────────────────────────────────────────────────────────

def _complete(table: TableSection) -> bool:
    """True when raw_rows hold every row the table represents."""
    return bool(table.raw_rows) and not table.truncated and table.total_rows <= len(table.raw_rows)


def _column(rows: Sequence[Dict], target: str) -> Optional[str]:
    for key in rows[0] if rows else []:
        if _norm(key) == target:
            return key
    return None


def _check_bridge_ends(doc: ReportDocument, chart: ChartSection) -> List[CheckResult]:
    """A bridge's steps add up by construction (ir.ChartSection); its two ends
    must also be the report's own revenue and net profit figures."""
    vals = chart.series[0].values
    results = []
    for idx, labels in ((0, ("Total Revenue", "Revenue")), (len(vals) - 1, ("Net Profit",))):
        kpi = _kpi(doc, *labels)
        if kpi is None:
            continue
        ok = _close(vals[idx], kpi.value)
        results.append(CheckResult(f"{chart.title} {chart.categories[idx]}", ok, "" if ok else (
            f"{chart.title} starts or ends at {format_value(vals[idx], 'money', doc.meta.currency)} "
            f"for {chart.categories[idx]}, but {kpi.label} is {kpi.display}."
        )))
    return results


def _check_chart_against_tables(chart: ChartSection, tables: Sequence[TableSection], currency: str) -> List[CheckResult]:
    results: List[CheckResult] = []
    if chart.chart_type == "waterfall":
        return results  # steps are signed changes, not table rows; see _check_bridge_ends
    named = [c for c in chart.categories if c.strip().lower() != "other"]
    if not named:
        return results
    for s in chart.series:
        for table in tables:
            if not _complete(table):
                continue
            rows = table.raw_rows
            val_key = _column(rows, _norm(s.name))
            if not val_key:
                continue
            label_key = next(
                (k for k in rows[0] if k != val_key
                 and sum(1 for c in named if c in {str(r.get(k)) for r in rows}) >= max(1, int(len(named) * 0.8))),
                None,
            )
            if not label_key:
                continue
            labels = [str(r.get(label_key)) for r in rows]
            if len(set(labels)) != len(labels):
                continue  # duplicate labels: a point cannot be tied to one row
            by_label = {str(r.get(label_key)): to_decimal(r.get(val_key)) for r in rows}
            name = f"{chart.title or 'Chart'} vs {table.title}"
            wrong = [
                (cat, v, by_label[cat]) for cat, v in zip(chart.categories, s.values)
                if cat in by_label and v is not None and by_label[cat] is not None and not _close(v, by_label[cat])
            ]
            if wrong:
                cat, v, t = wrong[0]
                results.append(CheckResult(name, False, (
                    f"{chart.title or 'The chart'} shows {cat} at {format_value(v, chart.unit, currency)}, "
                    f"but the {table.title} table has {format_value(t, chart.unit, currency)}."
                )))
            else:
                results.append(CheckResult(name, True))
            if "Other" in chart.categories:
                other = s.values[chart.categories.index("Other")]
                rest = [v for lbl, v in by_label.items() if lbl not in chart.categories and v is not None]
                expected = sum(rest, Decimal(0))
                ok = other is not None and _close(other, expected)
                results.append(CheckResult(f"{name} (Other)", ok, "" if ok else (
                    f"The Other slice of {chart.title or 'the chart'} is {format_value(other, chart.unit, currency)}, "
                    f"but the rows it groups add up to {format_value(expected, chart.unit, currency)}."
                )))
            break
    return results


_TOTAL_PREFIX = re.compile(r"^total\s+", re.IGNORECASE)


def _check_kpis_against_tables(doc: ReportDocument) -> List[CheckResult]:
    results: List[CheckResult] = []
    currency = doc.meta.currency
    for kpi in doc.kpis:
        if kpi.value is None or kpi.unit not in ("money", "count"):
            continue
        targets = {_norm(kpi.label), _norm(_TOTAL_PREFIX.sub("", kpi.label))}
        subject = _TOTAL_PREFIX.sub("", kpi.label).strip()
        for table in doc.tables:
            if not _complete(table):
                continue
            rows = table.raw_rows
            # Line-item tables carry a "section" per row: items of a section
            # must add up to that section's stated total.
            if any("section" in r for r in rows) and "amount" in rows[0]:
                items = [to_decimal(r.get("amount")) for r in rows if _norm(r.get("section")) in targets]
                if items and all(v is not None for v in items):
                    total = sum(items, Decimal(0))
                    ok = _close(total, kpi.value)
                    results.append(CheckResult(f"{kpi.label} vs {table.title} items", ok, "" if ok else (
                        f"The {subject} line items add up to {format_value(total, kpi.unit, currency)}, "
                        f"but {kpi.label} is {kpi.display}. A subtotal may be counted twice, or an item is missing."
                    )))
                continue
            col = next((k for k in rows[0] if _norm(k) in targets), None)
            if not col:
                continue
            vals = [to_decimal(r.get(col)) for r in rows]
            if any(v is None for v in vals):
                continue  # a missing period cannot be reconciled; the table already shows "—"
            total = sum(vals, Decimal(0))
            ok = _close(total, kpi.value)
            results.append(CheckResult(f"{kpi.label} vs {table.title}", ok, "" if ok else (
                f"The {table.title} table adds up to {format_value(total, kpi.unit, currency)} for {subject}, "
                f"but {kpi.label} is {kpi.display}. Check the period each figure covers before relying on either."
            )))
    return results


def _kpi(doc: ReportDocument, *labels: str):
    wanted = {_norm(x) for x in labels}
    return next((m for m in doc.kpis if _norm(m.label) in wanted and m.value is not None), None)


def _check_pnl_identity(doc: ReportDocument) -> List[CheckResult]:
    results: List[CheckResult] = []
    rev = _kpi(doc, "Total Revenue", "Revenue", "Total Income", "Income")
    exp = _kpi(doc, "Total Expenses", "Total Expense", "Expenses", "Expense")
    net = _kpi(doc, "Net Profit", "Net Income")
    currency = doc.meta.currency
    if rev and exp and net:
        expected = rev.value - exp.value
        ok = _close(net.value, expected)
        results.append(CheckResult("Revenue − Expenses = Net Profit", ok, "" if ok else (
            f"{net.label} is {net.display}, but {rev.label} − {exp.label} is "
            f"{format_value(expected, 'money', currency)}. The source may include other income or costs; "
            "confirm before relying on the net figure."
        )))
    for table in doc.tables:
        rows = table.raw_rows or []
        if not rows or not {"revenue", "expenses", "net_profit"} <= set(rows[0]):
            continue
        bad = []
        for r in rows:
            rv, ev, nv = (to_decimal(r.get(k)) for k in ("revenue", "expenses", "net_profit"))
            if None not in (rv, ev, nv) and not _close(nv, rv - ev):
                bad.append(str(r.get("month") or "a row"))
        results.append(CheckResult(f"{table.title} net = revenue − expenses", not bad, "" if not bad else (
            f"In the {table.title} table, Net Profit is not Revenue − Expenses for {', '.join(bad[:3])}."
        )))
    return results


def _check_row_count(doc: ReportDocument) -> List[CheckResult]:
    p = doc.meta.provenance
    if not p or p.row_count is None or p.row_count <= 1 or p.truncated:
        return []
    details = [t for t in doc.tables if t.title == "Details"]
    if not details:
        return []
    shown = details[0].total_rows
    ok = shown >= p.row_count
    return [CheckResult("Row count", ok, "" if ok else (
        f"The source returned {p.row_count:,} records, but the report holds {shown:,}. Some records were not included."
    ))]


def reconcile(doc: ReportDocument) -> List[CheckResult]:
    results: List[CheckResult] = []
    for chart in doc.charts:
        results += _check_chart_against_tables(chart, doc.tables, doc.meta.currency)
        if chart.chart_type == "waterfall":
            results += _check_bridge_ends(doc, chart)
    results += _check_kpis_against_tables(doc)
    results += _check_pnl_identity(doc)
    results += _check_row_count(doc)
    return results


# ── 2. narrative grounding ───────────────────────────────────────────────────

@dataclass
class Grounding:
    text: str
    checked: int = 0
    verified: int = 0
    removed: List[str] = field(default_factory=list)


_PREFIX = re.compile(r"^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)?)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9*\"'(\[])")


#: A claim about the whole business ("68% of total revenue", "five customers
#: generating all revenue"). False whenever the report lists only a top N.
_WHOLE_CLAIM = re.compile(
    r"\b(?:of\s+(?:the\s+)?(?:total|overall|entire|whole)\b(?!\s+(?:shown|listed))"
    r"|total\s+(?:revenue|sales|income|spend|expenses?)"
    r"|of\s+(?:revenue|sales|income|spend)\b"
    r"|all\s+(?:of\s+)?(?:the\s+|your\s+|our\s+)?(?:revenue|sales|income|customers|vendors|suppliers|spend)"
    r"|(?:entire|whole)\s+(?:revenue|business|customer\s+base)"
    r"|(?:only|just)\s+(?:\d+|two|three|four|five|six|seven|eight|nine|ten)\s+(?:customers|clients|vendors|suppliers))",
    re.IGNORECASE,
)


def _sentence_verdict(sentence: str, facts: FactSheet, scoped: bool) -> tuple[bool, int, int]:
    figures = extract_figures(sentence)
    if scoped and facts.partial and _WHOLE_CLAIM.search(sentence):
        return False, len(figures), 0
    among = facts.scope(sentence) if scoped and figures else None
    verified = 0
    for f in figures:
        pool = among
        if f.change:  # "tripled": must be a change between periods
            pool = facts.change_ids if pool is None else pool & facts.change_ids
        verified += facts.match(f.lo, f.hi, percent=f.percent, among=pool) is not None
    return verified == len(figures), len(figures), verified


def ground_text(markdown: str, facts: FactSheet, *, scoped: bool = True) -> Grounding:
    """Keep only sentences whose every figure matches a fact.

    `scoped` (the default, for anything a model wrote) ties each figure to the
    subjects its sentence names; computed takeaways pass `scoped=False`.
    """
    result = Grounding(text="")
    lines_out: List[tuple[str, str]] = []  # (kind, line)
    for raw in (markdown or "").splitlines():
        if not raw.strip():
            lines_out.append(("blank", ""))
            continue
        prefix = _PREFIX.match(raw).group(1)
        body = raw[len(prefix):]
        kept: List[str] = []
        for sentence in _SENTENCE_END.split(body):
            ok, checked, verified = _sentence_verdict(sentence, facts, scoped)
            result.checked += checked
            result.verified += verified
            if ok:
                kept.append(sentence)
            else:
                result.removed.append(sentence.strip())
        if kept:
            kind = "heading" if prefix.lstrip().startswith("#") else "content"
            lines_out.append((kind, prefix + " ".join(kept)))
    # A heading whose content was all removed goes too.
    cleaned: List[str] = []
    for i, (kind, line) in enumerate(lines_out):
        if kind == "heading":
            nxt = next((k for k, _ in lines_out[i + 1:] if k != "blank"), None)
            if nxt != "content":
                continue
        if kind == "blank" and (not cleaned or cleaned[-1] == ""):
            continue
        cleaned.append(line)
    result.text = "\n".join(cleaned).strip()
    return result


# ── apply to a document ──────────────────────────────────────────────────────

def _ground_narrative(narrative: NarrativeSection, facts: FactSheet) -> tuple[Optional[NarrativeSection], Grounding]:
    """Ground a narrative. A structured (report/template) narrative is grounded
    part by part so its headline, summary, drivers, risks and actions survive
    as parts; a chat-copy narrative is grounded as markdown."""
    total = Grounding(text="")
    # Template sentences are computed from the facts (like takeaways); only
    # text a model wrote is tied to the subjects it names.
    scoped = narrative.origin != "template"
    if narrative.origin == "chat" or not (narrative.headline or narrative.summary or narrative.drivers
                                          or narrative.risks or narrative.actions):
        total = ground_text(narrative.markdown, facts, scoped=scoped)
        insight = narrative.insight
        if insight and not ground_text(insight, facts, scoped=scoped).text:
            insight = None
        section = NarrativeSection(markdown=total.text, insight=insight) if total.text else None
        return section, total

    def keep(items: Sequence[str]) -> List[str]:
        out = []
        for item in items:
            g = ground_text(item, facts, scoped=scoped)
            total.checked += g.checked
            total.verified += g.verified
            total.removed += g.removed
            if g.text:
                out.append(g.text)
        return out

    # The headline stays whole or goes: half a headline is worse than none.
    headline = None
    if narrative.headline:
        g = ground_text(narrative.headline, facts, scoped=scoped)
        total.checked += g.checked
        total.verified += g.verified
        total.removed += g.removed
        headline = None if g.removed else narrative.headline
    if headline is None and narrative.fallback_headline:
        g = ground_text(narrative.fallback_headline, facts, scoped=False)  # computed, like a takeaway
        headline = narrative.fallback_headline if not g.removed else None
    section = NarrativeSection.from_parts(
        origin=narrative.origin,
        headline=headline,
        summary=keep(narrative.summary),
        drivers=keep(narrative.drivers),
        risks=keep(narrative.risks),
        actions=keep(narrative.actions),
    )
    return section, total


def verify_document(
    doc: ReportDocument,
    narrate: Optional[Callable[[ReportDocument], ReportDocument]] = None,
) -> ReportDocument:
    """Reconcile, optionally narrate, then ground; returns a new document.

    Order matters: failed checks become warnings first, so `narrate` (the
    report narrator) can see and mention them; grounding runs last, so no
    narrative sentence — from any source — skips the figure check.
    """
    from gemini_brain.observability.metrics import METRICS

    checks = reconcile(doc)
    failed = [c for c in checks if not c.passed]
    body = [s for s in doc.sections if s.kind != "methodology"]
    body += [CalloutSection(tone="warning", text=c.message) for c in failed]
    doc = doc.model_copy(update={"sections": body + list(doc.of("methodology"))})
    if narrate is not None:
        doc = narrate(doc)
    facts = build_factsheet(doc)

    sections = []
    for s in doc.sections:
        if s.kind in ("narrative", "methodology"):
            continue
        if s.kind == "chart" and s.takeaway:
            grounded = ground_text(s.takeaway, facts, scoped=False)
            if grounded.removed:
                # Takeaways are computed from the data, so this means a bug in
                # insights.py or facts.py, not a model error. Drop, and say so in logs.
                logger.warning("Dropped ungrounded chart takeaway %r: %s", s.title, grounded.removed)
                s = s.model_copy(update={"takeaway": None})
        sections.append(s)
    narrative = doc.narrative
    removed: List[str] = []
    grounding: Optional[Grounding] = None
    new_narrative: Optional[NarrativeSection] = None
    if narrative is not None:
        new_narrative, grounding = _ground_narrative(narrative, facts)
        removed = grounding.removed
        METRICS.report_numbers_checked.inc(grounding.checked)
        METRICS.report_numbers_verified.inc(grounding.verified)
        METRICS.report_sentences_removed.inc(len(removed))
    METRICS.report_checks_failed.inc(len(failed))

    # Narrative keeps its place right after the KPI row.
    if new_narrative is not None:
        at = 1 if sections and sections[0].kind == "kpis" else 0
        sections.insert(at, new_narrative)

    method = [line for s in doc.of("methodology") for line in s.lines]
    if new_narrative is not None and new_narrative.origin == "report":
        method.append("The summary was written from the verified figures in this report; every figure in it was checked.")
    elif new_narrative is not None and new_narrative.origin == "template":
        method.append("The summary was generated directly from the verified figures in this report.")
    if checks:
        method.append(
            f"Consistency checks: {len(checks) - len(failed)} of {len(checks)} passed"
            + (" — see the warnings above." if failed else ".")
        )
    if removed:
        method.append(
            f"{len(removed)} statement{'s' if len(removed) != 1 else ''} removed from the summary because "
            f"{'their' if len(removed) != 1 else 'its'} figures could not be matched to the data."
        )
    if method:
        sections.append(MethodologySection(lines=method))

    if removed or failed:
        logger.info(
            "Report integrity: %d/%d checks failed; %d/%d narrative figures verified; %d sentences removed",
            len(failed), len(checks),
            grounding.verified if grounding else 0, grounding.checked if grounding else 0, len(removed),
        )
        for sentence in removed:
            logger.info("Removed unverified narrative sentence: %s", sentence[:300])

    out = doc.model_copy(update={"sections": sections})
    out.integrity = {
        "checks_run": len(checks),
        "checks_failed": len(failed),
        "figures_checked": grounding.checked if grounding else 0,
        "figures_verified": grounding.verified if grounding else 0,
        "sentences_removed": len(removed),
    }
    return out
