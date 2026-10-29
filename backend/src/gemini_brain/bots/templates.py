"""templates.py — Ready-made bot drafts for one-click creation.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.watches import all_watches, default_ids


@dataclass(frozen=True)
class Template:
    id: str
    title: str          # card title
    summary: str        # one line under the title
    draft: BotDraft     # org_ids left empty: the server fills the user's current org


_TEMPLATES: List[Template] = [
    Template(
        id="vat_weekly_digest",
        title="Weekly VAT digest",
        summary="Every Monday: new VAT problems and your return position.",
        draft=BotDraft(
            name="VAT Watch",
            schedules=[Schedule(kind="weekly", day="mon", time="08:00")],
            watches=default_ids(),
            deliver=["inbox"],
            source="template:vat_weekly_digest",
        ),
    ),
    Template(
        id="vat_return_reminder",
        title="VAT return reminder",
        summary="14, 7 and 1 days before each return: the position and what to fix.",
        draft=BotDraft(
            name="Return Reminder",
            schedules=[Schedule(kind="before_deadline", days_before=[14, 7, 1], time="09:00")],
            watches=default_ids(),
            deliver=["inbox", "bell"],
            source="template:vat_return_reminder",
        ),
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
            watches=[w.id for w in all_watches()],
            deliver=["inbox", "bell"],
            source="template:vat_full_watch",
        ),
    ),
]

_TEMPLATE_MAP: Dict[str, Template] = {t.id: t for t in _TEMPLATES}


def all_templates(agent: str = "vat") -> List[Template]:
    """Return all ready-made templates for the agent (only 'vat' supported in phase 1)."""
    if agent != "vat":
        return []
    return list(_TEMPLATES)


def get(template_id: str) -> Template:
    """Get a template by id. Raises KeyError if unknown."""
    if template_id not in _TEMPLATE_MAP:
        raise KeyError(f"Unknown template id: {template_id!r}")
    return _TEMPLATE_MAP[template_id]
