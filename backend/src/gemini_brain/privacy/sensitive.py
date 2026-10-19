"""
sensitive.py — Values a user must never type into the chat, dropped from the question before anything sees it.

A card number in a question is removed at once, whatever PII_MODE says: the model, the saved chat, the answer log
and the privacy snapshot all get the question with "[card number removed]" in its place. Unlike a name or a TRN,
it is never put back in the answer: Accutax holds no card data (PCI DSS). The user is told twice: one line at the
top of the answer, and a warning on screen (the "sensitive_notice" block, saved with the turn).

The chat page drops card numbers in the browser before sending (frontend/src/sensitive.js) and leaves the same
marker, so a question that arrives with the marker counts as dropped too.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from gemini_brain.config.settings import settings
from gemini_brain.privacy import recognizers

#: What stands in the question where a value was.
REMOVED = {"CARD": "[card number removed]"}

#: The line put at the top of the answer.
ANSWER_LINE = {"CARD": "We removed a card number. Do not share card details here."}


def enabled() -> bool:
    return (settings.pii_card_guard or "on").strip().lower() != "off"


def drop(text: str) -> Tuple[str, Dict[str, int]]:
    """`text` without card numbers, and how many were dropped per kind (markers already in it included)."""
    text = text or ""
    if not enabled() or not text:
        return text, {}
    counts: Dict[str, int] = {}
    for kind, marker in REMOVED.items():
        if marker in text:
            counts[kind] = text.count(marker)
    spans = recognizers.find(text, kinds=list(REMOVED))
    for span in reversed(spans):
        text = text[:span.start] + REMOVED[span.kind] + text[span.end:]
        counts[span.kind] = counts.get(span.kind, 0) + 1
    return text, counts


def with_answer_line(answer: str, counts: Dict[str, int]) -> str:
    """The answer, led by one line per kind dropped."""
    lines = [ANSWER_LINE[k] for k in REMOVED if counts.get(k)]
    if not lines:
        return answer
    return "\n\n".join([" ".join(lines), answer or ""]).strip()


def notice_block(counts: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """The on-screen warning above the answer, or None when nothing was dropped."""
    kinds = [k for k in REMOVED if counts.get(k)]
    if not kinds:
        return None
    return {"type": "sensitive_notice", "kinds": kinds, "count": sum(counts[k] for k in kinds)}
