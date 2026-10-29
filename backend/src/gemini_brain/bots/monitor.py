"""monitor.py — Event-driven per-document check engine for VAT bots.

Phase 3 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md:
- Task 3.1: Receives batched document events (bill.saved, invoice.saved) from Nest backend.
- Task 3.2: Runs per-document checks scoped to the saved document IDs; writes one-sentence explanations; respects quiet hours.
- Task 3.3: Attaches watch attribution ("Why did I get this?").
"""
from __future__ import annotations

import datetime as dt
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Set
from zoneinfo import ZoneInfo

from gemini_brain.api.auth import get_user_allowed_orgs
from gemini_brain.bots import store, watches
from gemini_brain.bots.schema import BotDraft
from gemini_brain.config.settings import settings
from gemini_brain.vat_review import tool as vat_review_tool

logger = logging.getLogger("gemini_brain.bots.monitor")

DEFAULT_QUIET_START = 20  # 20:00 (8 PM)
DEFAULT_QUIET_END = 8     # 08:00 (8 AM)


def is_in_quiet_hours(
    now: Optional[dt.datetime] = None,
    tz_name: Optional[str] = None,
    start_hour: int = DEFAULT_QUIET_START,
    end_hour: int = DEFAULT_QUIET_END,
) -> bool:
    """Check if current time is within quiet hours (default 20:00 - 08:00)."""
    tz = ZoneInfo(tz_name or settings.report_timezone or "UTC")
    curr = now or dt.datetime.now(tz)
    h = curr.hour
    if start_hour > end_hour:
        return h >= start_hour or h < end_hour
    return start_hour <= h < end_hour


def generate_finding_sentence(
    finding: Dict[str, Any],
    rule: str,
    call_model: Optional[Any] = None,
) -> str:
    """Produce a concise 1-sentence explanation for a new document finding."""
    bills = finding.get("bills") or []
    bill = finding.get("bill") if isinstance(finding.get("bill"), dict) else {}
    invoice = finding.get("invoice") if isinstance(finding.get("invoice"), dict) else {}

    bills_nums = [
        str(b.get("bill_number") or b.get("document_number") or b.get("id", ""))
        for b in bills
        if isinstance(b, dict) and (b.get("bill_number") or b.get("document_number") or b.get("id"))
    ]

    doc_num = (
        finding.get("document_number")
        or (bills_nums[0] if bills_nums else "")
        or bill.get("bill_number")
        or bill.get("document_number")
        or invoice.get("invoice_number")
        or invoice.get("document_number")
        or str(finding.get("document_id") or "")
        or finding.get("key", "")[:8]
    )

    supplier = (
        finding.get("supplier")
        or finding.get("customer")
        or bill.get("vendor_name")
        or bill.get("supplier")
        or invoice.get("customer_name")
        or "Unknown"
    )

    amount = finding.get("amount") or bill.get("total") or invoice.get("total")
    try:
        amount_str = f"AED {float(amount):,.2f}" if amount is not None else ""
    except (ValueError, TypeError):
        amount_str = ""

    # Optional model refinement first if call_model provided
    if call_model:
        try:
            prompt = (
                f"Turn this finding into one clear, direct sentence for a financial audit card: "
                f"Rule: {rule}, Supplier: {supplier}, Document: {doc_num}, Amount: {amount_str}."
            )
            res = call_model(prompt=prompt, purpose="bot_finding", max_tokens=60)
            if isinstance(res, str) and res.strip():
                return res.strip()
        except Exception:
            pass

    if rule in ("duplicate_bill", "same_number", "same_total_near_date", "similar_vendor_same_day"):
        if len(bills_nums) >= 2:
            return f"Duplicate bill detected between {bills_nums[0]} and {bills_nums[1]} ({supplier})."
        elif doc_num:
            return f"Bill #{doc_num} from {supplier} has a potential duplicate record ({amount_str})."
        return "Duplicate bill detected in records."

    if rule in ("vat_rate_wrong", "vat_rate_not_standard", "vat_wrong_sign", "vat_without_amount"):
        return f"Document #{doc_num} from {supplier} has an unexpected VAT rate ({amount_str})."

    if rule in ("bill_missing_details", "missing_supplier", "missing_number", "future_date"):
        missing = ", ".join(finding.get("missing_fields") or ["key details"])
        return f"Document #{doc_num} is missing {missing}."

    if rule in ("unusual_bill", "large_for_supplier", "large_one_off_supplier"):
        return f"Bill #{doc_num} from {supplier} ({amount_str}) is unusually large or unfamiliar."

    if rule in ("sales_vat_wrong", "vat_zero"):
        return f"Sales invoice #{doc_num} has incorrect or zero output VAT ({amount_str})."

    return f"VAT anomaly detected on document #{doc_num} ({rule})."


def handle_document_event(
    event: str,
    org_id: int,
    document_ids: Sequence[int],
    *,
    now: Optional[dt.datetime] = None,
    call_model: Optional[Any] = None,
) -> Dict[str, Any]:
    """Process a document lifecycle event ('bill.saved', 'invoice.saved') for all active bots."""
    if event not in ("bill.saved", "invoice.saved"):
        raise ValueError(f"Unsupported event type: {event}")

    doc_ids_set = {int(did) for did in document_ids if did is not None}
    if not doc_ids_set:
        return {
            "status": "ok",
            "event": event,
            "org_id": org_id,
            "bots_checked": 0,
            "findings_matched": 0,
            "new_findings": 0,
            "quiet_hours": False,
            "details": [],
        }

    side = "purchases" if event == "bill.saved" else "sales"
    active_bots = store.list_active_bots_for_org(org_id)
    in_quiet = is_in_quiet_hours(now)

    total_matched = 0
    total_new = 0
    bot_details: List[Dict[str, Any]] = []

    for bot in active_bots:
        bot_id = bot["id"]
        owner_id = bot["owner_user_id"]

        # 1. Dynamic permission verification
        allowed = get_user_allowed_orgs(owner_id)
        if org_id not in allowed:
            logger.warning("Bot %s owner %s no longer has access to org %s", bot_id, owner_id, org_id)
            continue

        # 2. Check if bot has watches for this side
        draft = BotDraft(**bot["draft"])
        calls = watches.review_calls(draft.watches, draft.sensitivity)
        side_calls = [c for c in calls if c.get("side") == side]
        if not side_calls:
            continue

        # 3. Dismissed finding exclusions
        dismissed = store.get_findings(bot_id, state="dismissed")
        exclude_keys = {f["key"] for f in dismissed}

        # 4. Run review_vat for that org and side
        matched_items: List[Dict[str, Any]] = []
        for call_params in side_calls:
            try:
                rev = vat_review_tool.run(
                    {
                        **call_params,
                        "period": {"preset": "this_quarter"},
                        "limit": 50,
                    },
                    organization_ids=[org_id],
                    subject=f"bot-monitor:{bot_id}",
                    deadline=time.monotonic() + 15.0,
                    exclude=exclude_keys,
                    unlimited=True,  # the saved document may be past the first 50 problems
                )
            except Exception as ex:
                logger.warning("Monitor review failed for bot %s: %s", bot_id, ex)
                continue

            # Check duplicate groups containing document_ids
            for g in rev.get("groups") or []:
                group_docs = {
                    int(b["document_id"] if b.get("document_id") is not None else b["id"])
                    for b in g.get("bills", [])
                    if b.get("document_id") is not None or b.get("id") is not None
                }
                if group_docs.intersection(doc_ids_set):
                    rule = g.get("rule", "duplicate")
                    w = watches.watch_for_rule(rule, side=side)
                    first_b = (g.get("bills") or [{}])[0]
                    matched_items.append({
                        "key": g.get("key") or g.get("item_key"),
                        "org_id": org_id,
                        "rule": rule,
                        "watch_id": w.id if w else None,
                        "watch_label": w.label if w else None,
                        "amount": float(g.get("extra_amount") or g.get("amount") or 0.0),
                        "state": "new",
                        "why": g.get("why") or g.get("reason", ""),
                        "supplier": first_b.get("vendor_name") or first_b.get("supplier"),
                        "document_number": first_b.get("document_number") or first_b.get("bill_number"),
                    })

            # Check single anomaly findings containing document_ids
            for f in rev.get("findings") or []:
                bill = f.get("bill") or f.get("invoice") or {}
                bill_doc_id = bill.get("document_id") if bill.get("document_id") is not None else bill.get("id")
                if bill_doc_id is not None and int(bill_doc_id) in doc_ids_set:
                    rule = f.get("rule", "anomaly")
                    w = watches.watch_for_rule(rule, side=side)
                    matched_items.append({
                        "key": f.get("key") or f.get("item_key"),
                        "org_id": org_id,
                        "rule": rule,
                        "watch_id": w.id if w else None,
                        "watch_label": w.label if w else None,
                        "amount": float(bill.get("total") or bill.get("amount") or 0.0) if (bill.get("total") is not None or bill.get("amount") is not None) else None,
                        "state": "new",
                        "why": f.get("why") or f.get("reason", ""),
                        "supplier": bill.get("vendor_name") or bill.get("supplier"),
                        "document_number": bill.get("document_number") or bill.get("bill_number") or bill.get("invoice_number"),
                    })

        total_matched += len(matched_items)

        # 5. Diff against existing findings in DB
        existing = store.get_findings(bot_id)
        existing_keys = {ef["key"] for ef in existing}

        new_for_bot: List[Dict[str, Any]] = []
        for item in matched_items:
            if item["key"] not in existing_keys:
                item["explanation"] = generate_finding_sentence(item, item["rule"], call_model=call_model)
                new_for_bot.append(item)

        if new_for_bot:
            total_new += len(new_for_bot)
            # Record run record with trigger="event"
            run_rec = store.record_run(bot_id, trigger="event", status="running")
            store.upsert_findings(bot_id, run_rec["id"], new_for_bot)
            store.finish_run(
                run_id=run_rec["id"],
                status="ok",
                credits=15,  # Lightweight per-document check
                summary={
                    "headline": f"Detected {len(new_for_bot)} issue{'s' if len(new_for_bot) != 1 else ''} on saved document{'s' if len(doc_ids_set) > 1 else ''}.",
                    "event": event,
                    "documents_checked": len(doc_ids_set),
                    "new_findings": len(new_for_bot),
                    "in_quiet_hours": in_quiet,
                },
            )

        bot_details.append({
            "bot_id": bot_id,
            "name": bot["name"],
            "matched": len(matched_items),
            "new": len(new_for_bot),
            "in_quiet_hours": in_quiet,
        })

    return {
        "status": "ok",
        "event": event,
        "org_id": org_id,
        "bots_checked": len(active_bots),
        "findings_matched": total_matched,
        "new_findings": total_new,
        "quiet_hours": in_quiet,
        "details": bot_details,
    }
