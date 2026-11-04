"""payables_scan.py — The AP bot's names for the open-documents scan: open bills that are overdue or due soon, large
first bills from new suppliers, and the payables position (docs/BOT_AGENTS_AND_AP_PLAN.md, task B3). The code lives in
bots/open_docs_scan.py, shared with the AR bot (docs/BOT_AR_PLAN.md, task C3).
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.bots import open_docs_scan
from gemini_brain.bots.open_docs_scan import CUBE_ROW_LIMIT, VENDOR_CHUNK, money  # noqa: F401
from gemini_brain.semantic import cube_client  # noqa: F401  (tests patch cube_client.load through here)


def bill_key(org_id: int, document_id: Any) -> str:
    return open_docs_scan.doc_key("payables", org_id, document_id)


def supplier_key(org_id: int, vendor_name: str) -> str:
    return open_docs_scan.party_key("payables", org_id, vendor_name)


def open_bills(org_id: int, today: dt.date, due_by: dt.date, *, subject: str, deadline: float
               ) -> Tuple[List[Dict[str, Any]], bool]:
    return open_docs_scan.open_docs("payables", org_id, today, due_by, subject=subject, deadline=deadline)


def due_items(org_id: int, today: dt.date, *, overdue_days: int, due_soon_days: int, rules: Sequence[str],
              subject: str, deadline: float) -> Tuple[List[Dict[str, Any]], int, bool]:
    return open_docs_scan.due_items("payables", org_id, today, overdue_days=overdue_days, due_soon_days=due_soon_days,
                                    rules=rules, subject=subject, deadline=deadline)


def new_supplier_items(org_id: int, start: dt.date, end: dt.date, *, min_amount: float, subject: str,
                       deadline: float) -> Tuple[List[Dict[str, Any]], int, bool]:
    return open_docs_scan.new_party_items("payables", org_id, start, end, min_amount=min_amount, subject=subject,
                                          deadline=deadline)


def position(org_ids: Sequence[int], today: dt.date, *, subject: str, deadline: float) -> Optional[Dict[str, Any]]:
    return open_docs_scan.position("payables", org_ids, today, subject=subject, deadline=deadline)
