"""Lexical top-k tool retrieval for the LLM router.

Fast and dependency-free: token overlap against the chat index (REGISTRY
descriptions + OpenAPI `x-accutax-chat` hints). Always pins `answer_directly`
and `unsupported`. Short queries widen the window so recall stays high.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

from gemini_brain.tools.chat_index import IndexedTool, get_index

logger = logging.getLogger("gemini_brain.router.tool_retriever")

TOP_K = 12
SHORT_QUERY_K = 24
SHORT_TOKEN_THRESHOLD = 3
PINNED = ("answer_directly", "unsupported")

_STOP = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "is", "are", "was",
    "be", "we", "our", "my", "me", "us", "please", "show", "get", "give", "tell",
    "what", "which", "who", "how", "this", "that", "last", "next", "current",
    "previous", "year", "month", "quarter", "report", "statement", "and", "or",
    "with", "from", "about", "can", "you", "i", "do", "did", "does", "aed",
})

_ALIASES = (
    (re.compile(r"\bp\s*&\s*l\b", re.I), " profit loss "),
    (re.compile(r"\bpnl\b", re.I), " profit loss "),
    (re.compile(r"\bar\b", re.I), " receivable aging "),
    (re.compile(r"\bap\b", re.I), " payable aging "),
    (re.compile(r"\bbs\b", re.I), " balance sheet "),
    (re.compile(r"\bfta\b", re.I), " vat return "),
)


def _expand_query(query: str) -> str:
    text = f" {query} "
    for pattern, repl in _ALIASES:
        text = pattern.sub(repl, text)
    return text


def tokenize(text: str) -> Set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 1}


def _score(query: str, query_tokens: Set[str], item: IndexedTool) -> float:
    if not query_tokens:
        return 0.0
    blob_tokens = tokenize(item.search_text)
    overlap = query_tokens & blob_tokens
    score = float(len(overlap))

    name_tokens = tokenize(item.spec.name.replace("_", " "))
    path_tokens = tokenize(item.spec.endpoint.replace("/", " ").replace("-", " "))
    score += 4.0 * len(query_tokens & name_tokens)
    score += 5.0 * len(query_tokens & path_tokens)

    q = query.lower()
    for phrase in item.use_for:
        p = phrase.lower().strip()
        if len(p) >= 6 and p in q:
            score += 12.0
        score += 3.0 * len(query_tokens & tokenize(p))

    for phrase in item.do_not_use_for:
        p = phrase.lower().strip()
        if len(p) >= 6 and p in q:
            score -= 10.0
        score += -4.0 * len(query_tokens & tokenize(p))

    if not item.tagged and item.source == "openapi":
        score *= 0.85
    return score


def retrieve_tool_names(
    query: str,
    session_state: Optional[Dict[str, Any]] = None,
    k: int = TOP_K,
) -> List[str]:
    """Return pinned tools plus the top-k index hits for `query`."""
    index = get_index()
    expanded = _expand_query(query)
    query_tokens = tokenize(expanded)
    k_eff = SHORT_QUERY_K if len(query_tokens) < SHORT_TOKEN_THRESHOLD else k

    scored: List[tuple] = []
    for name, item in index.by_name.items():
        if name in PINNED:
            continue
        scored.append((_score(expanded, query_tokens, item), name))
    scored.sort(key=lambda row: (-row[0], row[1]))

    picked: List[str] = []
    seen: Set[str] = set()

    def _add(name: str) -> None:
        if name in seen or name not in index.by_name:
            return
        seen.add(name)
        picked.append(name)

    for name in PINNED:
        _add(name)

    last = (session_state or {}).get("last_executed_task")
    if isinstance(last, str) and last:
        _add(last)
        by_ep = index.by_endpoint.get(last)
        if by_ep:
            _add(by_ep.spec.name)

    for score, name in scored:
        if score <= 0 and len(query_tokens) >= SHORT_TOKEN_THRESHOLD:
            continue
        _add(name)
        if len(picked) >= k_eff + len(PINNED):
            break

    if len(picked) <= len(PINNED):
        for name in index.by_name:
            _add(name)

    logger.debug("Tool retrieve query=%r tokens=%s names=%s", query, query_tokens, picked)
    return picked
