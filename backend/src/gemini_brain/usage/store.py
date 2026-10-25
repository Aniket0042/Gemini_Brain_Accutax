"""
store.py — The usage tables (backend/scripts/ops/usage_limits.sql): spend events, who pays, plans, caps, top-ups, alerts.

Owner 0 is "internal" (Accutax staff) or "not known". Usage is never kept as a counter: an owner's month is the sum
of its charges since the month began, plus the holds of questions still running (a hold older than HOLD_MINUTES is
a crashed request and no longer counts). Every function raises on a database error; callers decide what that means.
"""
from __future__ import annotations

import datetime
import json
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gemini_brain.db.connection import get_connection

HOLD_MINUTES = 5
INTERNAL = 0
#: First key of the per-person advisory lock that makes the speed check and the event insert one step.
_GATE_LOCK = 7231


class Refused(Exception):
    """A question the speed limits refuse: USAGE_BUSY (one is still running) or USAGE_RATE (too many a minute)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code
_SUPER_ADMIN_CACHE: Dict[int, tuple] = {}
_CACHE_SECONDS = 300


def _run(sql: str, params: Any = None, *, fetch: str = "") -> Any:
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute(sql, params)
        out = cur.fetchall() if fetch == "all" else cur.fetchone() if fetch == "one" else cur.rowcount
        conn.commit()
        return out
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


# ── Events ───────────────────────────────────────────────────────────────────

def open_event(kind: str, user_id: Optional[int], session_id: Optional[str], organization_ids: Sequence[int],
               holds: Dict[int, float], model_mode: str = "full", *, gate: Optional[Tuple[int, int]] = None) -> int:
    """A running event with its holds (owner -> credits). Returns its id.

    `gate` (running, per_minute): first check, under a lock on this person, that fewer than `running` of their
    events of this kind are running and fewer than `per_minute` started in the last minute; else raise Refused.
    The lock holds until the event is inserted, so two questions sent at once cannot both pass. The check is shared
    by every copy of the service, as the table is."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        if gate is not None and user_id is not None:
            running, per_minute = gate
            cur.execute("SELECT pg_advisory_xact_lock(%s, %s)", (_GATE_LOCK, int(user_id)))
            cur.execute(
                f"""SELECT COUNT(*) FILTER (WHERE status = 'running' AND created_at > now() - interval '{HOLD_MINUTES} minutes'),
                           COUNT(*) FILTER (WHERE created_at > now() - interval '1 minute')
                    FROM public.ai_usage_events
                    WHERE user_id = %s AND kind = %s AND created_at > now() - interval '{HOLD_MINUTES} minutes'""",
                (user_id, kind))
            now_running, last_minute = cur.fetchone()
            if now_running >= running:
                raise Refused("USAGE_BUSY")
            if last_minute >= per_minute:
                raise Refused("USAGE_RATE")
        cur.execute(
            """INSERT INTO public.ai_usage_events (kind, status, user_id, session_id, organization_ids, model_mode,
                   reserved_credits) VALUES (%s, 'running', %s, %s, %s, %s, %s) RETURNING id""",
            (kind, user_id, session_id, list(organization_ids), model_mode, round(sum(holds.values()), 3)))
        event_id = int(cur.fetchone()[0])
        _put_charges(cur, event_id, user_id, holds)
        conn.commit()
        return event_id
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def close_event(event_id: Optional[int], entry: Dict[str, Any], charges: Dict[int, float]) -> None:
    """Replace a running event's holds with its real cost; insert the event when it was never opened."""
    conn = get_connection()
    cur = conn.cursor()
    try:
        values = (entry["status"], entry["calls"], entry["input_tokens"], entry["output_tokens"],
                  entry["cache_read_tokens"], entry["cache_write_tokens"], entry["cost_usd"], entry["credits"],
                  json.dumps(entry.get("models") or {}))
        if event_id is None:
            cur.execute(
                """INSERT INTO public.ai_usage_events (kind, status, user_id, session_id, organization_ids, model_mode,
                       calls, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd, credits,
                       models, finished_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now()) RETURNING id""",
                (entry["kind"], entry["status"], entry.get("user_id"), entry.get("session_id"),
                 list(entry.get("organization_ids") or []), entry.get("model_mode") or "full", *values[1:]))
            event_id = int(cur.fetchone()[0])
        else:
            cur.execute(
                """UPDATE public.ai_usage_events SET status = %s, calls = %s, input_tokens = %s, output_tokens = %s,
                       cache_read_tokens = %s, cache_write_tokens = %s, cost_usd = %s, credits = %s, models = %s,
                       finished_at = now() WHERE id = %s""", (*values, event_id))
            cur.execute("DELETE FROM public.ai_usage_charges WHERE event_id = %s", (event_id,))
        _put_charges(cur, event_id, entry.get("user_id"), charges)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _put_charges(cur: Any, event_id: int, user_id: Optional[int], charges: Dict[int, float]) -> None:
    for owner, credits in charges.items():
        cur.execute("INSERT INTO public.ai_usage_charges (event_id, owner_user_id, user_id, credits) "
                    "VALUES (%s, %s, %s, %s)", (event_id, int(owner), user_id, round(float(credits), 3)))


_COUNTED = f"(e.status <> 'running' OR e.created_at > now() - interval '{HOLD_MINUTES} minutes')"


def owner_used(owner: int, since: datetime.datetime) -> float:
    row = _run(f"""SELECT COALESCE(SUM(c.credits), 0) FROM public.ai_usage_charges c
                   JOIN public.ai_usage_events e ON e.id = c.event_id
                   WHERE c.owner_user_id = %s AND c.created_at >= %s AND {_COUNTED}""", (owner, since), fetch="one")
    return float(row[0] or 0)


def user_used(owner: int, user_id: int, since: datetime.datetime) -> float:
    row = _run(f"""SELECT COALESCE(SUM(c.credits), 0) FROM public.ai_usage_charges c
                   JOIN public.ai_usage_events e ON e.id = c.event_id
                   WHERE c.owner_user_id = %s AND c.user_id = %s AND c.created_at >= %s AND {_COUNTED}""",
               (owner, user_id, since), fetch="one")
    return float(row[0] or 0)


def user_daily_history(user_id: int, since: datetime.datetime, until: datetime.datetime) -> float:
    """Credits a user spent between two times, all owners together (for the spike alarm)."""
    row = _run("""SELECT COALESCE(SUM(credits), 0) FROM public.ai_usage_charges
                  WHERE user_id = %s AND created_at >= %s AND created_at < %s""", (user_id, since, until), fetch="one")
    return float(row[0] or 0)


def platform_cost(since: datetime.datetime) -> float:
    row = _run(f"""SELECT COALESCE(SUM(CASE WHEN e.status = 'running' THEN e.reserved_credits / 1000.0
                                            ELSE e.cost_usd END), 0)
                   FROM public.ai_usage_events e WHERE e.created_at >= %s AND {_COUNTED}""", (since,), fetch="one")
    return float(row[0] or 0)


# ── Plans, caps, top-ups ─────────────────────────────────────────────────────

def plan_row(owner: int) -> Optional[Dict[str, Any]]:
    row = _run("""SELECT plan, monthly_credits, user_daily_credits, updated_by, updated_at
                  FROM public.ai_owner_plans WHERE owner_user_id = %s""", (owner,), fetch="one")
    if not row:
        return None
    return {"plan": row[0], "monthly_credits": _num(row[1]), "user_daily_credits": _num(row[2]),
            "updated_by": row[3], "updated_at": row[4]}


def set_plan(owner: int, plan: str, monthly_credits: Optional[float], user_daily_credits: Optional[float],
             by: int) -> None:
    _run("""INSERT INTO public.ai_owner_plans (owner_user_id, plan, monthly_credits, user_daily_credits, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_user_id) DO UPDATE SET plan = EXCLUDED.plan, monthly_credits = EXCLUDED.monthly_credits,
                user_daily_credits = EXCLUDED.user_daily_credits, updated_by = EXCLUDED.updated_by, updated_at = now()""",
         (owner, plan, monthly_credits, user_daily_credits, by))


def cap_row(owner: int, user_id: int) -> Optional[Dict[str, Any]]:
    row = _run("SELECT daily_credits, disabled FROM public.ai_user_caps WHERE owner_user_id = %s AND user_id = %s",
               (owner, user_id), fetch="one")
    return {"daily_credits": _num(row[0]), "disabled": bool(row[1])} if row else None


def caps_of(owner: int) -> Dict[int, Dict[str, Any]]:
    rows = _run("SELECT user_id, daily_credits, disabled FROM public.ai_user_caps WHERE owner_user_id = %s",
                (owner,), fetch="all")
    return {int(r[0]): {"daily_credits": _num(r[1]), "disabled": bool(r[2])} for r in rows}


def set_cap(owner: int, user_id: int, daily_credits: Optional[float], disabled: bool, by: int) -> None:
    _run("""INSERT INTO public.ai_user_caps (owner_user_id, user_id, daily_credits, disabled, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (owner_user_id, user_id) DO UPDATE SET daily_credits = EXCLUDED.daily_credits,
                disabled = EXCLUDED.disabled, updated_by = EXCLUDED.updated_by, updated_at = now()""",
         (owner, user_id, daily_credits, disabled, by))


def topup_credits(owner: int) -> float:
    row = _run("""SELECT COALESCE(SUM(credits), 0) FROM public.ai_topups
                  WHERE owner_user_id = %s AND (expires_at IS NULL OR expires_at > now())""", (owner,), fetch="one")
    return float(row[0] or 0)


def add_topup(owner: int, credits: float, reason: str, expires_at: Optional[datetime.datetime], by: int) -> int:
    row = _run("""INSERT INTO public.ai_topups (owner_user_id, credits, reason, expires_at, created_by)
                  VALUES (%s, %s, %s, %s, %s) RETURNING id""", (owner, credits, reason, expires_at, by), fetch="one")
    return int(row[0])


def topups_of(owner: int) -> List[Dict[str, Any]]:
    rows = _run("""SELECT id, credits, reason, expires_at, created_at FROM public.ai_topups
                   WHERE owner_user_id = %s ORDER BY created_at DESC LIMIT 50""", (owner,), fetch="all")
    return [{"id": r[0], "credits": float(r[1]), "reason": r[2], "expires_at": r[3], "created_at": r[4]} for r in rows]


# ── People ───────────────────────────────────────────────────────────────────

def seats(owner: int) -> int:
    """The owner plus each person with an ACTIVE collaborator invitation to one of the owner's organizations."""
    row = _run("""SELECT COUNT(DISTINCT c.accepted_by_user_id) FROM public.collaborators c
                  JOIN public.organizations o ON o.id = c.organization_id
                  WHERE o.user_id = %s AND c.status = 'ACTIVE' AND c.accepted_by_user_id IS NOT NULL
                    AND c.accepted_by_user_id <> %s""", (owner, owner), fetch="one")
    return 1 + int(row[0] or 0)


def collaborators_of(owner: int) -> List[Dict[str, Any]]:
    """People invited to the owner's organizations (ACTIVE or INVITED), with their user id when they have one."""
    rows = _run("""SELECT DISTINCT COALESCE(c.accepted_by_user_id, u.id) AS uid, LOWER(c.email), c.status
                   FROM public.collaborators c JOIN public.organizations o ON o.id = c.organization_id
                   LEFT JOIN public.users u ON LOWER(TRIM(u.email)) = LOWER(TRIM(c.email))
                   WHERE o.user_id = %s AND c.status IN ('ACTIVE', 'INVITED')""", (owner,), fetch="all")
    return [{"user_id": r[0], "email": r[1], "status": r[2]} for r in rows]


def owned_by(user_id: int) -> List[int]:
    rows = _run("SELECT id FROM public.organizations WHERE user_id = %s ORDER BY id", (user_id,), fetch="all")
    return [int(r[0]) for r in rows]


def is_super_admin(user_id: Optional[int]) -> bool:
    if user_id is None:
        return False
    cached = _SUPER_ADMIN_CACHE.get(int(user_id))
    if cached and time.time() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    row = _run("SELECT is_super_admin FROM public.users WHERE id = %s", (user_id,), fetch="one")
    value = bool(row and row[0])
    _SUPER_ADMIN_CACHE[int(user_id)] = (time.time(), value)
    return value


def user_emails(user_ids: Sequence[int]) -> Dict[int, str]:
    ids = [int(u) for u in user_ids if u is not None]
    if not ids:
        return {}
    rows = _run("SELECT id, email FROM public.users WHERE id = ANY(%s)", (ids,), fetch="all")
    return {int(r[0]): str(r[1]) for r in rows}


# ── Reports ──────────────────────────────────────────────────────────────────

def member_usage(owner: int, month_start: datetime.datetime, day_start: datetime.datetime) -> List[Dict[str, Any]]:
    """Each person who used the owner's bucket this month: credits this month and today."""
    rows = _run(f"""SELECT c.user_id, SUM(c.credits),
                           SUM(c.credits) FILTER (WHERE c.created_at >= %s), COUNT(DISTINCT c.event_id)
                    FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                    WHERE c.owner_user_id = %s AND c.created_at >= %s AND {_COUNTED}
                    GROUP BY c.user_id""", (day_start, owner, month_start), fetch="all")
    return [{"user_id": r[0], "month_credits": float(r[1] or 0), "today_credits": float(r[2] or 0),
             "events": int(r[3] or 0)} for r in rows]


def owners_usage(month_start: datetime.datetime) -> List[Dict[str, Any]]:
    """Every owner charged this month, with their plan row if any."""
    rows = _run(f"""SELECT c.owner_user_id, SUM(c.credits), COUNT(DISTINCT c.user_id), COUNT(DISTINCT c.event_id)
                    FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                    WHERE c.created_at >= %s AND {_COUNTED} GROUP BY c.owner_user_id""", (month_start,), fetch="all")
    return [{"owner_user_id": int(r[0]), "month_credits": float(r[1] or 0), "users": int(r[2] or 0),
             "events": int(r[3] or 0)} for r in rows]


def search_owners(text: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Users who own organizations, by part of their email or their exact user id."""
    text = (text or "").strip()
    uid = int(text) if text.isdigit() else -1
    rows = _run("""SELECT u.id, u.email, COUNT(o.id) FROM public.users u JOIN public.organizations o ON o.user_id = u.id
                   WHERE u.id = %s OR u.email ILIKE %s
                   GROUP BY u.id, u.email ORDER BY u.email LIMIT %s""",
                (uid, "%" + text.replace("%", r"\%").replace("_", r"\_") + "%", limit), fetch="all")
    return [{"owner_user_id": int(r[0]), "email": r[1], "organizations": int(r[2])} for r in rows]


def plans_all() -> Dict[int, Dict[str, Any]]:
    rows = _run("SELECT owner_user_id, plan, monthly_credits, user_daily_credits FROM public.ai_owner_plans",
                fetch="all")
    return {int(r[0]): {"plan": r[1], "monthly_credits": _num(r[2]), "user_daily_credits": _num(r[3])} for r in rows}


# ── Alerts ───────────────────────────────────────────────────────────────────

def alert_once(owner: int, cycle: str, level: str, detail: Optional[Dict[str, Any]] = None) -> bool:
    """Record an alert. True only the first time this (owner, cycle, level) is raised."""
    count = _run("""INSERT INTO public.ai_usage_alerts (owner_user_id, cycle, level, detail) VALUES (%s, %s, %s, %s)
                    ON CONFLICT DO NOTHING""", (owner, cycle, level, json.dumps(detail or {}, default=str)))
    return count == 1


def recent_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    rows = _run("""SELECT owner_user_id, cycle, level, detail, created_at FROM public.ai_usage_alerts
                   ORDER BY created_at DESC LIMIT %s""", (limit,), fetch="all")
    return [{"owner_user_id": r[0], "cycle": r[1], "level": r[2], "detail": r[3], "created_at": r[4]} for r in rows]


def _num(value: Any) -> Optional[float]:
    return float(value) if value is not None else None
