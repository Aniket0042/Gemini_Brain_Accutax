"""Report narrator: the written analysis at the top of every report.

The chat answer is written for a chat turn ("one short line, then bullets")
and used to be pasted into files as-is. A report needs more: a headline, an
executive summary, the drivers behind the numbers, the risks worth watching,
and what to do next — and every sentence must be defensible.

How it stays accurate:

1. `build_brief` turns the validated document into a numbered fact list
   (F1, F2, …): headline figures, each chart's verified takeaway, the leading
   items with their shares, period changes, bridge steps, failed consistency
   checks. Numbers are written exactly as the report prints them.
2. The model (the primary Bedrock model by default) writes JSON from that
   list only, citing fact ids after every sentence. It is told not to
   compute anything the list does not state.
3. `parse_narrative` keeps only sentences whose citations all exist; the
   citations are stripped for display.
4. integrity.verify_document then checks every figure in every sentence
   against the full FactSheet, like any narrative.

If the model is off, slow, or returns something unusable, `template_narrative`
writes a plainer version from the same facts — a report never ships without
its summary, and never waits longer than the configured timeout for one.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, List, Optional, Sequence, Tuple

from gemini_brain.artifacts.ir import (
    ChartSection,
    NarrativeSection,
    ReportDocument,
    format_value,
)

logger = logging.getLogger("gemini_brain.artifacts.narrator")

#: Brief size limits: enough for a full analysis, small enough to keep the call fast.
_MAX_FACTS = 70
_TOP_ITEMS = 5
_CONCENTRATION_PCT = Decimal(40)

_HUNDRED = Decimal(100)


# ── report kind ──────────────────────────────────────────────────────────────

def classify_report(doc: ReportDocument) -> str:
    """pnl | tax | aging | trend | ranking | general — picks the analyst's angle."""
    from gemini_brain.artifacts.report_spec import _is_time_axis

    text = " ".join([doc.meta.title] + [m.label for m in doc.kpis] + [c.title for c in doc.charts]).lower()
    labels = {m.label.lower() for m in doc.kpis}
    if any(c.chart_type == "waterfall" for c in doc.charts) or (
        labels & {"total revenue", "revenue"} and labels & {"net profit", "total expenses"}
    ):
        return "pnl"
    if re.search(r"\b(vat|tax)\b", text):
        return "tax"
    if re.search(r"\b(aging|ageing|overdue|receivable|payable|outstanding)\b", text):
        return "aging"
    if any(_is_time_axis(c.categories) for c in doc.charts):
        return "trend"
    if doc.charts or doc.tables:
        return "ranking"
    return "general"


_KIND_GUIDANCE = {
    "pnl": (
        "This is a profit and loss report. Lead with the result (net profit and margin). "
        "Drivers: what moved revenue, which costs are largest, how the months compare. "
        "Risks: cost lines that grow faster than revenue, thin margins, figures that do not reconcile."
    ),
    "tax": (
        "This is a tax/VAT report. Lead with the amount due or reclaimable. "
        "Drivers: where output and input tax come from. Risks: filing deadlines only if stated, "
        "unusual movements, anything that does not reconcile."
    ),
    "aging": (
        "This is an aging/outstanding balances report. Lead with the total outstanding and how much is overdue. "
        "Drivers: which customers or buckets hold the most. Risks: concentration in old buckets or a few names."
    ),
    "trend": (
        "This is a trend report. Lead with the direction and size of the change over the period. "
        "Drivers: the periods that made the difference. Risks: declines, volatility, the latest period's direction."
    ),
    "ranking": (
        "This is a ranking/breakdown report. Lead with who or what leads and how concentrated the total is. "
        "Drivers: the leaders and their shares. Risks: dependence on a few items, a long tail."
    ),
    "general": "Lead with the single most important figure, then explain what it means.",
}


# ── the fact brief ───────────────────────────────────────────────────────────

@dataclass
class Brief:
    kind: str
    facts: List[Tuple[str, str]] = field(default_factory=list)  # (id, sentence)
    context: List[str] = field(default_factory=list)

    def add(self, text: str) -> None:
        if len(self.facts) < _MAX_FACTS:
            self.facts.append((f"F{len(self.facts) + 1}", text))

    @property
    def ids(self) -> set:
        return {fid for fid, _ in self.facts}

    def render(self) -> str:
        return "\n".join(f"{fid}: {text}" for fid, text in self.facts)


def _money(v: Decimal, unit: str, currency: str) -> str:
    return format_value(v, unit, currency)  # exact, as printed in the report tables


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    return part / whole * _HUNDRED


def _chart_facts(brief: Brief, chart: ChartSection, currency: str) -> None:
    from gemini_brain.artifacts.report_spec import _is_time_axis

    if chart.takeaway:
        brief.add(f"{chart.title}: {chart.takeaway}")
    if chart.chart_type == "waterfall":
        totals = set(chart.total_indices)
        for i, (cat, v) in enumerate(zip(chart.categories, chart.series[0].values)):
            if v is None:
                continue
            kind = "total" if i in totals else ("reduces profit by" if v < 0 else "adds")
            brief.add(f"Profit bridge — {cat} {kind} {_money(abs(v) if i not in totals else v, 'money', currency)}.")
        return
    timeish = _is_time_axis(chart.categories)
    for s in chart.series[:3]:
        points = [(c, v) for c, v in zip(chart.categories, s.values) if v is not None]
        if not points:
            continue
        if timeish:
            for cat, v in points[:12]:
                brief.add(f"{s.name} in {cat}: {_money(v, chart.unit, currency)}.")
            (f_lbl, first), (l_lbl, last) = points[0], points[-1]
            if first and len(points) >= 2:
                brief.add(
                    f"{s.name} changed by {format_value(_pct(last - first, abs(first)), 'percent')} "
                    f"from {f_lbl} to {l_lbl}."
                )
            continue
        total = sum((v for _, v in points), Decimal(0))
        ranked = sorted((p for p in points if p[0].strip().lower() != "other"), key=lambda p: p[1], reverse=True)
        positive = total > 0 and all(v >= 0 for _, v in points)
        base = f"the {chart.ranked_subset} shown" if chart.ranked_subset else "the total"
        if chart.ranked_subset:
            brief.add(
                f"{s.name} — only the top {chart.ranked_subset} are listed, not every one; "
                f"shares are of these {chart.ranked_subset}, not of the whole business."
            )
        for cat, v in ranked[:_TOP_ITEMS]:
            share = f" ({format_value(_pct(v, total), 'percent')} of {base})" if positive else ""
            brief.add(f"{s.name} — {cat}: {_money(v, chart.unit, currency)}{share}.")
        if len(ranked) > _TOP_ITEMS:
            brief.add(f"{s.name} — {len(ranked) - _TOP_ITEMS} further items are smaller than {ranked[_TOP_ITEMS - 1][0]}.")
        if positive:
            across = f"the {chart.ranked_subset} shown" if chart.ranked_subset else "all items"
            brief.add(f"{s.name} — total across {across}: {_money(total, chart.unit, currency)}.")


def build_brief(doc: ReportDocument) -> Brief:
    brief = Brief(kind=classify_report(doc))
    currency = doc.meta.currency
    meta = doc.meta
    brief.context.append(f"Report: {meta.title}")
    if meta.period:
        brief.context.append(f"Period: {meta.period}")
    if meta.entity:
        brief.context.append(f"Entity: {meta.entity}")
    for m in doc.kpis:
        if m.value is not None or m.text:
            brief.add(f"{m.label}: {m.display}.")
    for chart in doc.charts:
        _chart_facts(brief, chart, currency)
    for s in doc.of("callout"):
        if s.tone in ("warning", "caveat"):
            brief.add(f"Data caveat: {s.text}")
    return brief


# ── model call ───────────────────────────────────────────────────────────────

_SYSTEM = """You write the executive analysis at the top of a finance report for a business owner in the UAE.
You are given a numbered list of verified FACTS. They are the only information you have.

Rules — accuracy comes before everything:
- Every sentence must end with the ids of the facts it relies on, in square brackets, e.g. "... [F3, F7]".
- Use a figure only if it appears in a cited fact, written the same way. Never add, subtract, average,
  or work out a percentage yourself. If a comparison needs a number the facts do not give, describe it
  qualitatively ("well ahead of", "the largest by far") — never as a multiple ("six times", "twice",
  "doubled", "half") unless a fact states that multiple.
- Never mention anything that is not in the facts: no targets, budgets, prior years, industry norms, or causes you cannot see.
- When a fact says only the top N are listed, the report does not have the full list. Say shares are of those N
  ("of the top 5"), never "of total revenue" or "of all sales", and never claim those N are all the customers.
- Plain business English. No jargon about data, systems, tables, or fields. No emojis. Amounts as the facts write them.

Write JSON only, in exactly this shape:
{"headline": "...", "summary": ["...", "..."], "drivers": ["..."], "risks": ["..."], "actions": ["..."]}

- headline: the single most important finding, at most 20 words.
- summary: 2 to 4 sentences — the result, what drove it, and what it means.
- drivers: 2 to 4 short sentences on what moved the numbers.
- risks: 0 to 3 short sentences, only where the facts show a real concern (concentration, decline,
  a caveat, a figure that does not reconcile). Use an empty list when there is none.
- actions: 1 to 3 practical next steps a business owner can take, each tied to a cited fact.
  Phrase them as suggestions ("Review ...", "Follow up ...").
"""


def _user_message(brief: Brief, query: str) -> str:
    return (
        f"Request: {query.strip()[:300]}\n"
        + "\n".join(brief.context)
        + f"\n\nReport angle: {_KIND_GUIDANCE[brief.kind]}\n\nFACTS:\n{brief.render()}"
    )


def _default_model_call(system: str, user: str) -> str:
    from gemini_brain.config.settings import settings
    from gemini_brain.reasoning.bedrock_client import BedrockAdapter

    model_id = settings.report_narrative_model_id or settings.bedrock_model_id
    adapter = BedrockAdapter(model_id=model_id, label="report narrator")
    return adapter.converse(
        system_prompt=system,
        messages=[{"role": "user", "content": [{"text": user}]}],
        temperature=0.0,
        max_tokens=1200,
        purpose="report_narration",
    )


#: Swappable for tests; production calls Bedrock.
ModelCall = Callable[[str, str], str]
_model_call: ModelCall = _default_model_call


_CITATION = re.compile(r"\s*\[(F\d+(?:\s*,\s*F\d+)*)\]")


def _clean(sentence: str, known: set, *, require_citation: bool) -> Optional[str]:
    """Strip citations; None when a citation is unknown or a required one is missing."""
    cited = [fid.strip() for group in _CITATION.findall(sentence) for fid in group.split(",")]
    if any(fid not in known for fid in cited):
        return None
    if require_citation and not cited:
        return None
    text = _CITATION.sub("", sentence).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    if text and text[-1] not in ".!?":
        text += "."  # the citation often sat where the full stop belonged
    return text or None


def parse_narrative(raw: str, brief: Brief) -> Optional[NarrativeSection]:
    """Model JSON to a NarrativeSection, keeping only properly cited sentences."""
    from gemini_brain.utils.json_parser import extract_json

    data = extract_json(raw or "")
    if not isinstance(data, dict):
        return None
    known = brief.ids

    def part(key: str, *, limit: int) -> List[str]:
        items = data.get(key) or []
        if isinstance(items, str):
            items = [items]
        out = []
        for item in items[:limit]:
            if isinstance(item, str):
                cleaned = _clean(item, known, require_citation=True)
                if cleaned:
                    out.append(cleaned)
        return out

    headline = data.get("headline")
    # A headline often carries no citation; its figures are still grounded later.
    headline = _clean(headline, known, require_citation=False) if isinstance(headline, str) else None
    if headline:
        headline = headline.rstrip(".")  # a headline reads as a title, not a sentence
    return NarrativeSection.from_parts(
        origin="report",
        headline=headline,
        summary=part("summary", limit=4),
        drivers=part("drivers", limit=4),
        risks=part("risks", limit=3),
        actions=part("actions", limit=3),
    )


# ── template fallback ────────────────────────────────────────────────────────

def template_narrative(doc: ReportDocument, brief: Brief) -> Optional[NarrativeSection]:
    """Plain sentences from the same facts, for when the model is unavailable."""
    takeaways = [c.takeaway for c in doc.charts if c.takeaway]
    kpis = [m for m in doc.kpis if m.value is not None]
    by_label = {m.label.lower(): m for m in kpis}
    period = f" for {doc.meta.period}" if doc.meta.period else ""
    headline: Optional[str] = None
    drivers = list(takeaways[:4])

    net = by_label.get("net profit") or by_label.get("operating profit")
    revenue = by_label.get("total revenue")
    margin = by_label.get("margin")
    if brief.kind == "pnl" and net and revenue:
        # A P&L leads with its result, not with a chart observation.
        headline = f"{net.label} of {net.display} on revenue of {revenue.display}"
        if margin:
            headline += f", a {margin.display} margin"
    elif takeaways:
        # Lead with the first finding; its remaining detail stays a driver
        # so the headline is not repeated word for word below it.
        first = drivers.pop(0)
        head, _, rest = first.partition(". ") if ". " in first else first.partition("; ")
        headline = head.rstrip(".;")
        if rest:
            drivers.insert(0, rest[:1].upper() + rest[1:])
    summary: List[str] = []
    if kpis and not takeaways:
        # With no chart findings, the headline figures are the summary; with
        # findings, restating the KPI cards would only repeat them.
        figures = ", ".join(f"{m.label} {m.display}" for m in kpis[:4])
        summary.append(f"Headline figures{period}: {figures}.")
    if headline is None and kpis:
        headline = f"{kpis[0].label} was {kpis[0].display}"
    risks: List[str] = []
    actions: List[str] = []
    for chart in doc.charts:
        if chart.chart_type in ("bar", "hbar", "pie", "donut") and len(chart.series) == 1:
            points = [(c, v) for c, v in zip(chart.categories, chart.series[0].values) if v is not None]
            total = sum((v for _, v in points), Decimal(0))
            named = [p for p in points if p[0].strip().lower() != "other"]
            if total > 0 and all(v >= 0 for _, v in points) and len(named) >= 3:
                lead, value = max(named, key=lambda p: p[1])
                share = _pct(value, total)
                # A top-N list cannot show concentration in the whole business.
                if share >= _CONCENTRATION_PCT and not chart.ranked_subset:
                    risks.append(
                        f"{lead} accounts for {format_value(share, 'percent')} of the total, "
                        "so results depend heavily on a single name."
                    )
                    actions.append(f"Review exposure to {lead} and how to broaden the base.")
        if chart.chart_type == "waterfall":
            steps = [(c, v) for i, (c, v) in enumerate(zip(chart.categories, chart.series[0].values))
                     if i not in chart.total_indices and v is not None and v < 0]
            if steps:
                biggest = min(steps, key=lambda p: p[1])[0]
                actions.append(f"Review {biggest}, the largest cost line, for savings.")
    for s in doc.of("callout"):
        if s.tone == "warning":
            risks.append(s.text)
    return NarrativeSection.from_parts(
        origin="template", headline=headline, summary=summary,
        drivers=drivers, risks=risks[:3], actions=actions[:3],
    )


# ── entry point ──────────────────────────────────────────────────────────────

def write_narrative(doc: ReportDocument, query: str = "") -> ReportDocument:
    """Return `doc` with its narrative replaced by the report narrative.

    Never raises and never blocks past the configured timeout: any failure
    falls back to the template, and a document with no facts keeps whatever
    narrative it had.
    """
    from gemini_brain.config.settings import settings

    brief = build_brief(doc)
    if not brief.facts:
        return doc
    section: Optional[NarrativeSection] = None
    if (settings.report_narrative_mode or "llm").lower() == "llm":
        section = _narrate_with_model(brief, query, settings.report_narrative_timeout_seconds)
    template = template_narrative(doc, brief)
    if section is None:
        section = template
    elif template is not None and template.headline:
        # Kept in reserve: if grounding removes the written headline, the
        # report still leads with a (computed, verified) one.
        section = section.model_copy(update={"fallback_headline": template.headline})
    elif not section.headline and template is not None and template.headline:
        # Every report leads with a headline; borrow the computed one if the model gave none.
        section = NarrativeSection.from_parts(
            origin=section.origin, headline=template.headline, summary=section.summary,
            drivers=section.drivers, risks=section.risks, actions=section.actions,
        )
    if section is None:
        return doc
    sections = [s for s in doc.sections if s.kind != "narrative"]
    at = 1 if sections and sections[0].kind == "kpis" else 0
    sections.insert(at, section)
    return doc.model_copy(update={"sections": sections})


_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="report-narrator")


def _narrate_with_model(brief: Brief, query: str, timeout: float) -> Optional[NarrativeSection]:
    system, user = _SYSTEM, _user_message(brief, query)
    # copy_context keeps the per-request LLM trace and token accounting attached.
    ctx = contextvars.copy_context()
    future = _EXECUTOR.submit(ctx.run, _model_call, system, user)
    try:
        raw = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        logger.warning("Report narration timed out after %.1fs; using the template", timeout)
        return None
    except Exception as e:
        logger.warning("Report narration failed (%s); using the template", e)
        return None
    section = parse_narrative(raw, brief)
    if section is None:
        logger.warning("Report narration returned no usable JSON; using the template")
    return section


__all__: Sequence[str] = (
    "Brief", "build_brief", "classify_report", "parse_narrative",
    "template_narrative", "write_narrative",
)
