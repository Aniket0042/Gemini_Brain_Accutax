"""
dictionary.py — The names of an organization's contacts, to find them anywhere in text (PII plan, phase 1).

Supplier and customer names are not patterns: "Al Noor Trading" looks like any other words. So the privacy
layer learns them from the books: the distinct supplier, customer, payment counterparty and bank payee names of
each selected organization, read from Cube with the same tenant guard as every figure. The organizations' own
names are added per request.

`Dictionary.find(text)` returns every place a known name appears, whatever its spelling: case, punctuation,
'&' or 'and', and a legal form at the end ("Al Noor Trading L.L.C" finds "AL NOOR TRADING LLC"). With
`partial=True` it also finds a single distinctive word of a name ("futtaim") when no other name has that word;
that is for questions, where users type part of a name.

Generic names ("Cash Customer", "Misc") and public bodies, banks and utilities (policy.public_entity) are left
out: they identify no private party, and the model needs them to read a payment.

Names are cached per organization for CACHE_SECONDS. `warm()` loads them in the background, so a request
never waits for the dictionary unless it asks to.
"""
from __future__ import annotations

import concurrent.futures
import contextvars
import logging
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from gemini_brain.privacy.policy import LEGAL_SUFFIXES, normalize_name, public_entity

logger = logging.getLogger("gemini_brain.privacy.dictionary")

CACHE_SECONDS = 600.0
LOAD_TIMEOUT_SECONDS = 8.0
MAX_NAMES_PER_SOURCE = 20000
#: Cube refuses a query whose limit is above this ("The query limit has been exceeded"), so a request for many
#: organizations at once is capped here, never above it.
CUBE_MAX_ROWS = 50000
#: Normalized names shorter than this are not searched for: too many false matches.
MIN_NAME_CHARS = 3
#: Names shorter than this ("Sai", "ABC") count only where the text writes them with a capital, so a short name that
#: is also a word ("sai", "abc" in prose) is left alone.
MIN_LOWERCASE_CHARS = 4
#: A word of a name found on its own (partial=True) must be at least this long and belong to one name only.
MIN_PARTIAL_CHARS = 5

#: (Cube view, dimension, kind) the names are read from. A view the deployment lacks is skipped.
SOURCES: Sequence[Tuple[str, str, str]] = (
    ("purchases", "vendor_name", "SUPPLIER"),
    ("payables", "vendor_name", "SUPPLIER"),
    ("sales", "customer_name", "CUSTOMER"),
    ("receivables", "customer_name", "CUSTOMER"),
    ("payments", "counterparty_name", "PARTY"),
    ("bank", "payee", "PARTY"),
)

GENERIC_NAMES = {
    "cash", "cash customer", "cash sales", "cash sale", "walk in", "walk in customer", "walkin customer", "misc",
    "miscellaneous", "general", "customer", "supplier", "vendor", "various", "others", "other", "sundry",
    "sundry creditors", "sundry debtors", "unknown", "test", "sales", "purchase", "purchases", "none", "null",
    "opening balance", "petty cash", "self", "internal", "staff", "employee", "employees", "bank", "owner",
}

#: Words too common in business names or in questions to stand for one name on their own.
COMMON_WORDS = {
    "trading", "general", "services", "service", "company", "international", "group", "holding", "holdings",
    "solutions", "technologies", "technology", "enterprises", "enterprise", "industries", "industrial",
    "contracting", "contractors", "consultants", "consulting", "consultancy", "foods", "food", "logistics",
    "electronics", "electrical", "electric", "middle", "emirates", "emirati", "dubai", "sharjah", "ajman",
    "fujairah", "global", "national", "united", "arabian", "royal", "golden", "supplies", "supply", "suppliers",
    "systems", "system", "products", "product", "store", "stores", "market", "markets", "restaurant", "cafeteria",
    "transport", "travel", "tourism", "building", "materials", "material", "equipment", "engineering", "marketing",
    "media", "digital", "software", "network", "networks", "properties", "property", "real", "estate", "investment",
    "investments", "capital", "finance", "financial", "insurance", "medical", "pharmacy", "health", "healthcare",
    "clinic", "hospital", "school", "education", "training", "printing", "furniture", "fashion", "textiles",
    "jewellery", "jewelry", "motors", "automobiles", "auto", "cars", "rental", "rentals", "cleaning", "security",
    "facilities", "management", "maintenance", "projects", "partners", "associates", "brothers", "sons", "office",
    "accounts", "account", "invoice", "invoices", "payment", "payments", "customer", "customers", "vendor", "vendors",
    "supplier", "purchase", "purchases", "sales", "total", "amount", "month", "quarter", "report", "balance",
    "bills", "money", "which", "where", "there", "their", "about", "these", "those", "would", "could", "should",
    "first", "second", "third", "between", "against", "during", "before", "after", "since", "until", "largest",
    "highest", "lowest", "biggest", "smallest", "average", "india", "indian", "private", "limited", "east",
    "west", "north", "south", "world", "worldwide", "express", "smart", "prime", "star", "plus", "best", "green",
    "white", "black", "silver", "crystal", "pearl", "diamond", "falcon", "eagle", "ocean", "marine", "desert",
}

@dataclass(frozen=True)
class Entry:
    name: str
    kind: str          # SUPPLIER | CUSTOMER | PARTY | ORG
    key: str           # normalized name


@dataclass(frozen=True)
class NameSpan:
    start: int
    end: int
    kind: str
    key: str
    text: str
    score: float
    partial: bool = False


#: Words as normalize_name splits them; '&' is the word "and".
_WORD = re.compile(r"[^\W_]+|&")
_SUFFIXES = {tuple(s.split()) for s in LEGAL_SUFFIXES}
_MAX_SUFFIX_WORDS = max(len(s) for s in _SUFFIXES)
#: Most characters between two words of one name ("Al-Noor", "L.L.C", "Smith & Sons"). A line break never joins.
MAX_GAP_CHARS = 3


def _words(text: str) -> List[Tuple[int, int, str]]:
    return [(m.start(), m.end(), "and" if m.group(0) == "&" else m.group(0).casefold()) for m in _WORD.finditer(text)]


def _joined(text: str, tokens: List[Tuple[int, int, str]], first: int, count: int) -> bool:
    """True when `count` words from `first` on stand together: short gaps, no line break between them."""
    for k in range(first, first + count - 1):
        gap = text[tokens[k][1]:tokens[k + 1][0]]
        if len(gap) > MAX_GAP_CHARS or "\n" in gap:
            return False
    return True


class Dictionary:
    """The known names of some organizations, indexed by word for search. Search time grows with the text, not with
    the number of names: each word is looked up, and only words that start a name are tried further."""

    def __init__(self, entries: Iterable[Entry] = ()):
        self.entries: Dict[str, Entry] = {}
        for e in entries:
            if not e.key or len(e.key) < MIN_NAME_CHARS or e.key in GENERIC_NAMES or public_entity(e.key):
                continue
            known = self.entries.get(e.key)
            if known is None:
                self.entries[e.key] = e
            elif known.kind != e.kind and "ORG" not in (known.kind, e.kind):
                # A supplier who is also a customer is a party; an organization's own name stays ORG.
                self.entries[e.key] = Entry(known.name, "PARTY", e.key)
            elif e.kind == "ORG":
                self.entries[e.key] = e
        self._first = {key.split()[0] for key in self.entries}
        self._max_words = max((len(key.split()) for key in self.entries), default=0)
        self._partial_words = self._index_partial()

    def __len__(self) -> int:
        return len(self.entries)

    def _index_partial(self) -> Dict[str, str]:
        """Distinctive words -> the one name they belong to."""
        owners: Dict[str, set] = {}
        for key in self.entries:
            for word in set(key.split()):
                if len(word) >= MIN_PARTIAL_CHARS and word not in COMMON_WORDS and word not in LEGAL_SUFFIXES \
                        and not word.isdigit():
                    owners.setdefault(word, set()).add(key)
        return {w: next(iter(keys)) for w, keys in owners.items() if len(keys) == 1}

    def _name_at(self, text: str, tokens: List[Tuple[int, int, str]], i: int) -> Optional[Tuple[Entry, int]]:
        """The longest known name starting at word i, and how many words it takes."""
        words = [t[2] for t in tokens[i:i + self._max_words]]
        for size in range(len(words), 0, -1):
            entry = self.entries.get(" ".join(words[:size]))
            if entry is not None and _joined(text, tokens, i, size):
                if len(entry.key) < MIN_LOWERCASE_CHARS and not text[tokens[i][0]].isupper():
                    continue
                return entry, size
        return None

    def _suffix_end(self, text: str, tokens: List[Tuple[int, int, str]], j: int) -> int:
        """The word index after any legal forms that follow a name at word j ("LLC", "L.L.C", "Pvt Ltd")."""
        while j < len(tokens):
            for size in range(min(_MAX_SUFFIX_WORDS, len(tokens) - j), 0, -1):
                if tuple(t[2] for t in tokens[j:j + size]) in _SUFFIXES and _joined(text, tokens, j - 1, size + 1):
                    j += size
                    break
            else:
                return j
        return j

    def find(self, text: str, partial: bool = False) -> List[NameSpan]:
        """Known names in `text`, in order and not overlapping. Whole names score 0.95, single words 0.6.
        A legal form after the name belongs to the match ("L.L.C." with its dots); a sentence's full stop does not."""
        if not text or not self.entries:
            return []
        tokens = _words(text)
        spans: List[NameSpan] = []
        i = 0
        while i < len(tokens):
            word = tokens[i][2]
            found = self._name_at(text, tokens, i) if word in self._first else None
            if found is not None:
                entry, size = found
                j = self._suffix_end(text, tokens, i + size)
                start, end = tokens[i][0], tokens[j - 1][1]
                if j > i + size and text[end:end + 1] == "." and not text[end + 1:end + 2].isalnum():
                    end += 1
                spans.append(NameSpan(start, end, entry.kind, entry.key, text[start:end], 0.95))
                i = j
                continue
            if partial and word in self._partial_words:
                entry = self.entries[self._partial_words[word]]
                start, end = tokens[i][0], tokens[i][1]
                spans.append(NameSpan(start, end, entry.kind, entry.key, text[start:end], 0.6, partial=True))
            i += 1
        return spans


# ── Loading and cache ───────────────────────────────────────────────────────

_lock = threading.Lock()
_names: Dict[int, Tuple[float, List[Entry]]] = {}       # org id -> (loaded at, entries)
_loading: set = set()
_built: "OrderedDict[tuple, Tuple[float, Dictionary]]" = OrderedDict()
_BUILT_MAX = 32


def load_many(org_ids: Sequence[int], *, subject: str) -> Dict[int, List[Entry]]:
    """The distinct contact names of these organizations, from Cube: one query per source for all of them, the
    sources at the same time. A source that fails is skipped and logged."""
    from gemini_brain.semantic import cube_client

    orgs = sorted({int(o) for o in org_ids})
    out: Dict[int, List[Entry]] = {o: [] for o in orgs}
    if not orgs:
        return out

    def one(source: Tuple[str, str, str]) -> List[Tuple[int, Entry]]:
        view, dimension, kind = source
        member, org_key = f"{view}.{dimension}", f"{view}.organization_id"
        limit = min(MAX_NAMES_PER_SOURCE * len(orgs), CUBE_MAX_ROWS)
        result = cube_client.load({"dimensions": [member], "limit": limit},
                                  organization_ids=orgs, subject=subject,
                                  deadline=time.monotonic() + LOAD_TIMEOUT_SECONDS)
        if len(result.rows) >= limit:
            logger.warning("privacy dictionary: %s hit the %d-row limit for orgs %s; some names are not known",
                           member, limit, orgs)
        found = []
        for row in result.rows:
            name = str(row.get(member) or "").strip()
            try:
                org = int(Decimal(str(row.get(org_key))))
            except (InvalidOperation, ValueError, TypeError):
                continue
            if name and org in out:
                found.append((org, Entry(name, kind, normalize_name(name))))
        return found

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SOURCES), thread_name_prefix="privacy-names") as pool:
        futures = {pool.submit(contextvars.copy_context().run, one, s): s for s in SOURCES}
        for future, (view, dimension, _) in futures.items():
            try:
                for org, entry in future.result():
                    out[org].append(entry)
            except Exception as e:  # noqa: BLE001 - one missing view must not lose the others
                logger.info("privacy dictionary: %s.%s not read for orgs %s: %s", view, dimension, orgs, e)
    return out


def load_names(org_id: int, *, subject: str) -> List[Entry]:
    """The distinct contact names of one organization."""
    return load_many([org_id], subject=subject)[int(org_id)]


def _fresh(org_id: int) -> Optional[List[Entry]]:
    hit = _names.get(int(org_id))
    return hit[1] if hit and time.monotonic() - hit[0] < CACHE_SECONDS else None


def _load_into_cache(org_ids: Sequence[int], subject: str) -> None:
    orgs = [int(o) for o in org_ids]
    try:
        loaded = load_many(orgs, subject=subject)
        with _lock:
            for org, entries in loaded.items():
                _names[org] = (time.monotonic(), entries)
    except Exception as e:  # noqa: BLE001
        logger.warning("privacy dictionary: orgs %s not loaded: %s", orgs, e)
    finally:
        with _lock:
            _loading.difference_update(orgs)


def warm(org_ids: Sequence[int], *, subject: str) -> None:
    """Load the names of these organizations in the background, unless cached or already loading."""
    with _lock:
        todo = [int(o) for o in org_ids if _fresh(o) is None and int(o) not in _loading]
        _loading.update(todo)
    if todo:
        threading.Thread(target=_load_into_cache, args=(todo, subject), name="privacy-dictionary",
                         daemon=True).start()


def for_orgs(org_ids: Sequence[int], *, subject: str = "", org_names: Iterable[str] = (),
             fetch: bool = False) -> Dictionary:
    """The dictionary of these organizations plus their own names. fetch=False uses only what is cached (an
    organization not loaded yet adds nothing); fetch=True loads what is missing first, in this thread."""
    orgs = sorted({int(o) for o in org_ids})
    if fetch:
        with _lock:
            missing = [o for o in orgs if _fresh(o) is None]
        if missing:
            _load_into_cache(missing, subject)
    names = tuple(sorted({str(n) for n in org_names if n}))
    with _lock:
        parts = [(o, _names.get(o)) for o in orgs]
        stamp = tuple((o, p[0] if p else None) for o, p in parts)
        key = (stamp, names)
        hit = _built.get(key)
        if hit is not None:
            _built.move_to_end(key)
            return hit[1]
        entries = [e for _, p in parts if p and time.monotonic() - p[0] < CACHE_SECONDS for e in p[1]]
    entries += [Entry(n, "ORG", normalize_name(n)) for n in names]
    built = Dictionary(entries)
    with _lock:
        _built[key] = (time.monotonic(), built)
        while len(_built) > _BUILT_MAX:
            _built.popitem(last=False)
    return built


def clear() -> None:
    """Forget every cached name (tests)."""
    with _lock:
        _names.clear()
        _loading.clear()
        _built.clear()
