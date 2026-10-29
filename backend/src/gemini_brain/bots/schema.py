"""schema.py — Pydantic models for bot drafts, schedules, rules and issues.

Phase 1 of docs/VAT_BOT_IMPLEMENTATION_PLAN.md.
"""
from __future__ import annotations

import re
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

Weekday = Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
Delivery = Literal["inbox", "email", "bell"]
RuleLevel = Literal["allow", "ask", "never"]
Sensitivity = Literal["fewer", "balanced", "more"]

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class Schedule(BaseModel):
    """One time the bot runs. Exactly the fields for its kind are used; the others stay None."""
    kind: Literal["weekly", "monthly", "before_deadline", "after_period_end"]
    day: Optional[Weekday] = None                 # weekly
    day_of_month: Optional[int] = Field(None, ge=1, le=28)   # monthly (max 28 so every month has it)
    time: str = "08:00"                           # "HH:MM", 24 hours, in settings.report_timezone
    days_before: List[int] = Field(default_factory=list)     # before_deadline, e.g. [14, 7, 1]
    days_after: Optional[int] = Field(None, ge=0, le=27)     # after_period_end

    @field_validator("time")
    @classmethod
    def validate_time(cls, v: str) -> str:
        if not isinstance(v, str) or not _TIME_RE.match(v):
            raise ValueError(f"time must be in HH:MM 24-hour format, got {v!r}")
        return v


class Rules(BaseModel):
    run_review: RuleLevel = "allow"
    export_findings: RuleLevel = "allow"
    email_outside: RuleLevel = "ask"
    change_records: Literal["never"] = "never"    # fixed: a VAT bot never changes Accutax data


class BotDraft(BaseModel):
    agent: Literal["vat"] = "vat"
    name: str = Field("VAT Watch", min_length=1, max_length=60)
    org_ids: List[int] = Field(default_factory=list, max_length=10)
    schedules: List[Schedule] = Field(default_factory=list, max_length=6)
    watches: List[str] = Field(default_factory=list)        # ids from bots/watches.py
    sensitivity: Sensitivity = "balanced"
    deliver: List[Delivery] = Field(default_factory=lambda: ["inbox"])
    rules: Rules = Field(default_factory=Rules)
    notes: List[str] = Field(default_factory=list, max_length=20)
    #: Short questions the card shows when something was not clear (filled by the server, not the user).
    questions: List[str] = Field(default_factory=list)
    #: Where the draft came from: "template:<id>", "sentence", "chat".
    source: str = "blank"

    @field_validator("notes", mode="before")
    @classmethod
    def clean_notes(cls, v: Any) -> list[str]:
        if not v:
            return []
        cleaned: list[str] = []
        for note in v:
            if not isinstance(note, str):
                continue
            stripped = note.strip()
            if not stripped:
                continue
            cleaned.append(stripped[:200])
        return cleaned[:20]


class BotIssue(BaseModel):
    field: str      # "org_ids", "schedules[0].time", "watches", ...
    message: str    # plain English, shown under the field on the card
    fixed: bool     # True: the server changed the value; False: the user must fix it
