"""audit.py: one line per step of a write action, in the agent audit log (AGENT_AUDIT_LOG).

Steps: prepared, updated, confirm_started, confirmed, failed, discarded, refused, expired.
A line holds ids, the action kind, the document type, the Accutax record id and number, a SHA-256 of the payload
and the outcome. Never the payload, a customer's name, an amount or a token: the hash is enough to match a line to
the record in Accutax. Writing never raises.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from gemini_brain.agent import audit as agent_audit

STEPS = frozenset({"prepared", "updated", "confirm_started", "confirmed", "failed", "discarded", "refused", "expired"})


def payload_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def step(step_name: str, *, action_id: str, kind: str, user_id: Any, organization_id: Any,
         session_id: Optional[str] = None, payload: Any = None, **fields: Any) -> None:
    """Write one line. Extra fields must be ids, codes or counts (record_id, number, error_kind, status)."""
    entry = {"step": step_name if step_name in STEPS else "unknown", "action_id": action_id, "action_kind": kind,
             "user_id": user_id, "organization_id": organization_id, "session_id": session_id, **fields}
    if payload is not None:
        entry["payload_sha256"] = payload_hash(payload)
    agent_audit.event("write_action", **entry)
