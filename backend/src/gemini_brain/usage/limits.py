"""
limits.py — Whether a question may run, and on which model (docs/ORG_TOKEN_LIMITS_STRATEGY.md, section 0).

Checked before every question and file read, top to bottom; the strictest result wins:

    1. Platform    spend today (UTC) past USAGE_PLATFORM_PAUSE_USD pauses AI for everyone but super-admins
    2. Owner       each owner whose organizations the question reads: its monthly bucket (plan x seats + top-ups).
                   Used up: the lite model answers; LITE_GRACE more used: blocked until the next month.
    3. Person      inside each of those buckets: the person's daily cap (set by the owner), or AI turned off
    4. Speed       one question at a time per person, USAGE_RATE_PER_MINUTE a minute (in this process)

USAGE_ENFORCE: off (no check), log (check and log), soft (bars and warnings, nothing blocked), hard (all of it).
A failed check lets the question through: the limits protect spend, they must not take the assistant down.
Super-admins are charged to Accutax (owner 0) and are not limited.
"""
from __future__ import annotations

import collections
import datetime
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

from gemini_brain.config.settings import settings
from gemini_brain.usage import ledger, plans

logger = logging.getLogger("gemini_brain.usage.limits")

ALLOW, LITE, BLOCK = "allow", "lite", "block"
_RANK = {ALLOW: 0, LITE: 1, BLOCK: 2}
#: A person whose day is this many times their usual day (and at least SPIKE_MIN credits) raises an alert.
SPIKE_FACTOR = 5
SPIKE_MIN = 200


@dataclass
class Decision:
    action: str = ALLOW
    code: str = ""
    message: str = ""
    banner: str = ""
    owners: List[Dict[str, Any]] = field(default_factory=list)
    internal: bool = False
    enforce: str = "off"

    @property
    def model_mode(self) -> str:
        return "lite" if self.action == LITE else "full"


def enforce_mode() -> str:
    mode = (settings.usage_enforce or "off").strip().lower()
    return mode if mode in ("off", "log", "soft", "hard") else "off"


# ── Time ─────────────────────────────────────────────────────────────────────

@dataclass
class Periods:
    month_start: datetime.datetime
    next_month: datetime.datetime
    day_start: datetime.datetime
    next_day: datetime.datetime
    month_key: str
    day_key: str
    utc_day_start: datetime.datetime


def periods(now: Optional[datetime.datetime] = None) -> Periods:
    """The current month and day in USAGE_TIMEZONE, as UTC-aware datetimes."""
    try:
        tz = ZoneInfo(settings.usage_timezone or "Asia/Dubai")
    except Exception:  # noqa: BLE001 - an unknown zone name
        tz = ZoneInfo("Asia/Dubai")
    now = (now or datetime.datetime.now(datetime.timezone.utc)).astimezone(tz)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month = day.replace(day=1)
    next_month = (month + datetime.timedelta(days=32)).replace(day=1)
    utc_now = now.astimezone(datetime.timezone.utc)
    return Periods(month_start=month, next_month=next_month, day_start=day, next_day=day + datetime.timedelta(days=1),
                   month_key=month.strftime("%Y-%m"), day_key=day.strftime("%Y-%m-%d"),
                   utc_day_start=utc_now.replace(hour=0, minute=0, second=0, microsecond=0))


def _date(d: datetime.datetime) -> str:
    return d.strftime("%d %b %Y").lstrip("0")


# ── State of one bucket ──────────────────────────────────────────────────────

def owner_state(owner: int, user_id: Optional[int], p: Periods) -> Dict[str, Any]:
    """An owner's bucket this month and, when `user_id` is given, that person's day inside it."""
    from gemini_brain.usage import store
    row = store.plan_row(owner) or {}
    plan = plans.get(row.get("plan"))
    seats = store.seats(owner)
    topups = store.topup_credits(owner)
    if plan.per_seat is None:
        bucket = None
    else:
        base = row.get("monthly_credits")
        bucket = round((base if base is not None else plan.per_seat * seats) + topups, 3)
    used = store.owner_used(owner, p.month_start)
    if bucket is None:
        state = "ok"
    elif used >= bucket * (1 + plans.LITE_GRACE):
        state = "blocked"
    elif used >= bucket:
        state = "lite"
    elif used >= bucket * plans.WARN_AT[-1]:
        state = "warn"
    else:
        state = "ok"
    out: Dict[str, Any] = {
        "owner_user_id": owner, "plan": plan.key, "plan_label": plan.label, "seats": seats, "topups": topups,
        "bucket": bucket, "used": round(used, 3), "percent": _pct(used, bucket), "state": state,
        "resets_at": p.next_month.isoformat(), "user": None,
    }
    if user_id is not None:
        cap_row = store.cap_row(owner, user_id) or {}
        cap = cap_row.get("daily_credits")
        if cap is None:
            cap = row.get("user_daily_credits") if row.get("user_daily_credits") is not None else plan.user_daily
        used_today = store.user_used(owner, user_id, p.day_start)
        out["user"] = {"daily_cap": cap, "used_today": round(used_today, 3), "percent": _pct(used_today, cap),
                       "disabled": bool(cap_row.get("disabled")), "resets_at": p.next_day.isoformat()}
    return out


def _pct(used: float, total: Optional[float]) -> Optional[int]:
    if total is None:
        return None
    if total <= 0:
        return 100
    return int(min(999, round(100 * used / total)))


# ── The check ────────────────────────────────────────────────────────────────

def check(user_id: Optional[int], organization_ids: Sequence[int], *, kind: str = "agent_answer") -> Decision:
    mode = enforce_mode()
    if mode == "off":
        return Decision(enforce=mode)
    try:
        decision = _decide(user_id, organization_ids, kind)
    except Exception as e:  # noqa: BLE001 - fail open
        logger.warning("usage limits: check failed, question allowed: %s", e)
        return Decision(enforce=mode)
    decision.enforce = mode
    if decision.action != ALLOW and mode != "hard":
        logger.info("usage limits (%s): user %s orgs %s would be %s (%s)", mode, user_id, list(organization_ids),
                    decision.action, decision.code)
        decision.action, decision.code, decision.message = ALLOW, "", ""
    if mode == "log":
        decision.banner = ""
    if kind != "agent_answer" and decision.action == LITE:
        decision.action = ALLOW  # a file is always read with the reader's own model
    return decision


def _decide(user_id: Optional[int], organization_ids: Sequence[int], kind: str) -> Decision:
    from gemini_brain.usage import store
    p = periods()
    internal = store.is_super_admin(user_id)
    d = Decision(internal=internal)
    if internal:
        return d

    spent = store.platform_cost(p.utc_day_start)
    if spent >= settings.usage_platform_pause_usd:
        _alert(0, p.utc_day_start.strftime("%Y-%m-%d"), "platform_pause", {"usd": round(spent, 2)})
        return _worse(d, BLOCK, "USAGE_PAUSED", "AI is paused for a short time because of unusually high use. "
                                                "Try again later.")
    if spent >= settings.usage_platform_alert_usd:
        _alert(0, p.utc_day_start.strftime("%Y-%m-%d"), "platform_alert", {"usd": round(spent, 2)})

    owners = ledger.owners_of([int(o) for o in organization_ids])
    for owner in sorted({o for o in owners.values() if o is not None}):
        st = owner_state(owner, user_id, p)
        d.owners.append(st)
        _owner_alerts(st, p)
        several = len(set(owners.values())) > 1
        whose = "One of the organizations in this question" if several else "Your organization"
        renews = _date(p.next_month)
        if st["state"] == "blocked":
            d = _worse(d, BLOCK, "USAGE_LIMIT",
                       f"{whose} has used all of this month's AI. It is available again on {renews}. "
                       "The account owner can ask Accutax for more.")
        elif st["state"] == "lite":
            d = _worse(d, LITE, "USAGE_LITE", "")
            d.banner = (f"{whose} has used this month's AI, so answers now come from a faster, lighter model "
                        f"until {renews}.")
        elif st["state"] == "warn" and not d.banner:
            d.banner = f"{st['percent']}% of this month's AI is used. It renews on {renews}."
        u = st["user"] or {}
        if u.get("disabled"):
            d = _worse(d, BLOCK, "USAGE_DISABLED",
                       "The account owner has turned AI off for you for this organization.")
        elif u.get("daily_cap") is not None and u["used_today"] >= u["daily_cap"]:
            d = _worse(d, BLOCK, "USAGE_USER_DAILY",
                       f"You have used today's AI. It is available again at midnight ({settings.usage_timezone}). "
                       "The account owner can raise your daily limit.")
        if user_id is not None:
            _spike(owner, int(user_id), u.get("used_today") or 0, p)
    return d


def _worse(d: Decision, action: str, code: str, message: str) -> Decision:
    if _RANK[action] > _RANK[d.action]:
        d.action, d.code = action, code
        if message:
            d.message = message
    return d


# ── Alerts ───────────────────────────────────────────────────────────────────

_raised: set = set()


def _alert(owner: int, cycle: str, level: str, detail: Dict[str, Any]) -> None:
    """Record an alert once per cycle and log it. There is no mail service here; the admin page lists them."""
    key = (owner, cycle, level)
    if key in _raised:
        return
    try:
        from gemini_brain.usage import store
        if store.alert_once(owner, cycle, level, detail):
            logger.warning("usage alert: owner %s %s %s %s", owner, cycle, level, detail)
        _raised.add(key)
    except Exception as e:  # noqa: BLE001
        logger.warning("usage alert not recorded: %s", e)


def _owner_alerts(st: Dict[str, Any], p: Periods) -> None:
    pct = st.get("percent")
    if pct is None:
        return
    for level in (50, 80, 100):
        if pct >= level:
            _alert(st["owner_user_id"], p.month_key, str(level), {"percent": pct, "used": st["used"]})
    if st["state"] == "blocked":
        _alert(st["owner_user_id"], p.month_key, "lite_end", {"percent": pct})


def _spike(owner: int, user_id: int, today: float, p: Periods) -> None:
    if today < SPIKE_MIN:
        return
    try:
        from gemini_brain.usage import store
        before = store.user_daily_history(user_id, p.day_start - datetime.timedelta(days=14), p.day_start) / 14
        if today >= SPIKE_FACTOR * max(before, 1):
            _alert(owner, p.day_key, f"spike:{user_id}", {"today": today, "usual_day": round(before, 1)})
    except Exception as e:  # noqa: BLE001
        logger.warning("usage limits: spike check failed: %s", e)


# ── Speed ────────────────────────────────────────────────────────────────────

class Gate:
    """One question at a time per person and a per-minute rate, in this process (the service runs one worker)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: Dict[int, int] = collections.defaultdict(int)
        self._recent: Dict[int, collections.deque] = collections.defaultdict(collections.deque)

    def enter(self, user_id: int) -> Optional[Decision]:
        """None when the question may start (call leave() after it); else the refusal."""
        now = time.monotonic()
        with self._lock:
            recent = self._recent[user_id]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if self._running[user_id] >= 1:
                return Decision(action=BLOCK, code="USAGE_BUSY", enforce="hard",
                                message="Your previous question is still being answered. Ask again when it finishes.")
            if len(recent) >= max(1, settings.usage_rate_per_minute):
                return Decision(action=BLOCK, code="USAGE_RATE", enforce="hard",
                                message="You are asking very quickly. Wait a minute, then ask again.")
            self._running[user_id] += 1
            recent.append(now)
            return None

    def leave(self, user_id: int) -> None:
        with self._lock:
            self._running[user_id] = max(0, self._running[user_id] - 1)


gate = Gate()


# ── What the app shows ───────────────────────────────────────────────────────

def public(d: Decision) -> Optional[Dict[str, Any]]:
    """The usage block of a response: bars, banner and mode. None when limits are off or only logged."""
    if d.enforce in ("off", "log"):
        return None
    return {"mode": d.model_mode, "banner": d.banner or None, "internal": d.internal,
            "owners": [{k: o.get(k) for k in ("owner_user_id", "plan_label", "percent", "state", "resets_at", "user")}
                       for o in d.owners]}


def notice(d: Decision) -> Dict[str, Any]:
    titles = {"USAGE_LIMIT": "This month's AI is used up", "USAGE_USER_DAILY": "Today's AI is used up",
              "USAGE_DISABLED": "AI is off for you", "USAGE_PAUSED": "AI is paused",
              "USAGE_BUSY": "One question at a time", "USAGE_RATE": "Too many questions at once"}
    return {"kind": "limit", "code": d.code, "title": titles.get(d.code, "AI usage limit"), "message": d.message,
            "suggestions": [], "retryable": d.code in ("USAGE_BUSY", "USAGE_RATE", "USAGE_PAUSED"), "request_id": ""}


def refused_response(d: Decision) -> Dict[str, Any]:
    """The answer to a question that was not run. No model was called and nothing is charged."""
    return {"answer": d.message, "status": "failed", "notice": notice(d), "usage": public(d),
            "token_usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "llm_calls": 0}}
