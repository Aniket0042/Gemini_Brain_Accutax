"""
ner.py — Names in the user's own words that the books do not know (PII plan, the red-team gap pr28/pr29).

The contact dictionary only knows the names in the organizations' books. A user may type any other name: "Do we
have bills from Al Noor Trading LLC?", "Rajesh Kumar from our team asked...". This finds those by name recognition
(spaCy en_core_web_sm, the model Presidio already loads), on the user's questions only, never on data.

What counts as a name, so that ordinary finance questions keep their words:
  - a PERSON entity;
  - any other entity spaCy finds (it often calls a person a place or a company) when it carries a legal form
    ("LLC", "FZE", "Trading") or stands right after a filter word ("bills from X", "paid to X", "customer X");
  - capitalized words ending in a legal form, found by pattern, in case spaCy missed them.
And what never counts: finance terms and acronyms (VAT, TRN, AED, P&L, YTD), months and days, places, report words
(Rank, Chart, Statement), public bodies (policy.public_entity), anything with a digit, '@' or a placeholder, words
in lower case, and text not in Latin script (spaCy reads English only; Arabic names are left to the dictionary).

A false positive costs little: the placeholder goes back to the user's exact text in tool inputs and the answer, so
the model only loses what the word meant. The unit tests hold a list of ordinary questions where nothing may match.
If spaCy cannot be loaded, the pattern for legal forms still runs.
"""
from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from typing import Any, List, Optional

from gemini_brain.privacy.policy import LEGAL_SUFFIXES, public_entity

logger = logging.getLogger("gemini_brain.privacy.ner")

MODEL = "en_core_web_sm"
#: Longest text searched: questions are short, and a long paste is mostly a file's text.
MAX_CHARS = 2000


@dataclass(frozen=True)
class Found:
    start: int
    end: int
    kind: str          # PERSON | PARTY
    text: str


# ── Vocabulary that is never a name ─────────────────────────────────────────

_LEGAL_WORDS = {
    "llc", "l.l.c", "l.l.c.", "fze", "fzco", "fzc", "fz", "fz-llc", "ltd", "ltd.", "limited", "pvt", "pvt.", "inc",
    "inc.", "corp", "corp.", "corporation", "plc", "pjsc", "psc", "wll", "w.l.l", "spc", "llp", "opc", "dmcc", "est",
    "est.", "establishment", "gmbh", "trading", "co", "co.",
}

FINANCE_TERMS = {
    "vat", "trn", "aed", "usd", "inr", "eur", "gbp", "sar", "usdt", "p&l", "pnl", "p", "l", "ytd", "mtd", "qtd", "fy",
    "dso", "dpo", "dio", "ebitda", "ebit", "gst", "tds", "tcs", "ct", "kpi", "roi", "gl", "ar", "ap", "coa", "cogs",
    "opex", "capex", "iban", "swift", "pan", "tan", "cin", "gstin", "eid", "kyc", "uid", "upi", "irn", "pdf", "csv",
    "xlsx", "excel", "fta", "uae", "gcc", "ksa", "rcm", "esr", "ubo", "aml", "asp", "pint", "peppol", "emaratax",
    "vat201", "q1", "q2", "q3", "q4", "h1", "h2", "corporate", "tax", "taxes", "income", "output", "input", "net",
    "gross", "revenue", "profit", "loss", "margin", "expense", "expenses", "cost", "costs", "cash", "flow", "ledger",
    "journal", "trial", "sheet", "statement", "statements", "return", "returns", "refund", "penalty", "penalties",
    "invoice", "invoices", "bill", "bills", "credit", "debit", "note", "notes", "receivable", "receivables",
    "payable", "payables", "aging", "ageing", "overdue", "outstanding", "balance", "balances", "bank", "banks",
    "account", "accounts", "accounting", "audit", "auditor", "budget", "forecast", "payroll", "salary", "salaries",
    "wps", "gratuity", "depreciation", "asset", "assets", "liability", "liabilities", "equity", "capital", "dividend",
    "interest", "loan", "loans", "inventory", "stock", "purchase", "purchases", "sales", "sale", "customer",
    "customers", "supplier", "suppliers", "vendor", "vendors", "payment", "payments", "receipt", "receipts",
    "transaction", "transactions", "entity", "entities", "organization", "organizations", "organisation", "org",
    "orgs", "company", "companies", "business", "group", "branch", "branches", "item", "items", "product", "products",
    "service", "services", "project", "projects", "category", "categories", "standard", "rated", "zero", "exempt",
    "reverse", "charge", "designated", "zone", "zones", "free", "mainland", "registration", "deregistration",
    "voluntary", "disclosure", "e-invoicing", "einvoicing", "accredited", "provider", "authority", "law", "decree",
    "article", "clause", "cabinet", "decision", "ministerial", "federal", "public", "clarification", "guide",
    "excise", "customs", "duty", "import", "imports", "export", "exports", "rate", "rates", "threshold", "period",
    "quarter", "quarterly", "month", "monthly", "year", "yearly", "annual", "week", "weekly", "daily", "today",
    "yesterday", "tomorrow", "total", "totals", "amount", "amounts", "average", "top", "rank", "ranking", "chart",
    "graph", "table", "report", "reports", "summary", "dashboard", "breakdown", "trend", "trends", "compare",
    "comparison", "show", "list", "give", "get", "find", "draft", "create", "submit", "email", "send", "export",
    "download", "file", "upload", "explain", "what", "which", "who", "how", "why", "when", "where", "is", "are",
    "can", "do", "does", "did", "please", "thanks", "thank", "hi", "hello", "ok", "yes", "no", "mr", "mrs", "ms",
    "dr", "sir", "madam", "team", "manager", "director", "ceo", "cfo", "accountant", "owner", "staff", "employee",
    "readiness", "filing", "ready", "position", "preliminary", "dsr", "ifrs", "gaap", "ias", "consolidated",
    "segment", "region", "regions", "currency", "exchange", "fx", "forex", "crypto", "bitcoin",
}

PLACES = {
    "uae", "dubai", "abu", "dhabi", "sharjah", "ajman", "fujairah", "ras", "al", "khaimah", "rak", "umm", "quwain",
    "ain", "emirates", "emirate", "jebel", "ali", "jafza", "dafza", "dso", "kizad", "hamriyah", "saif", "deira",
    "bur", "karama", "jumeirah", "marina", "business", "bay", "downtown", "india", "mumbai", "delhi", "new",
    "bangalore", "bengaluru", "chennai", "kolkata", "hyderabad", "pune", "ahmedabad", "kerala", "gujarat",
    "maharashtra", "tamil", "nadu", "karnataka", "saudi", "arabia", "riyadh", "jeddah", "oman", "muscat", "qatar",
    "doha", "bahrain", "manama", "kuwait", "egypt", "cairo", "jordan", "lebanon", "pakistan", "karachi", "lahore",
    "bangladesh", "sri", "lanka", "nepal", "philippines", "manila", "china", "hong", "kong", "singapore", "malaysia",
    "japan", "korea", "usa", "us", "america", "united", "states", "uk", "kingdom", "britain", "england", "london",
    "europe", "eu", "germany", "france", "italy", "spain", "turkey", "russia", "africa", "nigeria", "kenya", "asia",
    "middle", "east", "gulf", "mena", "gcc", "north", "south", "west", "western", "eastern", "northern", "southern",
    "indian", "emirati", "arab", "arabic", "english", "american", "british", "european", "chinese", "local",
    "foreign", "international", "national", "domestic", "overseas",
}

MONTHS_DAYS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "wed", "thu", "fri", "sat",
    "sun", "ramadan", "eid", "diwali", "christmas", "weekend", "weekday",
}

_NEVER = FINANCE_TERMS | PLACES | MONTHS_DAYS | {s for s in LEGAL_SUFFIXES if " " not in s} | _LEGAL_WORDS

#: Words right before a name that make any entity there a name ("bills from X", "paid to X", "customer X").
CUE_WORDS = {
    "from", "to", "by", "with", "for", "pay", "paid", "paying", "supplier", "vendor", "customer", "client", "payee",
    "named", "called", "contact", "party", "counterparty", "owe", "owes", "owed", "bill", "invoice", "about",
}
_SKIP_BEFORE_CUE = {"the", "our", "my", "their", "his", "her", "a", "an", "mr", "mrs", "ms", "dr", "m/s"}

#: Words dropped from the start of an entity ("Email Theodore", "the Al Noor ..."). Places are not: "Al" starts names.
_LEAD = FINANCE_TERMS | MONTHS_DAYS | CUE_WORDS | _SKIP_BEFORE_CUE

#: Entity labels considered. PERSON always; the others only with a legal form or a cue word.
_OTHER_LABELS = {"ORG", "GPE", "FAC", "NORP", "LOC", "PRODUCT", "WORK_OF_ART", "EVENT"}

_LATIN_WORD = re.compile(r"^[A-Za-zÀ-ɏ][A-Za-zÀ-ɏ&.'\-]*$")
#: Capitalized words ending in a legal form: "Al Noor Trading LLC", "Zenith Foods FZE", "Kumar & Sons Pvt Ltd".
_LEGAL_RX = re.compile(
    r"\b((?:(?:[A-Z][\w'\-]*|&|of)\s+){0,5}[A-Z][\w'\-]*)\s+"
    r"((?:General\s+)?Trading(?:\s+(?:Co\.?|Company|LLC|L\.L\.C\.?|Est\.?|FZE|FZCO))?|L\.?L\.?C\.?|FZ[- ]?LLC|FZCO|FZE|"
    r"FZC|(?:Pvt\.?|Private)\s+(?:Ltd\.?|Limited)|Ltd\.?|Limited|Inc\.?|Corp\.?|PJSC|PSC|W\.?L\.?L\.?|DMCC|LLP|"
    r"GmbH|PLC|Est\.?|Establishment)(?![\w])")


# ── The model ───────────────────────────────────────────────────────────────

_lock = threading.Lock()
_nlp: Any = None
_failed = False


def _model() -> Any:
    """spaCy's English pipeline without the parts not needed for entities, loaded once. None if unavailable."""
    global _nlp, _failed
    if _nlp is not None or _failed:
        return _nlp
    with _lock:
        if _nlp is None and not _failed:
            try:
                import spacy
                _nlp = spacy.load(MODEL, disable=["parser", "lemmatizer"])
            except Exception as e:  # noqa: BLE001 - the legal-form pattern still runs
                _failed = True
                logger.warning("privacy ner: %s not loaded, only legal forms are found by pattern: %s", MODEL, e)
    return _nlp


def warm() -> None:
    """Load the model now (at startup), so the first question does not wait for it."""
    _model()


# ── Finding ─────────────────────────────────────────────────────────────────

def _words(text: str, start: int, end: int) -> List[re.Match]:
    return list(re.finditer(r"\S+", text[start:end]))


def _trim(text: str, start: int, end: int) -> Optional[tuple]:
    """The span without words that are no part of a name around it ("our", "the", "Email", a trailing "'s" or
    full stop), or None when nothing is left."""
    words = [(start + m.start(), start + m.end(), m.group(0)) for m in _words(text, start, end)]

    def bare(w: str) -> str:
        return re.sub(r"(?:'s|’s)$", "", w.strip(".,;:!?()\"'“”")).casefold()

    while words and bare(words[0][2]) in _LEAD:
        words.pop(0)
    while words and (bare(words[-1][2]) in _SKIP_BEFORE_CUE or bare(words[-1][2]) in CUE_WORDS):
        words.pop()
    if not words:
        return None
    s, e = words[0][0], words[-1][1]
    while e > s and text[e - 1] in ".,;:!?)\"'”":
        if text[e - 1] == "." and "." in text[s:e - 1].split()[-1]:
            break  # "L.L.C." keeps its last dot; "Ltd." at the end of a sentence does not
        e -= 1
    m = re.search(r"(?:'s|’s)$", text[s:e])
    if m:
        e = s + m.start()
    return (s, e) if e > s else None


def _plausible(name: str) -> bool:
    """True when `name` can be a person's or company's name, not a term, place, month or report word."""
    words = name.split()
    if not words or len(name) < 3:
        return False
    if any(ch.isdigit() for ch in name) or any(ch in name for ch in "@[]_/\\#%$:=<>{}|"):
        return False
    if not all(_LATIN_WORD.match(w) or w == "&" for w in words):
        return False
    if not any(w[0].isupper() for w in words):
        return False
    folded = [w.strip(".'-").casefold() for w in words]
    if all(w in _NEVER or w in ("&", "and", "of", "the") for w in folded):
        return False
    if len(words) == 1 and words[0].isupper() and len(words[0]) <= 4:
        return False  # an acronym: VAT, DSO, KYC
    if public_entity(name):
        return False
    return True


def _has_legal_form(name: str) -> bool:
    return any(w.strip(",").casefold() in _LEGAL_WORDS for w in name.split()[1:])


def _after_cue(text: str, start: int) -> bool:
    """True when the word before `start` (skipping "the", "our", "Mr") is a filter word."""
    before = re.findall(r"[\w/]+", text[max(0, start - 40):start].casefold())
    while before and before[-1] in _SKIP_BEFORE_CUE:
        before.pop()
    return bool(before) and before[-1] in CUE_WORDS


_JOIN = re.compile(r"\s*(?:,|\band\b|&)\s*", re.IGNORECASE)


def _split(text: str, start: int, end: int, kind: str) -> List[tuple]:
    """Two companies in one entity ("Alpha Tech LLC and Beta Supplies FZE") as two: split after a legal form."""
    for m in _JOIN.finditer(text, start, end):
        before = text[start:m.start()].split()
        if before and before[-1].strip(".,").casefold() in _LEGAL_WORDS and m.end() < end:
            return [(start, m.start(), kind)] + _split(text, m.end(), end, kind)
    return [(start, end, kind)]


def find(text: Any) -> List[Found]:
    """Names in a question that may be a person or a company, in order and not overlapping."""
    if not isinstance(text, str) or not text.strip():
        return []
    text = text[:MAX_CHARS]
    raw: List[tuple] = []           # (start, end, kind)
    nlp = _model()
    if nlp is not None:
        try:
            doc = nlp(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("privacy ner: spaCy failed on a question: %s", e)
            doc = None
        for ent in (doc.ents if doc is not None else ()):
            if ent.label_ != "PERSON" and ent.label_ not in _OTHER_LABELS:
                continue
            span = _trim(text, ent.start_char, ent.end_char)
            if span is None:
                continue
            s, e = span
            name = text[s:e]
            legal = _has_legal_form(name)
            if ent.label_ == "PERSON" or legal or _after_cue(text, s):
                raw.append((s, e, "PARTY" if legal or ent.label_ != "PERSON" else "PERSON"))
    for m in _LEGAL_RX.finditer(text):
        span = _trim(text, m.start(), m.end())
        if span is not None and _has_legal_form(text[span[0]:span[1]]):
            raw.append((span[0], span[1], "PARTY"))
    raw = [part for r in raw for part in _split(text, *r)]
    # Longest first, then keep the ones that do not overlap a kept one.
    out: List[Found] = []
    for s, e, kind in sorted(raw, key=lambda r: (-(r[1] - r[0]), r[0])):
        name = text[s:e]
        if not _plausible(name) or any(f.start < e and s < f.end for f in out):
            continue
        out.append(Found(s, e, kind, name))
    return sorted(out, key=lambda f: f.start)
