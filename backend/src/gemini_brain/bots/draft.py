"""draft.py — Turn a natural language sentence into a validated BotDraft.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from gemini_brain.bots.schema import BotDraft, BotIssue, Schedule
from gemini_brain.bots.templates import get as get_template
from gemini_brain.bots.validate import validate
from gemini_brain.bots.watches import all_watches
from gemini_brain.config.settings import settings
from gemini_brain.pii.redactor import redact_pii
from gemini_brain.reasoning import bedrock_client
from gemini_brain.semantic import periods

_WATCH_IDS = [w.id for w in all_watches()]

MAKE_BOT_DRAFT_TOOL = {
    "toolSpec": {
        "name": "make_bot_draft",
        "description": "Fills the settings of a VAT bot from a user's sentence.",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name for the bot"},
                    "org_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Organization names the user mentioned, exactly as written",
                    },
                    "schedules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {
                                    "type": "string",
                                    "enum": ["weekly", "monthly", "before_deadline", "after_period_end"],
                                },
                                "day": {
                                    "type": "string",
                                    "enum": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
                                },
                                "day_of_month": {"type": "integer"},
                                "time": {"type": "string"},
                                "days_before": {"type": "array", "items": {"type": "integer"}},
                                "days_after": {"type": "integer"},
                            },
                            "required": ["kind"],
                        },
                    },
                    "watches": {
                        "type": "array",
                        "items": {"type": "string", "enum": _WATCH_IDS},
                    },
                    "sensitivity": {
                        "type": "string",
                        "enum": ["fewer", "balanced", "more"],
                    },
                    "deliver": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["inbox", "email", "bell"]},
                    },
                    "notes": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "unclear": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Short questions when something was unclear (max 3)",
                    },
                },
            }
        },
    }
}

_SYSTEM_PROMPT = """You turn one sentence into the settings of a VAT bot for Accutax. Call make_bot_draft once.
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


def from_sentence(
    text: str,
    *,
    allowed_orgs: Dict[int, str],
    current_org_ids: Sequence[int],
    call_model: Callable[..., Dict[str, Any]] = bedrock_client.converse_tools,
) -> Tuple[BotDraft, List[BotIssue]]:
    """Turn a natural language sentence into a validated BotDraft."""
    stripped = text.strip()
    if not stripped or len(text) > 500:
        raise ValueError("Text must be non-empty and at most 500 characters")

    # Step 2: Redact PII before passing to model
    masked_text, _ = redact_pii(text)

    today_str = periods.today_in(settings.report_timezone).isoformat()
    system = _SYSTEM_PROMPT.format(today=today_str, tz=settings.report_timezone)

    messages = [{"role": "user", "content": [{"text": masked_text}]}]

    resp = call_model(
        system_prompt=system,
        messages=messages,
        tools=[MAKE_BOT_DRAFT_TOOL],
        tool_choice="make_bot_draft",
        temperature=0.0,
        purpose="bot_draft",
    )

    # Step 3: Extract toolUse named make_bot_draft
    msg = (resp.get("output") or {}).get("message") or {}
    content = msg.get("content") or []
    tool_use = None
    for block in content:
        if isinstance(block, dict) and "toolUse" in block:
            tu = block["toolUse"]
            if tu.get("name") == "make_bot_draft":
                tool_use = tu
                break

    if not tool_use or not isinstance(tool_use.get("input"), dict):
        fallback_draft = get_template("vat_weekly_digest").draft.model_copy(deep=True)
        fallback_draft.source = "sentence"
        return validate(
            fallback_draft,
            allowed_org_ids=list(allowed_orgs.keys()),
            current_org_ids=current_org_ids,
        )

    data = tool_use["input"]
    extra_issues: List[BotIssue] = []
    questions: List[str] = []

    # Step 4: Map org_names to IDs using original unmasked text against allowed_orgs
    lookup = {name.strip().lower(): oid for oid, name in allowed_orgs.items() if name}
    resolved_org_ids: List[int] = []
    raw_org_names = data.get("org_names") or []
    for oname in raw_org_names:
        clean = str(oname).strip().lower()
        if clean in lookup:
            oid = lookup[clean]
            if oid not in resolved_org_ids:
                resolved_org_ids.append(oid)
        else:
            questions.append(f"Which organization is '{oname}'?")

    # Step 5: Build BotDraft field by field
    draft_kwargs: Dict[str, Any] = {
        "agent": "vat",
        "source": "sentence",
        "org_ids": resolved_org_ids,
    }

    if "name" in data and isinstance(data["name"], str) and data["name"].strip():
        draft_kwargs["name"] = data["name"][:60]

    if "watches" in data and isinstance(data["watches"], list):
        draft_kwargs["watches"] = [w for w in data["watches"] if isinstance(w, str)]

    if "sensitivity" in data and data["sensitivity"] in ("fewer", "balanced", "more"):
        draft_kwargs["sensitivity"] = data["sensitivity"]

    if "deliver" in data and isinstance(data["deliver"], list):
        draft_kwargs["deliver"] = [d for d in data["deliver"] if d in ("inbox", "email", "bell")]

    if "notes" in data and isinstance(data["notes"], list):
        draft_kwargs["notes"] = [str(n) for n in data["notes"]]

    schedules: List[Schedule] = []
    raw_schedules = data.get("schedules") or []
    if isinstance(raw_schedules, list):
        for raw_s in raw_schedules:
            if isinstance(raw_s, dict):
                try:
                    schedules.append(Schedule(**raw_s))
                except Exception as ex:
                    extra_issues.append(
                        BotIssue(
                            field="schedules",
                            message=f"Schedule ignored: {ex}",
                            fixed=True,
                        )
                    )
    draft_kwargs["schedules"] = schedules

    # Step 6: unclear questions
    unclear = data.get("unclear") or []
    if isinstance(unclear, list):
        for q in unclear[:3]:
            if q and str(q) not in questions:
                questions.append(str(q))

    draft_kwargs["questions"] = questions[:3]

    draft = BotDraft(**draft_kwargs)

    # Step 7: Run validate
    validated_draft, issues = validate(
        draft,
        allowed_org_ids=list(allowed_orgs.keys()),
        current_org_ids=current_org_ids,
    )

    return validated_draft, extra_issues + issues
