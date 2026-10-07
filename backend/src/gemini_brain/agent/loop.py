"""
loop.py — The bounded tool-using agent (Phase 2, review section 6.3).

One model plans, calls tools and writes the answer. Hard limits: a number of
tool calls and a deadline for the whole question. Figures come only from tool
results; the verifier checks the answer against them (report only for now).
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.agent import tools
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
#: Sent once when an answer comes back cut off or empty (usually a table too long for the answer).
#: With less than this many seconds or no tool calls left, the model is told to answer now.
WRAP_UP_SECONDS = 15.0
WRAP_UP = "Time or tool budget is nearly used up. Do not call more tools: answer now from the results you have."
SHORTER = ("Your answer was cut off or empty. Answer again in under 250 words from the tool results you have: "
           "summarise long series per organization (total, best and worst period) instead of a full table.")
_NUMBER_IN_TEXT = re.compile(r"\d[\d,]*(?:\.\d+)?")


@dataclass
class AgentResult:
    answer: str
    status: str                                   # "ok" | "deadline" | "error"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    verification: Dict[str, Any] = field(default_factory=dict)
    usage: Dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


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


def _evidence(results: Sequence[Dict[str, Any]]) -> List[Any]:
    """What the verifier may match figures against: the results, their magnitudes (the verifier reads
    "-143,160" in prose as 143160), and numbers quoted inside text results."""
    evidence: List[Any] = list(results)
    values: List[float] = []
    _numbers(list(results), values)
    evidence.append([abs(v) for v in values if v < 0])
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
    max_tool_calls: Optional[int] = None,
    deadline_seconds: Optional[float] = None,
) -> AgentResult:
    """Answer one question with tools. Never raises."""
    started = time.monotonic()
    deadline = started + (deadline_seconds or settings.agent_deadline_seconds)
    budget = max_tool_calls or settings.agent_max_tool_calls
    orgs = [int(o) for o in organization_ids]
    ctx = tools.ToolContext(organization_ids=orgs, subject=subject, deadline=deadline)
    adapter = BedrockAdapter(model_id or settings.agent_model_id or SONNET5_ID, label="agent")
    calls: List[Dict[str, Any]] = []
    ok_results: List[Dict[str, Any]] = []
    answer, status = "", "ok"

    try:
        today = periods.today_in(settings.report_timezone)
        system = system_prompt(org_meta, orgs, today, settings.report_timezone)
        specs = tools.specs(ctx)
        messages = _messages(history, redact_pii(question)[0])
        used, asked_shorter = 0, False
        while True:
            if deadline - time.monotonic() < MIN_SECONDS_FOR_MODEL_CALL:
                status, answer = "deadline", OUT_OF_TIME
                break
            resp = adapter.converse_with_tools(system, messages, specs, max_tokens=MAX_ANSWER_TOKENS, purpose="agent")
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
            results = []
            for use in uses:
                t0 = time.monotonic()
                if used >= budget:
                    result, ok = {"error": BUDGET_USED}, False
                else:
                    used += 1
                    result, ok = tools.execute(use.get("name", ""), use.get("input"), ctx)
                calls.append({"name": use.get("name"), "input": use.get("input"), "ok": ok,
                              "ms": int((time.monotonic() - t0) * 1000), "rows": result.get("row_count"),
                              **({"error": result.get("error")} if not ok else {})})
                if ok:
                    ok_results.append(result)
                results.append({"toolResult": {"toolUseId": use["toolUseId"], "content": [{"json": result}],
                                               "status": "success" if ok else "error"}})
            if used >= budget or deadline - time.monotonic() < WRAP_UP_SECONDS:
                results.append({"text": WRAP_UP})
            messages.append({"role": "user", "content": results})
    except Exception as e:  # noqa: BLE001 - the agent must never break the request it shadows
        logger.warning("agent failed: %s", e, exc_info=True)
        status, answer = "error", answer or ""

    report = verify_answer(answer, _evidence(ok_results), enforce=False) if answer and status == "ok" else None
    return AgentResult(
        answer=answer,
        status=status,
        tool_calls=calls,
        verification=report.to_public() if report else {},
        usage=adapter.get_token_usage(),
        elapsed_ms=int((time.monotonic() - started) * 1000),
    )
