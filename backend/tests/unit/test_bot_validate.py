import pytest

from gemini_brain.bots.schema import BotDraft, Schedule
from gemini_brain.bots.templates import all_templates
from gemini_brain.bots.validate import validate


def test_clean_template_gives_no_issues():
    for tmpl in all_templates():
        draft = tmpl.draft.model_copy(update={"org_ids": [101]})
        validated, issues = validate(draft, allowed_org_ids=[101], current_org_ids=[101])
        # Clean draft with valid org should produce no issues
        assert issues == []
        assert validated.org_ids == [101]


def test_check_1_org_ids_empty():
    # 1a: fill with current_org_ids (fixed=True)
    d = BotDraft(org_ids=[])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.org_ids == [101]
    assert any(iss.field == "org_ids" and iss.fixed for iss in issues)

    # 1b: current_org_ids also empty (fixed=False)
    v2, issues2 = validate(d, allowed_org_ids=[], current_org_ids=[])
    assert any(iss.field == "org_ids" and not iss.fixed for iss in issues2)


def test_check_2_unauthorized_org_removed():
    d = BotDraft(org_ids=[101, 999])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.org_ids == [101]
    unauth_issue = next(iss for iss in issues if "999" in iss.message)
    assert unauth_issue.fixed is True
    assert "999" in unauth_issue.message


def test_check_3_unknown_watch_removed():
    d = BotDraft(org_ids=[101], watches=["duplicate_bill", "unknown_watch"])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.watches == ["duplicate_bill"]
    assert any("unknown_watch" in iss.message and iss.fixed for iss in issues)


def test_check_4_watches_empty_filled_defaults():
    d = BotDraft(org_ids=[101], watches=[])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert len(v.watches) > 0
    assert any("default checks" in iss.message and iss.fixed for iss in issues)


def test_check_5_schedules_empty():
    d = BotDraft(org_ids=[101], schedules=[])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert any(iss.field == "schedules" and not iss.fixed for iss in issues)
    assert "How often should it run?" in v.questions


def test_check_6_incomplete_schedules():
    # weekly missing day
    s1 = Schedule.model_construct(kind="weekly", day=None, time="08:00")
    # monthly missing day_of_month
    s2 = Schedule.model_construct(kind="monthly", day_of_month=None, time="08:00")
    # before_deadline missing days_before
    s3 = Schedule(kind="before_deadline", days_before=[], time="08:00")

    d = BotDraft(org_ids=[101], schedules=[s1, s2, s3])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])

    unfixed = [iss for iss in issues if not iss.fixed]
    assert len(unfixed) == 3
    fields = [iss.field for iss in unfixed]
    assert "schedules[0].day" in fields
    assert "schedules[1].day_of_month" in fields
    assert "schedules[2].days_before" in fields


def test_check_7_days_before_out_of_bounds():
    s = Schedule(kind="before_deadline", days_before=[70, 14, 0, 7], time="08:00")
    d = BotDraft(org_ids=[101], schedules=[s])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.schedules[0].days_before == [14, 7]
    assert any("1..60" in iss.message and iss.fixed for iss in issues)


def test_check_8_duplicate_schedules():
    s1 = Schedule(kind="weekly", day="mon", time="08:00")
    s2 = Schedule(kind="weekly", day="mon", time="08:00")
    d = BotDraft(org_ids=[101], schedules=[s1, s2])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert len(v.schedules) == 1
    assert any("duplicate schedule" in iss.message and iss.fixed for iss in issues)


def test_check_9_deliver_without_inbox():
    d = BotDraft(org_ids=[101], deliver=["email"])  # type: ignore[list-item]
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.deliver[0] == "inbox"
    assert any("inbox" in iss.message and iss.fixed for iss in issues)


def test_check_9_email_not_available_yet():
    d = BotDraft(org_ids=[101], deliver=["inbox", "email", "bell"])  # type: ignore[list-item]
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.deliver == ["inbox", "bell"]
    assert any(iss.field == "deliver" and "Email is not available" in iss.message and iss.fixed for iss in issues)


def test_check_10_name_empty():
    d = BotDraft.model_construct(name="   ", org_ids=[101])
    v, issues = validate(d, allowed_org_ids=[101], current_org_ids=[101])
    assert v.name == "VAT Watch"
    assert any("VAT Watch" in iss.message and iss.fixed for iss in issues)
