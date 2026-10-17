"""
sentinel.py — Reports the personal data each model call sends (PII plan, phase 0).

Report only: it never blocks or changes a call. With PII_SENTINEL=report every Bedrock call is scanned just
before it is sent, and one JSON line per call goes to PII_SENTINEL_LOG: the call site, how many values of each
kind were found and where in the payload. The log never holds the values themselves. Each sample is a masked
preview ("A•• N••• T••••••") and a fingerprint, so the report can count distinct values without storing them.

What it looks for:
  - patterns, checked where a check digit exists: email, UAE mobile, Emirates ID, UAE IBAN (mod-97),
    UAE TRN (15 digits starting 100), card numbers (Luhn);
  - fields whose key names a person, business or contact detail, in tool results and tool inputs:
    vendor_name, customer_name, organization_name, *_trn, *_tax_id, *_address, email, phone ...;
  - those names again anywhere else in the call (earlier answers, the question, the system prompt), and the
    organization names the request registered with `note_request`;
  - free-text fields (descriptions, references), counted as needing review: they can hold names;
  - attached files and images, which cannot be scanned: counted with their size.

Scanning never raises: a failure is logged and the call goes on.
"""
from __future__ import annotations

import contextvars
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.privacy.sentinel")

MAX_BYTES = 50 * 1024 * 1024
BACKUPS = 5
MAX_SAMPLES = 25
MAX_FINGERPRINTS = 500
#: Shorter field values ("AB", "-") are not searched for elsewhere in the call: too many false matches.
MIN_NAME_CHARS = 4
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
#: Fingerprints count distinct values within one process; they cannot be matched across processes.
_SALT = secrets.token_bytes(16)

# ── Patterns ────────────────────────────────────────────────────────────────

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_UAE_MOBILE = re.compile(r"(?<![\w+])(?:\+971|00971|0)[\s-]?5\d(?:[\s-]?\d){7}(?!\d)")
_EMIRATES_ID = re.compile(r"(?<!\d)784-?\d{4}-?\d{7}-?\d(?!\d)")
_UAE_IBAN = re.compile(r"\bAE\d{2}(?:\s?\d){19}\b")
_TRN = re.compile(r"(?<!\d)100\d{12}(?!\d)")
_CARD = re.compile(r"(?<![\w.-])(?:\d[ -]?){12,18}\d(?![\w.])")


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _iban_ok(value: str) -> bool:
    s = re.sub(r"\s", "", value).upper()
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


def _card_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 13 <= len(digits) <= 19 and digits[0] in "3456" and len(set(digits)) > 1 and _luhn(digits)


#: kind -> (pattern, validator or None)
PATTERNS: Dict[str, Tuple[re.Pattern, Any]] = {
    "EMAIL": (_EMAIL, None),
    "PHONE": (_UAE_MOBILE, None),
    "EMIRATES_ID": (_EMIRATES_ID, None),
    "IBAN": (_UAE_IBAN, _iban_ok),
    "TRN": (_TRN, None),
    "CARD": (_CARD, _card_ok),
}

# ── Field keys ──────────────────────────────────────────────────────────────

PARTY_KEYS = {
    "vendor_name", "customer_name", "contact_name", "contactname", "supplier_name", "buyer_name", "party_name",
    "payee", "payer", "payee_name", "payer_name", "employee_name", "beneficiary", "beneficiary_name",
    "account_holder", "counterparty", "customer", "vendor", "supplier", "seller_name",
}
ORG_KEYS = {"organization_name", "organisation_name", "org_name", "company_name", "entity"}
FREE_TEXT_KEYS = {"description", "line_description", "journal_description", "memo", "narration", "notes", "reference",
                  "credit_reason"}
_EMPTY = {"", "-", "—", "unknown", "null", "none", "n/a"}


def key_kind(key: Any) -> Optional[str]:
    """What a JSON field holds, from its key ("purchases.vendor_trn" -> "TRN"). None for anything else."""
    k = str(key).rsplit(".", 1)[-1].lower()
    if k in PARTY_KEYS:
        return "PARTY_NAME"
    if k in ORG_KEYS:
        return "ORG_NAME"
    if k in ("trn", "tax_id", "vat_number") or k.endswith("_trn") or k.endswith("_tax_id"):
        return "TRN"
    if "email" in k:
        return "EMAIL"
    if "phone" in k or "mobile" in k:
        return "PHONE"
    if "iban" in k:
        return "IBAN"
    if k == "address" or k.endswith("_address"):
        return "ADDRESS"
    if k in FREE_TEXT_KEYS:
        return "FREE_TEXT"
    return None


# ── Request context ─────────────────────────────────────────────────────────

_request: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("pii_sentinel_request",
                                                                                    default=None)


def note_request(*, session_id: Optional[str] = None, organization_ids: Sequence[int] = (),
                 org_names: Iterable[str] = ()) -> None:
    """Tell the sentinel which chat this is and which organization names to look for. Each request sets it anew."""
    try:
        _request.set({"session_id": session_id, "organization_ids": [int(o) for o in organization_ids or ()],
                      "org_names": [str(n) for n in org_names or () if n]})
    except Exception as e:  # noqa: BLE001 - the sentinel must never break a request
        logger.debug("pii sentinel: request not noted: %s", e)


# ── Scan ────────────────────────────────────────────────────────────────────

def enabled() -> bool:
    return (settings.pii_sentinel or "off").strip().lower() == "report"


def _mask(value: str) -> str:
    """'Al Noor Trading' -> 'A• N••• T••••••': first character of each word, length kept, capped."""
    out = re.sub(r"(?<=\w)\w", "•", value.strip())
    return out[:24] + ("…" if len(out) > 24 else "")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(_SALT + value.strip().lower().encode("utf-8")).hexdigest()[:12]


class _Scan:
    def __init__(self) -> None:
        self.found: Dict[str, int] = {}
        self.where: Dict[str, Dict[str, int]] = {}
        self.distinct: Dict[str, set] = {}
        self.samples: List[Dict[str, Any]] = []
        self.texts: List[Tuple[str, str]] = []          # (location, text) for the pattern and name passes
        self.names: Dict[str, str] = {}                  # lower-cased field value -> kind
        self.free_text_fields = 0
        self.files = 0
        self.file_bytes = 0
        self.chars = 0

    def hit(self, kind: str, location: str, value: str, key: Optional[str] = None) -> None:
        self.found[kind] = self.found.get(kind, 0) + 1
        bucket = self.where.setdefault(location, {})
        bucket[kind] = bucket.get(kind, 0) + 1
        fp = _fingerprint(value)
        seen = self.distinct.setdefault(kind, set())
        if fp not in seen and len(self.samples) < MAX_SAMPLES:
            sample = {"kind": kind, "where": location, "preview": _mask(value), "fp": fp}
            if key:
                sample["key"] = key
            self.samples.append(sample)
        seen.add(fp)

    def text(self, location: str, value: Any) -> None:
        if isinstance(value, str) and value.strip():
            self.texts.append((location, value))
            self.chars += len(value)

    def walk(self, value: Any, location: str, key: Optional[str] = None, depth: int = 0) -> None:
        """A JSON value from a tool result or tool input: fields by their key, every string for patterns."""
        if depth > 12:
            return
        if isinstance(value, dict):
            for k, v in value.items():
                self.walk(v, location, str(k), depth + 1)
            return
        if isinstance(value, (list, tuple)):
            for v in value:
                self.walk(v, location, key, depth + 1)
            return
        kind = key_kind(key) if key else None
        if kind and not isinstance(value, bool) and isinstance(value, (str, int)):
            text = str(value).strip()
            if text.lower() in _EMPTY:
                return
            if kind == "FREE_TEXT":
                self.free_text_fields += 1
                self.text(location, text)  # free text is still scanned for patterns and names
                return
            self.hit(kind, location, text, key)
            if kind in ("PARTY_NAME", "ORG_NAME") and len(text) >= MIN_NAME_CHARS:
                self.names.setdefault(text.lower(), kind)
            return
        self.text(location, value)

    def block(self, block: Any, role: str) -> None:
        if not isinstance(block, dict):
            return
        if "text" in block:
            self.text(f"{role}_text", block["text"])
        if "toolUse" in block:
            self.walk((block["toolUse"] or {}).get("input"), "tool_input")
        if "toolResult" in block:
            for part in (block["toolResult"] or {}).get("content") or []:
                if isinstance(part, dict) and "json" in part:
                    self.walk(part["json"], "tool_result")
                elif isinstance(part, dict) and "text" in part:
                    self.text("tool_result", part["text"])
        for kind in ("document", "image"):
            if kind in block:
                data = ((block[kind] or {}).get("source") or {}).get("bytes") or b""
                self.files += 1
                self.file_bytes += len(data) if isinstance(data, (bytes, bytearray)) else 0

    def patterns(self) -> None:
        for location, text in self.texts:
            for kind, (pattern, valid) in PATTERNS.items():
                for m in pattern.finditer(text):
                    if valid is None or valid(m.group(0)):
                        self.hit(kind, location, m.group(0))

    def known_names(self, extra: Dict[str, str]) -> None:
        """Field values (and the request's organization names) found again outside the tool results."""
        names = {**self.names, **extra}
        if not names:
            return
        pattern = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                             + r")(?!\w)", re.IGNORECASE)
        for location, text in self.texts:
            if location in ("tool_result", "tool_input"):
                continue  # already counted by their keys
            for m in pattern.finditer(text):
                self.hit(names.get(m.group(0).lower(), "PARTY_NAME"), location, m.group(0))


def _system_texts(system: Any) -> List[str]:
    if isinstance(system, str):
        return [system]
    return [b["text"] for b in system or [] if isinstance(b, dict) and isinstance(b.get("text"), str)]


def scan(system: Any, messages: Sequence[Dict[str, Any]], org_names: Iterable[str] = ()) -> Dict[str, Any]:
    """What one call's payload holds. Pure: writes nothing."""
    s = _Scan()
    for text in _system_texts(system):
        s.text("system", text)
    for m in messages or []:
        role = m.get("role", "user") if isinstance(m, dict) else "user"
        for block in (m.get("content") or []) if isinstance(m, dict) else []:
            s.block(block, role)
    s.patterns()
    s.known_names({str(n).lower(): "ORG_NAME" for n in org_names if n and len(str(n)) >= MIN_NAME_CHARS})
    return {
        "found": s.found,
        "distinct": {k: len(v) for k, v in s.distinct.items()},
        # Salted, so the report can count distinct values across the calls of one run without seeing them.
        "fingerprints": {k: sorted(v)[:MAX_FINGERPRINTS] for k, v in s.distinct.items()},
        "where": s.where,
        "free_text_fields": s.free_text_fields,
        "files": s.files,
        "file_bytes": s.file_bytes,
        "chars": s.chars,
        "samples": s.samples,
    }


def inspect(*, model_id: str, label: str, purpose: str, system: Any,
            messages: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Scan one outbound call and log it, when PII_SENTINEL=report. Returns the entry (None when off or failed)."""
    if not enabled():
        return None
    try:
        t0 = time.perf_counter()
        request = _request.get() or {}
        result = scan(system, messages, request.get("org_names") or ())
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "purpose": purpose or "",
            "label": label or "",
            "model": model_id or "",
            "session_id": request.get("session_id"),
            "organization_ids": request.get("organization_ids") or [],
            "messages": len(messages or []),
            **result,
            "scan_ms": round((time.perf_counter() - t0) * 1000, 1),
        }
        _write(entry)
        return entry
    except Exception as e:  # noqa: BLE001 - report only: the call goes on
        logger.warning("pii sentinel: scan failed: %s", e)
        return None


# ── Log file ────────────────────────────────────────────────────────────────

def log_path() -> Optional[Path]:
    raw = (settings.pii_sentinel_log or "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_absolute() else _BACKEND_ROOT / path


def _write(entry: Dict[str, Any]) -> None:
    path = log_path()
    if path is None:
        return
    try:
        line = json.dumps(entry, default=str, ensure_ascii=False) + "\n"
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > MAX_BYTES:
                _rotate(path)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception as e:  # noqa: BLE001
        logger.warning("pii sentinel: could not write: %s", e)


def _rotate(path: Path) -> None:
    """pii_sentinel.jsonl -> .1 -> .2 ... ; the oldest beyond BACKUPS is dropped."""
    oldest = path.with_suffix(path.suffix + f".{BACKUPS}")
    if oldest.exists():
        oldest.unlink()
    for n in range(BACKUPS - 1, 0, -1):
        src = path.with_suffix(path.suffix + f".{n}")
        if src.exists():
            os.replace(src, path.with_suffix(path.suffix + f".{n + 1}"))
    os.replace(path, path.with_suffix(path.suffix + ".1"))
