"""
history.py — Which earlier turns of a chat the agent sees (plan docs/CONTEXT_MODELS_REPORTS_PLAN.md, step 1.2).

The newest turns are sent whole, up to a token budget. When the thread is longer, the oldest turns are
replaced by a short summary built in code (no model call): the question of each turn and the queries it
ran (view, measures, period, filters, organizations), never its figures. A figure the user asks about
again is fetched again, so a summary can never put a wrong number into an answer.

What each turn queried is saved in the session's conversation_state ("turn_specs") when the turn is
saved; older threads without it get a summary of their questions only. The same state keeps the previous
turn's chart or file request (step 1.3) and the figures the last few turns fetched (step 1.5).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

#: Messages read from the thread; far more than the budget usually holds.
FETCH_MESSAGES = 80
#: Earlier turns sent whole, in estimated tokens (docs/USAGE_LIMITS_DESIGN.md section 3).
TOKEN_BUDGET = 24_000
#: When the whole thread does not fit, the turns sent whole fill at most this share of the budget,
#: leaving room for the summary of the older ones.
KEEP_SHARE = 0.8
SUMMARY_TOKENS = 1_500
#: Turn specs kept in conversation_state.
MAX_SPECS = 60
STATE_KEY = "turn_specs"
QUESTION_CHARS = 200
#: The previous turn's chart or file request (artifacts.delivery.as_state), for follow-ups.
DELIVERY_KEY = "last_delivery"
#: Figures the last few turns fetched, for the verifier when an answer reuses them (plan step 1.5).
FIGURES_KEY = "recent_figures"
FIGURE_TURNS = 3
MAX_FIGURES_PER_TURN = 400

SUMMARY_HEAD = ("Earlier in this chat (older turns, summarized; their figures are not kept — fetch them again "
                "if they are needed):")


def estimate_tokens(text: str) -> int:
    """A cautious estimate: answers are dense with figures and table markup, about 3 characters a token."""
    return len(text or "") // 3 + 1


def turns(history: Sequence[Dict[str, Any]]) -> List[List[Dict[str, str]]]:
    """Earlier messages grouped into turns, each starting with a user message (leading answers dropped)."""
    out: List[List[Dict[str, str]]] = []
    for m in history or []:
        role = "assistant" if str(m.get("role")).lower() == "assistant" else "user"
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if role == "user" and (not out or out[-1][-1]["role"] == "assistant"):
            out.append([])
        if not out:  # an answer before any question
            continue
        out[-1].append({"role": role, "content": text})
    return out


def select(history: Sequence[Dict[str, Any]], specs: Optional[Sequence[Dict[str, Any]]] = None,
           budget: int = TOKEN_BUDGET) -> Tuple[Optional[str], List[Dict[str, str]]]:
    """(summary of the older turns or None, the newest whole turns as messages)."""
    all_turns = turns(history)
    sizes = [sum(estimate_tokens(m["content"]) for m in t) for t in all_turns]
    if sum(sizes) <= budget:
        return None, [m for t in all_turns for m in t]
    kept, used = 0, 0
    for size in reversed(sizes):
        if kept and used + size > budget * KEEP_SHARE:
            break
        kept, used = kept + 1, used + size
    older = all_turns[:len(all_turns) - kept]
    newest = all_turns[len(all_turns) - kept:]
    return summary(older, specs), [m for t in newest for m in t]


def summary(older: Sequence[Sequence[Dict[str, str]]], specs: Optional[Sequence[Dict[str, Any]]] = None) -> Optional[str]:
    """One line per older turn: its question and what it queried. No figures from the answers."""
    if not older:
        return None
    by_question = {}
    for spec in specs or []:
        by_question[_short(spec.get("q"))] = spec.get("calls") or []
    lines = []
    for n, turn in enumerate(older, 1):
        question = _short(" ".join(m["content"] for m in turn if m["role"] == "user"))
        calls = by_question.get(question) or []
        lines.append(f"{n}. Asked: {question}" + (f" — queried: {'; '.join(calls)}" if calls else ""))
    # Over the cap, drop the oldest lines first and say so.
    dropped = 0
    while len(lines) > 1 and estimate_tokens("\n".join(lines)) > SUMMARY_TOKENS:
        lines.pop(0)
        dropped += 1
    head = SUMMARY_HEAD + (f"\n({dropped} even older turns not shown.)" if dropped else "")
    return head + "\n" + "\n".join(lines)


def describe_calls(calls: Sequence[Dict[str, Any]]) -> List[str]:
    """What a turn's successful tool calls queried, without results: 'query_metrics pnl: revenue; period last_quarter'."""
    out = []
    for call in calls or []:
        if not call.get("ok"):
            continue
        given = call.get("input") or {}
        what = given.get("view") or given.get("type") or ""
        parts = [f"{call.get('name')} {what}".strip()]
        if given.get("measures"):
            parts.append(", ".join(_member(m) for m in given["measures"]))
        period = given.get("period") or {}
        if isinstance(period, dict) and (period.get("preset") or period.get("start")):
            parts.append("period " + (period.get("preset") or f"{period.get('start')} to {period.get('end')}"))
        if given.get("as_of"):
            parts.append(f"as of {given['as_of']}")
        if given.get("group_by"):
            parts.append("by " + ", ".join(_member(g) for g in given["group_by"]))
        if given.get("granularity"):
            parts.append(f"by {given['granularity']}")
        for f in given.get("filters") or []:
            if isinstance(f, dict):
                values = ", ".join(str(v) for v in f.get("values") or [])
                parts.append(f"where {_member(f.get('member'))} {f.get('operator')} {values}".strip())
        if given.get("organization_ids"):
            parts.append("organizations " + ", ".join(str(o) for o in given["organization_ids"]))
        if given.get("limit"):
            parts.append(f"limit {given['limit']}")
        out.append("; ".join(parts))
    return out


def add_spec(state: Dict[str, Any], question: str, calls: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """conversation_state with this turn's spec appended (the oldest dropped past MAX_SPECS)."""
    specs = list(state.get(STATE_KEY) or [])
    specs.append({"q": _short(question), "calls": describe_calls(calls)})
    return {**state, STATE_KEY: specs[-MAX_SPECS:]}


def add_figures(state: Dict[str, Any], data: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """conversation_state with the numbers this turn's figure tools returned (only the last few turns kept)."""
    numbers: List[float] = []
    for item in data or []:
        _collect(item.get("result"), numbers)
    turns_kept = list(state.get(FIGURES_KEY) or [])
    turns_kept.append(sorted(set(numbers), key=abs, reverse=True)[:MAX_FIGURES_PER_TURN])
    return {**state, FIGURES_KEY: turns_kept[-FIGURE_TURNS:]}


def earlier_figures(state: Dict[str, Any]) -> List[float]:
    """All numbers kept from the last few turns, flat."""
    return [float(v) for turn in state.get(FIGURES_KEY) or [] for v in turn or []
            if isinstance(v, (int, float)) and not isinstance(v, bool)]


def _collect(payload: Any, out: List[float], depth: int = 0) -> None:
    if depth > 8 or isinstance(payload, bool) or payload is None:
        return
    if isinstance(payload, (int, float)):
        out.append(round(float(payload), 4))
    elif isinstance(payload, str):
        try:
            out.append(round(float(payload.replace(",", "")), 4))
        except ValueError:
            pass
    elif isinstance(payload, dict):
        for v in payload.values():
            _collect(v, out, depth + 1)
    elif isinstance(payload, (list, tuple)):
        for v in payload:
            _collect(v, out, depth + 1)


def _short(text: Any) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= QUESTION_CHARS else text[:QUESTION_CHARS - 1] + "…"


def _member(name: Any) -> str:
    return str(name).split(".")[-1]
