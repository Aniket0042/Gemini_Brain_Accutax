"""
scrubber.py — Replaces personal data with placeholders before a model sees it (PII plan, phase 2).

Text (questions, earlier turns, the attached-file block) is searched two ways: known names from the contact
dictionary, and identifiers by pattern. Tool results and tool inputs go field by field as the policy classes
each key: name and contact fields become placeholders whole; free text and other text is searched like any
text; figures, dates and document numbers pass untouched and unscanned.

Names of public bodies, banks and utilities stay (policy.public_entity), and so does a field that is empty.
Files and images pass as they are: uploads keep the current reader until phase 5 (decision D3).
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from gemini_brain.privacy import policy, recognizers
from gemini_brain.privacy.dictionary import Dictionary
from gemini_brain.privacy.vault import Vault

_EMPTY = {"", "-", "—", "unknown", "null", "none", "n/a"}


class Scrubber:
    def __init__(self, vault: Vault, names: Optional[Dictionary] = None):
        self.vault = vault
        self.names = names or Dictionary()

    def spans(self, text: str, partial: bool = False) -> List[Tuple[int, int, str, str]]:
        """(start, end, kind, value) to hide, in order, not overlapping. Names win over patterns."""
        out: List[Tuple[int, int, str, str]] = []
        for s in self.names.find(text, partial=partial):
            out.append((s.start, s.end, s.kind, self.names.entries[s.key].name))
        for s in recognizers.find(text):
            if not any(a < s.end and s.start < b for a, b, _, _ in out):
                out.append((s.start, s.end, s.kind, s.text))
        return sorted(out)

    def text(self, text: Any, partial: bool = False) -> Any:
        """The text with names and identifiers replaced. `partial`: also single distinctive words of a name, for
        the user's question, where people type part of a name."""
        if not isinstance(text, str) or not text.strip():
            return text
        spans = self.spans(text, partial)
        if not spans:
            return text
        parts, at = [], 0
        for start, end, kind, value in spans:
            parts.append(text[at:start])
            parts.append(self.vault.token(kind, value))
            at = end
        parts.append(text[at:])
        return "".join(parts)

    def json(self, value: Any, key: Optional[str] = None, depth: int = 0) -> Any:
        """A copy of a tool result or tool input with personal data replaced, field by field."""
        if depth > 16:
            return value
        if isinstance(value, dict):
            return {k: self.json(v, str(k), depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [self.json(v, key, depth + 1) for v in value]
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return value
        rule = policy.field_rule(key) if key else policy.FieldRule(policy.SCAN)
        if rule.cls == policy.KEEP:
            return value
        if rule.cls == policy.TOKEN:
            text = str(value).strip()
            if text.lower() in _EMPTY:
                return value
            if rule.kind in ("SUPPLIER", "CUSTOMER", "PARTY", "PERSON", "ORG") and policy.public_entity(text):
                return value
            return self.vault.token(rule.kind or "PARTY", text)
        return self.text(value) if isinstance(value, str) else value

    def message(self, message: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
        """A copy of one Bedrock message with its text, tool inputs and tool results masked."""
        out = {**message, "content": []}
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                out["content"].append(block)
            elif "text" in block:
                out["content"].append({**block, "text": self.text(block["text"], partial)})
            elif "toolUse" in block:
                use = block["toolUse"] or {}
                out["content"].append({**block, "toolUse": {**use, "input": self.json(use.get("input"))}})
            elif "toolResult" in block:
                result = block["toolResult"] or {}
                parts = []
                for part in result.get("content") or []:
                    if isinstance(part, dict) and "json" in part:
                        parts.append({**part, "json": self.json(part["json"])})
                    elif isinstance(part, dict) and "text" in part:
                        parts.append({**part, "text": self.text(part["text"])})
                    else:
                        parts.append(part)
                out["content"].append({**block, "toolResult": {**result, "content": parts}})
            else:
                out["content"].append(copy.copy(block))     # files and images: unchanged (D3)
        return out

    def org_meta(self, org_meta: Dict[int, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        """The organizations' metadata with their names as placeholders (decision D2)."""
        return {o: {**(m or {}), "name": self.vault.token("ORG", (m or {}).get("name") or f"Organization {o}")}
                for o, m in org_meta.items()}
