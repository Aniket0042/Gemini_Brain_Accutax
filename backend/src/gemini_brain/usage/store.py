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

def seats(owner: int, as_of: Optional[datetime.datetime] = None) -> int:
    """The owner plus each person with an ACTIVE collaborator invitation to one of the owner's organizations,
    accepted (or approved) before `as_of`. A row with neither time is an old one and counts."""
    row = _run("""SELECT COUNT(DISTINCT c.accepted_by_user_id) FROM public.collaborators c
                  JOIN public.organizations o ON o.id = c.organization_id
                  WHERE o.user_id = %s AND c.status = 'ACTIVE' AND c.accepted_by_user_id IS NOT NULL
                    AND c.accepted_by_user_id <> %s
                    AND (%s::timestamptz IS NULL OR COALESCE(c.approved_at, c.accepted_at) IS NULL
                         OR COALESCE(c.approved_at, c.accepted_at) < %s::timestamptz)""",
               (owner, owner, as_of, as_of), fetch="one")
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


def member_events(owner: int, user_id: int, since: datetime.datetime, limit: int = 50) -> Dict[str, Any]:
    """One person's use of an owner's bucket since `since`: totals and the newest events (no question text: the
    usage tables never hold it)."""
    total = _run(f"""SELECT COUNT(*), COALESCE(SUM(e.input_tokens), 0), COALESCE(SUM(e.output_tokens), 0),
                              COALESCE(SUM(e.cache_read_tokens + e.cache_write_tokens), 0), COALESCE(SUM(c.credits), 0),
                              COUNT(*) FILTER (WHERE e.model_mode = 'lite')
                       FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                       WHERE c.owner_user_id = %s AND c.user_id = %s AND c.created_at >= %s AND {_COUNTED}""",
                 (owner, user_id, since), fetch="one")
    rows = _run(f"""SELECT e.id, e.created_at, e.kind, e.status, e.model_mode, e.calls, e.input_tokens, e.output_tokens,
                             e.cache_read_tokens + e.cache_write_tokens, c.credits, e.models, e.organization_ids
                      FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                      WHERE c.owner_user_id = %s AND c.user_id = %s AND c.created_at >= %s AND {_COUNTED}
                      ORDER BY e.created_at DESC LIMIT %s""", (owner, user_id, since, limit), fetch="all")
    return {
        "totals": {"events": int(total[0]), "input_tokens": int(total[1]), "output_tokens": int(total[2]),
                   "cached_tokens": int(total[3]), "credits": float(total[4]), "cost_usd": round(float(total[4]) / 1000, 4),
                   "lite_events": int(total[5])},
        "events": [{"id": r[0], "at": r[1], "kind": r[2], "status": r[3], "mode": r[4], "calls": r[5],
                    "input_tokens": r[6], "output_tokens": r[7], "cached_tokens": r[8], "credits": float(r[9]),
                    "models": sorted((r[10] or {}).keys()), "organization_ids": list(r[11] or [])} for r in rows],
    }


def overview(month_start: datetime.datetime, days_since: datetime.datetime, timezone: str) -> Dict[str, Any]:
    """The admin page's month at a glance: spend per day since `days_since` (days in `timezone`), the people who
    used the most, and the month's spend by model and by kind. Finished events only."""
    by_day = _run("""SELECT (created_at AT TIME ZONE %s)::date, SUM(cost_usd), COUNT(*) FILTER (WHERE kind = 'agent_answer')
                     FROM public.ai_usage_events WHERE created_at >= %s AND status <> 'running'
                     GROUP BY 1 ORDER BY 1""", (timezone, days_since), fetch="all")
    people = _run(f"""SELECT c.user_id, c.owner_user_id, SUM(c.credits), COUNT(DISTINCT c.event_id)
                      FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                      WHERE c.created_at >= %s AND c.user_id IS NOT NULL AND {_COUNTED}
                      GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 10""", (month_start,), fetch="all")
    models = _run("""SELECT m.key, SUM((m.value->>'cost_usd')::numeric), SUM((m.value->>'calls')::int)
                     FROM public.ai_usage_events e, jsonb_each(COALESCE(e.models, '{}'::jsonb)) m
                     WHERE e.created_at >= %s AND e.status <> 'running'
                     GROUP BY 1 ORDER BY 2 DESC""", (month_start,), fetch="all")
    kinds = _run("""SELECT kind, COUNT(*), SUM(credits), COUNT(*) FILTER (WHERE model_mode = 'lite'),
                           COUNT(*) FILTER (WHERE status NOT IN ('ok', 'ready'))
                    FROM public.ai_usage_events WHERE created_at >= %s AND status <> 'running'
                    GROUP BY 1 ORDER BY 3 DESC""", (month_start,), fetch="all")
    return {
        "by_day": [{"day": r[0].isoformat(), "cost_usd": round(float(r[1] or 0), 4), "questions": int(r[2] or 0)}
                   for r in by_day],
        "top_people": [{"user_id": int(r[0]), "owner_user_id": int(r[1]), "credits": float(r[2] or 0),
                        "events": int(r[3] or 0)} for r in people],
        "models": [{"model": r[0], "cost_usd": round(float(r[1] or 0), 4), "calls": int(r[2] or 0)} for r in models],
        "kinds": [{"kind": r[0], "events": int(r[1]), "credits": float(r[2] or 0), "lite": int(r[3]),
                   "not_ok": int(r[4])} for r in kinds],
    }


def owners_usage(month_start: datetime.datetime) -> List[Dict[str, Any]]:
    """Every owner charged this month, with their plan row if any."""
    rows = _run(f"""SELECT c.owner_user_id, SUM(c.credits), COUNT(DISTINCT c.user_id), COUNT(DISTINCT c.event_id),
                           array_remove(array_agg(DISTINCT c.user_id), NULL)
                    FROM public.ai_usage_charges c JOIN public.ai_usage_events e ON e.id = c.event_id
                    WHERE c.created_at >= %s AND {_COUNTED} GROUP BY c.owner_user_id""", (month_start,), fetch="all")
    return [{"owner_user_id": int(r[0]), "month_credits": float(r[1] or 0), "users": int(r[2] or 0),
             "events": int(r[3] or 0), "user_ids": [int(u) for u in r[4] or []]} for r in rows]


def search_owners(text: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Users who own organizations, by part of their email or their exact user id."""
    text = (text or "").strip()
    uid = int(text) if text.isdigit() else -1
    rows = _run("""SELECT u.id, u.email, COUNT(o.id) FROM public.users u JOIN public.organizations o ON o.user_id = u.id
                   WHERE u.id = %s OR u.email ILIKE %s
                   GROUP BY u.id, u.email ORDER BY u.email LIMIT %s""",
                (uid, "%" + text.replace("%", r"\%").replace("_", r"\_") + "%", limit), fetch="all")
    return [{"owner_user_id": int(r[0]), "email": r[1], "organizations": int(r[2])} for r in rows]


def search_collaborators(text: str, limit: int = 20) -> List[Dict[str, Any]]:
    """People invited to someone else's organizations (ACTIVE or INVITED), by part of their email or their user id,
    with the owner whose bucket they use."""
    text = (text or "").strip()
    uid = int(text) if text.isdigit() else -1
    rows = _run("""SELECT DISTINCT COALESCE(c.accepted_by_user_id, u.id), LOWER(c.email), c.status, o.user_id, ow.email
                   FROM public.collaborators c
                   JOIN public.organizations o ON o.id = c.organization_id
                   JOIN public.users ow ON ow.id = o.user_id
                   LEFT JOIN public.users u ON LOWER(TRIM(u.email)) = LOWER(TRIM(c.email))
                   WHERE c.status IN ('ACTIVE', 'INVITED')
                     AND (COALESCE(c.accepted_by_user_id, u.id) = %s OR c.email ILIKE %s)
                   ORDER BY 2 LIMIT %s""",
                (uid, "%" + text.replace("%", r"\%").replace("_", r"\_") + "%", limit), fetch="all")
    return [{"user_id": r[0], "email": r[1], "status": r[2], "owner_user_id": int(r[3]), "owner_email": r[4]}
            for r in rows]


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
