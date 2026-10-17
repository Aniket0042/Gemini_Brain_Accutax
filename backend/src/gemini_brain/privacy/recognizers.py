"""
recognizers.py — Finds personal identifiers in text by pattern (PII plan, phase 1).

UAE and India identifiers from the PII table, each with the check its format allows: a check digit (Luhn,
mod-97, Verhoeff, GSTIN mod-36), a fixed prefix or character set, or a context word near the match
("passport", "utr", "irn"). Formats too loose to tell apart from document numbers and amounts on their own
(a bare 10-digit Indian mobile, a 12-digit RRN, a 64-hex IRN) are only found next to a context word.

`find(text)` returns non-overlapping spans with a score. A failed check digit lowers the score instead of
dropping the match where the format itself is distinctive (Emirates ID starts 784): a real value with a
typo is still personal data. Pure regex, no NLP model: names are found by the contact dictionary instead.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

#: Spans scoring below this are dropped.
THRESHOLD = 0.5
#: Characters before a match searched for its context words.
CONTEXT_CHARS = 48


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    kind: str
    text: str
    score: float


# ── Check digits ────────────────────────────────────────────────────────────

def luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def iban_ok(value: str) -> bool:
    s = re.sub(r"\s", "", value).upper()
    if len(s) < 15:
        return False
    moved = s[4:] + s[:4]
    return int("".join(str(int(c, 36)) for c in moved)) % 97 == 1


_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5], [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7], [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3], [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4], [5, 8, 0, 3, 7, 9, 6, 1, 4, 2],
    [8, 9, 1, 6, 0, 4, 3, 5, 2, 7], [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8],
]


def verhoeff(digits: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(digits)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


_B36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
#: GST state codes in use (01-38), 97 (other territory) and 99 (centre jurisdiction).
_GST_STATES = {f"{n:02d}" for n in range(1, 39)} | {"97", "99"}


def gstin_ok(value: str) -> bool:
    v = value.upper()
    if v[:2] not in _GST_STATES or not pan_ok(v[2:12]):
        return False
    total = 0
    for i, ch in enumerate(v[:14]):
        product = _B36.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _B36[(36 - total % 36) % 36] == v[14]


#: The 4th character of a PAN says who holds it: person, company, HUF, firm, AOP, trust, BOI, local
#: authority, artificial juridical person, government, LLP.
_PAN_HOLDER = set("PCHFATBLJGE")


def pan_ok(value: str) -> bool:
    return len(value) == 10 and value[3].upper() in _PAN_HOLDER


def card_ok(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 13 <= len(digits) <= 19 and digits[0] in "3456" and len(set(digits)) > 1 and luhn(digits)


#: UPI handles of the main payment apps and banks. Any other "x@y" without a dot is not taken as a UPI id.
UPI_PSPS = {
    "upi", "ybl", "ibl", "axl", "paytm", "apl", "okaxis", "oksbi", "okhdfcbank", "okicici", "icici", "sbi", "hdfcbank",
    "axisbank", "kotak", "kbl", "barodampay", "idfcbank", "indus", "federal", "pnb", "unionbank", "yesbank", "rbl",
    "aubank", "jupiteraxis", "fbl", "ikwik", "freecharge", "airtel", "jio", "waicici", "wahdfcbank", "wasbi", "waaxis",
    "abfspay", "axisb", "pingpay", "timecosmos", "slice", "naviaxis", "superyes",
}

# ── Rules ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: re.Pattern
    #: Score when the check passes (or there is no check).
    score: float
    #: Check on the matched text; None = format only.
    check: Optional[Callable[[str], bool]] = None
    #: Score when the check fails; 0 drops the match.
    failed_score: float = 0.0
    #: Words that, found just before the match, raise its score to 0.95.
    context: Tuple[str, ...] = ()
    #: The match counts only next to a context word.
    needs_context: bool = False


def _rx(pattern: str, flags: int = 0) -> re.Pattern:
    return re.compile(pattern, flags)


#: IFSC bank codes of the larger Indian banks. A code outside this list counts only next to "IFSC": the bare
#: format also fits document numbers such as BILL0000123.
IFSC_BANKS = {
    "SBIN", "HDFC", "ICIC", "UTIB", "KKBK", "PUNB", "BARB", "CNRB", "UBIN", "IDIB", "IOBA", "YESB", "INDB", "FDRL",
    "IDFB", "BKID", "MAHB", "UCBA", "CBIN", "PSIB", "KARB", "SIBL", "RATN", "AUBL", "CIUB", "DBSS", "HSBC", "SCBL",
    "CITI", "DEUT", "ESFB", "BDBL", "JAKA", "TMBL", "KVBL", "CSBK", "DLXB", "NTBL", "IBKL", "UJVN", "JSFB", "AIRP",
    "PYTM", "FINO", "ABHY", "SVCB", "COSB", "SRCB", "APGB", "KSCB", "MSCI",
}

# Every pattern has boundaries, so a value is never read out of a longer word or number:
# "INV-BULK-26-20241216180347-114231" and "1,234,567.89" stay whole.
RULES: Sequence[Rule] = (
    # ── Contact details ──
    Rule("EMAIL", _rx(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,24}(?!\w|\.\w)"),
         0.9),
    Rule("UPI_ID", _rx(r"(?<![\w.-])[A-Za-z0-9._-]{2,256}@([A-Za-z]{2,64})(?![\w@]|\.\w)"), 0.85,
         check=lambda v: v.rsplit("@", 1)[1].lower() in UPI_PSPS, context=("upi", "vpa")),
    Rule("PHONE", _rx(r"(?<![\w.@+-])(?:\+971|00971|0)[\s-]?5\d(?:[\s-]?\d){7}(?!\w|[.,]\d)"), 0.85,
         context=("mob", "mobile", "tel", "phone", "call", "whatsapp", "contact")),
    Rule("PHONE", _rx(r"(?<![\w+])(?:\+91|0091)[\s-]?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), 0.85,
         context=("mob", "mobile", "ph", "phone", "call", "whatsapp", "tel", "contact")),
    Rule("PHONE", _rx(r"(?<![\w+.,-])(?:0)?[6-9]\d{4}[\s-]?\d{5}(?!\w|[.,]\d)"), 0.4,
         context=("mob", "mobile", "ph", "phone", "call", "whatsapp", "tel", "contact"), needs_context=True),
    # ── UAE ──
    Rule("TRN", _rx(r"(?<![\w-])100\d{12}(?![\w-])"), 0.8, context=("trn", "tax registration", "vat no", "vat number",
                                                                    "vat reg", "tax id", "trn no")),
    Rule("EMIRATES_ID", _rx(r"(?<![\w-])784-?\d{4}-?\d{7}-?\d(?![\w-])"), 0.95,
         check=lambda v: luhn(re.sub(r"\D", "", v)), failed_score=0.75, context=("eid", "emirates id", "id no")),
    Rule("IBAN", _rx(r"(?<!\w)AE\d{2}(?:\s?\d){19}(?!\w)", re.I), 0.95, check=iban_ok),
    # ── Cards ──
    Rule("CARD", _rx(r"(?<![\w.,-])(?<!\d\s)(?:\d[ -]?){12,18}\d(?!\w|[.,]\d|[\s-]\d)"), 0.9, check=card_ok,
         context=("card", "visa", "master", "mastercard", "amex", "rupay")),
    # ── India ──
    Rule("GSTIN", _rx(r"(?<!\w)\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z](?!\w)"), 0.95, check=gstin_ok,
         context=("gstin", "gst no", "gst number", "gst")),
    Rule("PAN", _rx(r"(?<!\w)[A-Z]{5}\d{4}[A-Z](?!\w)"), 0.85, check=pan_ok,
         context=("pan", "permanent account")),
    Rule("AADHAAR", _rx(r"(?<![\w.,-])(?<!\d\s)[2-9]\d{3}[\s-]?\d{4}[\s-]?\d{4}(?!\w|[.,]\d|[\s-]\d)"), 0.85,
         check=lambda v: verhoeff(re.sub(r"\D", "", v)), context=("aadhaar", "aadhar", "uid", "uidai")),
    Rule("AADHAAR", _rx(r"(?<!\w)[Xx*]{4}[\s-]?[Xx*]{4}[\s-]?\d{4}(?!\w)"), 0.7),
    Rule("TAN", _rx(r"(?<!\w)[A-Z]{4}\d{5}[A-Z](?!\w)"), 0.4, context=("tan",), needs_context=True),
    Rule("CIN", _rx(r"(?<!\w)[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}(?!\w)"), 0.9, context=("cin",)),
    Rule("LLPIN", _rx(r"(?<!\w)[A-Z]{3}-\d{4}(?!\w)"), 0.4, context=("llpin", "llp id", "llp no"), needs_context=True),
    Rule("IFSC", _rx(r"(?<!\w)[A-Z]{4}0[A-Z0-9]{6}(?!\w)"), 0.85, check=lambda v: v[:4] in IFSC_BANKS,
         context=("ifsc",)),
    Rule("IFSC", _rx(r"(?<!\w)[A-Z]{4}0[A-Z0-9]{6}(?!\w)"), 0.4, context=("ifsc",), needs_context=True),
    Rule("UTR", _rx(r"(?<!\w)(?:[A-Z]{4}[A-Z0-9]{12}|[A-Z]{4}[A-Z0-9]{18}|\d{12})(?!\w)"), 0.4,
         context=("utr", "rrn", "neft", "rtgs", "imps", "ref no", "txn ref"), needs_context=True),
    Rule("EWAY_BILL", _rx(r"(?<!\w)\d{12}(?!\w)"), 0.4, context=("ewb", "e-way", "eway", "e way"), needs_context=True),
    Rule("IRN", _rx(r"(?<!\w)[0-9a-f]{64}(?!\w)", re.I), 0.4, context=("irn", "invoice reference number"),
         needs_context=True),
    # ── Passports ──
    Rule("PASSPORT", _rx(r"(?<!\w)[A-Z]\d{7}(?!\w)"), 0.4, context=("passport", "ppt", "passport no"),
         needs_context=True),
    Rule("PASSPORT", _rx(r"(?<!\w)(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{6,9}(?!\w)"), 0.4,
         context=("passport", "passport no"), needs_context=True),
)

_WORD_CACHE: Dict[Tuple[str, ...], re.Pattern] = {}


def _context_rx(words: Tuple[str, ...]) -> re.Pattern:
    rx = _WORD_CACHE.get(words)
    if rx is None:
        rx = re.compile(r"(?<![a-z])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z])", re.I)
        _WORD_CACHE[words] = rx
    return rx


def _has_context(text: str, start: int, words: Tuple[str, ...]) -> bool:
    return bool(words) and bool(_context_rx(words).search(text[max(0, start - CONTEXT_CHARS):start]))


def _scored(rule: Rule, text: str, m: re.Match) -> float:
    value = m.group(0)
    if rule.check is not None and not rule.check(value):
        return rule.failed_score
    near = _has_context(text, m.start(), rule.context)
    if rule.needs_context and not near:
        return 0.0
    return 0.95 if near else rule.score


def find(text: str, threshold: float = THRESHOLD, kinds: Optional[Sequence[str]] = None) -> List[Span]:
    """Identifiers in `text`, as non-overlapping spans in text order. On overlap the higher score wins, then the
    longer span: an IBAN is not also read as a card number, a GSTIN not also as a PAN."""
    if not text:
        return []
    found: List[Span] = []
    for rule in RULES:
        if kinds and rule.kind not in kinds:
            continue
        for m in rule.pattern.finditer(text):
            score = _scored(rule, text, m)
            if score >= threshold:
                found.append(Span(m.start(), m.end(), rule.kind, m.group(0), score))
    found.sort(key=lambda s: (-s.score, -(s.end - s.start), s.start))
    kept: List[Span] = []
    for span in found:
        if all(span.end <= k.start or span.start >= k.end for k in kept):
            kept.append(span)
    return sorted(kept, key=lambda s: s.start)
