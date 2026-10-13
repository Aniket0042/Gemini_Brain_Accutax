"""Fetch real bills of a test organization from the books (Cube), for the upload evaluation's "recorded" cases.

    python scripts/eval/fetch_books_samples.py --org 24 --out books_samples.json

Run it where Cube is configured (the VM). It lists the organization's latest bills with the same tool the agent
uses (list_documents), keeps a few with their amounts, and picks the supplier with the most bills for the
statement case. The output holds business data of a test organization: keep it out of the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]


def fetch(org_id: int, limit: int = 100) -> dict:
    from gemini_brain.semantic import list_documents

    result = list_documents.run(
        {"type": "bills", "limit": limit, "period": {"start": "2020-01-01", "end": time.strftime("%Y-%m-%d")}},
        organization_ids=[org_id], subject="upload-eval-books", deadline=time.monotonic() + 60)
    bills = []
    for row in result["rows"]:
        try:
            bills.append({"number": row["document_number"], "date": str(row["document_date"])[:10], "vendor": row["vendor_name"],
                          "currency": row.get("currency") or "AED", "net": float(row["net_purchases"]), "vat": float(row["input_vat"]),
                          "organization_name": row.get("organization_name")})
        except (KeyError, TypeError, ValueError):
            continue
    usable = [b for b in bills if b["number"] and b["vendor"] and b["net"] > 0 and b["date"]]
    by_vendor = defaultdict(list)
    for b in usable:
        by_vendor[b["vendor"]].append(b)
    name, supplier_bills = max(by_vendor.items(), key=lambda kv: len(kv[1]), default=(None, []))
    return {
        "organization_id": org_id,
        "organization_name": (usable[0].get("organization_name") if usable else None) or f"Organization {org_id}",
        "bills": usable[:5],
        "supplier": {"name": name, "currency": supplier_bills[0]["currency"] if supplier_bills else "AED",
                     "bills": supplier_bills[:40]},
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--org", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    data = fetch(args.org)
    args.out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{len(data['bills'])} bills; supplier {data['supplier']['name']!r} with {len(data['supplier']['bills'])} bills -> {args.out}")
