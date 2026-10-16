"""Citations in a law answer, checked by code.

A law answer cites the FTA sources it was given as [1], [2]. The prompt asks for that; this makes two things certain:
a citation number that names no source given to the model is taken out (it would point the reader nowhere), and an
answer that cites nothing at all is reported, so the uncited answers can be found and fixed. It never adds a citation
the model did not write.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Set, Tuple

#: [3], [1, 2], [1,2,4], also with a range written as [1-3] or [1–3].
_CITE = re.compile(r"\[(\d{1,3}(?:\s*[,–-]\s*\d{1,3})*)\]")


def _numbers(inside: str) -> list:
    out = []
    for part in re.split(r"\s*,\s*", inside):
        bounds = re.split(r"\s*[–-]\s*", part)
        if len(bounds) == 2 and bounds[0].isdigit() and bounds[1].isdigit() and int(bounds[0]) <= int(bounds[1]) <= int(bounds[0]) + 20:
            out += list(range(int(bounds[0]), int(bounds[1]) + 1))
        elif part.strip().isdigit():
            out.append(int(part))
    return out


def cited(answer: str) -> Set[int]:
    """Every source number the answer cites."""
    return {n for m in _CITE.finditer(answer or "") for n in _numbers(m.group(1))}


def enforce(answer: str, available: Iterable[int]) -> Tuple[str, Dict[str, Any]]:
    """(answer, report). Citation numbers that name no available source are taken out of the text; a bracket left
    empty goes too. report: {"cited": [...], "removed": [...], "uncited": bool}."""
    known = {int(n) for n in available}
    removed: Set[int] = set()

    def fix(m: "re.Match[str]") -> str:
        numbers = _numbers(m.group(1))
        keep = [n for n in numbers if n in known]
        removed.update(n for n in numbers if n not in known)
        if keep == numbers:
            return m.group(0)
        return f"[{', '.join(str(n) for n in keep)}]" if keep else ""

    text = _CITE.sub(fix, answer or "")
    if removed:
        text = re.sub(r"[ \t]+([.,;:])", r"\1", re.sub(r"[ \t]{2,}", " ", text))
    left = cited(text)
    return text, {"cited": sorted(left), "removed": sorted(removed), "uncited": not left}
