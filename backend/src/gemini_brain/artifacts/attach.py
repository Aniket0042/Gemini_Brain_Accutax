"""Attach canvas / artifact blocks to a response after retrieval."""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from gemini_brain.artifacts.delivery import detect_delivery
from gemini_brain.artifacts.report_spec import (
    build_report_spec,
    merge_chart_blocks,
    spec_has_content,
)

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


def _promote_visuals(out: List[Dict[str, Any]], spec: Dict[str, Any]) -> None:
    """Put chart/table blocks in the chat card so the transcript matches the mock."""
    types = {b.get("type") for b in out}
    if "chart" not in types:
        for chart in spec.get("charts") or []:
            out.append({"type": "chart", **chart})
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


def _mint_artifact(spec: Dict[str, Any], fmt: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
    try:
        from gemini_brain.artifacts.generate import generate_and_store
        return generate_and_store(spec, fmt, **kwargs)
    except Exception as e:
        logger.warning("Artifact generation skipped (%s): %s", fmt, e)
        return None


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
) -> List[Dict[str, Any]]:
    """Append canvas/artifact blocks when the user asked for a graph or file."""
    out = list(blocks or [])
    delivery = detect_delivery(query)
    if delivery.mode == "none":
        return out

    usable = status in ("ok", "partial") and data not in (None, [], {})
    if not usable:
        if delivery.wants_file or delivery.wants_chart:
            out.append({"type": "markdown", "text": NOTHING_TO_EXPORT})
        return out

    spec = build_report_spec(
        data,
        query,
        org_name=org_name,
        title=title,
        chart_hint=delivery.chart_hint,
    )
    merge_chart_blocks(spec, out)
    insight = _insight_from_blocks(out)
    if insight:
        spec["insight"] = insight
    if not spec_has_content(spec):
        out.append({"type": "markdown", "text": NOTHING_TO_EXPORT})
        return out

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

    minted = False
    for fmt in formats:
        artifact = _mint_artifact(
            spec,
            fmt,
            user_id=user_id or 0,
            organization_id=organization_id,
            session_id=session_id,
        )
        if artifact:
            out.append(artifact)
            minted = True
    if delivery.wants_file and delivery.format and not minted:
        out.append({
            "type": "markdown",
            "text": "I could not build that file just now. The figures are still in the canvas.",
        })
    return out
