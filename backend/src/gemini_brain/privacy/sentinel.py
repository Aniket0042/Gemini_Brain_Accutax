"""
sentinel.py — Reports the personal data each model call sends (PII plan, phases 0-1).

Report only: it never blocks or changes a call. With PII_SENTINEL=report every Bedrock call is scanned just
before it is sent, and one JSON line per call goes to PII_SENTINEL_LOG: the call site, how many values of each
kind were found and where in the payload. The log never holds the values themselves. Each sample is a masked
preview ("A•• N••• T••••••") and a fingerprint, so the report can count distinct values without storing them.

What it looks for:
  - identifiers by pattern (recognizers.py): UAE and India, with their check digits and context words;
  - fields the policy marks as names or contact details (policy.py), in tool results and tool inputs:
    vendor_name, customer_name, organization_name, *_trn, *_address, email, phone ...;
  - known names anywhere else in the call (the question, earlier answers, the system prompt, free-text fields):
    the organizations' contact dictionary (dictionary.py, when cached), the organization names the request
    noted, and the names this call carries in its own fields;
  - free-text fields (descriptions, references), counted: they can hold names;
  - attached files and images, which cannot be scanned: counted with their size.

Figure, date and document-number fields are never scanned (policy "keep"). Scanning never raises: a failure is
logged and the call goes on.
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
from gemini_brain.privacy import dictionary, policy, recognizers
from gemini_brain.privacy.vault import TOKEN_RX

logger = logging.getLogger("gemini_brain.privacy.sentinel")

MAX_BYTES = 50 * 1024 * 1024
BACKUPS = 5
MAX_SAMPLES = 25
MAX_FINGERPRINTS = 500
#: Shorter field values ("AB", "-") are not searched for elsewhere in the call: too many false matches.
MIN_NAME_CHARS = 3  # as the dictionary's: a 3-letter name counts where it is capitalized
_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
#: Fingerprints count distinct values within one process; they cannot be matched across processes.
_SALT = secrets.token_bytes(16)
_EMPTY = {"", "-", "—", "unknown", "null", "none", "n/a"}


def key_kind(key: Any) -> Optional[str]:
    """What a JSON field holds, as the report counts it ("purchases.vendor_trn" -> "TRN"); "FREE_TEXT" for
    descriptions; None for figures, dates, document numbers and any other field."""
    rule = policy.field_rule(key)
    if rule.cls == policy.TOKEN:
        return policy.report_kind(rule.kind or "")
    if rule.cls == policy.FREE_TEXT:
        return "FREE_TEXT"
    return None


# ── Request context ─────────────────────────────────────────────────────────

_request: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("pii_sentinel_request",
                                                                                    default=None)


def note_request(*, session_id: Optional[str] = None, organization_ids: Sequence[int] = (),
                 org_names: Iterable[str] = (), subject: str = "") -> None:
    """Tell the sentinel which chat this is and which organizations it covers. Each request sets it anew. When the
    sentinel is on, the organizations' contact names start loading in the background."""
    try:
        orgs = [int(o) for o in organization_ids or ()]
        _request.set({"session_id": session_id, "organization_ids": orgs,
                      "org_names": [str(n) for n in org_names or () if n]})
        if enabled() and orgs:
            dictionary.warm(orgs, subject=subject or "pii-sentinel")
    except Exception as e:  # noqa: BLE001 - the sentinel must never break a request
        logger.debug("pii sentinel: request not noted: %s", e)


def request_scope() -> Dict[str, Any]:
    """The session, organizations and organization names this request noted ({} when none)."""
    return dict(_request.get() or {})


# ── Scan ────────────────────────────────────────────────────────────────────

def _setting() -> str:
    return (settings.pii_sentinel or "off").strip().lower()


def enabled() -> bool:
    """Whether calls are scanned and logged: PII_SENTINEL=report or strict."""
    return _setting() in ("report", "strict")


def strict() -> bool:
    """PII_SENTINEL=strict: a value found in a payload about to go out is hidden before it goes (`enforce`)."""
    return _setting() == "strict"


def _mask(value: str) -> str:
    """'Al Noor Trading' -> 'A• N••• T••••••': first character of each word, length kept, capped."""
    out = re.sub(r"(?<=\w)\w", "•", value.strip())
    return out[:24] + ("…" if len(out) > 24 else "")


def _fingerprint(value: str) -> str:
    """The same value in any spelling gets one fingerprint: names normalized, numbers without separators."""
    base = policy.normalize_name(value) if re.search(r"[^\W\d_]", value) else re.sub(r"\W", "", value)
    return hashlib.sha256(_SALT + base.encode("utf-8")).hexdigest()[:12]


class _Scan:
    def __init__(self) -> None:
        self.found: Dict[str, int] = {}
        self.where: Dict[str, Dict[str, int]] = {}
        self.distinct: Dict[str, set] = {}
        self.samples: List[Dict[str, Any]] = []
        self.texts: List[Tuple[str, str]] = []          # (location, text) for the pattern and name passes
        self.names: Dict[str, str] = {}                  # normalized field value -> report kind
        #: (kind, lower-cased value) -> the value as found. In memory only: never written (see `leaked_values`).
        self.raw: Dict[Tuple[str, str], str] = {}
        self.free_text_fields = 0
        self.files = 0
        self.file_bytes = 0
        self.chars = 0

    def hit(self, kind: str, location: str, value: str, key: Optional[str] = None) -> None:
        self.raw.setdefault((kind, value.strip().lower()), value.strip())
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
        """A JSON value from a tool result or tool input, field by field as the policy classes its key."""
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
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return
        rule = policy.field_rule(key) if key else policy.FieldRule(policy.SCAN)
        text = str(value).strip()
        if rule.cls == policy.KEEP or text.lower() in _EMPTY or TOKEN_RX.fullmatch(text):
            return  # figures and the like, empty fields, and placeholders (already masked) are not leaks
        if rule.cls == policy.TOKEN:
            kind = policy.report_kind(rule.kind or "")
            if kind in ("PARTY_NAME", "ORG_NAME") and policy.public_entity(text):
                return  # a bank, utility or public body: visible on purpose
            self.hit(kind, location, text, key)
            normalized = policy.normalize_name(text)
            if kind in ("PARTY_NAME", "ORG_NAME") and len(normalized) >= MIN_NAME_CHARS:
                self.names.setdefault(normalized, kind)
            return
        if isinstance(value, str):
            if rule.cls == policy.FREE_TEXT:
                self.free_text_fields += 1
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
            for span in recognizers.find(text):
                if not policy.public_contact(span.kind, span.text):
                    self.hit(span.kind, location, span.text)

    def known_names(self, known: dictionary.Dictionary) -> None:
        """Known names in the texts: the contact dictionary first, then names this call carries in its fields."""
        own = dictionary.Dictionary(dictionary.Entry(n, "ORG" if kind == "ORG_NAME" else "PARTY", n)
                                    for n, kind in self.names.items() if n not in known.entries)
        for location, text in self.texts:
            taken: List[Tuple[int, int]] = []
            for span in known.find(text):
                taken.append((span.start, span.end))
                self.hit(policy.report_kind(span.kind), location, span.text)
            for span in own.find(text):
                if not any(s < span.end and span.start < e for s, e in taken):
                    self.hit(policy.report_kind(span.kind), location, span.text)


def _system_texts(system: Any) -> List[str]:
    if isinstance(system, str):
        return [system]
    return [b["text"] for b in system or [] if isinstance(b, dict) and isinstance(b.get("text"), str)]


def _scan(system: Any, messages: Sequence[Dict[str, Any]], org_names: Iterable[str] = (),
          known: Optional[dictionary.Dictionary] = None) -> Tuple["_Scan", dictionary.Dictionary]:
    s = _Scan()
    for text in _system_texts(system):
        s.text("system", text)
    for m in messages or []:
        role = m.get("role", "user") if isinstance(m, dict) else "user"
        for block in (m.get("content") or []) if isinstance(m, dict) else []:
            s.block(block, role)
    s.patterns()
    if known is None:
        known = dictionary.Dictionary(dictionary.Entry(n, "ORG", policy.normalize_name(n)) for n in org_names if n)
    s.known_names(known)
    return s, known


def scan(system: Any, messages: Sequence[Dict[str, Any]], org_names: Iterable[str] = (),
         known: Optional[dictionary.Dictionary] = None) -> Dict[str, Any]:
    """What one call's payload holds. Pure: writes nothing. `known`: the contact dictionary to search with
    (default: the organization names alone)."""
    s, known = _scan(system, messages, org_names, known)
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
        "dictionary_names": len(known),
        "samples": s.samples,
    }


def _known() -> dictionary.Dictionary:
    request = _request.get() or {}
    return dictionary.for_orgs(request.get("organization_ids") or (), org_names=request.get("org_names") or ())


def leaked_values(system: Any, messages: Sequence[Dict[str, Any]],
                  known: Optional[dictionary.Dictionary] = None) -> List[Tuple[str, str]]:
    """(kind, value) of the personal data one payload carries, as found. For `enforce`: never log these."""
    s, _ = _scan(system, messages, known=known if known is not None else _known())
    return [(kind, value) for (kind, _), value in s.raw.items()]


#: Sentinel kinds -> the vault's placeholder kinds.
_VAULT_KIND = {"PARTY_NAME": "PARTY", "ORG_NAME": "ORG"}


def enforce(system: Any, messages: List[Dict[str, Any]]) -> Tuple[Any, List[Dict[str, Any]], Dict[str, int]]:
    """PII_SENTINEL=strict, with PII_MODE=on: hide what the masking let through, then let the call go on.

    The payload is scanned as it is about to be sent. Each value found gets a placeholder in the request's vault,
    so the answer still shows the real value, and the masking learns it, so the snapshot and every later call of
    the request hide it too. Returns the payload to send (a copy when anything was hidden; the caller's lists are
    never changed) and the count of values hidden per kind. Without an active "on" masking there is nothing to
    put real values back with, so the payload goes unchanged."""
    if not strict():
        return system, messages, {}
    from gemini_brain.privacy import masking as masking_module
    m = masking_module._current.get()
    if m is None or not m.sends_masked:
        return system, messages, {}
    values = leaked_values(system, messages)
    if not values:
        return system, messages, {}
    m.catch([(_VAULT_KIND.get(kind, kind), value) for kind, value in values])
    counts: Dict[str, int] = {}
    for kind, _ in values:
        counts[kind] = counts.get(kind, 0) + 1
    new_system = m.scrubber.replace_caught(system)
    new_messages = m.scrubber.replace_caught(list(messages))
    logger.warning("pii sentinel (strict): hid %s that the masking let through", counts)
    try:
        from gemini_brain.agent import audit
        request = _request.get() or {}
        audit.event("pii_sentinel_caught", session_id=request.get("session_id"),
                    organization_ids=request.get("organization_ids") or [], kinds=counts)
    except Exception as e:  # noqa: BLE001
        logger.debug("pii sentinel: audit not written: %s", e)
    return new_system, new_messages, counts


def inspect(*, model_id: str, label: str, purpose: str, system: Any,
            messages: Sequence[Dict[str, Any]], extra: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Scan one outbound call and log it, when PII_SENTINEL=report or strict. Returns the entry (None when off or
    failed). `extra`: fields added to the entry (strict mode: what it hid before the call went)."""
    if not enabled():
        return None
    try:
        t0 = time.perf_counter()
        request = _request.get() or {}
        result = scan(system, messages, known=_known())
        entry = {
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "purpose": purpose or "",
            "label": label or "",
            "model": model_id or "",
            "session_id": request.get("session_id"),
            "organization_ids": request.get("organization_ids") or [],
            "messages": len(messages or []),
            **result,
            **(extra or {}),
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
