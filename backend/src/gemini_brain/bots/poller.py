"""poller.py — Find bills and sales invoices saved since the last look, so monitor bots check them without Accutax
backend hooks (docs/VAT_BOT_IMPLEMENTATION_PLAN.md, phase 3 without Nest).

Accutax's `expense` (bills) and `income` (sales invoices) tables have no "updated" timestamp, and `created_date` is
not reliable (seed data carries dates in the future), so new documents are found by id: ids only grow. Each poll
reads the highest id now, then the documents between the last highest id and this one, for the organizations that
have an active bot. Index scans on the primary key only.

Limits, by design:
- Only new documents. An edit to an old bill, or a draft finalised later, is not seen here; the bot's scheduled run
  (weekly, or before the return) checks the whole period and catches it.
- The last id is kept in memory. After a restart polling starts from the newest id, so documents saved while the
  service was down are left to the scheduled run.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Set

from gemini_brain.db import connection

logger = logging.getLogger("gemini_brain.bots.poller")

#: Per side: the table, its type column, the document types the VAT review reads (as in semantic/model/cubes), and
#: the event name the monitor takes.
SOURCES = {
    "purchases": {"table": "public.expense", "type_col": "expense_type",
                  "types": ("BILL", "EXPENSE", "CASH_EXPENSE", "VENDOR_CREDIT"), "event": "bill.saved"},
    "sales": {"table": "public.income", "type_col": "income_type",
              "types": ("INVOICE", "CASH_INVOICE", "INCOME", "CREDIT_NOTE"), "event": "invoice.saved"},
}

#: Most documents one poll hands to the monitor per side; the rest wait for the next poll.
MAX_DOCUMENTS = 2000

#: side -> the highest id already looked at. Empty until the first poll.
_last_id: Dict[str, int] = {}


def _max_id(cur: Any, table: str) -> int:
    cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")  # table names come from SOURCES, never from input
    return int(cur.fetchone()[0])


def new_documents(org_ids: Sequence[int]) -> Dict[str, Dict[int, List[int]]]:
    """{side: {org_id: [document ids]}} saved since the last poll, for these organizations. The first poll only
    records where to start and returns nothing."""
    out: Dict[str, Dict[int, List[int]]] = {}
    conn = connection.get_connection()
    try:
        with conn:
            with conn.cursor() as cur:
                for side, src in SOURCES.items():
                    top = _max_id(cur, src["table"])
                    since = _last_id.get(side)
                    if since is None or not org_ids or top <= since:
                        _last_id[side] = max(top, since or 0)
                        continue
                    cur.execute(
                        f"SELECT id, organization_id FROM {src['table']} "
                        f"WHERE id > %s AND id <= %s AND organization_id = ANY(%s) AND {src['type_col']} = ANY(%s) "
                        f"ORDER BY id LIMIT %s",
                        (since, top, list(org_ids), list(src["types"]), MAX_DOCUMENTS),
                    )
                    rows = cur.fetchall()
                    grouped: Dict[int, List[int]] = defaultdict(list)
                    for doc_id, org_id in rows:
                        grouped[int(org_id)].append(int(doc_id))
                    if grouped:
                        out[side] = dict(grouped)
                    # A full page: carry on from the last one next time rather than skip the rest.
                    _last_id[side] = int(rows[-1][0]) if len(rows) >= MAX_DOCUMENTS else top
    finally:
        conn.close()
    return out


def poll(*, active_org_ids: Callable[[], Set[int]], handle_event: Callable[..., Dict[str, Any]]) -> Dict[str, Any]:
    """One poll: find new documents for organizations with an active bot and hand each organization's batch to the
    monitor (bots/monitor.handle_document_event). Returns counts for the log."""
    orgs = sorted(active_org_ids())
    found = new_documents(orgs)
    checked = new = 0
    for side, by_org in found.items():
        event = SOURCES[side]["event"]
        for org_id, doc_ids in by_org.items():
            try:
                res = handle_event(event=event, org_id=org_id, document_ids=doc_ids)
                checked += len(doc_ids)
                new += int(res.get("new_findings") or 0)
            except Exception:
                logger.exception("Monitor failed for %s on org %s", event, org_id)
    return {"orgs": len(orgs), "documents": checked, "new_findings": new}


def reset_for_tests(last_id: Optional[Dict[str, int]] = None) -> None:
    _last_id.clear()
    _last_id.update(last_id or {})
