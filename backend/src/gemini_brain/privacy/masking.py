"""
masking.py — One agent request's masking: what the model is sent, and the real values put back (PII plan, phase 2).

PII_MODE decides what happens:
  on      the model gets placeholders for names, contact details and identifiers. Tool inputs and the answer get
          the real values back before anything runs or anyone sees them.
  shadow  the model gets the originals, as before. The masked version of every call is built anyway and handed to
          the sentinel (purpose "agent:masked"), so its log shows what "on" would still let through.
  off     nothing: Masking.start returns None (and only then).

The agent keeps its own messages with real values (tool results, the question). Each model call masks them again,
memoized per message, so one vault gives the whole request consistent placeholders.

The request's masking is also `current()`: later model calls of the same request (the report narrator) mask with
the same vault and contact names, so a supplier is [SUPPLIER_3] in the answer and in the report alike.
"""
from __future__ import annotations

import contextvars
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.privacy import dictionary
from gemini_brain.privacy.policy import normalize_name
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault

logger = logging.getLogger("gemini_brain.privacy.masking")

MODES = ("off", "shadow", "on")

PLACEHOLDER_RULES = """
Privacy placeholders
- Names, contact details and ID numbers are hidden behind placeholders in square brackets: [ORG_1] is an
  organization selected in this chat, [SUPPLIER_2] a supplier, [CUSTOMER_3] a customer, [PARTY_4] a party,
  [PERSON_1] a person, [TRN_1] a tax registration number, [EMAIL_1] an email address, and so on. The user sees
  the real value in each one's place.
- Write a placeholder exactly as given, brackets included, wherever you would write that name or number, in
  answers and in tool inputs (for example a filter value). Never guess, describe or comment on what a placeholder
  hides, and never mention placeholders, masking or privacy to the user.
- Two different placeholders are two different values. A value that is missing in the data is missing, not a
  placeholder.
"""


class Masking:
    def __init__(self, mode: str, scrubber: Scrubber):
        self.mode = mode
        self.vault = scrubber.vault
        self.scrubber = scrubber
        self.ms = 0.0
        self._memo: Dict[int, tuple] = {}
        #: The before/after snapshot of this request (snapshots.begin), or None.
        self.capture: Any = None

    @classmethod
    def start(cls, mode: str, organization_ids: Sequence[int], org_meta: Dict[int, Dict[str, Any]], *,
              subject: str) -> Optional["Masking"]:
        """The masking for one request, or None when PII_MODE is off. Loads the organizations' contact names
        (cached for 10 minutes). When they cannot be loaded it masks without them: name and contact fields, the
        organizations' own names and identifier patterns are still hidden. It never fails open."""
        mode = (mode or "off").strip().lower()
        _current.set(None)  # a request never inherits the masking of an earlier one
        if mode not in ("shadow", "on"):
            return None
        t0 = time.perf_counter()
        org_names = [(m or {}).get("name") for m in org_meta.values()]
        try:
            names = dictionary.for_orgs(organization_ids, subject=subject, fetch=True, org_names=org_names)
        except Exception as e:  # noqa: BLE001 - without contact names, fields and patterns are still masked
            logger.warning("privacy masking: contact names not loaded, masking without them: %s", e)
            names = dictionary.Dictionary(dictionary.Entry(n, "ORG", normalize_name(n)) for n in org_names if n)
        masking = cls(mode, Scrubber(Vault(), names))
        masking.ms += (time.perf_counter() - t0) * 1000
        _current.set(masking)
        return masking

    @property
    def sends_masked(self) -> bool:
        return self.mode == "on"

    def org_meta(self, org_meta: Dict[int, Dict[str, Any]]) -> Dict[int, Dict[str, Any]]:
        return self.scrubber.org_meta(org_meta)

    def outbound(self, messages: Sequence[Dict[str, Any]], question: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """The messages as the model should see them. `question`: the message holding the user's question, which is
        also searched for single distinctive words of a name."""
        t0 = time.perf_counter()
        out = []
        for m in messages:
            hit = self._memo.get(id(m))
            if hit is None or hit[0] is not m:
                hit = (m, self.scrubber.message(m, partial=m is question))
                self._memo[id(m)] = hit
            out.append(hit[1])
        self.ms += (time.perf_counter() - t0) * 1000
        return out

    def text(self, text: str) -> str:
        return self.scrubber.text(text)

    def rehydrate(self, text: str) -> str:
        return self.vault.rehydrate(text)

    def rehydrate_json(self, value: Any) -> Any:
        return self.vault.rehydrate_json(value)

    def stats(self) -> Dict[str, Any]:
        out = {"mode": self.mode, "hidden": self.vault.counts(), "unresolved": self.vault.unresolved,
               "names_known": len(self.scrubber.names), "ms": round(self.ms, 1)}
        if self.capture is not None:
            out["snapshot"] = self.capture.id
        return out


_current: contextvars.ContextVar[Optional[Masking]] = contextvars.ContextVar("pii_masking", default=None)


def current(mode: str, *, subject: str = "") -> Optional[Masking]:
    """The masking of this request: the agent's, or a new one when no agent ran in this request (names of the
    organizations the request noted, if any). None when PII_MODE is off."""
    mode = (mode or "off").strip().lower()
    if mode not in ("shadow", "on"):
        return None
    found = _current.get()
    if found is not None:
        return found
    from gemini_brain.privacy import sentinel
    request = sentinel.request_scope()
    orgs = request.get("organization_ids") or []
    names = request.get("org_names") or []
    return Masking.start(mode, orgs, {o: {"name": n} for o, n in zip(orgs, names)}, subject=subject or "privacy")
