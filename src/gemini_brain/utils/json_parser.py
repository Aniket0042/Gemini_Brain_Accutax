"""
json_parser.py — Robust JSON extraction from LLM outputs.

Extracted from the inline parsing logic scattered across
gemini_brain_adapter.py (lines 155-170 and others).
Handles markdown-fenced JSON, raw JSON, and partial extraction.
"""
from __future__ import annotations

import json
import re
import logging
from typing import Any, Optional

logger = logging.getLogger("gemini_brain.utils.json_parser")


def extract_json(text: str) -> Optional[Any]:
    """Extract a JSON object or value from an LLM response.

    Handles three common formats:
      1. Markdown-fenced JSON:  ```json\\n{...}\\n```
      2. Raw JSON (optionally surrounded by whitespace or preamble)
      3. Substring extraction — finds the first ``{`` to last ``}``

    Returns the parsed Python object, or ``None`` if extraction fails.
    """
    if not text or not text.strip():
        return None

    raw = text.strip()

    # 1. Strip markdown code fences
    if raw.startswith("```"):
        # Remove opening fence (```json, ```, etc.)
        parts = raw.split("```")
        if len(parts) >= 3:
            raw = parts[1]
        elif len(parts) == 2:
            raw = parts[1]
        # Remove optional language tag on first line
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    # 2. Try direct parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Substring extraction: first { to last }
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    # 4. Try array extraction: first [ to last ]
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    logger.debug("Failed to extract JSON from: %.100s", text)
    return None
