"""
multi_org_followup.py — Resolve short multi-org follow-ups in code.

A follow-up such as "and last quarter?", "now only show the top 2", "same for
payables" or "drop Org A and redo" changes one thing about the previous
question. These used to go through a model rewrite, which kept the old period
("and last quarter?" re-ran this year), ignored "top 2", and could not drop an
organization at all, while the answer claimed it had.

Here the thread's user messages are folded in order, starting from the last
question that stands on its own: each follow-up may swap the period, set a
top/bottom N, name a new figure, or narrow the organizations. A follow-up with
any other content ("why is the second one so low?") is left to the model
rewrite, and resolve() returns None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

#: "top 2", "bottom 3"; "first 3 months" is a period, not a row count.
LIMIT = re.compile(
    r"\b(top|bottom|first|highest|lowest|best|worst|largest|smallest)\s+(\d{1,3})\b"
    r"(?!\s*(?:days?|weeks?|months?|quarters?|years?))",
    re.IGNORECASE,
)
_BOTTOM_WORDS = {"bottom", "lowest", "worst", "smallest"}

_EXCLUDE = re.compile(r"\b(drop|exclude|excluding|remove|without|except|leave\s+out|ignore|minus)\b", re.IGNORECASE)

#: Words a modifier-only follow-up may carry besides the period, count and org names.
_FILLER = frozenset("""
a about again all also an and are as at be but by can compare could do does figures for from give
how i in is it just leave let list me minus now numbers of ok okay on one ones only org orgs
organisation organisations organization organizations entities entity companies company others out
please rank ranked re redo rerun remaining remove rest result results run same see show so table that
the them then these this those to too us use using vs versus we what with without you drop exclude
excluding except ignore instead period instead
""".split())


def _period_phrase(text: str) -> Optional[str]:
    from gemini_brain.orchestrator.multi_org_dates import period_phrase

    return period_phrase(text or "")


@dataclass
class Modifiers:
    """What one message changes."""

    period: Optional[str] = None
    limit: Optional[Tuple[str, int]] = None  # ("top" | "bottom", n)
    exclude: List[int] = field(default_factory=list)
    only: List[int] = field(default_factory=list)
    residual: List[str] = field(default_factory=list)  # words that are none of the above


def _org_patterns(org_meta: Dict[int, Dict[str, Any]], org_ids: Iterable[int]) -> List[Tuple[int, "re.Pattern[str]"]]:
    """Each selected org's name as a pattern; longest first so "..._Org10" wins over "..._Org1"."""
    named = [(oid, str((org_meta.get(oid) or {}).get("name") or "")) for oid in org_ids]
    named = [(oid, name) for oid, name in named if name.strip()]
    named.sort(key=lambda x: -len(x[1]))
    return [(oid, re.compile(r"(?<!\w)" + re.escape(name) + r"(?!\w)", re.IGNORECASE)) for oid, name in named]


def strip_org_names(text: str, org_meta: Dict[int, Dict[str, Any]], org_ids: Iterable[int]) -> Tuple[str, List[int]]:
    """The text without the selected orgs' names, and the orgs it named, in text order."""
    found: List[Tuple[int, int]] = []
    for oid, pattern in _org_patterns(org_meta, org_ids):
        m = pattern.search(text)
        if m:
            found.append((m.start(), oid))
            text = pattern.sub(" ", text)
    return text, [oid for _, oid in sorted(found)]


def parse(text: str, org_meta: Dict[int, Dict[str, Any]], org_ids: Iterable[int]) -> Modifiers:
    """Split one message into the changes it makes and whatever else it says."""
    mods = Modifiers()
    rest, named = strip_org_names(text or "", org_meta, org_ids)
    if named:
        if _EXCLUDE.search(rest):
            mods.exclude = named
        else:
            mods.only = named
    phrase = _period_phrase(rest)
    if phrase:
        mods.period = phrase
        rest = rest.replace(phrase, " ")
    m = LIMIT.search(rest)
    if m:
        mods.limit = ("bottom" if m.group(1).lower() in _BOTTOM_WORDS else "top", int(m.group(2)))
        rest = LIMIT.sub(" ", rest)
    mods.residual = [w for w in re.findall(r"[a-z0-9&']+", rest.lower()) if w not in _FILLER]
    return mods


def is_modifier_only(mods: Modifiers) -> bool:
    return not mods.residual and bool(mods.period or mods.limit or mods.exclude or mods.only)


@dataclass
class Resolved:
    """The question to answer, and the organizations to answer it for."""

    question: str
    exclude: Set[int] = field(default_factory=set)
    only: Optional[Set[int]] = None

    def org_ids(self, org_ids: List[int]) -> List[int]:
        """The thread's orgs narrowed by the follow-ups; all of them if nothing would be left."""
        kept = [o for o in org_ids if o not in self.exclude and (self.only is None or o in self.only)]
        return kept or list(org_ids)


_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
#: A period that names a part of a year but no year: "Q2", "March", "H1".
_YEARLESS = re.compile(r"^(?:q[1-4]|h[12]|jan\w*|feb\w*|mar\w*|apr\w*|may|jun\w*|jul\w*|aug\w*|sep\w*|oct\w*"
                       r"|nov\w*|dec\w*)$", re.IGNORECASE)


def _with_period(question: str, period: Optional[str]) -> str:
    if not period:
        return question
    current = _period_phrase(question)
    if current:
        # "and Q2?" after "Q1 2025" means Q2 2025, not Q2 of this year.
        year = _YEAR.search(current)
        if year and _YEARLESS.match(period.strip()):
            period = f"{period.strip()} {year.group(0)}"
        return question.replace(current, period, 1)
    return f"{question.rstrip(' ?.!')} {period}"


def _with_limit(question: str, limit: Optional[Tuple[str, int]]) -> str:
    if not limit:
        return question
    return f"{LIMIT.sub(' ', question).rstrip(' ?.!')} {limit[0]} {limit[1]}"


def names_a_figure(text: str) -> bool:
    """Whether a message asks for a figure of its own ("same for payables")."""
    from gemini_brain.orchestrator.multi_org_metrics import is_scorecard_question, match_metrics, match_series

    return bool(match_metrics(text) or match_series(text) or is_scorecard_question(text))


def resolve(
    query: str,
    history: List[Dict[str, Any]],
    org_meta: Dict[int, Dict[str, Any]],
    org_ids: List[int],
    *,
    stands_alone,
) -> Optional[Resolved]:
    """The follow-up folded onto the thread's last self-contained question, or None.

    `stands_alone(text)` says whether a message needs no earlier context
    (multi_org.needs_rewrite negated). None means a model rewrite is needed.
    """
    users = [str(m.get("content") or "") for m in history if str(m.get("role") or "") == "user"]
    base_at = next((i for i in range(len(users) - 1, -1, -1) if stands_alone(users[i])), None)
    if base_at is None:
        return None

    def strip(text: str) -> str:
        return strip_org_names(text, org_meta, org_ids)[0]

    base = strip(users[base_at])
    period = _period_phrase(base)
    limit: Optional[Tuple[str, int]] = None
    exclude: Set[int] = set()
    only: Optional[Set[int]] = None
    first = parse(users[base_at], org_meta, org_ids)
    exclude |= set(first.exclude)
    only = set(first.only) if first.only else None

    later = users[base_at + 1:]
    for position, text in enumerate(later + [query]):
        mods = parse(text, org_meta, org_ids)
        if mods.residual:
            if not names_a_figure(strip(text)):
                if position == len(later):
                    return None  # the current message says something the fold cannot express
                continue  # an earlier "why is it low?" asked about the answer; it changed nothing
            # A new figure on the same terms: its own period if it names one.
            base, limit = strip(text), mods.limit or limit
            period = mods.period or period
        else:
            period = mods.period or period
            limit = mods.limit or limit
        exclude |= set(mods.exclude)
        if mods.only:
            only = set(mods.only)

    question = _with_limit(_with_period(base, period), limit)
    return Resolved(question=" ".join(question.split()), exclude=exclude, only=only)


def last_period(history: List[Dict[str, Any]]) -> Optional[str]:
    """The period of the most recent user message that names one."""
    for m in reversed(history):
        if str(m.get("role") or "") == "user":
            phrase = _period_phrase(str(m.get("content") or ""))
            if phrase:
                return phrase
    return None


def keep_period(rewritten: str, history: List[Dict[str, Any]]) -> str:
    """A model rewrite that dropped the thread's period gets it back.

    "why is the second one so low?" came back without "this year", so the
    figures silently switched from the full year to year-to-date.
    """
    if _period_phrase(rewritten):
        return rewritten
    return _with_period(rewritten, last_period(history))
