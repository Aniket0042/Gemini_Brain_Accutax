"""store.py — PostgreSQL persistence layer for bots, runs, findings, and memory.

Phase 2 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md, Task 2.2.
All operations go through connection pool (gemini_brain.db.connection).
Every function that reads or modifies a bot enforces owner_user_id filtering.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Set, Union

from gemini_brain.bots.schema import BotDraft
from gemini_brain.db import connection

logger = logging.getLogger("gemini_brain.bots.store")


@contextmanager
def _get_cur(conn: Optional[Any] = None) -> Iterator[Any]:
    """Yield a cursor. If conn is provided, use it without closing; otherwise borrow from pool."""
    if conn is not None:
        with conn.cursor() as cur:
            yield cur
    else:
        c = connection.get_connection()
        try:
            with c:
                with c.cursor() as cur:
                    yield cur
        finally:
            c.close()


def _iso(val: Any) -> Optional[str]:
    if val is None:
        return None
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return str(val)


def _bot_from_row(row: Sequence[Any]) -> Dict[str, Any]:
    draft_val = row[5]
    if isinstance(draft_val, str):
        try:
            draft_val = json.loads(draft_val)
        except Exception:
            pass
    return {
        "id": str(row[0]),
        "owner_user_id": int(row[1]),
        "agent": str(row[2]),
        "name": str(row[3]),
        "org_ids": list(row[4] or []),
        "draft": draft_val,
        "status": str(row[6]),
        "status_note": str(row[7]) if row[7] is not None else None,
        "created_at": _iso(row[8]),
        "updated_at": _iso(row[9]),
        "last_run_at": _iso(row[10]),
        "next_run_at": _iso(row[11]),
    }


def _run_from_row(row: Sequence[Any]) -> Dict[str, Any]:
    summary_val = row[7]
    if isinstance(summary_val, str):
        try:
            summary_val = json.loads(summary_val)
        except Exception:
            pass
    return {
        "id": str(row[0]),
        "bot_id": str(row[1]),
        "trigger": str(row[2]),
        "status": str(row[3]),
        "started_at": _iso(row[4]),
        "finished_at": _iso(row[5]),
        "credits": int(row[6] or 0),
        "summary": summary_val if isinstance(summary_val, dict) else {},
        "error": str(row[8]) if row[8] is not None else None,
    }


def create(
    owner_user_id: int,
    draft: Union[BotDraft, Dict[str, Any]],
    status: str = "active",
    status_note: Optional[str] = None,
    next_run_at: Optional[dt.datetime] = None,
    *,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Create a new bot record for owner_user_id."""
    if isinstance(draft, BotDraft):
        draft_dict = draft.model_dump()
        name = draft.name
        agent = draft.agent
        org_ids = list(draft.org_ids)
    else:
        draft_dict = dict(draft)
        name = draft_dict.get("name", "VAT Watch")
        agent = draft_dict.get("agent", "vat")
        org_ids = list(draft_dict.get("org_ids") or [])

    draft_json = json.dumps(draft_dict)

    query = """
    INSERT INTO ai_bots (
        owner_user_id, agent, name, org_ids, draft, status, status_note, next_run_at
    ) VALUES (
        %s, %s, %s, %s, %s::jsonb, %s, %s, %s
    ) RETURNING
        id, owner_user_id, agent, name, org_ids, draft, status, status_note,
        created_at, updated_at, last_run_at, next_run_at;
    """

    with _get_cur(conn) as cur:
        cur.execute(
            query,
            (owner_user_id, agent, name, org_ids, draft_json, status, status_note, next_run_at),
        )
        row = cur.fetchone()
        bot = _bot_from_row(row)
        if draft.notes:
            sync_notes_to_memory(bot["id"], draft.notes, conn=conn)
        return bot


def get(
    bot_id: str,
    owner_user_id: Optional[int] = None,
    *,
    conn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Retrieve a bot by ID. If owner_user_id is provided, enforce ownership."""
    query = """
    SELECT id, owner_user_id, agent, name, org_ids, draft, status, status_note,
           created_at, updated_at, last_run_at, next_run_at
    FROM ai_bots
    WHERE id = %s
    """
    params: List[Any] = [bot_id]
    if owner_user_id is not None:
        query += " AND owner_user_id = %s"
        params.append(owner_user_id)

    with _get_cur(conn) as cur:
        cur.execute(query, tuple(params))
        row = cur.fetchone()
        return _bot_from_row(row) if row else None


def list_for_user(
    owner_user_id: int,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return all bots belonging to owner_user_id, newest first."""
    query = """
    SELECT id, owner_user_id, agent, name, org_ids, draft, status, status_note,
           created_at, updated_at, last_run_at, next_run_at
    FROM ai_bots
    WHERE owner_user_id = %s
    ORDER BY created_at DESC;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (owner_user_id,))
        rows = cur.fetchall() or []
        return [_bot_from_row(r) for r in rows]


def update(
    bot_id: str,
    owner_user_id: int,
    patch: Dict[str, Any],
    *,
    conn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Update fields of an existing bot owned by owner_user_id."""
    allowed_fields = {"name", "draft", "org_ids", "status", "status_note", "next_run_at", "last_run_at"}
    set_clauses: List[str] = ["updated_at = now()"]
    params: List[Any] = []

    for k, v in patch.items():
        if k not in allowed_fields:
            continue
        if k == "draft":
            draft_dict = v.model_dump() if isinstance(v, BotDraft) else dict(v)
            set_clauses.append("draft = %s::jsonb")
            params.append(json.dumps(draft_dict))
            # Also keep name and org_ids synchronized if present in draft
            if "name" not in patch and "name" in draft_dict:
                set_clauses.append("name = %s")
                params.append(draft_dict["name"])
            if "org_ids" not in patch and "org_ids" in draft_dict:
                set_clauses.append("org_ids = %s")
                params.append(list(draft_dict["org_ids"]))
        elif k == "org_ids":
            set_clauses.append("org_ids = %s")
            params.append(list(v))
        else:
            set_clauses.append(f"{k} = %s")
            params.append(v)

    if len(set_clauses) == 1:
        # Nothing to update besides timestamp
        return get(bot_id, owner_user_id, conn=conn)

    params.extend([bot_id, owner_user_id])
    query = f"""
    UPDATE ai_bots
    SET {", ".join(set_clauses)}
    WHERE id = %s AND owner_user_id = %s
    RETURNING id, owner_user_id, agent, name, org_ids, draft, status, status_note,
              created_at, updated_at, last_run_at, next_run_at;
    """

    with _get_cur(conn) as cur:
        cur.execute(query, tuple(params))
        row = cur.fetchone()
        bot = _bot_from_row(row) if row else None
        if bot and "draft" in patch:
            # The route passes a BotDraft, run.py a dict: both carry the notes.
            new_draft = patch["draft"]
            draft_dict = new_draft.model_dump() if isinstance(new_draft, BotDraft) else dict(new_draft or {})
            if "notes" in draft_dict:
                sync_notes_to_memory(bot_id, draft_dict.get("notes") or [], conn=conn)
        return bot


def set_status(
    bot_id: str,
    owner_user_id: int,
    status: str,
    status_note: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Change bot status ('active', 'paused', 'error') with optional note."""
    if status not in ("active", "paused", "error"):
        raise ValueError(f"Invalid bot status: {status}")

    return update(
        bot_id=bot_id,
        owner_user_id=owner_user_id,
        patch={"status": status, "status_note": status_note},
        conn=conn,
    )


def delete(
    bot_id: str,
    owner_user_id: int,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Delete a bot. Cascades to runs, findings, and memory."""
    query = """
    DELETE FROM ai_bots
    WHERE id = %s AND owner_user_id = %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id, owner_user_id))
        return bool(cur.rowcount and cur.rowcount > 0)


def record_run(
    bot_id: str,
    trigger: str,
    status: str = "running",
    credits: int = 0,
    summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Start recording a bot run."""
    summary_json = json.dumps(summary or {})
    query = """
    INSERT INTO ai_bot_runs (
        bot_id, trigger, status, credits, summary, error
    ) VALUES (
        %s, %s, %s, %s, %s::jsonb, %s
    ) RETURNING
        id, bot_id, trigger, status, started_at, finished_at, credits, summary, error;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id, trigger, status, credits, summary_json, error))
        row = cur.fetchone()
        return _run_from_row(row)


def finish_run(
    run_id: str,
    status: str,
    credits: int = 0,
    summary: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Complete a bot run and record outcome figures."""
    summary_json = json.dumps(summary or {})
    query = """
    UPDATE ai_bot_runs
    SET status = %s, credits = %s, summary = %s::jsonb, error = %s, finished_at = now()
    WHERE id = %s
    RETURNING id, bot_id, trigger, status, started_at, finished_at, credits, summary, error;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (status, credits, summary_json, error, run_id))
        row = cur.fetchone()
        if not row:
            return None
        run_res = _run_from_row(row)
        # Update last_run_at on parent bot
        cur.execute("UPDATE ai_bots SET last_run_at = now() WHERE id = %s", (run_res["bot_id"],))
        return run_res


def list_runs(
    bot_id: str,
    limit: int = 20,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return historical runs for a bot, newest first."""
    query = """
    SELECT id, bot_id, trigger, status, started_at, finished_at, credits, summary, error
    FROM ai_bot_runs
    WHERE bot_id = %s
    ORDER BY started_at DESC
    LIMIT %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id, limit))
        rows = cur.fetchall() or []
        return [_run_from_row(r) for r in rows]


def upsert_findings(
    bot_id: str,
    run_id: Optional[str],
    findings: List[Dict[str, Any]],
    *,
    conn: Optional[Any] = None,
) -> None:
    """Upsert problems discovered in a run. Stable key identifies duplicates across runs."""
    if not findings:
        return

    query = """
    INSERT INTO ai_bot_findings (
        bot_id, key, org_id, rule, state, amount, first_seen_run, last_seen_run, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, now()
    )
    ON CONFLICT (bot_id, key) DO UPDATE SET
        state = CASE
            WHEN ai_bot_findings.state = 'dismissed' THEN 'dismissed'
            ELSE EXCLUDED.state
        END,
        amount = EXCLUDED.amount,
        last_seen_run = EXCLUDED.last_seen_run,
        updated_at = now();
    """

    with _get_cur(conn) as cur:
        for f in findings:
            key = str(f["key"])
            org_id = int(f.get("org_id", 0))
            rule = str(f.get("rule", ""))
            state = str(f.get("state", "new"))
            amount = f.get("amount")
            cur.execute(query, (bot_id, key, org_id, rule, state, amount, run_id, run_id))


def get_findings(
    bot_id: str,
    state: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return findings for a bot, optionally filtered by state ('new', 'seen', 'fixed', 'dismissed')."""
    query = """
    SELECT bot_id, key, org_id, rule, state, amount, first_seen_run, last_seen_run, updated_at
    FROM ai_bot_findings
    WHERE bot_id = %s
    """
    params: List[Any] = [bot_id]
    if state is not None:
        query += " AND state = %s"
        params.append(state)
    query += " ORDER BY updated_at DESC;"

    with _get_cur(conn) as cur:
        cur.execute(query, tuple(params))
        rows = cur.fetchall() or []
        return [
            {
                "bot_id": str(r[0]),
                "key": str(r[1]),
                "org_id": int(r[2]),
                "rule": str(r[3]),
                "state": str(r[4]),
                "amount": float(r[5]) if r[5] is not None else None,
                "first_seen_run": str(r[6]) if r[6] else None,
                "last_seen_run": str(r[7]) if r[7] else None,
                "updated_at": _iso(r[8]),
            }
            for r in rows
        ]


def update_finding_state(
    bot_id: str,
    key: str,
    state: str,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Update state of a specific finding (e.g. mark dismissed or fixed)."""
    if state not in ("new", "seen", "fixed", "dismissed"):
        raise ValueError(f"Invalid finding state: {state}")

    query = """
    UPDATE ai_bot_findings
    SET state = %s, updated_at = now()
    WHERE bot_id = %s AND key = %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (state, bot_id, key))
        return bool(cur.rowcount and cur.rowcount > 0)


def due_bots(
    now: Optional[dt.datetime] = None,
    limit: int = 50,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return active bots due to run on or before `now`."""
    current_time = now or dt.datetime.now(dt.timezone.utc)
    query = """
    SELECT id, owner_user_id, agent, name, org_ids, draft, status, status_note,
           created_at, updated_at, last_run_at, next_run_at
    FROM ai_bots
    WHERE status = 'active' AND next_run_at IS NOT NULL AND next_run_at <= %s
    ORDER BY next_run_at ASC
    LIMIT %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (current_time, limit))
        rows = cur.fetchall() or []
        return [_bot_from_row(r) for r in rows]


def active_org_ids(*, conn: Optional[Any] = None) -> Set[int]:
    """Every organization at least one active bot covers: the ones the monitor poll looks at."""
    with _get_cur(conn) as cur:
        cur.execute("SELECT DISTINCT unnest(org_ids) FROM ai_bots WHERE status = 'active'")
        return {int(r[0]) for r in cur.fetchall() or []}


def list_active_bots_for_org(
    org_id: int,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return all active bots configured to monitor org_id (Phase 3 Monitor hooks)."""
    query = """
    SELECT id, owner_user_id, agent, name, org_ids, draft, status, status_note,
           created_at, updated_at, last_run_at, next_run_at
    FROM ai_bots
    WHERE status = 'active' AND %s = ANY(org_ids)
    ORDER BY created_at DESC;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (org_id,))
        rows = cur.fetchall() or []
        return [_bot_from_row(r) for r in rows]


def add_memory(
    bot_id: str,
    text: str,
    source: str = "note",
    finding_key: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> int:
    """Add a memory entry (note or dismissal) to a bot."""
    if source not in ("note", "dismissal"):
        raise ValueError(f"Invalid memory source: {source}")

    query = """
    INSERT INTO ai_bot_memory (
        bot_id, text, source, finding_key
    ) VALUES (
        %s, %s, %s, %s
    ) RETURNING id;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id, text.strip(), source, finding_key))
        row = cur.fetchone()
        return int(row[0])


def list_memory(
    bot_id: str,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """List memory entries for a bot."""
    query = """
    SELECT id, bot_id, text, source, finding_key, created_at
    FROM ai_bot_memory
    WHERE bot_id = %s
    ORDER BY created_at ASC;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id,))
        rows = cur.fetchall() or []
        return [
            {
                "id": int(r[0]),
                "bot_id": str(r[1]),
                "text": str(r[2]),
                "source": str(r[3]),
                "finding_key": str(r[4]) if r[4] else None,
                "created_at": _iso(r[5]),
            }
            for r in rows
        ]


def sync_notes_to_memory(
    bot_id: str,
    notes: Sequence[str],
    *,
    conn: Optional[Any] = None,
) -> None:
    """Sync notes from BotDraft into ai_bot_memory with source='note'."""
    with _get_cur(conn) as cur:
        cur.execute("DELETE FROM ai_bot_memory WHERE bot_id = %s AND source = 'note'", (bot_id,))
        for note in notes:
            if isinstance(note, str) and note.strip():
                cur.execute(
                    "INSERT INTO ai_bot_memory (bot_id, text, source, finding_key) VALUES (%s, %s, 'note', NULL)",
                    (bot_id, note.strip()),
                )


def delete_memory(
    bot_id: str,
    memory_id: int,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Delete a memory entry for a bot. If linked to a dismissed finding, restores it to 'seen'."""
    with _get_cur(conn) as cur:
        cur.execute("SELECT finding_key FROM ai_bot_memory WHERE id = %s AND bot_id = %s", (memory_id, bot_id))
        row = cur.fetchone()
        if not row:
            return False
        finding_key = str(row[0]) if row[0] else None

        cur.execute("DELETE FROM ai_bot_memory WHERE id = %s AND bot_id = %s", (memory_id, bot_id))
        if finding_key:
            cur.execute(
                "UPDATE ai_bot_findings SET state = 'seen', updated_at = now() WHERE bot_id = %s AND key = %s",
                (bot_id, finding_key),
            )
        return True


def set_next_run(
    bot_id: str,
    next_run_at: Optional[dt.datetime],
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Update next_run_at timestamp on a bot."""
    query = """
    UPDATE ai_bots
    SET next_run_at = %s, updated_at = now()
    WHERE id = %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (next_run_at, bot_id))
        return bool(cur.rowcount and cur.rowcount > 0)


def delete_memory_by_finding_key(
    bot_id: str,
    finding_key: str,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Delete memory lines created by dismissing a specific finding."""
    query = """
    DELETE FROM ai_bot_memory
    WHERE bot_id = %s AND finding_key = %s;
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (bot_id, finding_key))
        return bool(cur.rowcount and cur.rowcount > 0)


def dismiss_finding(
    bot_id: str,
    owner_user_id: int,
    key: str,
    note: Optional[str] = None,
    supplier: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Dismiss a finding on an owned bot, recording a memory entry with rule text and supplier."""
    bot = get(bot_id, owner_user_id, conn=conn)
    if not bot:
        return False

    with _get_cur(conn) as cur:
        cur.execute("SELECT rule FROM ai_bot_findings WHERE bot_id = %s AND key = %s", (bot_id, key))
        row = cur.fetchone()
        if not row:
            return False
        rule = str(row[0])

        cur.execute(
            "UPDATE ai_bot_findings SET state = 'dismissed', updated_at = now() WHERE bot_id = %s AND key = %s",
            (bot_id, key),
        )

        if note:
            mem_text = note
        elif supplier:
            mem_text = f"Dismissed {rule} from {supplier}"
        else:
            mem_text = f"Dismissed finding: {rule}"

        cur.execute(
            """
            INSERT INTO ai_bot_memory (bot_id, text, source, finding_key)
            VALUES (%s, %s, 'dismissal', %s)
            """,
            (bot_id, mem_text, key),
        )
        return True


def undismiss_finding(
    bot_id: str,
    owner_user_id: int,
    key: str,
    *,
    conn: Optional[Any] = None,
) -> bool:
    """Un-dismiss a finding, restoring it to 'seen' state and deleting the dismissal memory."""
    bot = get(bot_id, owner_user_id, conn=conn)
    if not bot:
        return False

    with _get_cur(conn) as cur:
        cur.execute(
            "UPDATE ai_bot_findings SET state = 'seen', updated_at = now() WHERE bot_id = %s AND key = %s",
            (bot_id, key),
        )
        if not cur.rowcount:
            return False

        cur.execute(
            "DELETE FROM ai_bot_memory WHERE bot_id = %s AND finding_key = %s",
            (bot_id, key),
        )
        return True


def get_inbox(
    owner_user_id: int,
    *,
    conn: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return latest run results and active findings across all bots owned by user."""
    bots = list_for_user(owner_user_id, conn=conn)

    runs: List[Dict[str, Any]] = []
    for b in bots:
        recent = list_runs(b["id"], limit=1, conn=conn)
        if recent:
            r = dict(recent[0])
            r["bot_name"] = b["name"]
            runs.append(r)

    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)

    findings_query = """
    SELECT f.bot_id, b.name AS bot_name, f.key, f.org_id, f.rule, f.state,
           f.amount, f.first_seen_run, f.last_seen_run, f.updated_at
    FROM ai_bot_findings f
    JOIN ai_bots b ON b.id = f.bot_id
    WHERE b.owner_user_id = %s AND f.state IN ('new', 'seen')
    ORDER BY f.updated_at DESC
    LIMIT 100;
    """
    findings: List[Dict[str, Any]] = []
    unread_count = 0
    with _get_cur(conn) as cur:
        cur.execute(findings_query, (owner_user_id,))
        rows = cur.fetchall() or []
        from gemini_brain.bots import watches
        for r in rows:
            rule_str = str(r[4])
            state = str(r[5])
            if state == "new":
                unread_count += 1
            w = watches.watch_for_rule(rule_str)
            findings.append({
                "bot_id": str(r[0]),
                "bot_name": str(r[1]),
                "key": str(r[2]),
                "org_id": int(r[3]),
                "rule": rule_str,
                "watch_id": w.id if w else None,
                "watch_label": w.label if w else None,
                "why_text": f"Triggered by watch '{w.label}' (Rule: {rule_str})" if w else f"Triggered by rule '{rule_str}'",
                "state": state,
                "amount": float(r[6]) if r[6] is not None else None,
                "first_seen_run": str(r[7]) if r[7] else None,
                "last_seen_run": str(r[8]) if r[8] else None,
                "updated_at": _iso(r[9]),
            })

    approvals = get_approvals_for_user(owner_user_id, status="pending", conn=conn)
    total_unread = unread_count + len(approvals)

    return {
        "unread_count": total_unread,
        "bots_count": len(bots),
        "runs": runs,
        "findings": findings,
        "approvals": approvals,
    }


def get_approvals_for_user(
    owner_user_id: int,
    status: Optional[str] = None,
    *,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Return all approval items from runs of bots owned by user."""
    bots = list_for_user(owner_user_id, conn=conn)
    bot_map = {b["id"]: b["name"] for b in bots}
    if not bot_map:
        return []

    approvals: List[Dict[str, Any]] = []
    for bot_id, bot_name in bot_map.items():
        runs = list_runs(bot_id, limit=10, conn=conn)
        for r in runs:
            summary = r.get("summary") or {}
            appr = summary.get("approval")
            if isinstance(appr, dict):
                if not status or appr.get("status") == status:
                    appr_item = dict(appr)
                    if "bot_name" not in appr_item or not appr_item["bot_name"]:
                        appr_item["bot_name"] = bot_name
                    approvals.append(appr_item)
    return approvals


def update_approval_status(
    bot_id: str,
    approval_id: str,
    new_status: str,
    updates: Optional[Dict[str, Any]] = None,
    *,
    conn: Optional[Any] = None,
) -> Optional[Dict[str, Any]]:
    """Update status ('approved' or 'rejected') and content of an approval item."""
    runs = list_runs(bot_id, limit=20, conn=conn)
    for r in runs:
        summary = r.get("summary") or {}
        appr = summary.get("approval")
        if isinstance(appr, dict) and appr.get("id") == approval_id:
            appr["status"] = new_status
            appr["decided_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            if updates:
                for k in ("recipient", "subject", "content"):
                    if k in updates and updates[k] is not None:
                        appr[k] = updates[k]
            with _get_cur(conn) as cur:
                cur.execute(
                    "UPDATE ai_bot_runs SET summary = %s::jsonb WHERE id = %s",
                    (json.dumps(summary), r["id"]),
                )
            return appr
    return None


def mark_inbox_seen(
    owner_user_id: int,
    *,
    conn: Optional[Any] = None,
) -> int:
    """Transition all 'new' findings for owner's bots to 'seen'."""
    query = """
    UPDATE ai_bot_findings f
    SET state = 'seen', updated_at = now()
    FROM ai_bots b
    WHERE f.bot_id = b.id AND b.owner_user_id = %s AND f.state = 'new';
    """
    with _get_cur(conn) as cur:
        cur.execute(query, (owner_user_id,))
        return cur.rowcount or 0

