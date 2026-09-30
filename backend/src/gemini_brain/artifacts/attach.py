"""Attach canvas / artifact blocks to a response after retrieval."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from gemini_brain.artifacts import theme
from gemini_brain.artifacts.delivery import detect_delivery
from gemini_brain.artifacts.insights import add_takeaways
from gemini_brain.artifacts.integrity import verify_document
from gemini_brain.artifacts.narrator import write_narrative
from gemini_brain.artifacts.ir import Provenance, ReportDocument
from gemini_brain.artifacts.report_spec import build_report_spec

logger = logging.getLogger("gemini_brain.artifacts.attach")

NOTHING_TO_EXPORT = (
    "I can only build a file or chart from retrieved financial data. "
    "Ask for a report or a set of figures, then request the PDF, Excel, or graph."
)


def _canvas_block(spec: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "canvas",
        "title": spec.get("title") or "Report",
        "spec": spec,
    }


def _insight_from_blocks(blocks: List[Dict[str, Any]]) -> Optional[str]:
    for block in blocks:
        if block.get("type") != "markdown":
            continue
        text = re.sub(r"^#+\s*", "", (block.get("text") or "").strip(), flags=re.M)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 40:
            continue
        sentence = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
        if 40 <= len(sentence) <= 280:
            return sentence
    return None


def _full_narrative_from_blocks(blocks: List[Dict[str, Any]]) -> Optional[str]:
    """The complete narrated answer (not just `insight`'s one extracted
    sentence) — so an exported file can carry the same explanation the chat
    already shows, not just numbers with no context."""
    parts = [
        (b.get("text") or "").strip()
        for b in blocks
        if b.get("type") == "markdown" and (b.get("text") or "").strip()
    ]
    text = "\n\n".join(parts)
    return text or None


def _promote_visuals(out: List[Dict[str, Any]], spec: Dict[str, Any]) -> None:
    """Put chart/table blocks in the chat card so the transcript matches the mock.

    The spec's charts replace any formatter chart blocks already in `out`:
    they are the same charts after the requested type and colors were applied,
    so the chat card can never show a line chart while the PDF shows a pie.
    """
    spec_charts = [{"type": "chart", **chart} for chart in spec.get("charts") or []]
    first_chart = next((i for i, b in enumerate(out) if b.get("type") == "chart"), None)
    out[:] = [b for b in out if b.get("type") != "chart"]
    insert_at = first_chart if first_chart is not None else len(out)
    out[insert_at:insert_at] = spec_charts
    types = {b.get("type") for b in out}
    if "table" not in types:
        tables = spec.get("tables") or []
        line = next(
            (t for t in tables if any(c.get("key") == "line_item" for c in (t.get("columns") or []))),
            None,
        )
        monthly = next(
            (t for t in tables if str(t.get("title") or "").lower().startswith("monthly")
             or any(c.get("key") == "month" for c in (t.get("columns") or []))),
            None,
        )
        chosen = line or monthly or (tables[0] if tables else None)
        if chosen:
            out.append({"type": "table", **chosen})


def _with_chart_colors(out: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Formatter charts that reach the chat card without going through a spec
    still get the same resolved colors every other chart gets."""
    return [theme.resolve_chart_colors(dict(b)) if b.get("type") == "chart" else b for b in out]


def attach_delivery(
    query: str,
    data: Any,
    blocks: Optional[List[Dict[str, Any]]] = None,
    *,
    status: str = "ok",
    user_id: Optional[int] = None,
    organization_id: Optional[int] = None,
    session_id: Optional[str] = None,
    org_name: Optional[str] = None,
    title: Optional[str] = None,
    answer_text: Optional[str] = None,
    provenance: Optional[Provenance] = None,
) -> List[Dict[str, Any]]:
    """Append canvas/artifact blocks when the user asked for a graph or file.

    `answer_text` is the narrated response (`result["answer"]` in
    gemini_brain_runner.py), passed separately because it's ONLY used here to
    extract `insight`/`narrative` for the spec — `blocks` (built before
    narration runs, from render_blocks()) never actually contains a markdown
    copy of it. It must never be appended to `out`/`blocks` itself: the
    frontend already renders `result.answer` as the main chat text, so a copy
    of it inside `blocks` would render a second time.

    The builder's loose spec is validated into a `ReportDocument` (see ir.py)
    before anything is shown or exported; canvas, chat card, and every file
    read that document's `to_spec()` projection, so none of them can receive
    a chart or number that failed validation. `provenance` (source tier,
    endpoint, row count, window notice) is printed on every output.
    """
    out = list(blocks or [])
    delivery = detect_delivery(query)
    if delivery.mode == "none":
        return _with_chart_colors(out)

    usable = status in ("ok", "partial") and data not in (None, [], {})
    if not usable:
        if delivery.wants_file or delivery.wants_chart:
            out.append({"type": "markdown", "text": NOTHING_TO_EXPORT})
        return _with_chart_colors(out)

    spec = build_report_spec(
        data,
        query,
        org_name=org_name,
        title=title,
        chart_hint=delivery.chart_hint,
        blocks=out,
    )
    narration_source = list(out)
    if answer_text and answer_text.strip():
        narration_source.append({"type": "markdown", "text": answer_text})
    insight = _insight_from_blocks(narration_source)
    if insight:
        spec["insight"] = insight
    narrative = _full_narrative_from_blocks(narration_source)
    if narrative:
        spec["narrative"] = narrative
    # Validate, then reconcile the numbers and ground the narrative (integrity.py):
    # an unverifiable sentence is removed before any output sees it.
    # Takeaways are computed from the validated values first, so they are
    # grounded along with the narrative.
    # The report narrator writes the analysis after the consistency checks (so it
    # can mention a failed one) and before grounding (so its figures are checked).
    document = verify_document(
        add_takeaways(ReportDocument.from_spec(spec, query=query, provenance=provenance)),
        narrate=lambda doc: write_narrative(doc, query),
    )
    if not document.has_content():
        out.append({"type": "markdown", "text": NOTHING_TO_EXPORT})
        return _with_chart_colors(out)
    spec = document.to_spec()

    if delivery.wants_chart or delivery.wants_file:
        _promote_visuals(out, spec)
        out.append(_canvas_block(spec))

    formats: List[str] = []
    if delivery.wants_file and delivery.format:
        formats.append(delivery.format)
    if delivery.wants_chart or delivery.wants_file:
        for extra in ("pdf", "csv"):
            if extra not in formats:
                formats.append(extra)

    # The format the user actually named (if any) — marked so the chat card
    # can show just that one button instead of the full pdf/csv bonus bundle.
    # A pure chart request (no format word) has no primary; every format
    # stays a bonus there, and the chat card falls back to showing both.
    primary_fmt = delivery.format if (delivery.wants_file and delivery.format) else None

    # All formats render in parallel, each bounded by the render timeout.
    from gemini_brain.artifacts.generate import render_formats

    rendered = render_formats(
        spec, formats, user_id=user_id or 0, organization_id=organization_id, session_id=session_id,
    )
    minted = False
    for fmt in formats:
        artifact = rendered.get(fmt)
        if artifact:
            artifact["primary"] = fmt == primary_fmt
            out.append(artifact)
            minted = True
    if delivery.wants_file and delivery.format and not minted:
        out.append({
            "type": "markdown",
            "text": "I could not build that file just now. The figures are still in the canvas.",
        })
    return out
