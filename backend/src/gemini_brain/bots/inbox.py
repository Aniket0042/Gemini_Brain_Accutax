"""inbox.py — What the bots Inbox page shows, built from the stored findings and runs (docs/VAT_BOT_IMPLEMENTATION_PLAN.md,
phase 3 of the bots pages).

Per finding: its check in words, the rule, how sure it is, its bills with links into Accutax, and the VAT it puts at
stake. Across findings: counts per state and per bot, the VAT at stake per currency, and what was fixed this week.
Every figure comes from the stored finding details (review_vat's own numbers); nothing here is written by a model.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.agent import links
from gemini_brain.bots import agents, watches
from gemini_brain.vat_review.tool import RULE_TEXT

OPEN_STATES = ("new", "seen")


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def bill_url(side: str, document_id: Any) -> Optional[str]:
    """The bill's or sales invoice's page in Accutax, or None without an app URL or an id."""
    base = links._app_url()
    doc = _num(document_id)
    if not base or doc is None:
        return None
    return base + (links.INVOICE if side == "sales" else links.BILL).format(id=int(doc))


def vat_at_stake(rule: str, detail: Dict[str, Any]) -> float:
    """Forward to the VAT agent (moved to bots/agents_vat.py)."""
    return agents.get("vat").engine.stake(rule, detail)


def enrich(f: Dict[str, Any], agent: str = "vat") -> Dict[str, Any]:
    """One stored finding, as the inbox card shows it. Titles come from the agent's words in the browser."""
    detail = dict(f.get("detail") or {})
    side = detail.get("side") or "purchases"
    detail["bills"] = [{**b, "url": bill_url(side, b.get("id"))} for b in detail.get("bills") or []]
    w = watches.watch_for_rule(f["rule"], side=side, agent=agent)
    return {
        **f,
        "agent": agent,
        "detail": detail,
        "side": side,
        "watch_id": w.id if w else None,
        "watch_label": w.label if w else None,
        "rule_text": RULE_TEXT.get(f["rule"], f["rule"]),
        "at_stake": agents.get(agent).engine.stake(f["rule"], detail),
        "currency": detail.get("currency") or "AED",
    }


def build(findings: Sequence[Dict[str, Any]], runs: Sequence[Dict[str, Any]], bots: Sequence[Dict[str, Any]],
          approvals: Sequence[Dict[str, Any]] = (), now: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """The whole inbox: findings (enriched, newest first), counts, per-bot counts, VAT at stake, fixed this week,
    the latest run of each bot."""
    now = now or dt.datetime.now(dt.timezone.utc)
    agent_of = {b["id"]: b.get("agent") or "vat" for b in bots}
    items = [enrich(f, agent_of.get(f.get("bot_id"), "vat")) for f in findings]
    counts = {"all": 0, "new": 0, "seen": 0, "fixed": 0, "dismissed": 0}
    by_bot: Dict[str, int] = defaultdict(int)
    stake: Dict[str, float] = defaultdict(float)
    fixed_week: List[Dict[str, Any]] = []
    week_ago = (now - dt.timedelta(days=7)).isoformat()
    for f in items:
        state = f["state"]
        if state in counts:
            counts[state] += 1
        if state in OPEN_STATES:
            counts["all"] += 1
            by_bot[f["bot_id"]] += 1
            stake[f["currency"]] += f["at_stake"]
        elif state == "fixed" and (f.get("updated_at") or "") >= week_ago:
            fixed_week.append(f)
    return {
        "unread_count": counts["new"] + len(approvals),
        "bots_count": len(bots),
        "counts": counts,
        "bots": [{"id": b["id"], "name": b["name"], "open": by_bot.get(b["id"], 0)} for b in bots],
        "at_stake": [{"currency": c, "amount": round(a, 2)} for c, a in sorted(stake.items()) if a],
        "fixed_this_week": fixed_week[:10],
        "runs": sorted(runs, key=lambda r: r.get("started_at") or "", reverse=True),
        "findings": items,
        "approvals": list(approvals),
    }
