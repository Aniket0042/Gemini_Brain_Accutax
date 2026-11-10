"""
external.py — What an answer looks like when it leaves Accutax for an outside AI host (the MCP connector).

In the Accutax app the user sees real names: the model got placeholders and the answer was put back together
here. Through the MCP connector the "user" is ChatGPT's or Claude's own model, so the answer leaves as the model
would see it: supplier, customer and person names, TRNs, IBANs, emails and other identifiers become placeholders
([SUPPLIER_1], [TRN_2]). Amounts, dates and document numbers stay (decision D1), and so do the organizations' own
names: the user picks an organization by its name, and it is their own company.

An organization in MCP_FULL_DETAIL_ORGS opted in to full detail: its answers go out with real values. An answer
covering several organizations keeps real values only when every one of them opted in. "*" opts in every
organization: masking is off for the connector.

Only the answer, its tables and its FTA sources leave, never traces, SQL, raw results or the privacy view. Masking
never fails open: without the contact names it still masks name fields and identifier patterns, and when masking
itself breaks the answer is withheld.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.config.settings import settings
from gemini_brain.privacy import dictionary
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault

logger = logging.getLogger("gemini_brain.privacy.external")

WITHHELD = "Accutax could not prepare this answer for sharing outside Accutax. Try again, or ask in the Accutax app."

#: The envelope fields that may leave. Everything else (traces, results, usage, privacy) stays.
_KEPT = ("answer", "status", "notice", "agent", "request_id", "error")


#: MCP_FULL_DETAIL_ORGS value that opts every organization in.
ALL = "*"


def full_detail_orgs() -> frozenset:
    out = set()
    for part in (settings.mcp_full_detail_orgs or "").split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return frozenset(out)


def full_detail(organization_ids: Sequence[int]) -> bool:
    """True when every organization of the answer opted in to full detail ("*": all of them)."""
    orgs = [int(o) for o in organization_ids]
    if not orgs:
        return False
    if any(p.strip() == ALL for p in (settings.mcp_full_detail_orgs or "").split(",")):
        return True
    allowed = full_detail_orgs()
    return all(o in allowed for o in orgs)


def _scrubber(organization_ids: Sequence[int], subject: str) -> Scrubber:
    try:
        names = dictionary.for_orgs(organization_ids, subject=subject, fetch=True)
    except Exception as e:  # noqa: BLE001 - without contact names, fields and patterns are still masked
        logger.warning("external view: contact names not loaded, masking without them: %s", e)
        names = dictionary.Dictionary()
    return Scrubber(Vault(), names)


def _table(block: Dict[str, Any], scrubber: Scrubber) -> Dict[str, Any]:
    rows = [r for r in block.get("rows") or [] if isinstance(r, dict)]
    return {
        "type": "table",
        "title": scrubber.text(block.get("title")) if block.get("title") else block.get("title"),
        "columns": block.get("columns") or [],
        "rows": [scrubber.json(r) for r in rows],
        "total_rows": block.get("total_rows") or len(rows),
    }


def _blocks(blocks: Sequence[Any], scrubber: Optional[Scrubber]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for block in blocks or []:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind == "table":
            out.append(_table(block, scrubber) if scrubber is not None else {
                k: block.get(k) for k in ("type", "title", "columns", "rows", "total_rows")})
        elif kind == "fta_sources" and block.get("text"):
            out.append({"type": "fta_sources", "text": block["text"]})  # published FTA documents: nothing to hide
    return out


def view(envelope: Dict[str, Any], organization_ids: Sequence[int], *, subject: str = "") -> Dict[str, Any]:
    """The part of a /query envelope that may go to an outside AI host, masked unless the organizations opted in.
    `masked` says which it is; `placeholders` how many values were hidden."""
    out = {k: envelope.get(k) for k in _KEPT if envelope.get(k) is not None}
    if full_detail(organization_ids):
        return {**out, "blocks": _blocks(envelope.get("blocks") or [], None), "masked": False, "placeholders": 0}
    try:
        scrubber = _scrubber(organization_ids, subject)
        if isinstance(out.get("answer"), str):
            out["answer"] = scrubber.text(out["answer"])
        notice = out.get("notice")
        if isinstance(notice, dict) and isinstance(notice.get("message"), str):
            out["notice"] = {**notice, "message": scrubber.text(notice["message"])}
        blocks = _blocks(envelope.get("blocks") or [], scrubber)
        hidden = len(scrubber.vault)
    except Exception as e:  # noqa: BLE001 - never fail open: no answer rather than an unmasked one
        logger.error("external view: masking failed, answer withheld: %s", e, exc_info=True)
        return {**{k: out[k] for k in ("request_id", "agent") if k in out}, "answer": WITHHELD, "status": "failed",
                "blocks": [], "masked": True, "placeholders": 0}
    return {**out, "blocks": blocks, "masked": True, "placeholders": hidden}
