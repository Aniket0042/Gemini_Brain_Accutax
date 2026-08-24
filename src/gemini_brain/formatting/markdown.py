"""
markdown.py — Markdown cleaner, sanitizer, and normalizer.

Ensures LLM output and tabular projections render without broken tables, unclosed
delimiters, double-escaped HTML entities, or excessive whitespace.
"""
from __future__ import annotations

import html
import re
from typing import Optional


def normalize_markdown(text: Optional[str]) -> str:
    """Cleans, normalizes, and repairs malformed markdown text.

    1. Returns empty string if None or blank
    2. Unescapes double-escaped HTML entities (&amp;, &lt;, &gt;, &#39;, &quot;)
    3. Repairs unclosed code fences
    4. Repairs unclosed bold markers
    5. Ensures markdown tables have preceding and trailing blank lines
    6. Collapses excessive newlines (3+ -> 2)
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    s = text.strip()
    if not s:
        return ""

    # 1. Unescape common HTML entities (safe, preserves valid markdown formatting)
    s = html.unescape(s)

    # 2. Repair unclosed code fences (``` or ~~~)
    fence_count = len(re.findall(r"^```", s, flags=re.MULTILINE))
    if fence_count % 2 != 0:
        s = s + "\n```"

    # 3. Repair unclosed bold (**)
    bold_count = len(re.findall(r"\*\*", s))
    if bold_count % 2 != 0:
        s = s + "**"

    # 4. Ensure blank lines around markdown tables so frontend parsers don't eat headers
    lines = s.split("\n")
    processed_lines = []
    in_table = False

    for line in lines:
        is_table_line = bool(re.match(r"^\s*\|.*\|\s*$", line))
        if is_table_line:
            if not in_table:
                # Starting a table — ensure blank line before it if previous line was text
                if processed_lines and processed_lines[-1].strip() != "":
                    processed_lines.append("")
                in_table = True
            processed_lines.append(line)
        else:
            if in_table:
                # Exiting a table — ensure blank line after it if current line is text
                if line.strip() != "":
                    processed_lines.append("")
                in_table = False
            processed_lines.append(line)

    s = "\n".join(processed_lines)

    # 5. Collapse excessive whitespace
    s = re.sub(r"\n{3,}", "\n\n", s)

    return s.strip()
