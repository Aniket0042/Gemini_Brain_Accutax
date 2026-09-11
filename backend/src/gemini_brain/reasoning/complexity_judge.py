"""
complexity_judge.py — Gemini-based complexity tier judging (SIMPLE / MEDIUM / COMPLEX).

Extracted from gemini_brain_adapter.py lines 113-119 (_COMPLEXITY_SYSTEM)
and lines 313-326 (_judge_complexity).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Tuple

from gemini_brain.config.constants import COMPLEXITY_PREVIEW_MAX_CHARS

logger = logging.getLogger("gemini_brain.reasoning.complexity_judge")

# ── Verbatim System Prompt ───────────────────────────────────────────────────
COMPLEXITY_SYSTEM_PROMPT: str = """Judge the analytical complexity needed to answer an accounting question given live data.

  SIMPLE  — Just display/list the data. No analysis needed.
  MEDIUM  — Ranking, distributions, basic comparisons, identifying top items.
  COMPLEX — Forecasting, multi-period trends, scenario analysis, risk assessment, strategic advice.

Respond with ONLY one word: SIMPLE, MEDIUM, or COMPLEX"""


def judge_complexity(
    query: str,
    data: Any,
    call_gemini: Callable[[str, str, int], Tuple[str, int, int]],
    preview_max_chars: int = COMPLEXITY_PREVIEW_MAX_CHARS,
) -> Tuple[str, int, int]:
    """Judge analytical complexity tier (SIMPLE, MEDIUM, COMPLEX) using Gemini.

    Parameters
    ----------
    query : str
        User's question.
    data : Any
        JSON payload returned from REST API call.
    call_gemini : callable
        ``(system, user_text, max_tokens) -> (text, input_tokens, output_tokens)``
    preview_max_chars : int
        Max characters of serialised data preview.

    Returns
    -------
    Tuple[str, int, int]
        (complexity_tier, prompt_tokens, candidate_tokens)
    """
    preview = json.dumps(data, default=str, ensure_ascii=False)
    if len(preview) > preview_max_chars:
        preview = preview[:preview_max_chars] + "..."

    try:
        text, ri, ro = call_gemini(
            COMPLEXITY_SYSTEM_PROMPT,
            f"QUESTION: {query}\n\nDATA:\n{preview}",
            max_tokens=10,
        )
        c = text.strip().upper()
        tier = c if c in ("SIMPLE", "MEDIUM", "COMPLEX") else "MEDIUM"
        return tier, ri, ro
    except Exception as e:
        logger.warning("Complexity judging exception: %s", e)
        return "MEDIUM", 0, 0
