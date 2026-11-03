"""agents_ap.py — The AP (accounts payable) bot agent: bills recorded twice, overdue and due soon, large bills from new
suppliers, unusual bills and bills missing details (docs/BOT_AGENTS_AND_AP_PLAN.md, tasks B1, B2, B4, B5).
Registered on import.

Read only, like every bot: it never pays, changes or deletes a bill.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Sequence

from gemini_brain.bots import agents, payables_scan
from gemini_brain.bots.ap_engine import DUPLICATE_RULES, ApEngine, _today
from gemini_brain.bots.agents_vat import RULE_TITLES as VAT_RULE_TITLES
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.templates import Template
from gemini_brain.bots.watches import Watch

logger = logging.getLogger("gemini_brain.bots.agents_ap")

WATCHES = (
    Watch(id="duplicate_bill", label="A bill looks recorded twice", side="purchases",
          rules=("same_number", "same_total_near_date", "similar_vendor_same_day"), default_on=True,
          desc="Same number, or same total from the same supplier a few days apart, so it could be paid twice.",
          bar="Recorded twice"),
    Watch(id="bill_overdue", label="A bill is past its due date", side="purchases", rules=("bill_overdue",),
          default_on=True, desc="Open bills whose due date has passed.", bar="Overdue", kind="state"),
    Watch(id="bill_due_soon", label="A bill is due soon", side="purchases", rules=("bill_due_soon",),
          default_on=True, desc="Open bills due in the next few days, so you can plan the payment.", bar="Due soon",
          kind="state"),
    Watch(id="new_supplier", label="A large bill from a new supplier", side="purchases", rules=("new_supplier_large",),
          default_on=True, desc="The first bill from a supplier with no earlier bills, above a set amount.",
          bar="New supplier"),
    Watch(id="unusual_bill", label="A bill far above the supplier's usual amount", side="purchases",
          rules=("large_for_supplier", "large_one_off_supplier"), default_on=True,
          desc="Compared with that supplier's past bills.", bar="Unusually large"),
    Watch(id="bill_missing_details", label="A bill is missing its supplier or number, or is dated in the future",
          side="purchases", rules=("missing_supplier", "missing_number", "future_date"), default_on=False,
          desc="Bills that are hard to match to a payment.", bar="Missing details"),
)

_DEFAULT_IDS = [w.id for w in WATCHES if w.default_on]

TEMPLATES = (
    Template(
        id="ap_weekly_check", title="Weekly payables check",
        summary="Every Monday: duplicates, bills due this week and what is overdue.",
        draft=BotDraft(agent="ap", name="Bill Guard", schedules=[Schedule(kind="weekly", day="mon", time="08:00")],
                       watches=_DEFAULT_IDS, deliver=["inbox", "bell"], source="template:ap_weekly_check"),
        tag="Most popular", runs="Mondays, 08:00",
    ),
    Template(
        id="ap_pay_run_prep", title="Pay-run prep",
        summary="Every Thursday: the bills due next week and anything overdue.",
        draft=BotDraft(agent="ap", name="Pay-run Prep", schedules=[Schedule(kind="weekly", day="thu", time="09:00")],
                       watches=["bill_due_soon", "bill_overdue"], sensitivity="more", deliver=["inbox"],
                       source="template:ap_pay_run_prep"),
        tag="Plan payments", runs="Thursdays, 09:00",
    ),
    Template(
        id="ap_daily_watch", title="Daily payables watch",
        summary="Every morning: new overdue bills, duplicates and large bills from new suppliers.",
        draft=BotDraft(agent="ap", name="Daily Bill Guard", schedules=[Schedule(kind="daily", time="08:00")],
                       watches=[w.id for w in WATCHES if w.id != "bill_missing_details"], deliver=["inbox", "bell"],
                       source="template:ap_daily_watch"),
        tag="Every morning", runs="Every day, 08:00",
    ),
)

DRAFT_PROMPT = """You turn one sentence into the settings of an accounts payable (bills) bot for Accutax. Call make_bot_draft once.
Fill only what the sentence says or clearly means. Leave the rest out: code fills defaults.
"every Monday" is a weekly schedule on mon. "every morning", "daily", "every day" is a daily schedule.
"on the 1st of each month" is a monthly schedule. Never use before_deadline or after_period_end for this bot.
A time like "9am" is "09:00". No time given: leave time out.
"email me" adds email to deliver. "alert me", "notify me right away" adds bell.
Pick watches only from the list. "overdue", "late bills", "unpaid" is bill_overdue. "due this week", "due soon",
"coming due", "what to pay" is bill_due_soon. "duplicates", "paid twice" is duplicate_bill. "new supplier",
"new vendor" is new_supplier. "unusual", "much bigger than usual" is unusual_bill. "missing details" is
bill_missing_details.
Organization names the user wrote go in org_names, exactly as written. Never invent an organization.
Anything you are not sure about goes in unclear as a short question. Never ask more than 3.
Never put figures, amounts or dates of transactions in any field.
Today's date is {today} in time zone {tz}."""

COPY: Dict[str, Any] = {
    "label": "AP agent",
    "default_name": "Bill Guard",
    "create_hint": "Every Monday at 8am check my bills for duplicates and tell me what is due this week",
    "examples": [
        "Every Monday, check my bills for duplicates and tell me what is due this week",
        "Tell me every morning about bills that are overdue",
        "Warn me about a large bill from a new supplier",
    ],
    "tip": "under any payables answer in chat, press Make this a bot to repeat it on a schedule.",
    "stake_label": "Could be paid twice",
    "stake_note": "The total of the extra copies, if every group proves to be a duplicate.",
    "problem_word": "problem",
    "readonly_rule": "Pay, change or delete bills",
    "notes_placeholder": "Bills from Etisalat come monthly and are always paid by direct debit; don't flag them as overdue",
    "watch_short": {
        "duplicate_bill": "duplicates",
        "bill_overdue": "overdue bills",
        "bill_due_soon": "bills due soon",
        "new_supplier": "new suppliers",
        "unusual_bill": "unusual amounts",
        "bill_missing_details": "missing details",
    },
    "rule_titles": {
        **{r: VAT_RULE_TITLES[r] for r in ("same_number", "same_total_near_date", "similar_vendor_same_day",
                                            "missing_supplier", "missing_number", "future_date", "large_for_supplier",
                                            "large_one_off_supplier", "round_amount")},
        "bill_overdue": "Bill is overdue",
        "bill_due_soon": "Bill is due soon",
        "new_supplier_large": "Large bill from a new supplier",
    },
    "sales_rule_titles": {},
    "dismiss_labels": {"purchases": {"bill_overdue": "I'll handle it", "bill_due_soon": "I'll handle it"}},
    "side_titles": {"purchases": "Bills you received"},
    "fixed_text": "is no longer open",
}

COLUMNS = (
    {"key": "number", "label": "Bill"},
    {"key": "date", "label": "Date"},
    {"key": "due_date", "label": "Due"},
    {"key": "party", "label": "Supplier"},
    {"key": "total", "label": "Amount", "num": True},
    {"key": "outstanding", "label": "Outstanding", "num": True},
)


def summary_cards(bots: Sequence[Dict[str, Any]], findings: Sequence[Dict[str, Any]], org_ids: Sequence[int],
                  subject: str) -> List[Dict[str, Any]]:
    """The AP cards on the Bots home: open problems, and what is due in 7 days and overdue for the organizations the
    bots cover (else the user's first)."""
    open_total = new_total = duplicates = 0
    for f in findings:
        if f["state"] not in ("new", "seen"):
            continue
        open_total += 1
        new_total += f["state"] == "new"
        duplicates += f["rule"] in DUPLICATE_RULES
    orgs = list(dict.fromkeys(o for b in bots for o in b["org_ids"])) or list(org_ids)[:1]
    payables = None
    if orgs:
        try:
            payables = payables_scan.position(orgs, _today(), subject=subject, deadline=time.monotonic() + 10)
        except Exception:
            logger.warning("Payables position for the bots home failed", exc_info=True)
    return [
        {"kind": "open_problems", "total": open_total, "new": new_total, "duplicates": duplicates,
         "other": open_total - duplicates},
        {"kind": "payables", "position": payables},
    ]


AGENT = agents.BotAgent(
    key="ap",
    name="Accounts payable",
    watches=WATCHES,
    templates=TEMPLATES,
    schedule_kinds=("daily", "weekly", "monthly"),
    engine=ApEngine(),
    #: As VAT for now; scheduled AP runs call no model (docs/BOT_AGENTS_AND_AP_PLAN.md, decision 4).
    credits_per_run=90,
    copy=COPY,
    columns=COLUMNS,
    draft_prompt=DRAFT_PROMPT,
    fallback_template="ap_weekly_check",
    summary_cards=summary_cards,
)

agents.register(AGENT)
