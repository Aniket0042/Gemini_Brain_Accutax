"""
fast_path.py — Fast-path regex matching to bypass LLM tool-calling for canonical questions.

Extracted from model_arena/backend/cost_optimizer.py lines 200-331.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.sql_fallback.cost_optimizer import (
    get_cached_result,
    set_cached_result,
)

logger = logging.getLogger("gemini_brain.sql_fallback.fast_path")


from gemini_brain.router.rules import get_sql_fast_path_rules

# Fast path table generated from consolidated rules.py (includes income_total and expense_total)
_FAST_PATH: List[Tuple[Any, str, Any]] = get_sql_fast_path_rules()


def try_fast_path(
    question: str,
    org_id: int,
    agent_handlers: Dict[str, Any],
) -> Optional[Tuple[Dict[str, Any], str, Dict[str, Any]]]:
    """Short-circuit the LLM tool-calling loop for canonical questions."""
    handler = agent_handlers.get("finance_agent")
    if not handler:
        return None

    for pattern, task, builder in _FAST_PATH:
        m = pattern.search(question)
        if not m:
            continue

        try:
            params = builder(m, org_id)
        except Exception:
            params = {"organization_id": org_id}

        cached = get_cached_result(org_id, task, params)
        if cached is not None:
            logger.info("Fast-path CACHE HIT: task=%s", task)
            return cached, task, params

        try:
            result = handler(task, params)
        except Exception as e:
            logger.warning("Fast-path %s failed: %s — falling back to LLM", task, e)
            return None

        if isinstance(result, dict) and result.get("success"):
            set_cached_result(org_id, task, params, result)

        logger.info("Fast-path HIT: task=%s  q=%.60s", task, question)
        return result, task, params

    return None
