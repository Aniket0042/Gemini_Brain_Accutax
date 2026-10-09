"""
loop.py — The bounded tool-using agent (Phase 2, review section 6.3).

UAE VAT law questions go to the knowledge-base answer (law.py). Everything
else goes through the tool loop: the planner model chooses tools, and the
answer model writes from their results (AGENT_ANSWER_MODEL_ID; the planner when
unset). Hard limits: a number of tool calls and a deadline for the whole
question. Tool calls from one turn run in parallel. The system prompt and tool
definitions are marked for Bedrock prompt caching. Figures come only from tool
results; the verifier checks the answer against them (report only for now).
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from gemini_brain.agent import law, tools
from gemini_brain.agent.prompt import system_prompt
from gemini_brain.config.constants import SONNET5_ID
from gemini_brain.config.settings import settings
from gemini_brain.pii.redactor import redact_pii
from gemini_brain.policy.verifier import verify_answer
from gemini_brain.reasoning.bedrock_client import BedrockAdapter
from gemini_brain.semantic import periods

logger = logging.getLogger("gemini_brain.agent.loop")

MAX_HISTORY_MESSAGES = 6
MAX_ANSWER_TOKENS = 3000
#: Below this many seconds left, another model call would not finish in time.
MIN_SECONDS_FOR_MODEL_CALL = 4.0
BUDGET_USED = ("Tool budget used. Answer now with the figures you already have, "
               "and say plainly which part could not be answered.")
OUT_OF_TIME = ("I could not finish this within the time limit. Please ask again, "
               "or narrow the question (fewer organizations or a shorter period).")
#: With less than this many seconds or no tool calls left, the model is told to answer now.
WRAP_UP_SECONDS = 15.0
WRAP_UP = "Time or tool budget is nearly used up. Do not call more tools: answer now from the results you have."
#: Sent once when an answer comes back cut off or empty (usually a table too long for the answer).
SHORTER = ("Your answer was cut off or empty. Answer again in under 250 words from the tool results you have: "
           "summarise long series per organization (total, best and worst period) instead of a full table.")
_NUMBER_IN_TEXT = re.compile(r"\d[\d,]*(?:\.\d+)?")
_DIGITS = re.compile(r"\d+")
_IDENTIFIER = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[\w./#-]+$")
_CACHE_POINT = {"cachePoint": {"type": "default"}}
#: Turned off for the process if Bedrock ever refuses a cache point.
_cache = {"on": True}


@dataclass
class AgentResult:
    answer: str
    status: str                                   # "ok" | "deadline" | "error"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    route: str = "tools"                          # "tools" | "law"
    #: Successful figure-tool and app_guide results, in call order: {"tool", "input", "result"}.
    #: Used to build files and links into the app.
    data: List[Dict[str, Any]] = field(default_factory=list)
    #: Blocks shown with the answer; the law route's FTA sources (citation chips and source list).
    blocks: List[Dict[str, Any]] = field(default_factory=list)


def _messages(history: Optional[Sequence[Dict[str, Any]]], question: str) -> List[Dict[str, Any]]:
    """Bedrock messages: earlier turns (redacted, roles alternating, starting with the user), then the question."""
    out: List[Dict[str, Any]] = []
    for m in list(history or [])[-MAX_HISTORY_MESSAGES:]:
        role = "assistant" if str(m.get("role")).lower() == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if not text or (not out and role == "assistant"):
            continue
        if role == "user":
            text = redact_pii(text)[0]
        if out and out[-1]["role"] == role:
            out[-1]["content"][0]["text"] += "\n\n" + text
        else:
            out.append({"role": role, "content": [{"text": text}]})
    if out and out[-1]["role"] == "user":
        out.append({"role": "assistant", "content": [{"text": "(no answer)"}]})
    out.append({"role": "user", "content": [{"text": question}]})
    return out


def _cached(system: str, specs: List[Dict[str, Any]]) -> tuple:
    if not _cache["on"]:
        return [{"text": system}], specs
    return [{"text": system}, _CACHE_POINT], specs + [_CACHE_POINT]


def _converse(adapter: Any, system: str, messages: List[Dict[str, Any]], specs: List[Dict[str, Any]]) -> Dict[str, Any]:
    sys_blocks, tool_list = _cached(system, specs)
    try:
        return adapter.converse_with_tools(sys_blocks, messages, tool_list, max_tokens=MAX_ANSWER_TOKENS, purpose="agent")
    except Exception as e:
        if not _cache["on"] or "cache" not in str(e).lower():
            raise
        logger.warning("agent: prompt caching refused, continuing without it: %s", e)
        _cache["on"] = False
        return adapter.converse_with_tools([{"text": system}], messages, specs,
                                           max_tokens=MAX_ANSWER_TOKENS, purpose="agent")


def _usage(*adapters: Any) -> Dict[str, Any]:
    """Token counts and cost summed over the adapters used (the same adapter counted once)."""
    total: Dict[str, Any] = {}
    for adapter in {id(a): a for a in adapters}.values():
        for key, value in adapter.get_token_usage().items():
            if isinstance(value, (int, float)):
                total[key] = round(total.get(key, 0) + value, 6)
    return total


def _text_of(message: Dict[str, Any]) -> str:
    return "\n".join(b["text"] for b in message.get("content") or [] if "text" in b).strip()


def _numbers(payload: Any, out: List[float], depth: int = 0) -> None:
    if depth > 8 or isinstance(payload, bool):
        return
    if isinstance(payload, (int, float)):
        out.append(float(payload))
    elif isinstance(payload, str):
        try:
            out.append(float(payload.replace(",", "")))
        except ValueError:
            pass
    elif isinstance(payload, dict):
        for v in payload.values():
            _numbers(v, out, depth + 1)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _numbers(v, out, depth + 1)


def _identifier_digits(payload: Any, out: List[str], depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(payload, str):
        # Only identifiers: letters with digits ("INV-0012", "JE-24-2026-00000773"); dates and plain numbers are not.
        if _IDENTIFIER.match(payload):
            out.extend(_DIGITS.findall(payload))
    elif isinstance(payload, dict):
        for v in payload.values():
            _identifier_digits(v, out, depth + 1)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _identifier_digits(v, out, depth + 1)


def _evidence(results: Sequence[Dict[str, Any]]) -> List[Any]:
    """What the verifier may match figures against: the results, their magnitudes (the verifier reads
    "-143,160" in prose as 143160), and numbers quoted inside text results."""
    evidence: List[Any] = list(results)
    values: List[float] = []
    _numbers(list(results), values)
    evidence.append([abs(v) for v in values if v < 0])
    # Digits inside identifiers ("INV-BULK-26-20241216180347-114231", "JE-24-2026-00000773") are part of the
    # document number the answer quotes, not figures; let the verifier match them.
    identifiers: List[str] = []
    _identifier_digits(list(results), identifiers)
    evidence.append(identifiers)
    for r in results:
        for key in ("sources_and_rules", "guide"):
            if isinstance(r.get(key), str):
                evidence.append([n.replace(",", "") for n in _NUMBER_IN_TEXT.findall(r[key])])
    return evidence


def run_agent(
    question: str,
    organization_ids: Sequence[int],
    org_meta: Dict[int, Dict[str, Any]],
    *,
    history: Optional[Sequence[Dict[str, Any]]] = None,
    subject: str = "agent",
    model_id: Optional[str] = None,
    answer_model_id: Optional[str] = None,
    max_tool_calls: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
    progress: Optional[Callable[[str, Any], None]] = None,
    brief: bool = False,
    attachment: Optional[str] = None,
) -> AgentResult:
    """Answer one question. Never raises. `progress(tool_name, tool_input)` is called before each tool runs."""
    started = time.monotonic()
    deadline = started + (deadline_seconds or settings.agent_deadline_seconds)
    budget = max_tool_calls or settings.agent_max_tool_calls
    orgs = [int(o) for o in organization_ids]
    ctx = tools.ToolContext(organization_ids=orgs, subject=subject, deadline=deadline)
    planner_id = model_id or settings.agent_model_id or SONNET5_ID
    writer_id = answer_model_id or settings.agent_answer_model_id or planner_id
    planner = BedrockAdapter(planner_id, label="agent")
    writer = planner if writer_id == planner_id else BedrockAdapter(writer_id, label="agent-answer")
    calls: List[Dict[str, Any]] = []
    ok_results: List[Dict[str, Any]] = []
    data: List[Dict[str, Any]] = []
    answer, status = "", "ok"

    try:
        messages = _messages(history, redact_pii(question)[0])
        law_answer = law.answer(question, [dict(m) for m in messages], brief=brief)
        if law_answer is not None:
            return AgentResult(answer=law_answer.answer, status="ok", usage=law_answer.usage, route="law",
                               elapsed_ms=int((time.monotonic() - started) * 1000),
                               blocks=list(law_answer.blocks or []),
                               verification=_law_verification(law_answer))

        today = periods.today_in(settings.report_timezone)
        system = system_prompt(org_meta, orgs, today, settings.report_timezone, brief=brief, attachment=attachment)
        specs = tools.specs(ctx)
        used, asked_shorter, adapter = 0, False, planner
        while True:
            if deadline - time.monotonic() < MIN_SECONDS_FOR_MODEL_CALL:
                status, answer = "deadline", OUT_OF_TIME
                break
            resp = _converse(adapter, system, messages, specs)
            message = (resp.get("output") or {}).get("message") or {"role": "assistant", "content": []}
            messages.append(message)
            uses = [b["toolUse"] for b in message.get("content") or [] if "toolUse" in b]
            if resp.get("stopReason") != "tool_use" or not uses:
                answer = _text_of(message)
                cut_off = resp.get("stopReason") == "max_tokens" or not answer
                if cut_off and not asked_shorter:
                    asked_shorter = True
                    if not message.get("content"):
                        message["content"] = [{"text": "(empty)"}]
                    messages.append({"role": "user", "content": [{"text": SHORTER}]})
                    continue
                break
            allowed = uses[:max(0, budget - used)]
            used += len(allowed)
            for use in allowed:
                _notify(progress, use)
            outcomes = _run_tools(allowed, ctx)
            results = []
            for use in uses:
                result, ok, ms = outcomes.get(use["toolUseId"], ({"error": BUDGET_USED}, False, 0))
                calls.append({"name": use.get("name"), "input": use.get("input"), "ok": ok, "ms": ms,
                              "rows": result.get("row_count"), **({"error": result.get("error")} if not ok else {})})
                if ok:
                    ok_results.append(result)
                    if use.get("name") in tools.DATA_TOOLS or use.get("name") == "app_guide":
                        data.append({"tool": use.get("name"), "input": use.get("input"), "result": result})
                results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": [{"json": result}],
                                               "status": "success" if ok else "error"}})
            if used >= budget or deadline - time.monotonic() < WRAP_UP_SECONDS:
                results.append({"text": WRAP_UP})
            messages.append({"role": "user", "content": results})
            adapter = writer
    except Exception as e:  # noqa: BLE001 - the agent must never break the request it shadows
        logger.warning("agent failed: %s", e, exc_info=True)
        status, answer = "error", answer or ""

    report = verify_answer(answer, _evidence(ok_results), enforce=False) if answer and status == "ok" else None
    return AgentResult(
        answer=answer,
        status=status,
        tool_calls=calls,
        verification=report.to_public() if report else {},
        usage=_usage(planner, writer),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        data=data,
    )


def _law_verification(law_answer: Any) -> Dict[str, Any]:
    """Check the figures in a law answer (rates, thresholds, penalties) against the cited FTA passages,
    as the current path does. Report only."""
    numbers = [n for b in law_answer.blocks or [] if isinstance(b, dict) and b.get("type") == "fta_sources"
               for n in b.get("evidence_numbers") or []]
    if not law_answer.answer:
        return {}
    try:
        return verify_answer(law_answer.answer, [numbers], enforce=False).to_public()
    except Exception as e:  # noqa: BLE001 - verification is a report; the answer still goes out
        logger.warning("agent: law answer not verified: %s", e)
        return {}


def _notify(progress: Optional[Callable[[str, Any], None]], use: Dict[str, Any]) -> None:
    if progress is None:
        return
    try:
        progress(use.get("name", ""), use.get("input"))
    except Exception as e:  # noqa: BLE001 - a status line must never break the answer
        logger.debug("agent progress callback failed: %s", e)


def _run_tools(uses: Sequence[Dict[str, Any]], ctx: tools.ToolContext) -> Dict[str, tuple]:
    """Run one turn's tool calls at the same time. toolUseId -> (result, ok, ms)."""
    def one(use: Dict[str, Any]) -> tuple:
        t0 = time.monotonic()
        result, ok = tools.execute(use.get("name", ""), use.get("input"), ctx)
        return result, ok, int((time.monotonic() - t0) * 1000)

    if len(uses) <= 1:
        return {u["toolUseId"]: one(u) for u in uses}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(uses), thread_name_prefix="agent-call") as pool:
        # Each call runs in a copy of this context, so the request's trace collectors see it.
        futures = {u["toolUseId"]: pool.submit(contextvars.copy_context().run, one, u) for u in uses}
        return {uid: f.result() for uid, f in futures.items()}
