"""
preview.py — Answer a chat with the agent, as "Accutax AI".

Every chat is answered here (the module keeps its name from when the agent was a
preview beside the old answer path). The turn is saved to the thread, so the
thread shows it and follow-ups have context.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from gemini_brain.agent import answer_log, links, tiles
from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.agent.preview")

MODEL_KEY = "accutax-agent"
PRIMARY_LABEL = "Accutax AI"

_STEP = {
    "query_metrics": "Fetching figures",
    "list_documents": "Listing documents",
    "cash_forecast": "Projecting cash",
    "search_vat_kb": "Searching UAE VAT law",
    "app_guide": "Checking the Accutax guide",
}


def catalog_entry() -> Dict[str, Any]:
    """The one model GET /models lists, in the shape of api.models.ModelInfo."""
    return {
        "key": MODEL_KEY,
        "label": PRIMARY_LABEL,
        "description": ("Answers on governed figures (Cube): ledger P&L, balance sheet, general ledger, invoices, "
                        "bills, VAT, inventory, bank, cash forecast, files and charts, plus UAE VAT law."),
        "provider": "bedrock",
        "supports": ["tools"],
        "efforts": [],
        "latency_class": "standard",
        "primary": True,
        "available": True,
    }


def step_label(name: str, params: Any) -> str:
    """A short status line for one tool call."""
    label = _STEP.get(name, "Working")
    if isinstance(params, dict):
        what = params.get("view") or params.get("type")
        if what:
            label += f": {str(what).replace('_', ' ')}"
    return label + "…"


def answer(question: str, organization_ids: Sequence[int], org_meta: Callable[[], Dict[int, Dict[str, Any]]], *,
           session_id: Optional[str], user_id: int, db_name: str = "",
           progress: Optional[Callable[[str], None]] = None, brief: bool = False) -> Dict[str, Any]:
    """Answer with the agent and save the turn. Returns a QueryResponse-shaped dict. Never raises."""
    from gemini_brain.agent.loop import run_agent

    started = time.monotonic()
    orgs = [int(o) for o in organization_ids]
    thread = thread_context(session_id, orgs, user_id, db_name)
    try:
        meta = org_meta() or {}
    except Exception as e:  # noqa: BLE001 - names only label the answer
        logger.warning("agent preview: organization names unavailable: %s", e)
        meta = {}

    on_tool = (lambda name, params: progress(step_label(name, params))) if progress else None
    delivery = delivery_for(question, thread)
    attached = attachment(question, delivery)
    rid, traces = _start_traces()
    try:
        result = run_agent(question, orgs, meta, history=thread.messages, turn_specs=thread.specs,
                           earlier_figures=thread.figures, subject=f"agent-preview:{user_id}", progress=on_tool,
                           brief=brief, attachment=attached)
    finally:
        traces = _collect_traces(rid)
    data = getattr(result, "data", None) or []
    answer_text = result.answer or "I could not produce an answer for that request. Please try again."
    answer_text = links.link_documents(answer_text, links.document_links(data))
    blocks = tiles.kpi_tiles(data) + list(getattr(result, "blocks", None) or []) + links.guide_buttons(data)
    blocks += _files(question, result, orgs, meta, user_id=user_id, session_id=session_id, answer_text=answer_text,
                     delivery=delivery)
    if session_id:
        if _save_turn(session_id, user_id, orgs, question, answer_text, db_name, blocks):
            record_turn(session_id, question, result.tool_calls, delivery, data, db_name)

    usage = dict(result.usage or {})
    usage["elapsed_seconds"] = round(time.monotonic() - started, 2)
    status, notice = _status_and_notice(result)
    response = {
        "answer": answer_text,
        "status": status,
        "notice": notice,
        "organizations": _org_chips(orgs, meta, figures_failed=bool(notice and notice["code"] == UNAVAILABLE)),
        "context_window": _context_window(session_id, usage, db_name),
        "token_usage": usage,
        "routing_info": {
            "type": 6 if result.route == "law" else 1,
            "type_label": "UAE VAT law (knowledge base)" if result.route == "law" else "Figures (governed metrics)",
            "path": f"agent_{result.route}",
            "reason": f"{PRIMARY_LABEL}: {len(result.tool_calls)} tool call(s)",
            "bedrock_model": settings.agent_model_id or None,
        },
        "policy": {"model": MODEL_KEY, "model_label": PRIMARY_LABEL, "auto": False},
        "verification": result.verification or None,
        "blocks": blocks or None,
        "api_traces": traces.get("api", []),
        "llm_traces": traces.get("llm", []),
        "agent_trace": [{"step": "tool", **{k: v for k, v in call.items() if k in ("name", "input", "ok", "ms", "rows", "error")}}
                        for call in result.tool_calls],
    }
    answer_log.record(question, orgs, user_id=user_id, session_id=session_id, brief=brief, attachment=attached,
                      result=result, response=response)
    return response


UNAVAILABLE = "UPSTREAM_UNAVAILABLE"


def _status_and_notice(result: Any) -> tuple:
    """The response status and the notice card for it, as the current path shows them:
    a timeout, a failed answer, or figures Cube could not return."""
    from gemini_brain.agent.tools import FIGURES_UNAVAILABLE
    from gemini_brain.resilience.envelope import build_notice
    if result.status == "deadline":
        return "degraded", build_notice("UPSTREAM_TIMEOUT", subject="your figures")
    if result.status != "ok":
        return "failed", build_notice("MODEL_UNAVAILABLE", subject="your answer")
    if any(call.get("error") == FIGURES_UNAVAILABLE for call in result.tool_calls):
        return "partial", build_notice(UNAVAILABLE, subject="your figures")
    return "ok", None


def _org_chips(orgs: List[int], meta: Dict[int, Dict[str, Any]], *, figures_failed: bool) -> List[Dict[str, Any]]:
    """The organizations a multi-organization answer covers, for the chips above it."""
    if len(orgs) < 2:
        return []
    return [{"id": o, "name": (meta.get(o) or {}).get("name") or f"Organization {o}",
             "currency": (meta.get(o) or {}).get("currency") or "", "status": "failed" if figures_failed else "ok"}
            for o in orgs]


def _context_window(session_id: Optional[str], usage: Dict[str, Any], db_name: str) -> Optional[Dict[str, Any]]:
    """Set the chat's context-window meter to how full the model's context was on this answer:
    the largest single model call (its whole prompt, cached or not, and its output)."""
    if not session_id:
        return None
    try:
        from gemini_brain.memory.context_window import set_context_window_usage
        held = usage.get("context_tokens")
        if held is None:  # an older adapter: the uncached and cached prompt plus the output, over the calls
            held = sum(int(usage.get(k) or 0) for k in
                       ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"))
            held //= max(1, int(usage.get("llm_calls") or 1))
        return set_context_window_usage(session_id, int(held), db_name=db_name)
    except Exception as e:  # noqa: BLE001 - the meter is a convenience
        logger.warning("agent preview: context-window meter not updated: %s", e)
        return None


def _start_traces() -> tuple:
    """Collect this answer's Cube calls (API trace card) and model calls (LLM trace card), as the other paths do."""
    import uuid
    rid = str(uuid.uuid4())
    try:
        from gemini_brain.observability.api_tracer import start_api_trace
        from gemini_brain.observability.llm_tracer import start_llm_trace
        start_api_trace(trace_id=rid)
        start_llm_trace(trace_id=rid)
    except Exception as e:  # noqa: BLE001 - traces are diagnostics only
        logger.debug("agent preview: traces not started: %s", e)
    return rid, {}


def _collect_traces(rid: str) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    try:
        from gemini_brain.observability.api_tracer import clear_api_trace, get_api_traces
        from gemini_brain.observability.llm_tracer import clear_llm_trace, get_llm_traces
        out = {"api": get_api_traces(trace_id=rid), "llm": get_llm_traces(trace_id=rid)}
        clear_api_trace(trace_id=rid)
        clear_llm_trace(trace_id=rid)
    except Exception as e:  # noqa: BLE001
        logger.debug("agent preview: traces not collected: %s", e)
    return out


def attachment(question: str, delivery: Any = None) -> Optional[str]:
    """What _files will attach ("chart", "XLSX file", ...), so the model is told the truth about it.
    `delivery`: the one delivery_for chose; else the question alone decides."""
    if delivery is None:
        delivery = delivery_for(question)
    if delivery is None or delivery.mode == "none":
        return None
    if delivery.wants_file and delivery.format:
        return f"{delivery.format.upper()} file" + (" and chart" if delivery.wants_chart else "")
    return "chart" if delivery.wants_chart else "file"


def export_rows(data: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The rows a file or chart is built from: the figure-tool result with the most rows, with plain column names."""
    best: List[Dict[str, Any]] = []
    for item in data or []:
        result = item.get("result") or {}
        if item.get("tool") == "cash_forecast":
            rows = [{"organization": org.get("organization_name"), "currency": org.get("currency"), **week}
                    for org in result.get("organizations") or [] for week in org.get("weeks") or []]
        else:
            rows = [_plain(r) for r in result.get("rows") or []]
            granularity = (item.get("input") or {}).get("granularity")
            for row in rows:
                if granularity in row:
                    row[granularity] = period_label(row[granularity], granularity)
        if rows and len(rows) >= len(best):
            best = rows
    return best


def period_label(value: Any, granularity: str) -> Any:
    """A time bucket as people write it: 2026-03-01 by month is "Mar 2026", by quarter "Q1 2026"."""
    import datetime as dt
    try:
        day = dt.date.fromisoformat(str(value)[:10])
    except ValueError:
        return value
    if granularity == "month":
        return day.strftime("%b %Y")
    if granularity == "quarter":
        return f"Q{(day.month - 1) // 3 + 1} {day.year}"
    if granularity == "year":
        return str(day.year)
    if granularity == "week":
        return "Week of " + day.strftime("%d %b %Y")
    return day.isoformat()


def _plain(row: Dict[str, Any]) -> Dict[str, Any]:
    """'pnl.revenue' -> 'revenue', 'pnl.transaction_date.month' -> 'month'; the organization id is dropped."""
    out: Dict[str, Any] = {}
    for key, value in row.items():
        name = str(key).split(".")[-1]
        if name != "organization_id":
            out[name] = value
    return out


def _files(question: str, result: Any, orgs: List[int], meta: Dict[int, Dict[str, Any]], *, user_id: int,
           session_id: Optional[str], answer_text: str, delivery: Any = None) -> List[Dict[str, Any]]:
    """Chart, canvas and download blocks when the user asked for a file or a chart; [] otherwise. Never raises."""
    try:
        delivery = delivery if delivery is not None else delivery_for(question)
        if delivery is None or delivery.mode == "none":
            return []
        from gemini_brain.artifacts.attach import attach_delivery
        rows = export_rows(getattr(result, "data", None) or [])
        single = orgs[0] if len(orgs) == 1 else None
        return attach_delivery(
            question, rows, [], status="ok" if rows else "empty", user_id=user_id, organization_id=single,
            session_id=session_id, org_name=(meta.get(single) or {}).get("name") if single else None,
            answer_text=answer_text, delivery=delivery,
        )
    except Exception as e:  # noqa: BLE001 - the answer still reaches the user without the file
        logger.warning("agent preview: file not built: %s", e, exc_info=True)
        return []


@dataclass
class Thread:
    """What the agent may use from the chat thread: earlier messages, what its turns queried, the previous
    turn's chart or file request, and figures earlier turns fetched (for the verifier)."""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    specs: List[Dict[str, Any]] = field(default_factory=list)
    delivery: Optional[Dict[str, Any]] = None
    figures: List[float] = field(default_factory=list)


def thread_context(session_id: Optional[str], organization_ids: Sequence[int], user_id: int,
                   db_name: str = "") -> Thread:
    """The thread's context for this request; empty for a new chat. A thread owned by another user, or kept
    for another set of organizations, gives nothing: its history and figures never reach this answer."""
    if not session_id:
        return Thread()
    from gemini_brain.agent import history
    from gemini_brain.memory.session_memory import get_history_by_session, get_session_record, session_scope
    try:
        record = get_session_record(session_id, db_name=db_name)
    except Exception as e:  # noqa: BLE001 - answer without history rather than fail
        logger.warning("agent preview: thread %s not read: %s", session_id, e)
        return Thread()
    if not record:
        return Thread()
    if record.get("user_id") is not None and int(record["user_id"]) != int(user_id):
        logger.warning("agent preview: thread %s belongs to another user; its history is not used", session_id)
        return Thread()
    stored = session_scope(record)
    if stored and stored != sorted(int(o) for o in organization_ids):
        logger.warning("agent preview: thread %s is kept for organizations %s, not %s; its history is not used",
                       session_id, stored, sorted(organization_ids))
        return Thread()
    state = record.get("conversation_state") or {}
    try:
        messages = get_history_by_session(session_id, limit=history.FETCH_MESSAGES, db_name=db_name)
    except Exception as e:  # noqa: BLE001
        logger.warning("agent preview: no history for %s: %s", session_id, e)
        messages = []
    return Thread(messages=list(messages or []), specs=list(state.get(history.STATE_KEY) or []),
                  delivery=state.get(history.DELIVERY_KEY), figures=history.earlier_figures(state))


def record_turn(session_id: str, question: str, tool_calls: Sequence[Dict[str, Any]], delivery: Any = None,
                data: Optional[Sequence[Dict[str, Any]]] = None, db_name: str = "") -> None:
    """Save what this turn queried, its chart or file request and the figures it fetched, for later turns."""
    try:
        from gemini_brain.agent import history
        from gemini_brain.memory.session_memory import get_state_by_session, update_state_by_session
        state = dict(get_state_by_session(session_id, db_name=db_name) or {})
        state = history.add_spec(state, question, tool_calls)
        state = history.add_figures(state, data or [])
        state[history.DELIVERY_KEY] = _delivery_state(delivery)
        update_state_by_session(session_id, state, db_name=db_name)
    except Exception as e:  # noqa: BLE001 - later turns only lose some context
        logger.warning("agent preview: turn context not saved for %s: %s", session_id, e)


def delivery_for(question: str, thread: Optional[Thread] = None) -> Any:
    """The chart or file this answer gets: the one the question names, or the previous turn's for a
    follow-up that reshapes it ("add net profit to it"). None when the check itself fails."""
    try:
        from gemini_brain.artifacts.delivery import carry_delivery
        return carry_delivery(question, thread.delivery if thread else None)
    except Exception as e:  # noqa: BLE001
        logger.debug("agent preview: delivery not detected: %s", e)
        return None


def _delivery_state(delivery: Any) -> Optional[Dict[str, Any]]:
    if delivery is None or getattr(delivery, "mode", "none") == "none":
        return None
    from gemini_brain.artifacts.delivery import as_state
    return as_state(delivery)


def _save_turn(session_id: str, user_id: int, orgs: List[int], question: str, text: str, db_name: str,
               blocks: Optional[List[Dict[str, Any]]] = None) -> bool:
    """Save the question and answer to the thread. False when the turn was not saved."""
    try:
        from gemini_brain.memory.session_memory import ensure_session, save_message_by_session
        scope = {"organization_ids": orgs} if len(orgs) > 1 else {"organization_id": orgs[0] if orgs else None}
        if not ensure_session(session_id, user_id, db_name=db_name, **scope):
            logger.warning("agent preview: session %s refused this scope; turn not saved", session_id)
            return False
        save_message_by_session(session_id, "user", question, db_name=db_name)
        save_message_by_session(session_id, "assistant", text, db_name=db_name)
        if blocks:
            from gemini_brain.memory.session_memory import update_last_assistant_blocks
            update_last_assistant_blocks(session_id, blocks, db_name=db_name)
        return True
    except Exception as e:  # noqa: BLE001 - the answer still reaches the user
        logger.warning("agent preview: could not save turn to %s: %s", session_id, e)
        return False
