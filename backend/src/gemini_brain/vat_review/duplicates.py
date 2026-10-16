"""Duplicate bills: the same bill recorded twice (or more).

Three rules, from sure to unsure:
  same_number              high    the same supplier and the same document number
  same_total_near_date     medium  the same supplier and the same amount, dated within DAYS_APART days
  similar_vendor_same_day  low     supplier names that look alike, the same amount, the same date

A bill that carries no amount (or zero) is checked only by its number. Bills that were voided, cancelled, rejected or
never issued are left out and counted. Pure functions on a list of bill dicts; no I/O.
"""
from __future__ import annotations

import datetime as dt
import difflib
import re
from collections import defaultdict
from typing import Any, Dict, List, Sequence, Tuple

DAYS_APART = 3
NAME_SIMILARITY = 0.85
RULES = ("same_number", "same_total_near_date", "similar_vendor_same_day")
CONFIDENCE = {"same_number": "high", "same_total_near_date": "medium", "similar_vendor_same_day": "low"}
_NOT_A_BILL = re.compile(r"void|cancel|reject|draft|delet", re.I)
_LEGAL = re.compile(r"\b(l\.?l\.?c|fz[- ]?co|fze|ltd|limited|inc|co|company|est|establishment|trading|general trading)\b\.?", re.I)


def vendor_key(name: Any) -> str:
    """A supplier name for comparing: lower case, no legal suffix, no punctuation."""
    text = _LEGAL.sub(" ", str(name or "").lower())
    return re.sub(r"[^a-z0-9؀-ۿ]+", " ", text).strip()


def number_key(number: Any) -> str:
    """A document number for comparing: 'INV-0012' and 'inv12' are the same."""
    text = re.sub(r"[^a-z0-9]", "", str(number or "").lower())
    return re.sub(r"(?<![0-9])0+(?=[0-9])", "", text)


def _day(bill: Dict[str, Any]) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(bill.get("document_date") or "")[:10])
    except ValueError:
        return None


def _net(bill: Dict[str, Any]) -> float | None:
    value = bill.get("net_purchases")
    return round(float(value), 2) if isinstance(value, (int, float)) and abs(value) >= 0.005 else None


def usable(bills: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """The bills a review looks at, and how many were left out as not real bills."""
    kept = [b for b in bills if not _NOT_A_BILL.search(str(b.get("status") or ""))]
    return kept, len(bills) - len(kept)


def _similar(a: str, b: str) -> bool:
    if not a or not b or a == b:
        return False
    if min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= NAME_SIMILARITY


def _bill(b: Dict[str, Any]) -> Dict[str, Any]:
    return {k: b.get(k) for k in ("document_number", "document_id", "document_date", "document_type", "status",
                                  "vendor_name", "net_purchases", "input_vat")}


def _group(rule: str, members: List[Dict[str, Any]], why: str) -> Dict[str, Any]:
    members = sorted(members, key=lambda b: (str(b.get("document_date") or ""), str(b.get("document_number") or "")))
    nets = [n for n in (_net(b) for b in members) if n is not None]
    return {"rule": rule, "confidence": CONFIDENCE[rule], "why": why, "count": len(members),
            "vendor": members[0].get("vendor_name"), "bills": [_bill(b) for b in members],
            # What would be counted twice if every bill after the first is a repeat.
            "extra_amount": round(sum(nets) - nets[0], 2) if len(nets) > 1 else 0.0}


def find_duplicates(bills: Sequence[Dict[str, Any]], side: str = "purchases") -> List[Dict[str, Any]]:
    """Duplicate groups, most sure and largest first. `bills` are the usable bills of ONE organization.
    On sales the invoice number is the organization's own, so the same number twice is sure whoever the customer is."""
    groups: List[Dict[str, Any]] = []

    # 1. same supplier, same number
    by_number: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for b in bills:
        v, n = vendor_key(b.get("vendor_name")), number_key(b.get("document_number"))
        if n and (v or side == "sales"):
            by_number[("" if side == "sales" else v, n)].append(b)
    in_exact: Dict[int, int] = {}
    for members in by_number.values():
        if len(members) > 1:
            for b in members:
                in_exact[id(b)] = len(groups)
            why = (f"{len(members)} invoices carry the same number {members[0].get('document_number')}." if side == "sales"
                   else f"The same supplier has {len(members)} bills with the document number "
                        f"{members[0].get('document_number')}.")
            groups.append(_group("same_number", members, why))

    # 2. same supplier, same amount, dates close together
    by_total: Dict[Tuple[str, float], List[Dict[str, Any]]] = defaultdict(list)
    for b in bills:
        v, net = vendor_key(b.get("vendor_name")), _net(b)
        if v and net is not None and _day(b):
            by_total[(v, net)].append(b)
    for (_, net), members in by_total.items():
        members.sort(key=_day)
        cluster = [members[0]]
        for b in members[1:] + [None]:
            if b is not None and (_day(b) - _day(cluster[0])).days <= DAYS_APART:  # near the first bill, so a daily run does not chain
                cluster.append(b)
                continue
            # A cluster that is exactly one same_number group was reported by rule 1 already.
            same_exact = (len(cluster) > 1 and all(id(x) in in_exact for x in cluster)
                          and len({in_exact[id(x)] for x in cluster}) == 1)
            if len(cluster) > 1 and not same_exact:
                span = (_day(cluster[-1]) - _day(cluster[0])).days
                groups.append(_group("same_total_near_date", cluster,
                                     f"The same supplier has {len(cluster)} bills for {net:,.2f}, "
                                     f"{'on the same day' if span == 0 else f'dated within {span} days'}."))
            cluster = [b] if b is not None else []

    # 3. suppliers with alike names, same amount, same date
    by_day: Dict[Tuple[str, float], List[Dict[str, Any]]] = defaultdict(list)
    for b in bills:
        net, d = _net(b), _day(b)
        if net is not None and d:
            by_day[(d.isoformat(), net)].append(b)
    for (day, net), members in by_day.items():
        keys = {id(b): vendor_key(b.get("vendor_name")) for b in members}
        if len(set(keys.values())) < 2:
            continue
        seen: set = set()
        for a in members:
            if id(a) in seen:
                continue
            alike = [a] + [b for b in members if b is not a and id(b) not in seen and _similar(keys[id(a)], keys[id(b)])]
            if len(alike) > 1:
                seen.update(id(x) for x in alike)
                names = sorted({str(x.get("vendor_name")) for x in alike})
                groups.append(_group("similar_vendor_same_day", alike,
                                     f"Supplier names look alike ({', '.join(names)}) with the same amount {net:,.2f} "
                                     f"on {day}."))

    order = {r: i for i, r in enumerate(RULES)}
    groups.sort(key=lambda g: (order[g["rule"]], -g["extra_amount"], -g["count"]))
    for i, g in enumerate(groups, 1):
        g["group"] = i
    return groups
