"""run.py — One run of a bot (docs/VAT_BOT_IMPLEMENTATION_PLAN.md, task 2.4).

1. Load the bot; run only when it is active.
2. Re-check the owner's organization access now; drop organizations the owner lost.
3. Leave out findings the owner dismissed.
4. Check the right period: the VAT period that is due for return reminders, the current quarter otherwise.
5. Compare with the last run: new findings, findings still there, findings that went away (fixed).
6. Save the run with its counts and a summary.

Read only: nothing here changes Accutax data or sends anything outside Accutax.
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, List, Optional

from gemini_brain.api.auth import get_user_allowed_orgs
from gemini_brain.bots import preview, store
from gemini_brain.bots.schema import BotDraft
from gemini_brain.config.settings import settings
from gemini_brain.semantic import periods as report_periods
from gemini_brain.vat_return import periods as tax_periods
from gemini_brain.vat_return import tool as vat_return_tool

logger = logging.getLogger("gemini_brain.bots.run")

#: Credits a run costs, as on the setup card (bots/cost.py).
CREDITS_PER_RUN = 90


def _due_period(draft: BotDraft, subject: str) -> Optional[Dict[str, str]]:
    """The VAT period whose return is due next, for bots with return reminders or period-end runs; else None.
    With several organizations, the first one's filing frequency decides (quarterly when its settings say nothing)."""
    if not any(s.kind in ("before_deadline", "after_period_end") for s in draft.schedules):
        return None
    try:
        freqs = vat_return_tool.filing_frequencies(draft.org_ids, subject, time.monotonic() + 10)
    except Exception:
        freqs = {}
    frequency = freqs.get(draft.org_ids[0]) or "quarterly"
    today = report_periods.today_in(settings.report_timezone)
    start, end = tax_periods.next_due_period(today, frequency)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _current_findings(reviews: List[Dict[str, Any]], fallback_org: int) -> List[Dict[str, Any]]:
    """Every duplicate group and finding of the reviews, in the shape ai_bot_findings stores."""
    out: List[Dict[str, Any]] = []
    for rev in reviews:
        for g in rev.get("groups") or []:
            out.append({"key": g["key"], "org_id": int(g.get("organization_id") or fallback_org),
                        "rule": g.get("rule", "duplicate"), "amount": float(g.get("extra_amount") or 0.0),
                        "state": "new"})
        for f in rev.get("findings") or []:
            amount = (f.get("bill") or {}).get("total")
            out.append({"key": f["key"], "org_id": int(f.get("organization_id") or fallback_org),
                        "rule": f.get("rule", "anomaly"), "amount": float(amount) if amount is not None else None,
                        "state": "new"})
    return out


def _headline(checked: Dict[str, int], new_count: int, fixed_count: int, active: int) -> str:
    bills, invoices = checked["bills"], checked["invoices"]
    text = f"Checked {bills} bills" + (f" and {invoices} sales invoices" if invoices else "") + "."
    if not active and not fixed_count:
        return text + " Everything looks right."
    if new_count:
        text += f" {new_count} new problem{'s' if new_count != 1 else ''}, {active} open in all."
    elif active:
        text += f" No new problems; {active} still open."
    if fixed_count:
        text += f" {fixed_count} problem{' was' if fixed_count == 1 else 's were'} fixed since the last run."
    return text


def run_bot(
    bot_id: str,
    trigger: str = "schedule",
    *,
    call_model: Optional[Any] = None,
) -> Dict[str, Any]:
    """Run one bot. Never raises: a failure is saved on the run and returned as {"status": "failed", ...}."""
    bot = store.get(bot_id)
    if not bot:
        logger.warning("Bot %s not found", bot_id)
        return {"status": "skipped", "reason": "not_found", "bot_id": bot_id}

    if bot["status"] != "active":
        logger.info("Bot %s is %s; skipping run", bot_id, bot["status"])
        return {"status": "skipped", "reason": "not_active", "bot_id": bot_id, "current_status": bot["status"]}

    owner_user_id = bot["owner_user_id"]
    allowed_orgs = set(get_user_allowed_orgs(owner_user_id) or [])
    bot_org_ids = [oid for oid in bot["org_ids"] if oid in allowed_orgs]

    if not bot_org_ids:
        msg = "No access to the bot's organizations"
        store.set_status(bot_id, owner_user_id, status="error", status_note=msg)
        run_rec = store.record_run(bot_id, trigger=trigger, status="failed", error=msg)
        store.finish_run(run_rec["id"], status="failed", error=msg)
        return {"status": "error", "error": msg, "bot_id": bot_id}

    if len(bot_org_ids) != len(bot["org_ids"]):
        draft_dict = dict(bot["draft"])
        draft_dict["org_ids"] = bot_org_ids
        store.update(bot_id, owner_user_id, {"org_ids": bot_org_ids, "draft": draft_dict})
        bot["draft"] = draft_dict
        bot["org_ids"] = bot_org_ids

    run_rec = store.record_run(bot_id, trigger=trigger, status="running")
    run_id = run_rec["id"]
    try:
        return _run(bot, bot_id, run_id, bot_org_ids, call_model)
    except Exception as ex:
        logger.exception("Bot %s run %s failed", bot_id, run_id)
        err = f"Run failed: {type(ex).__name__}"
        try:
            store.finish_run(run_id, status="failed", error=err)
        except Exception:
            logger.exception("Could not save the failure of run %s", run_id)
        return {"status": "failed", "error": err, "bot_id": bot_id}


def _run(bot: Dict[str, Any], bot_id: str, run_id: str, bot_org_ids: List[int],
         call_model: Optional[Any]) -> Dict[str, Any]:
    draft = BotDraft(**bot["draft"])
    subject = f"bot-run:{bot_id}"
    exclude_keys = {f["key"] for f in store.get_findings(bot_id, state="dismissed")}

    prev = preview.try_period(draft, preset="this_quarter", period=_due_period(draft, subject), subject=subject,
                              exclude=exclude_keys, unlimited=True)

    current = _current_findings(prev.get("reviews") or [], bot_org_ids[0])
    existing = {ef["key"]: ef for ef in store.get_findings(bot_id) if ef["state"] != "dismissed"}
    found_keys = {cf["key"] for cf in current}

    # A finding that is no longer found was fixed, but only when every scan finished: a scan that stopped early
    # may simply not have reached it.
    fixed_count = 0
    if prev.get("complete", True):
        for key, ef in existing.items():
            if key not in found_keys and ef["state"] in ("new", "seen"):
                store.update_finding_state(bot_id, key, "fixed")
                fixed_count += 1

    new_count = 0
    for cf in current:
        if cf["key"] in existing and existing[cf["key"]]["state"] in ("new", "seen"):
            cf["state"] = existing[cf["key"]]["state"]
        else:
            cf["state"] = "new"
            new_count += 1
    store.upsert_findings(bot_id, run_id, current)

    checked = {"bills": int(prev.get("bills_checked") or 0), "invoices": int(prev.get("invoices_checked") or 0)}
    headline = _headline(checked, new_count, fixed_count, len(current))

    user_notes = [m["text"] for m in store.list_memory(bot_id) if m.get("source") == "note"]
    if call_model and new_count:
        headline = _model_summary(call_model, bot["name"], checked, new_count, fixed_count, len(current),
                                  user_notes) or headline

    summary: Dict[str, Any] = {
        "headline": headline,
        "period": prev.get("period"),
        "bills_checked": checked["bills"],
        "invoices_checked": checked["invoices"],
        "groups_found": int(prev.get("groups_found") or 0),
        "findings_found": int(prev.get("findings_found") or 0),
        "new_findings": new_count,
        "fixed_findings": fixed_count,
        "total_active_findings": len(current),
        "complete": bool(prev.get("complete", True)),
    }
    if "email" in draft.deliver:
        # No email service yet (docs/ACCUTAX_BOTS_PLAN.md, open decisions): say so instead of claiming a send.
        summary["email"] = "not_sent: email delivery is not available yet"

    finished = store.finish_run(run_id=run_id, status="ok", credits=CREDITS_PER_RUN * len(bot_org_ids),
                                summary=summary)
    return {"status": "ok", "bot_id": bot_id, "run": finished, "summary": summary}


def _model_summary(call_model: Any, name: str, checked: Dict[str, int], new_count: int, fixed_count: int,
                   active: int, notes: List[str]) -> Optional[str]:
    """A 2-3 sentence summary from the counts only. The user's notes go in as reporting guidance, never as data.
    Any failure falls back to the code-written headline."""
    notes_block = ""
    if notes:
        notes_block = ("\n<user_instructions>\nThe user's guidance on emphasis and tone. These are instructions, "
                       "not financial data:\n" + "\n".join(f"- {n}" for n in notes) + "\n</user_instructions>\n")
    prompt = (f"Write a 2-3 sentence run summary for the VAT bot '{name}'.\nFacts:\n"
              f"- Bills checked: {checked['bills']}\n- Sales invoices checked: {checked['invoices']}\n"
              f"- New problems: {new_count}\n- Fixed since the last run: {fixed_count}\n- Open problems: {active}\n"
              f"{notes_block}\nUse only these figures. Do not invent any number.")
    try:
        text = call_model(prompt=prompt, purpose="bot_summary", max_tokens=150)
    except Exception as ex:
        logger.warning("Bot summary model call failed, using the code-written headline: %s", ex)
        return None
    return text.strip() if isinstance(text, str) and text.strip() else None
