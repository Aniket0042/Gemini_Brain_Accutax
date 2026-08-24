"""
llm_router.py — Function-calling based LLM tool router using Google Gemini Flash.

Uses Gemini Tool Declarations and FunctionCallingConfig(mode='ANY') to guarantee
structured tool selection without relying on fragile JSON markdown extraction.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Any, Callable, Dict, Optional, Tuple

from gemini_brain.config.constants import GEMINI_MODEL
from gemini_brain.tools.context import RequestCtx

# NOTE: REGISTRY is intentionally NOT imported at module level here. tools/registry.py
# imports tools/schemas.py, which imports router/dates.py, which triggers
# router/__init__.py to load this module — a genuine import cycle. A module-level
# import of REGISTRY resolves fine in most import orders (app boot, the test suite)
# but fails with "partially initialized module" in others (e.g. importing
# gemini_brain.tools.registry directly, first, in a fresh script). Both functions
# below that need REGISTRY import it locally instead, by which point the cycle has
# already fully resolved.

logger = logging.getLogger("gemini_brain.router.llm_router")

ROUTER_SYSTEM_PROMPT = """You route questions for Accutax, a bookkeeping platform for UAE/GCC businesses (currency AED, VAT 5%).

Choose exactly one tool from the AVAILABLE TOOLS list below that best answers the question. Never answer from your own knowledge when a tool exists.

For date ranges, pass the user's phrase verbatim as a `period` parameter: "this month", "last quarter", "last 6 months", "2025". Do not compute dates yourself.

Never supply an organization id or user id — the system injects those.

If the question is about how to use the app, where to find a screen, or an accounting definition, choose answer_directly.
If nothing in the list genuinely fits, choose unsupported and give a one-line reason as the parameter.

Respond with ONLY this JSON, no markdown, no explanation:
{"name": "<tool_name>", "parameters": {<any parameters the tool needs>}}"""


@dataclass
class ToolCallResult:
    name: str
    params: Dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0


def parse_function_call(response_content: Any) -> Tuple[str, Dict[str, Any]]:
    """Extract tool name and args from Gemini response structure or raw JSON."""
    if isinstance(response_content, dict):
        if "functionCall" in response_content:
            fc = response_content["functionCall"]
            return fc.get("name", "unsupported"), fc.get("args", {})
        if "name" in response_content and "parameters" in response_content:
            return response_content["name"], response_content.get("parameters", {})

    if isinstance(response_content, str):
        # Fallback parse if Gemini raw string was returned
        try:
            parsed = json.loads(response_content)
            if isinstance(parsed, dict):
                if "name" in parsed:
                    return parsed["name"], parsed.get("parameters", parsed.get("args", {}))
                if "tool" in parsed:
                    return parsed["tool"], parsed.get("params", {})
        except Exception:
            pass

    return "unsupported", {"reason": "Could not parse tool call"}


def route_with_gemini(
    query: str,
    gemini_caller: Callable[..., Tuple[str, int, int]],
    ctx: Optional[RequestCtx] = None,
) -> ToolCallResult:
    """Route user query to registered tool using Gemini function calling."""
    from gemini_brain.tools.registry import REGISTRY, gemini_declarations

    declarations = gemini_declarations()
    catalog = "\n".join(f"- {d['name']}: {d['description']}" for d in declarations)
    system_prompt = ROUTER_SYSTEM_PROMPT + "\n\nAVAILABLE TOOLS:\n" + catalog

    if ctx and ctx.session_state:
        context_parts = []
        if ctx.session_state.get("last_executed_task"):
            context_parts.append(f"- Previous Tool/Topic: {ctx.session_state['last_executed_task']}")
        if ctx.session_state.get("active_year"):
            context_parts.append(f"- Active Year: {ctx.session_state['active_year']}")
        if ctx.session_state.get("contact_name"):
            context_parts.append(f"- Active Contact: {ctx.session_state['contact_name']}")
        if ctx.session_state.get("bank_account"):
            context_parts.append(f"- Active Bank: {ctx.session_state['bank_account']}")

        if context_parts:
            system_prompt += "\n\nACTIVE CONVERSATION CONTEXT:\n" + "\n".join(context_parts)
            system_prompt += "\nIf the user's question is a follow-up (e.g. 'what about Q2?', 'and last year?'), reuse the previous tool and apply the newly requested period/filter."

    if ctx and ctx.feedback:
        system_prompt += f"\n\nPREVIOUS ATTEMPT FAILED / CORRECTION FEEDBACK:\n{ctx.feedback}\nPlease select an alternative tool from the catalog, or return 'unsupported' if no endpoint matches."

    try:
        raw_res, ti, to = gemini_caller(
            system_prompt=system_prompt,
            user_message=query,
            max_tokens=250,
            thinking_budget=0,
        )
        tool_name, tool_params = parse_function_call(raw_res)

        if tool_name not in REGISTRY:
            logger.warning("Gemini LLM router returned unknown tool: %s", tool_name)
            tool_name = "unsupported"

        return ToolCallResult(
            name=tool_name,
            params=tool_params,
            tokens_in=ti,
            tokens_out=to,
        )
    except Exception as e:
        logger.error("Gemini LLM router call failed: %s", e)
        return ToolCallResult(
            name="unsupported",
            params={"reason": f"Routing error: {str(e)}"},
            tokens_in=0,
            tokens_out=0,
        )


def select_endpoint_structured(
    query: str,
    org_id: int,
    call_gemini: Callable[..., Tuple[str, int, int]],
    user_id: str = "",
    session_state: Optional[Dict[str, Any]] = None,
    feedback: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], int, int]:
    """Select API endpoint using Gemini Structured Function Calling with Pydantic parameter schemas.

    Returns (selection_dict, tokens_in, tokens_out).
    """
    from gemini_brain.tools.registry import REGISTRY

    ctx = RequestCtx(
        org_id=org_id,
        user_id=int(user_id) if str(user_id).isdigit() else 18,
        session_state=session_state,
        feedback=feedback,
    )
    tool_res = route_with_gemini(query, call_gemini, ctx=ctx)

    if tool_res.name == "answer_directly":
        return {
            "tool_name": "answer_directly",
            "endpoint": "",
            "intent": 1,
            "query_params": {},
            "path_params": {},
        }, tool_res.tokens_in, tool_res.tokens_out

    if tool_res.name == "unsupported" or tool_res.name not in REGISTRY:
        return None, tool_res.tokens_in, tool_res.tokens_out

    spec = REGISTRY[tool_res.name]
    try:
        p_obj = spec.params(**tool_res.params)
    except Exception:
        try:
            p_obj = spec.params()
        except Exception:
            p_obj = None

    q_params: Dict[str, Any] = {}
    p_params: Dict[str, Any] = {}
    if p_obj is not None:
        if hasattr(p_obj, "to_query"):
            q_params = p_obj.to_query(ctx)
        if hasattr(p_obj, "to_path_params"):
            p_params = p_obj.to_path_params(ctx)
    else:
        q_params = {"organization_id": org_id}

    sel = {
        "endpoint": spec.endpoint,
        "method": "GET",
        "path_params": p_params,
        "query_params": q_params,
        "tool_name": spec.name,
        "intent": spec.intent,
        "narrate": spec.narrate,
        "formatter": spec.formatter,
    }
    return sel, tool_res.tokens_in, tool_res.tokens_out


