"""
vault.py — The placeholders of one request and the real values behind them (PII plan, phase 2).

Every hidden value gets a typed placeholder: [SUPPLIER_3], [ORG_1], [TRN_2]. The same value always gets the
same placeholder within a request, whatever its spelling: names are compared normalized ("Al Noor Trading LLC"
= "AL NOOR TRADING L.L.C"), identifiers without separators. A name keeps its first kind: a party already
[SUPPLIER_1] stays that when it shows up as a customer too.

A vault lives for one request and never leaves the server. Nothing needs to outlive it: each request masks the
whole conversation again from the stored, real-valued history, so placeholders are consistent within every call.

`rehydrate()` puts the real values back into the model's answer and tool inputs. It accepts the usual ways a
model rewrites a placeholder ("[Supplier_1]", "SUPPLIER-1", "\\[SUPPLIER_1\\]") and counts the ones it cannot
resolve, which should stay 0.
"""
from __future__ import annotations

import json
import re
import threading
from collections import Counter
from typing import Any, Dict, Optional, Tuple

from gemini_brain.privacy.policy import normalize_name

#: Kinds whose values are names (shared key space: one party, one placeholder).
NAME_KINDS = ("SUPPLIER", "CUSTOMER", "PARTY", "PERSON", "ORG")
ID_KINDS = ("TRN", "EMAIL", "PHONE", "IBAN", "ADDRESS", "EMIRATES_ID", "CARD", "PASSPORT", "GSTIN", "PAN", "AADHAAR",
            "TAN", "CIN", "LLPIN", "IFSC", "UPI_ID", "UTR", "EWAY_BILL", "IRN")
KINDS = NAME_KINDS + ID_KINDS

_ALT = "|".join(sorted(KINDS, key=len, reverse=True))
#: [KIND_n] in any case, with an optional markdown escape; or bare KIND_n / KIND-n in capitals.
TOKEN_RX = re.compile(r"\\?\[\s*(" + _ALT + r")[ _-]?(\d+)\s*\\?\]|(?<![\w\[])(" + _ALT + r")[_-](\d+)(?!\w)",
                      re.IGNORECASE)


def _bare_ok(m: re.Match) -> bool:
    """A bare placeholder (no brackets) counts only when written in capitals: "Supplier-1" in prose is a word."""
    return m.group(1) is not None or m.group(3).isupper()


class Vault:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token_of: Dict[Tuple[str, str], str] = {}
        self._value_of: Dict[str, str] = {}                 # "SUPPLIER_3" -> real value
        self._counts: Counter = Counter()
        self.unresolved = 0

    @staticmethod
    def _key(kind: str, value: str) -> Tuple[str, str]:
        if kind in NAME_KINDS:
            return "NAME", normalize_name(value)
        if kind in ("EMAIL", "UPI_ID", "ADDRESS"):
            return kind, re.sub(r"\s+", " ", value.strip().casefold())
        return kind, re.sub(r"[\W_]", "", value).upper()

    def token(self, kind: str, value: Any) -> str:
        """The placeholder for a value, made on first use."""
        kind = kind if kind in KINDS else "PARTY"
        text = str(value).strip()
        key = self._key(kind, text)
        with self._lock:
            label = self._token_of.get(key)
            if label is None:
                self._counts[kind] += 1
                label = f"{kind}_{self._counts[kind]}"
                self._token_of[key] = label
                self._value_of[label] = text
        return f"[{label}]"

    def value(self, label: str) -> Optional[str]:
        """The real value of "SUPPLIER_3" (or "[SUPPLIER_3]")."""
        return self._value_of.get(label.strip("[]\\ ").upper().replace("-", "_").replace(" ", "_"))

    def __len__(self) -> int:
        return len(self._value_of)

    def counts(self) -> Dict[str, int]:
        """How many distinct values of each kind were hidden."""
        return dict(self._counts)

    def rehydrate(self, text: str, json_string: bool = False) -> str:
        """The text with every placeholder replaced by its real value. Unknown placeholders stay and are counted.
        `json_string`: the text is raw JSON whose strings hold the placeholders, so values are escaped for it."""
        if not text or not self._value_of:
            return text

        def put(m: re.Match) -> str:
            if not _bare_ok(m):
                return m.group(0)
            kind, n = (m.group(1), m.group(2)) if m.group(1) is not None else (m.group(3), m.group(4))
            real = self._value_of.get(f"{kind.upper()}_{int(n)}")
            if real is None:
                self.unresolved += 1
                return m.group(0)
            return json.dumps(real, ensure_ascii=False)[1:-1] if json_string else real

        return TOKEN_RX.sub(put, text)

    def rehydrate_json(self, value: Any) -> Any:
        """A copy of a JSON value (a tool input) with real values in every string."""
        if isinstance(value, str):
            return self.rehydrate(value)
        if isinstance(value, dict):
            return {k: self.rehydrate_json(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.rehydrate_json(v) for v in value]
        return value
