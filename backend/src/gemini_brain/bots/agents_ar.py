"""agents_ar.py — The AR (accounts receivable) bot agent: invoices that just went overdue or are due soon, customers
with old overdue debt, large first invoices to new customers, invoices issued twice, unusual invoices and invoices
missing details (docs/BOT_AR_PLAN.md, tasks C1, C2, C5). Registered on import.

Read only, like every bot: it never sends a reminder, changes an invoice or records a payment.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Sequence

from gemini_brain.bots import agents, open_docs_scan
from gemini_brain.bots.ar_engine import DUPLICATE_RULES, ArEngine, _today
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.templates import Template
from gemini_brain.bots.watches import Watch

logger = logging.getLogger("gemini_brain.bots.agents_ar")

WATCHES = (
    Watch(id="invoice_overdue", label="An invoice just went overdue", side="sales", rules=("invoice_overdue",),
          default_on=True, desc="Open invoices up to 30 days past their due date, so you can chase them early.",
          bar="Just overdue", kind="state"),
    Watch(id="invoice_due_soon", label="An invoice is due soon", side="sales", rules=("invoice_due_soon",),
          default_on=True, desc="Open invoices due in the next few days, so you can send a reminder.", bar="Due soon",
          kind="state"),
    Watch(id="customer_overdue", label="A customer owes a lot of old money", side="sales", rules=("customer_overdue",),
          default_on=True, desc="One line per customer whose overdue total is above a set amount, with its aging.",
          bar="Owes old money", kind="state"),
    Watch(id="new_customer", label="A large first invoice to a new customer", side="sales",
          rules=("new_customer_large",), default_on=True,
          desc="The first invoice to a customer with no earlier invoices, above a set amount.", bar="New customer"),
    Watch(id="duplicate_invoice", label="An invoice looks issued twice", side="sales",
          rules=("same_number", "same_total_near_date", "similar_vendor_same_day"), default_on=True,
          desc="Same number, or same total to the same customer a few days apart, so it could be billed twice.",
          bar="Issued twice"),
    Watch(id="unusual_invoice", label="An invoice far above the customer's usual amount", side="sales",
          rules=("large_for_supplier", "large_one_off_supplier"), default_on=False,
          desc="Compared with that customer's past invoices.", bar="Unusually large"),
    Watch(id="invoice_missing_details",
          label="An invoice is missing its customer or number, or is dated in the future", side="sales",
          rules=("missing_supplier", "missing_number", "future_date"), default_on=False,
          desc="Invoices that are hard to match to a payment.", bar="Missing details"),
)

_DEFAULT_IDS = [w.id for w in WATCHES if w.default_on]

TEMPLATES = (
    Template(
        id="ar_weekly_collections", title="Weekly collections check",
        summary="Every Monday: who is overdue, what falls due this week, and invoices issued twice.",
        draft=BotDraft(agent="ar", name="Collections Watch",
                       schedules=[Schedule(kind="weekly", day="mon", time="08:00")], watches=_DEFAULT_IDS,
                       deliver=["inbox", "bell"], source="template:ar_weekly_collections"),
        tag="Most popular", runs="Mondays, 08:00",
    ),
    Template(
        id="ar_reminder_prep", title="Reminder prep",
        summary="Every morning: invoices due in the next 7 days and any that just went overdue.",
        draft=BotDraft(agent="ar", name="Reminder Prep", schedules=[Schedule(kind="daily", time="09:00")],
                       watches=["invoice_due_soon", "invoice_overdue"], sensitivity="more", deliver=["inbox"],
                       source="template:ar_reminder_prep"),
        tag="Every morning", runs="Every day, 09:00",
    ),
    Template(
        id="ar_month_end_aging", title="Month-end aging",
        summary="On the 1st: customers with old debt and the receivables total.",
        draft=BotDraft(agent="ar", name="Month-end Aging",
                       schedules=[Schedule(kind="monthly", day_of_month=1, time="08:00")],
                       watches=["customer_overdue"], deliver=["inbox", "bell"], source="template:ar_month_end_aging"),
        tag="Month end", runs="1st of each month, 08:00",
    ),
)

DRAFT_PROMPT = """You turn one sentence into the settings of an accounts receivable (customer invoices) bot for Accutax. Call make_bot_draft once.
Fill only what the sentence says or clearly means. Leave the rest out: code fills defaults.
"every Monday" is a weekly schedule on mon. "every morning", "daily", "every day" is a daily schedule.
"on the 1st of each month", "at month end" is a monthly schedule on day 1. Never use before_deadline or after_period_end for this bot.
A time like "9am" is "09:00". No time given: leave time out.
"email me" adds email to deliver. "alert me", "notify me right away" adds bell.
Pick watches only from the list. "overdue", "late", "unpaid", "who hasn't paid" is invoice_overdue and customer_overdue.
"due this week", "due soon", "send reminders" is invoice_due_soon. "aging", "old debt", "who owes the most" is
customer_overdue. "new customer", "credit risk" is new_customer. "duplicates", "billed twice", "issued twice" is
duplicate_invoice. "unusual", "much bigger than usual" is unusual_invoice. "missing details" is invoice_missing_details.
Organization names the user wrote go in org_names, exactly as written. Never invent an organization.
Anything you are not sure about goes in unclear as a short question. Never ask more than 3.
Never put figures, amounts or dates of transactions in any field.
Today's date is {today} in time zone {tz}."""

RULE_TITLES = {
    "invoice_overdue": "Invoice just went overdue",
    "invoice_due_soon": "Invoice is due soon",
    "customer_overdue": "Customer owes old money",
    "new_customer_large": "Large invoice to a new customer",
    "same_number": "Invoice may be issued twice",
    "same_total_near_date": "Invoice may be issued twice",
    "similar_vendor_same_day": "Invoice may be issued twice",
    "missing_supplier": "Invoice with no customer",
    "missing_number": "Invoice with no number",
    "future_date": "Invoice dated in the future",
    "large_for_supplier": "Far above the customer's usual invoice",
    "large_one_off_supplier": "A customer's only invoice, and a large one",
    "round_amount": "Round amount",
}

COPY: Dict[str, Any] = {
    "label": "AR agent",
    "default_name": "Collections Watch",
    "create_hint": "Every Monday at 8am tell me which customers are overdue and what is due this week",
    "examples": [
        "Every Monday, tell me which customers are overdue and what is due this week",
        "Every morning, tell me about invoices that just went overdue",
        "Warn me about a large invoice to a new customer",
    ],
    "tip": "under any receivables answer in chat, press Make this a bot to repeat it on a schedule.",
    "stake_label": "Overdue to collect",
    "stake_note": "What customers owe past the due date, from the receivables view.",
    "problem_word": "problem",
    "readonly_rule": "Send reminders, change invoices or record payments",
    "notes_placeholder": "Arjun always pays at the end of the quarter; don't flag his invoices until they are 60 days late",
    "watch_short": {
        "invoice_overdue": "invoices just overdue",
        "invoice_due_soon": "invoices due soon",
        "customer_overdue": "customers with old debt",
        "new_customer": "new customers",
        "duplicate_invoice": "duplicates",
        "unusual_invoice": "unusual amounts",
        "invoice_missing_details": "missing details",
    },
    "rule_titles": RULE_TITLES,
    "sales_rule_titles": RULE_TITLES,
    "dismiss_labels": {"sales": {"invoice_overdue": "I'll chase it", "invoice_due_soon": "I'll chase it",
                                 "customer_overdue": "I'll chase it"}},
    "side_titles": {"sales": "Invoices you sent"},
    "fixed_text": "is paid",
    "fixed_party_text": "has nothing overdue now",
}

COLUMNS = (
    {"key": "number", "label": "Invoice"},
    {"key": "date", "label": "Date"},
    {"key": "due_date", "label": "Due"},
    {"key": "party", "label": "Customer"},
    {"key": "total", "label": "Amount", "num": True},
    {"key": "outstanding", "label": "Outstanding", "num": True},
)


def summary_cards(bots: Sequence[Dict[str, Any]], findings: Sequence[Dict[str, Any]], org_ids: Sequence[int],
                  subject: str) -> List[Dict[str, Any]]:
    """The AR cards on the Bots home: open problems, and what is overdue and due in 7 days for the organizations the
    bots cover (else the user's first)."""
    open_total = new_total = duplicates = 0
    for f in findings:
        if f["state"] not in ("new", "seen"):
            continue
        open_total += 1
        new_total += f["state"] == "new"
        duplicates += f["rule"] in DUPLICATE_RULES
    orgs = list(dict.fromkeys(o for b in bots for o in b["org_ids"])) or list(org_ids)[:1]
    receivables = None
    if orgs:
        try:
            receivables = open_docs_scan.position("receivables", orgs, _today(), subject=subject,
                                                  deadline=time.monotonic() + 10)
        except Exception:
            logger.warning("Receivables position for the bots home failed", exc_info=True)
    return [
        {"kind": "open_problems", "total": open_total, "new": new_total, "duplicates": duplicates,
         "other": open_total - duplicates},
        {"kind": "receivables", "position": receivables},
    ]


AGENT = agents.BotAgent(
    key="ar",
    name="Accounts receivable",
    watches=WATCHES,
    templates=TEMPLATES,
    schedule_kinds=("daily", "weekly", "monthly"),
    engine=ArEngine(),
    #: As VAT and AP for now; scheduled AR runs call no model (docs/BOT_AGENTS_AND_AP_PLAN.md, decision 4).
    credits_per_run=90,
    copy=COPY,
    columns=COLUMNS,
    draft_prompt=DRAFT_PROMPT,
    fallback_template="ar_weekly_collections",
    summary_cards=summary_cards,
)

agents.register(AGENT)
