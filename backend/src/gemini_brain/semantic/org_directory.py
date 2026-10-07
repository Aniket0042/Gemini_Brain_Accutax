"""
org_directory.py — Organization names and currencies for the agent, from Cube.

The agent labels answers with each organization's name and base currency. They
come from the same Cube the figures come from (view organization_directory),
with the same tenant guard, so the agent calls no Accutax endpoint. Two selected
organizations with the same name get their id added, so answers never mix them up.
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from typing import Any, Dict, Sequence

from gemini_brain.semantic import cube_client

logger = logging.getLogger("gemini_brain.semantic.org_directory")

VIEW = "organization_directory"
TIMEOUT_SECONDS = 8.0


def lookup(organization_ids: Sequence[int], *, subject: str) -> Dict[int, Dict[str, Any]]:
    """{org id: {"name", "currency"}} for the given, already-authorized organizations.

    An organization Cube does not return gets "Organization <id>" and no currency, so an
    amount is never labelled with a currency it does not have. Raises CubeError when Cube fails.
    """
    orgs = [int(o) for o in organization_ids]
    if not orgs:
        return {}
    result = cube_client.load(
        {"dimensions": [f"{VIEW}.organization_id", f"{VIEW}.organization_name", f"{VIEW}.currency"],
         "limit": len(orgs)},
        organization_ids=orgs, subject=subject, deadline=time.monotonic() + TIMEOUT_SECONDS,
    )
    known = {int(r[f"{VIEW}.organization_id"]): r for r in result.rows}

    def name(oid: int) -> str:
        return str((known.get(oid) or {}).get(f"{VIEW}.organization_name") or "").strip() or f"Organization {oid}"

    counts = Counter(name(o) for o in orgs)
    return {
        o: {"name": name(o) + (f" (ID {o})" if counts[name(o)] > 1 else ""),
            "currency": (known.get(o) or {}).get(f"{VIEW}.currency") or ""}
        for o in orgs
    }
