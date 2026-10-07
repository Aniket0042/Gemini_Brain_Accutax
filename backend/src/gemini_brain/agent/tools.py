"""
tools.py — The tools the agent may call, and how each call runs.

Tenant scope comes from the request, never from the model: every tool runs on
the organizations selected in the chat, and the model can only narrow that set
(`organization_ids`), never widen it. A ToolInputError goes back to the model so
it can correct the call; any other failure becomes a short error the model must
pass on, never an empty answer.
"""
from __future__ import annotations

import concurrent.futures
import copy
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Tuple

from gemini_brain.config.settings import settings
from gemini_brain.semantic import list_documents, query_metrics
from gemini_brain.semantic.catalog import ToolInputError, get_catalog
from gemini_brain.semantic.cube_client import CubeError

logger = logging.getLogger("gemini_brain.agent.tools")

#: Longest one tool call may take, within the request deadline.
TOOL_TIMEOUT_SECONDS = 25.0
#: The VAT knowledge base loads its index on first use; allow for that.
VAT_KB_TIMEOUT_SECONDS = 15.0
FIGURES_UNAVAILABLE = "Figures are temporarily unavailable. Tell the user so; never estimate figures."

_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="agent-tool")

_SCOPE_PROPERTY = {
    "type": "array", "items": {"type": "integer"},
    "description": "Narrow to some of the organizations selected in this chat (their ids). Omit for all of them.",
}

VAT_KB_SPEC = {"toolSpec": {
    "name": "search_vat_kb",
    "description": (
        "UAE VAT and e-invoicing law from official Federal Tax Authority documents: rules, rates, penalties, "
        "deadlines, worked calculations. Returns numbered sources and the rules for citing them. "
        "Not for an organization's own figures."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"question": {"type": "string", "description": "The law question, self-contained"}},
        "required": ["question"],
    }},
}}

APP_GUIDE_SPEC = {"toolSpec": {
    "name": "app_guide",
    "description": (
        "How to do something in the Accutax app (menus, screens, steps), from the official user guide. "
        "Returns the matching guide sections, or no match."
    ),
    "inputSchema": {"json": {
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    }},
}}


@dataclass
class ToolContext:
    organization_ids: List[int]
    subject: str
    deadline: float  # time.monotonic() value for the whole request

    def scope(self, requested: Any) -> List[int]:
        """The selected organizations, narrowed to the ones the model named. Never widened."""
        if not requested:
            return list(self.organization_ids)
        try:
            wanted = {int(o) for o in requested}
        except (TypeError, ValueError):
            raise ToolInputError("organization_ids must be a list of organization ids") from None
        outside = sorted(wanted - set(self.organization_ids))
        if outside:
            raise ToolInputError(f"Organizations {outside} are not selected in this chat")
        return [o for o in self.organization_ids if o in wanted]

    def call_deadline(self, budget: float = TOOL_TIMEOUT_SECONDS) -> float:
        return min(self.deadline, time.monotonic() + budget)


def _with_scope(spec: Dict[str, Any]) -> Dict[str, Any]:
    spec = copy.deepcopy(spec)
    spec["toolSpec"]["inputSchema"]["json"]["properties"]["organization_ids"] = _SCOPE_PROPERTY
    return spec


def specs(ctx: ToolContext) -> List[Dict[str, Any]]:
    """Tool definitions for this request. Without Cube, only the law and guide tools are offered."""
    out: List[Dict[str, Any]] = []
    try:
        catalog = get_catalog(ctx.organization_ids, ctx.subject, ctx.call_deadline())
        out += [_with_scope(query_metrics.tool_spec(catalog)), _with_scope(list_documents.tool_spec(catalog))]
    except Exception as e:  # noqa: BLE001 - the agent still answers law and how-to questions
        logger.warning("agent: Cube catalog unavailable, figure tools left out: %s", e)
    return out + [VAT_KB_SPEC, APP_GUIDE_SPEC]


def execute(name: str, params: Any, ctx: ToolContext) -> Tuple[Dict[str, Any], bool]:
    """Run one tool call. Returns (result, ok); never raises."""
    params = params if isinstance(params, dict) else {}
    try:
        if name in ("query_metrics", "list_documents"):
            orgs = ctx.scope(params.get("organization_ids"))
            tool = query_metrics if name == "query_metrics" else list_documents
            args = {k: v for k, v in params.items() if k != "organization_ids"}
            return tool.run(args, organization_ids=orgs, subject=ctx.subject, deadline=ctx.call_deadline()), True
        if name == "search_vat_kb":
            return _search_vat_kb(params, ctx), True
        if name == "app_guide":
            return _app_guide(params), True
        return {"error": f"Unknown tool {name!r}"}, False
    except ToolInputError as e:
        return {"error": str(e)}, False
    except CubeError as e:
        logger.warning("agent: %s failed in Cube: %s", name, e)
        return {"error": FIGURES_UNAVAILABLE}, False
    except Exception as e:  # noqa: BLE001
        logger.warning("agent: tool %s failed: %s", name, e, exc_info=True)
        return {"error": f"The {name} tool failed. Tell the user this part could not be answered."}, False


def _question(params: Dict[str, Any]) -> str:
    question = str(params.get("question") or "").strip()
    if not question:
        raise ToolInputError("question is required")
    return question


def _search_vat_kb(params: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    question = _question(params)
    if not settings.vat_kb_enabled or not settings.vat_kb_dir:
        return {"available": False, "note": "The VAT knowledge base is not available. Say so; do not answer from memory."}

    def search():
        from gemini_brain.vat_kb.retrieval import load_knowledge_base
        kb = load_knowledge_base(settings.vat_kb_dir)
        return None if kb is None else kb.search(question)

    timeout = max(1.0, min(VAT_KB_TIMEOUT_SECONDS, ctx.deadline - time.monotonic()))
    result = _pool.submit(search).result(timeout=timeout)
    if result is None:
        return {"available": False, "note": "The VAT knowledge base is not available. Say so; do not answer from memory."}
    if not result.hits:
        return {"available": True, "confident": False,
                "note": "No FTA source matches this question. Say so; do not answer from memory."}
    from gemini_brain.vat_kb.answer import prompt_block
    return {"available": True, "confident": bool(result.confident), "sources_and_rules": prompt_block(question, result)}


def _app_guide(params: Dict[str, Any]) -> Dict[str, Any]:
    from gemini_brain.knowledge.guide_loader import guide_context_for, guide_coverage_for
    question = _question(params)
    coverage = guide_coverage_for(question) or {}
    status = coverage.get("status")
    out = {"status": status, "matched_section": coverage.get("matched_section"), "app_url": coverage.get("app_url")}
    if status in ("no_match", "guide_missing"):
        out["note"] = "The guide has no section for this. Say so; never invent menus or steps."
    else:
        out["guide"] = guide_context_for(question)
    return out


def result_rows(results: Sequence[Dict[str, Any]]) -> int:
    return sum(int(r.get("row_count") or 0) for r in results)
