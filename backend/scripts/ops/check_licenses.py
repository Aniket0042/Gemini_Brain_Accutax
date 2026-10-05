"""Fail if an installed Python package carries a copyleft licence (AGPL / GPL / LGPL)
that has not been reviewed and listed in ALLOWED.

Run before every deploy and whenever a dependency is added:
    python backend/scripts/ops/check_licenses.py
Exit code 0 = clean, 1 = something to review. Uses only the standard library.

Why: the VAT knowledge base is built clean-room (docs/product/VAT_KNOWLEDGE_BASE_PLAN.md).
AGPL code such as PyMuPDF must never end up in AccuTax AI.
"""
from __future__ import annotations

import re
import sys
from importlib.metadata import distributions

# Packages that must never be installed in AccuTax AI, whatever their metadata says.
BLOCKED = {"pymupdf", "fitz", "pymupdfb", "lexrag"}

# Reviewed copyleft packages that are acceptable (e.g. LGPL used as an unmodified library).
# Add an entry only after review, with the reason.
ALLOWED: dict[str, str] = {
    "matplotlib": "PSF-based Matplotlib licence, not copyleft; its licence text only mentions GPL compatibility",
    "psycopg2-binary": "LGPL with exceptions; used unmodified as a library, existing dependency",
}

_COPYLEFT = re.compile(r"\b(A?GPL|LGPL|GNU (Affero |Lesser )?General Public License)\b", re.I)


def licence_text(dist) -> str:
    meta = dist.metadata
    parts = [meta.get("License", "") or "", meta.get("License-Expression", "") or ""]
    parts += [c for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")]
    return " | ".join(p for p in parts if p)


def main() -> int:
    problems = []
    for dist in distributions():
        name = (dist.metadata.get("Name") or "").lower()
        if not name:
            continue
        if name in BLOCKED:
            problems.append((name, dist.version, "blocked package"))
            continue
        text = licence_text(dist)
        if _COPYLEFT.search(text) and name not in ALLOWED:
            problems.append((name, dist.version, text[:160]))
    for name, version, why in sorted(problems):
        print(f"REVIEW  {name}=={version}: {why}")
    if problems:
        print(f"\n{len(problems)} package(s) need review. Remove them, or add a reviewed entry to ALLOWED.")
        return 1
    print("Licence check passed: no blocked or unreviewed copyleft packages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
