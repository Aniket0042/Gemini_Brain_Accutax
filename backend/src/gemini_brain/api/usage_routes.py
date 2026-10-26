"""
usage_routes.py — The AI usage API (docs/ORG_TOKEN_LIMITS_STRATEGY.md): the bars every user sees, the owner's page
(their bucket, who used it, each person's daily cap) and the Accutax admin page (plans, top-ups, alerts).

An owner is the user in organizations.user_id; the owner page is about the caller's own bucket only. The admin page is
for users with is_super_admin and those listed in USAGE_ADMINS. Everything needs USAGE_DB=on.
"""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from gemini_brain.api.auth import CurrentUser, get_current_user
from gemini_brain.config.settings import settings
from gemini_brain.usage import ledger, limits, plans

logger = logging.getLogger("gemini_brain.api.usage")

router = APIRouter(prefix="/api/v1/usage", tags=["AI usage"])

#: What a typical question costs, to put a daily cap in words people use.
TYPICAL_QUESTION_CREDITS = 20


def _store():
    if not ledger.db_on():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="AI usage tracking is off.")
    from gemini_brain.usage import store
    return store


def _is_admin(user: CurrentUser) -> bool:
    people = {p.strip().lower() for p in (settings.usage_admins or "").split(",") if p.strip()}
    if str(user.user_id).lower() in people or (user.email or "").strip().lower() in people:
        return True
    try:
        return _store().is_super_admin(user.user_id)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning("usage: super-admin check failed: %s", e)
        return False


def _admin(user: CurrentUser) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to manage AI usage.")


def _audit(user: CurrentUser, action: str, **detail: Any) -> None:
    try:
        from gemini_brain.agent import audit
        audit.event("usage_" + action, user_id=user.user_id, **detail)
    except Exception as e:  # noqa: BLE001
        logger.warning("usage audit not written: %s", e)


# ── Every user ───────────────────────────────────────────────────────────────

@router.get("", summary="The caller's AI usage: the buckets of the chosen organizations and their own day")
def my_usage(org_ids: str = Query("", description="Comma-separated organization ids; default: all the caller's"),
             current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    allowed = [int(o) for o in current_user.allowed_org_ids or []]
    try:
        asked = [int(o) for o in org_ids.split(",") if o.strip()]
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="org_ids must be numbers.")
    orgs = [o for o in asked if o in set(allowed)] if asked else allowed
    p = limits.periods()
    internal = store.is_super_admin(current_user.user_id)
    owners = sorted({o for o in ledger.owners_of(orgs).values() if o is not None}) if not internal else []
    states = [_public_owner(limits.owner_state(o, current_user.user_id, p)) for o in owners]
    return {"enforce": limits.enforce_mode(), "internal": internal, "owners": states,
            "is_owner": bool(store.owned_by(current_user.user_id)), "is_admin": _is_admin(current_user),
            "typical_question_credits": TYPICAL_QUESTION_CREDITS}


def _public_owner(st: Dict[str, Any]) -> Dict[str, Any]:
    """What any person in the bucket may see: percentages and dates, no credit amounts."""
    user = st.get("user") or {}
    return {"owner_user_id": st["owner_user_id"], "plan_label": st["plan_label"], "percent": st["percent"],
            "state": st["state"], "resets_at": st["resets_at"],
            "user": {"percent": user.get("percent"), "disabled": user.get("disabled"),
                     "resets_at": user.get("resets_at")} if user else None}


# ── The owner ────────────────────────────────────────────────────────────────

def _members(store: Any, owner: int, p: limits.Periods) -> List[Dict[str, Any]]:
    """The owner, the people invited to the owner's organizations, and anyone else charged to the bucket this month."""
    row = store.plan_row(owner) or {}
    plan = plans.get(row.get("plan"))
    default_cap = row.get("user_daily_credits") if row.get("user_daily_credits") is not None else plan.user_daily
    usage = {u["user_id"]: u for u in store.member_usage(owner, p.month_start, p.day_start)}
    caps = store.caps_of(owner)
    people: Dict[int, Dict[str, Any]] = {owner: {"role": "owner", "invitation": "ACTIVE"}}
    for c in store.collaborators_of(owner):
        if c["user_id"] is not None and int(c["user_id"]) != owner:
            people.setdefault(int(c["user_id"]), {"role": "collaborator", "invitation": c["status"]})
    for uid in usage:
        if uid is not None:
            people.setdefault(int(uid), {"role": "other", "invitation": None})
    emails = store.user_emails(list(people))
    out = []
    for uid, info in people.items():
        u = usage.get(uid) or {}
        cap = caps.get(uid) or {}
        effective = cap.get("daily_credits") if cap.get("daily_credits") is not None else default_cap
        out.append({"user_id": uid, "email": emails.get(uid), **info,
                    "month_credits": round(u.get("month_credits", 0), 1), "today_credits": round(u.get("today_credits", 0), 1),
                    "questions": u.get("events", 0), "daily_cap": effective, "cap_override": cap.get("daily_credits"),
                    "disabled": bool(cap.get("disabled")), "default_cap": default_cap})
    return sorted(out, key=lambda m: (m["role"] != "owner", -m["month_credits"]))


@router.get("/owner", summary="The caller's own bucket: how much is used, by whom, and each person's daily cap")
def owner_page(current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    owner = int(current_user.user_id)
    if not store.owned_by(owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own an organization.")
    p = limits.periods()
    return {"bucket": limits.owner_state(owner, None, p), "members": _members(store, owner, p),
            "typical_question_credits": TYPICAL_QUESTION_CREDITS}


class CapUpdate(BaseModel):
    daily_credits: Optional[float] = Field(default=None, ge=0, description="null: the plan's default")
    disabled: bool = False


@router.put("/owner/members/{user_id}", summary="Set one person's daily cap in the caller's bucket, or turn AI off")
def set_member_cap(user_id: int, body: CapUpdate, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    owner = int(current_user.user_id)
    if not store.owned_by(owner):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="You do not own an organization.")
    p = limits.periods()
    if user_id not in {m["user_id"] for m in _members(store, owner, p)}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="This person does not use your organizations.")
    bucket = limits.owner_state(owner, None, p)["bucket"]
    if body.daily_credits is not None and bucket is not None and body.daily_credits > bucket:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="A daily limit cannot be larger than the whole month's AI.")
    store.set_cap(owner, user_id, body.daily_credits, body.disabled, owner)
    _audit(current_user, "cap_set", owner=owner, member=user_id, daily_credits=body.daily_credits,
           disabled=body.disabled)
    return {"ok": True}


# ── Accutax admin ────────────────────────────────────────────────────────────

@router.get("/admin", summary="Platform spend, every owner charged this month, plans and alerts")
def admin_page(current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    _admin(current_user)
    p = limits.periods()
    charged = {o["owner_user_id"]: o for o in store.owners_usage(p.month_start)}
    planned = store.plans_all()
    owners = []
    for owner in sorted(set(charged) | set(planned)):
        if owner == ledger.INTERNAL_OWNER:
            continue
        st = limits.owner_state(owner, None, p)
        used = charged.get(owner) or {}
        st.update(users=used.get("users", 0), questions=used.get("events", 0), user_ids=used.get("user_ids", []))
        owners.append(st)
    emails = store.user_emails({u for o in owners for u in [o["owner_user_id"], *o["user_ids"]]})
    for o in owners:
        o["email"] = emails.get(o["owner_user_id"])
        # The people who used this bucket this month, the owner first.
        ids = sorted(o.pop("user_ids"), key=lambda u: (u != o["owner_user_id"], emails.get(u) or ""))
        o["people"] = [emails.get(u) or f"User {u}" for u in ids]
    internal = charged.get(ledger.INTERNAL_OWNER, {})
    return {
        "enforce": limits.enforce_mode(),
        "platform": {"today_usd": round(store.platform_cost(p.utc_day_start), 4),
                     "month_usd": round(store.platform_cost(p.month_start), 4),
                     "alert_usd": settings.usage_platform_alert_usd, "pause_usd": settings.usage_platform_pause_usd},
        "internal_month_credits": round(internal.get("month_credits", 0), 1),
        "owners": sorted(owners, key=lambda o: -(o["percent"] or 0)),
        "plans": [{"key": pl.key, "label": pl.label, "per_seat": pl.per_seat, "user_daily": pl.user_daily,
                   "max_seats": pl.max_seats}
                  for pl in plans.PLANS.values()],
        "alerts": store.recent_alerts(50),
    }


@router.get("/admin/owners", summary="Find owners, and collaborators with the owner whose bucket they use, by email or user id")
def admin_search_owners(q: str = Query(..., min_length=1, max_length=200),
                        current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    _admin(current_user)
    return {"owners": store.search_owners(q), "collaborators": store.search_collaborators(q)}


@router.get("/admin/owners/{owner_id}", summary="One owner's bucket, members and top-ups")
def admin_owner(owner_id: int, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    _admin(current_user)
    p = limits.periods()
    return {"bucket": limits.owner_state(owner_id, None, p), "plan_row": store.plan_row(owner_id),
            "members": _members(store, owner_id, p), "topups": store.topups_of(owner_id),
            "email": store.user_emails([owner_id]).get(owner_id)}


class PlanUpdate(BaseModel):
    plan: str
    monthly_credits: Optional[float] = Field(default=None, ge=0, description="null: plan size per seat x seats")
    user_daily_credits: Optional[float] = Field(default=None, ge=0, description="null: the plan's default cap")


@router.put("/admin/owners/{owner_id}/plan", summary="Put an owner on a plan, optionally with its own sizes")
def admin_set_plan(owner_id: int, body: PlanUpdate, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    _admin(current_user)
    if body.plan not in plans.PLANS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown plan {body.plan}.")
    store.set_plan(owner_id, body.plan, body.monthly_credits, body.user_daily_credits, current_user.user_id)
    _audit(current_user, "plan_set", owner=owner_id, plan=body.plan, monthly_credits=body.monthly_credits,
           user_daily_credits=body.user_daily_credits)
    return {"ok": True}


class TopupCreate(BaseModel):
    credits: float = Field(gt=0)
    reason: str = ""
    expires_days: Optional[int] = Field(default=365, ge=1, description="null: never expires")


@router.post("/admin/owners/{owner_id}/topups", summary="Give an owner extra credits")
def admin_add_topup(owner_id: int, body: TopupCreate, current_user: CurrentUser = Depends(get_current_user)) -> Dict[str, Any]:
    store = _store()
    _admin(current_user)
    expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=body.expires_days)
               if body.expires_days else None)
    topup_id = store.add_topup(owner_id, body.credits, body.reason, expires, current_user.user_id)
    _audit(current_user, "topup_added", owner=owner_id, credits=body.credits, topup=topup_id)
    return {"ok": True, "id": topup_id}
