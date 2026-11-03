import pytest

from gemini_brain.bots.cost import runs_per_month, monthly_credits
from gemini_brain.bots.schema import BotDraft, Schedule


def test_cost_weekly_one_org():
    draft = BotDraft(
        org_ids=[101],
        schedules=[Schedule(kind="weekly", day="mon", time="08:00")]
    )
    assert runs_per_month(draft, {}) == 5
    assert monthly_credits(draft, {}) == 390


def test_cost_reminder_quarterly_one_org():
    draft = BotDraft(
        org_ids=[101],
        schedules=[Schedule(kind="before_deadline", days_before=[14, 7, 1], time="09:00")]
    )
    assert runs_per_month(draft, {101: "quarterly"}) == 1
    assert monthly_credits(draft, {101: "quarterly"}) == 90


def test_cost_weekly_two_orgs():
    draft = BotDraft(
        org_ids=[101, 102],
        schedules=[Schedule(kind="weekly", day="mon", time="08:00")]
    )
    assert runs_per_month(draft, {}) == 5
    assert monthly_credits(draft, {}) == 585


def test_cost_no_schedules():
    draft = BotDraft(org_ids=[101], schedules=[])
    assert runs_per_month(draft, {}) == 0
    assert monthly_credits(draft, {}) == 0
