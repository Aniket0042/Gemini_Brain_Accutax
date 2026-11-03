"""agents_vat.py — The VAT bot agent: its checks, templates, engine (review_vat and the VAT return) and words
(docs/BOT_AGENTS_AND_AP_PLAN.md, tasks A2 and A3). Registered on import.

Behaviour is what the VAT bot did before bots became agent-agnostic; the code moved here from watches, templates,
run, preview and inbox.
"""
from __future__ import annotations

import logging
import time
from typing import AbstractSet, Any, Dict, List, Optional, Sequence

from gemini_brain.bots import agents
from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.templates import Template
from gemini_brain.bots.watches import Watch
from gemini_brain.config.settings import settings
from gemini_brain.semantic import periods as report_periods
from gemini_brain.vat_return import periods as tax_periods
from gemini_brain.vat_return import tool as vat_return_tool
from gemini_brain.vat_review import tool as vat_review_tool

logger = logging.getLogger("gemini_brain.bots.agents_vat")

#: UAE standard rate, for "VAT missing" on a sales invoice with no output VAT.
STANDARD_RATE = 0.05

WATCHES = (
    Watch(
        id="duplicate_bill",
        label="A bill looks recorded twice",
        side="purchases",
        rules=("same_number", "same_total_near_date", "similar_vendor_same_day"),
        default_on=True,
        desc="Same number, or same total from the same supplier a few days apart.",
        bar="Recorded twice",
    ),
    Watch(
        id="vat_rate_wrong",
        label="VAT on a bill is not 0% or 5%",
        side="purchases",
        rules=("vat_rate_not_standard", "vat_wrong_sign", "vat_without_amount"),
        default_on=True,
        desc="Wrong rate, negative VAT, or VAT with no amount.",
        bar="VAT not 0% or 5%",
    ),
    Watch(
        id="bill_missing_details",
        label="A bill is missing its supplier or number, or is dated in the future",
        side="purchases",
        rules=("missing_supplier", "missing_number", "future_date"),
        default_on=False,
        desc="These bills can't support a VAT claim.",
        bar="Missing details",
    ),
    Watch(
        id="unusual_bill",
        label="A bill far above the supplier's usual amount",
        side="purchases",
        rules=("large_for_supplier", "large_one_off_supplier"),
        default_on=False,
        desc="Compared with that supplier's past bills.",
        bar="Unusually large",
    ),
    Watch(
        id="sales_vat_wrong",
        label="A sales invoice with wrong or no output VAT",
        side="sales",
        rules=("vat_rate_not_standard", "vat_zero", "vat_wrong_sign"),
        default_on=True,
        desc="Zero VAT where 5% is expected, or a rate that isn't 0% or 5%.",
        bar="Sales VAT wrong",
    ),
)

DUPLICATE_RULES = frozenset(WATCHES[0].rules)
_DEFAULT_IDS = [w.id for w in WATCHES if w.default_on]

TEMPLATES = (
    Template(
        id="vat_weekly_digest",
        title="Weekly VAT digest",
        summary="Every Monday: new VAT problems and your return position.",
        draft=BotDraft(
            name="VAT Watch",
            schedules=[Schedule(kind="weekly", day="mon", time="08:00")],
            watches=_DEFAULT_IDS,
            deliver=["inbox"],
            source="template:vat_weekly_digest",
        ),
        tag="Most popular",
        runs="Mondays, 08:00",
    ),
    Template(
        id="vat_return_reminder",
        title="VAT return reminder",
        summary="14, 7 and 1 days before each return: the position and what to fix.",
        draft=BotDraft(
            name="Return Reminder",
            schedules=[Schedule(kind="before_deadline", days_before=[14, 7, 1], time="09:00")],
            watches=_DEFAULT_IDS,
            deliver=["inbox", "bell"],
            source="template:vat_return_reminder",
        ),
        tag="Never miss a deadline",
        runs="Before each return, 09:00",
    ),
    Template(
        id="vat_full_watch",
        title="Full VAT watch",
        summary="Weekly digest plus return reminders, every check on.",
        draft=BotDraft(
            name="VAT Guard",
            schedules=[
                Schedule(kind="weekly", day="mon", time="08:00"),
                Schedule(kind="before_deadline", days_before=[14, 7, 1], time="09:00"),
            ],
            watches=[w.id for w in WATCHES],
            deliver=["inbox", "bell"],
            source="template:vat_full_watch",
        ),
        tag="Everything on",
        runs="Mondays + before returns",
    ),
)

DRAFT_PROMPT = """You turn one sentence into the settings of a VAT bot for Accutax. Call make_bot_draft once.
Fill only what the sentence says or clearly means. Leave the rest out: code fills defaults.
"every Monday" is a weekly schedule on mon. "before the return", "before VAT is due" is before_deadline.
"after the quarter ends" is after_period_end. A time like "9am" is "09:00". No time given: leave time out.
"email me" adds email to deliver. "alert me", "notify me right away" adds bell.
Pick watches only from the list. "duplicates" is duplicate_bill. "wrong VAT", "VAT not 5%" is vat_rate_wrong.
"sales", "invoices I send" is sales_vat_wrong.
Organization names the user wrote go in org_names, exactly as written. Never invent an organization.
Anything you are not sure about goes in unclear as a short question. Never ask more than 3.
Never put figures, amounts or dates of transactions in any field.
Today's date is {today} in time zone {tz}."""

RULE_TITLES = {
    "same_number": "Bill may be recorded twice",
    "same_total_near_date": "Bill may be recorded twice",
    "similar_vendor_same_day": "Bill may be recorded twice",
    "vat_rate_not_standard": "VAT is not 0% or 5%",
    "vat_wrong_sign": "VAT and amount have opposite signs",
    "vat_without_amount": "VAT with no amount",
    "missing_supplier": "Bill with no supplier",
    "missing_number": "Bill with no number",
    "future_date": "Bill dated in the future",
    "large_for_supplier": "Far above the supplier's usual bill",
    "large_one_off_supplier": "A supplier's only bill, and a large one",
    "vat_zero": "No VAT on an amount",
    "round_amount": "Round amount",
    "supplier_trn_invalid": "Supplier's TRN missing or wrong",
}

COPY: Dict[str, Any] = {
    "label": "VAT agent",
    "default_name": "VAT Watch",
    "create_hint": "Every Monday at 9am check my bills for duplicates and wrong VAT, and alert me",
    "examples": [
        "Every Monday at 9am, check my bills for duplicates",
        "Remind me 7 days before each VAT return",
        "Check sales invoices for wrong VAT on the 1st of each month",
    ],
    "tip": "under any VAT answer in chat, press Make this a bot to repeat it on a schedule.",
    "stake_label": "At stake this quarter",
    "stake_note": ("VAT that could be wrong on your return if these stay unfixed: copies claimed twice, missing "
                   "output VAT and wrong rates."),
    "problem_word": "VAT problem",
    "readonly_rule": "Change bills, invoices or file a VAT return",
    "notes_placeholder": "Rent from Al Noor Properties is billed in two parts; don't flag those as duplicates",
    "watch_short": {
        "duplicate_bill": "duplicates",
        "vat_rate_wrong": "wrong VAT rates",
        "bill_missing_details": "missing details",
        "unusual_bill": "unusual amounts",
        "sales_vat_wrong": "sales VAT",
    },
    "rule_titles": RULE_TITLES,
    "sales_rule_titles": {
        "vat_zero": "Sales invoice with no VAT",
        "vat_rate_not_standard": "Sales invoice VAT is not 0% or 5%",
        "vat_wrong_sign": "Sales invoice VAT and amount have opposite signs",
        "vat_without_amount": "Sales invoice with VAT but no amount",
    },
    #: "Not a problem" under another name, per side and rule: a sales invoice with no VAT is usually zero-rated.
    "dismiss_labels": {"sales": {"vat_zero": "It's zero-rated"}},
    "side_titles": {"purchases": "Bills you received", "sales": "Invoices you sent"},
}

COLUMNS = (
    {"key": "number", "label": "Bill", "sales_label": "Invoice"},
    {"key": "date", "label": "Date"},
    {"key": "party", "label": "Supplier", "sales_label": "Customer"},
    {"key": "amount", "label": "Amount", "num": True},
    {"key": "vat", "label": "VAT", "num": True},
)


def _num(v: Any) -> Optional[float]:
    try:
        return None if v in (None, "") else float(v)
    except (TypeError, ValueError):
        return None


def vat_at_stake(rule: str, detail: Dict[str, Any]) -> float:
    """VAT the finding could put wrong on the return, from its bills:
    a duplicate: the VAT of every copy after the first (claimed twice);
    a sales invoice with no VAT: 5% of its amount (missing output VAT);
    a rate that is not 0% or 5%: the gap between its VAT and 5% of the amount;
    VAT with no amount, or opposite signs: the VAT itself. Other checks: 0 (they are not VAT amounts)."""
    bills = detail.get("bills") or []
    if not bills:
        return 0.0
    if rule in DUPLICATE_RULES:
        return round(sum(abs(_num(b.get("vat")) or 0.0) for b in bills[1:]), 2)
    b = bills[0]
    amount, vat = _num(b.get("amount")) or 0.0, _num(b.get("vat")) or 0.0
    if rule == "vat_zero":
        return round(abs(amount) * STANDARD_RATE, 2)
    if rule == "vat_rate_not_standard":
        return round(abs(vat - amount * STANDARD_RATE), 2)
    if rule in ("vat_without_amount", "vat_wrong_sign"):
        return round(abs(vat), 2)
    return 0.0


def finding_detail(item: Dict[str, Any], side: str) -> Dict[str, Any]:
    """What the inbox card shows for a duplicate group or a finding: the side, the sentence, how sure the check is,
    and its bills (number, date, supplier or customer, amount before VAT, VAT, Accutax id). From review_vat's own
    fields only; at most 10 bills."""
    bills = item.get("bills") or ([item["bill"]] if item.get("bill") else [])
    return {
        "side": side,
        "why": item.get("why") or "",
        "level": item.get("confidence") or item.get("severity") or item.get("level"),
        "currency": item.get("currency"),
        "bills": [{"id": b.get("document_id"), "number": b.get("document_number"), "date": b.get("document_date"),
                   "party": b.get("vendor_name"), "amount": b.get("net_purchases"), "vat": b.get("input_vat"),
                   # Payables fields (the AP bot), only when the item has them.
                   **{out: b[src] for src, out in _PAYABLE_FIELDS if b.get(src) is not None}}
                  for b in bills[:10]],
    }


#: Bill fields an AP finding carries beyond review_vat's, and their names on the inbox card.
_PAYABLE_FIELDS = (("due_date", "due_date"), ("outstanding", "outstanding"), ("days_overdue", "days_overdue"),
                   ("total", "total"))


def due_period(draft: BotDraft, subject: str) -> Optional[Dict[str, str]]:
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


def return_position(returns: Any, reviews: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The VAT return that is due next, from the run's vat_return check: period, due date, output VAT, input VAT and
    net. The first organization's (a bot over several shows one, and says how many). None when the check did not run."""
    orgs = returns if isinstance(returns, list) else []
    if not orgs:
        return None
    o = orgs[0]
    currency = next((org.get("currency") for rev in reviews or [] for org in rev.get("organizations") or []
                     if org.get("organization_id") == o.get("organization_id") and org.get("currency")), None)
    return {"kind": "vat_return", "organization_id": o.get("organization_id"), "organizations": len(orgs),
            "period": o.get("period"), "due_date": o.get("due_date"), "output_vat": o.get("output_vat"),
            "input_vat": o.get("input_vat"), "net_vat_payable": o.get("net_vat_payable"),
            "position": o.get("position"), "currency": currency or "AED"}


class VatEngine:
    """review_vat for the checks, the VAT return for the position."""

    def calls(self, watch_ids: Sequence[str], sensitivity: str = "balanced") -> List[Dict[str, Any]]:
        """One review_vat call per side ('purchases', 'sales') that has active rules."""
        by_id = {w.id: w for w in WATCHES}
        purchases_rules: set[str] = set()
        sales_rules: set[str] = set()
        for wid in watch_ids:
            if wid not in by_id:
                raise KeyError(f"Unknown watch id: {wid!r}")
            w = by_id[wid]
            if w.side == "purchases":
                purchases_rules.update(w.rules)
            elif w.side == "sales":
                sales_rules.update(w.rules)

        if sensitivity == "fewer":
            purchases_rules.discard("similar_vendor_same_day")
            purchases_rules.discard("large_one_off_supplier")
        elif sensitivity == "more":
            if purchases_rules:
                purchases_rules.add("round_amount")

        calls: List[Dict[str, Any]] = []
        if purchases_rules:
            calls.append({"mode": "all", "side": "purchases", "rules": sorted(purchases_rules)})
        if sales_rules:
            calls.append({"mode": "all", "side": "sales", "rules": sorted(sales_rules)})
        return calls

    def run(self, call: Dict[str, Any], *, org_ids: Sequence[int], period: Optional[Dict[str, str]], subject: str,
            deadline: float, exclude: AbstractSet[str] = frozenset(), unlimited: bool = False) -> Dict[str, Any]:
        return vat_review_tool.run({**call, "period": period or {"preset": "this_quarter"}, "limit": 50},
                                   organization_ids=list(org_ids), subject=subject, deadline=deadline,
                                   exclude=exclude, unlimited=unlimited)

    def period(self, draft: BotDraft, subject: str) -> Optional[Dict[str, str]]:
        return due_period(draft, subject)

    def position(self, org_ids: Sequence[int], subject: str, deadline: float) -> Any:
        """The VAT return due next, per organization (vat_return's own output); {} when the check fails."""
        try:
            out = vat_return_tool.run({"which": "next_due"}, organization_ids=list(org_ids), subject=subject,
                                      deadline=deadline)
            return out.get("organizations") or out
        except Exception:
            return {}

    def summarize_position(self, raw: Any, reviews: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        return return_position(raw, reviews)

    def stake(self, rule: str, detail: Dict[str, Any]) -> float:
        return vat_at_stake(rule, detail)

    def detail(self, item: Dict[str, Any], side: str) -> Dict[str, Any]:
        return finding_detail(item, side)


def summary_cards(bots: Sequence[Dict[str, Any]], findings: Sequence[Dict[str, Any]], org_ids: Sequence[int],
                  subject: str) -> List[Dict[str, Any]]:
    """The VAT cards on the Bots home: open problems (new, duplicates, other) and the next VAT return of the
    organization the bots cover first (else the user's first)."""
    open_total = new_total = duplicates = 0
    for f in findings:
        if f["state"] not in ("new", "seen"):
            continue
        open_total += 1
        new_total += f["state"] == "new"
        duplicates += f["rule"] in DUPLICATE_RULES

    orgs = [o for b in bots for o in b["org_ids"]] or list(org_ids)[:1]
    next_return = None
    if orgs:
        try:
            res = vat_return_tool.run({"which": "next_due"}, organization_ids=[orgs[0]], subject=subject,
                                      deadline=time.monotonic() + 10)
            o = (res.get("organizations") or [None])[0]
            if o:
                next_return = {"organization_id": o["organization_id"], "period": o["period"],
                               "due_date": o["due_date"], "days_to_due": o["days_to_due"]}
        except Exception:
            logger.warning("Next VAT return for the bots home failed", exc_info=True)

    return [
        {"kind": "open_problems", "total": open_total, "new": new_total, "duplicates": duplicates,
         "other": open_total - duplicates},
        {"kind": "vat_return", "next_return": next_return},
    ]


AGENT = agents.BotAgent(
    key="vat",
    name="VAT",
    watches=WATCHES,
    templates=TEMPLATES,
    schedule_kinds=("weekly", "monthly", "before_deadline", "after_period_end"),
    engine=VatEngine(),
    #: Measured on the VM (docs/ACCUTAX_BOTS_PLAN.md): one VAT review run with the model's summary.
    credits_per_run=90,
    copy=COPY,
    columns=COLUMNS,
    draft_prompt=DRAFT_PROMPT,
    fallback_template="vat_weekly_digest",
    summary_cards=summary_cards,
)

agents.register(AGENT)
