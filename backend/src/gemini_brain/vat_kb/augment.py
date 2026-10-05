"""Runner hook: add FTA sources to a VAT law answer, or change nothing.

`augment()` is called on the knowledge-answer path with the system prompt the runner has
already built. It returns that prompt unchanged — and no model change, blocks or trace —
unless all of these hold:

  * settings.vat_kb_enabled is on and settings.vat_kb_dir has a build,
  * the question type is FAQ (1), Accounting Concept (6) or Summary & Advice (7),
  * the detector says it is a VAT law question,
  * the search finishes within settings.vat_kb_timeout_seconds with a confident match.

It never raises. In shadow mode (vat_kb_shadow on, vat_kb_enabled off) the search runs on a
background thread and is only logged.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Any, Optional

from gemini_brain.config.settings import settings
from gemini_brain.vat_kb.detector import is_vat_law_question

log = logging.getLogger("gemini_brain.vat_kb.augment")

# Knowledge-answer types. Type 2 is included because the "How do I ..." pre-router sends law
# questions such as "How do I value a deemed supply?" there; the detector rejects real app how-tos.
VAT_QUESTION_TYPES = frozenset({1, 2, 6, 7})
# Data-path types the LLM classifier sometimes gives VAT law questions that contain amounts or dates.
REROUTE_FROM_TYPES = frozenset({3, 4, 5})
REROUTE_TO_TYPE = 6
MODEL_LABEL = "Claude Sonnet (VAT knowledge base)"
# A structured VAT answer (steps, documents, action) runs past the default 1,500-token cap on newer
# models: 7 of the 24 test answers stopped mid-sentence on Claude Sonnet 5.5.
ANSWER_MAX_TOKENS = 3000

_LABELS = [("haiku-4-5", "Claude Haiku 4.5"), ("sonnet-5-5", "Claude Sonnet 5.5"), ("sonnet-5", "Claude Sonnet 5"),
           ("sonnet-4-5", "Claude Sonnet 4.5"), ("claude-3-5-sonnet", "Claude Sonnet 3.5"), ("gpt-oss-120b", "gpt-oss-120b")]


def model_label(model_id: str) -> str:
    """Name shown in the app for the model that wrote a VAT answer."""
    for key, label in _LABELS:
        if key in (model_id or ""):
            return f"{label} (VAT knowledge base)"
    return MODEL_LABEL

_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vat_kb")


@dataclass
class VatAugmentation:
    system: str
    model_id: Optional[str] = None
    model_label: Optional[str] = None
    blocks: list = field(default_factory=list)
    trace_event: Optional[dict] = None
    max_tokens: int = 1500


def _search(query: str):
    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    kb = load_knowledge_base(settings.vat_kb_dir)
    return None if kb is None else kb.search(query)


# Results of searches done for reroute(), reused by augment() for the same question so it is
# searched once. Small and bounded; entries are only useful within one request.
_recent: dict[str, Any] = {}
_recent_lock = threading.Lock()
_RECENT_MAX = 32


def _search_within_timeout(query: str):
    with _recent_lock:
        if query in _recent:
            return _recent.pop(query)
    return _pool.submit(_search, query).result(timeout=settings.vat_kb_timeout_seconds)


def _confident_law_search(query: str):
    """Search result for a VAT law question with a confident match, else None. The result is kept
    so augment() for the same question does not search again. Never raises."""
    try:
        if not settings.vat_kb_enabled or not settings.vat_kb_dir or not is_vat_law_question(query):
            return None
        result = _pool.submit(_search, query).result(timeout=settings.vat_kb_timeout_seconds)
        if result is None or not result.confident:
            return None
        with _recent_lock:
            if len(_recent) >= _RECENT_MAX:
                _recent.pop(next(iter(_recent)))
            _recent[query] = result
        return result
    except Exception as e:
        log.warning("vat_kb law-question check failed, keeping the router's decision: %s", e)
        return None


def reroute(query: str, qtype: int, router_source: str) -> Optional[int]:
    """When the LLM classifier sends a VAT law question to a data path (typical when it contains
    amounts, e.g. a bad-debt calculation), return the knowledge-answer type to use instead.
    Returns None — keep the router's decision — unless the feature is on, the type came from the
    LLM classifier (never the rule-based fast router), the detector says VAT law, and the search
    finds a confident match. Never raises."""
    if router_source != "llm" or qtype not in REROUTE_FROM_TYPES:
        return None
    if _confident_law_search(query) is None:
        return None
    log.info("vat_kb reroute: type %d -> %d for %.80s", qtype, REROUTE_TO_TYPE, query)
    return REROUTE_TO_TYPE


_FOLLOW_UP_OPENER = re.compile(r"^\s*(and|also|but|so|then|now|what\s+about|how\s+about|same|ok(ay)?)\b", re.I)


def stands_alone(query: str) -> bool:
    """For the multi-org follow-up rewrite: True when this is a UAE VAT law question that names its own
    topic, so it is answered as asked. The rewrite turned "How do I value a deemed supply of services?",
    asked after a late-payment question, into the late-payment question again. A short question or one
    that opens like a follow-up ("and for exports?") is still rewritten. Never raises."""
    try:
        if not (settings.vat_kb_enabled and settings.vat_kb_dir):
            return False
        if _FOLLOW_UP_OPENER.match(query or "") or len((query or "").split()) < 6:
            return False
        return is_vat_law_question(query)
    except Exception:
        return False


def answers_without_org_data(query: str) -> bool:
    """For the multi-org planner: True when this is a VAT law question the knowledge base answers,
    so it is answered once instead of as a per-organization VAT figure ("What changed in UAE VAT
    from 2026" matched the net-VAT-payable metric). False — today's planning — when the feature is
    off or the search is not confident. Never raises."""
    return _confident_law_search(query) is not None


def _shadow(query: str) -> None:
    def run():
        started = time.perf_counter()
        try:
            result = _search(query)
            if result is None:
                log.info("vat_kb shadow: no build in %s", settings.vat_kb_dir)
                return
            log.info("vat_kb shadow: confident=%s top_dense=%.2f top_bm25=%.1f sources=%s ms=%d query=%.80s",
                     result.confident, result.top_dense, result.top_bm25,
                     [c.title[:60] for c in result.sources()], (time.perf_counter() - started) * 1000, query)
        except Exception as e:
            log.warning("vat_kb shadow search failed: %s", e)
    _pool.submit(run)


def augment(query: str, qtype: int, system: str) -> VatAugmentation:
    unchanged = VatAugmentation(system=system)
    try:
        if not (settings.vat_kb_enabled or settings.vat_kb_shadow) or not settings.vat_kb_dir:
            return unchanged
        if qtype not in VAT_QUESTION_TYPES or not is_vat_law_question(query):
            return unchanged
        if not settings.vat_kb_enabled:
            _shadow(query)
            return unchanged

        started = time.perf_counter()
        try:
            result = _search_within_timeout(query)
        except FutureTimeout:
            return _fallback(unchanged, "timeout", started)
        if result is None:
            return _fallback(unchanged, "no knowledge-base build", started)
        if not result.confident:
            return _fallback(unchanged, result.reason or "weak match", started)

        from gemini_brain.vat_kb.answer import prompt_block, sources_block
        sources = result.sources()
        return VatAugmentation(
            system=system + "\n" + prompt_block(query, result),
            model_id=settings.vat_kb_model_id or settings.bedrock_model_id,
            model_label=model_label(settings.vat_kb_model_id or settings.bedrock_model_id),
            max_tokens=ANSWER_MAX_TOKENS,
            blocks=[sources_block(query, result)],
            trace_event={
                "step": "vat_kb", "status": "used", "sources": [c.title for c in sources],
                "chunks": len(result.hits), "top_dense": round(result.top_dense, 3),
                "top_bm25": round(result.top_bm25, 1), "ms": round((time.perf_counter() - started) * 1000),
            },
        )
    except Exception as e:   # the knowledge base must never break an answer
        log.warning("vat_kb augment failed, answering without it: %s", e)
        return unchanged


def _fallback(unchanged: VatAugmentation, reason: str, started: float) -> VatAugmentation:
    unchanged.trace_event = {"step": "vat_kb", "status": "fallback", "reason": reason,
                             "ms": round((time.perf_counter() - started) * 1000)}
    log.info("vat_kb fallback: %s", reason)
    return unchanged


def warm_up_in_background() -> Optional[Any]:
    """Load the build and the embedding model at startup so the first VAT question does not
    pay ~2-5 s of loading inside its timeout. Does nothing when the feature is off."""
    if not (settings.vat_kb_enabled or settings.vat_kb_shadow) or not settings.vat_kb_dir:
        return None

    def run():
        try:
            result = _search("What is the standard VAT rate in the UAE?")
            log.info("vat_kb warm-up done: %s", "ready" if result is not None else "no build found")
        except Exception as e:
            log.warning("vat_kb warm-up failed: %s", e)
    thread = threading.Thread(target=run, name="vat_kb_warmup", daemon=True)
    thread.start()
    return thread
