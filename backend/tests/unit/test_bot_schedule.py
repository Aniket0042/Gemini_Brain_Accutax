import datetime as dt
from zoneinfo import ZoneInfo
import pytest

from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.schedule import next_runs
from gemini_brain.config.settings import settings


def test_weekly_runs():
    tz = ZoneInfo(settings.report_timezone)
    # Thursday, 29 Oct 2026, 10:00 AM
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=tz)

    draft = BotDraft(
        schedules=[Schedule(kind="weekly", day="mon", time="08:00")]
    )
    runs = next_runs(draft, now, frequencies={}, count=3)

    assert len(runs) == 3
    # 29 Oct is Thu. Fri 30, Sat 31, Sun 1 Nov, Mon 2 Nov!
    assert runs[0].at == dt.datetime(2026, 11, 2, 8, 0, tzinfo=tz)
    assert runs[1].at == dt.datetime(2026, 11, 9, 8, 0, tzinfo=tz)
    assert runs[2].at == dt.datetime(2026, 11, 16, 8, 0, tzinfo=tz)
    for r in runs:
        assert r.reason == "weekly"


def test_before_deadline_quarterly():
    tz = ZoneInfo(settings.report_timezone)
    # 29 Oct 2026: Q3 ended 30 Sep, due 28 Oct (yesterday). Next due is Q4 (ends 31 Dec, due 28 Jan 2027).
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=tz)

    draft = BotDraft(
        org_ids=[101],
        schedules=[Schedule(kind="before_deadline", days_before=[14, 7, 1], time="09:00")]
    )
    runs = next_runs(draft, now, frequencies={101: "quarterly"}, count=3)

    assert len(runs) == 3
    assert runs[0].at == dt.datetime(2027, 1, 14, 9, 0, tzinfo=tz)
    assert runs[1].at == dt.datetime(2027, 1, 21, 9, 0, tzinfo=tz)
    assert runs[2].at == dt.datetime(2027, 1, 27, 9, 0, tzinfo=tz)
    assert "14 days before the VAT return due 28 Jan" in runs[0].reason
    assert "7 days before the VAT return due 28 Jan" in runs[1].reason
    assert "1 days before the VAT return due 28 Jan" in runs[2].reason


def test_before_deadline_monthly():
    tz = ZoneInfo(settings.report_timezone)
    # 29 Oct 2026. September period ended 30 Sep, due 28 Oct (passed).
    # Next monthly period ending is October (31 Oct), due 28 Nov 2026.
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=tz)

    draft = BotDraft(
        org_ids=[102],
        schedules=[Schedule(kind="before_deadline", days_before=[7], time="09:00")]
    )
    runs = next_runs(draft, now, frequencies={102: "monthly"}, count=2)

    assert len(runs) == 2
    # 28 Nov - 7 days = 21 Nov 2026
    assert runs[0].at == dt.datetime(2026, 11, 21, 9, 0, tzinfo=tz)
    assert "7 days before the VAT return due 28 Nov" in runs[0].reason
    # Next is November period (ends 30 Nov), due 28 Dec - 7 days = 21 Dec 2026
    assert runs[1].at == dt.datetime(2026, 12, 21, 9, 0, tzinfo=tz)


def test_two_orgs_same_due_date_deduped():
    tz = ZoneInfo(settings.report_timezone)
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=tz)

    draft = BotDraft(
        org_ids=[101, 102],
        schedules=[Schedule(kind="before_deadline", days_before=[7], time="09:00")]
    )
    # Both are quarterly, so they share the exact same due date 28 Jan 2027
    runs = next_runs(draft, now, frequencies={101: "quarterly", 102: "quarterly"}, count=3)

    # Must produce one run on 21 Jan 2027, not two duplicates
    assert len(runs) >= 1
    assert runs[0].at == dt.datetime(2027, 1, 21, 9, 0, tzinfo=tz)
    # Only 1 run on that day
    dates = [r.at for r in runs]
    assert len(dates) == len(set(dates))


def test_after_period_end():
    tz = ZoneInfo(settings.report_timezone)
    now = dt.datetime(2026, 10, 29, 10, 0, tzinfo=tz)

    draft = BotDraft(
        org_ids=[101],
        schedules=[Schedule(kind="after_period_end", days_after=3, time="10:00")]
    )
    runs = next_runs(draft, now, frequencies={101: "quarterly"}, count=2)

    assert len(runs) == 2
    # Q4 ends 31 Dec 2026 + 3 days = 3 Jan 2027
    assert runs[0].at == dt.datetime(2027, 1, 3, 10, 0, tzinfo=tz)
    assert "3 days after Q4 ends" in runs[0].reason
