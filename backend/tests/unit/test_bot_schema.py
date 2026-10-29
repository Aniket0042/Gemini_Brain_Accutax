import pytest
from pydantic import ValidationError

from gemini_brain.bots.schema import BotDraft, Schedule, Rules


def test_default_draft():
    draft = BotDraft()
    assert draft.agent == "vat"
    assert draft.name == "VAT Watch"
    assert draft.deliver == ["inbox"]
    assert draft.rules.change_records == "never"
    assert draft.rules.run_review == "allow"
    assert draft.rules.export_findings == "allow"
    assert draft.rules.email_outside == "ask"
    assert draft.sensitivity == "balanced"
    assert draft.schedules == []
    assert draft.watches == []


def test_schedule_time_validation():
    s = Schedule(kind="weekly", day="mon", time="08:00")
    assert s.time == "08:00"

    with pytest.raises(ValidationError):
        Schedule(kind="weekly", day="mon", time="25:00")

    with pytest.raises(ValidationError):
        Schedule(kind="weekly", day="mon", time="8:00")

    with pytest.raises(ValidationError):
        Schedule(kind="weekly", day="mon", time="invalid")


def test_schedule_day_of_month_bounds():
    s = Schedule(kind="monthly", day_of_month=28)
    assert s.day_of_month == 28

    with pytest.raises(ValidationError):
        Schedule(kind="monthly", day_of_month=31)

    with pytest.raises(ValidationError):
        Schedule(kind="monthly", day_of_month=0)


def test_rules_change_records_immutable():
    with pytest.raises(ValidationError):
        Rules(change_records="allow")  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        BotDraft(rules=Rules(change_records="ask"))  # type: ignore[arg-type]


def test_note_trimmed_and_capped():
    long_note = "A" * 300
    draft = BotDraft(notes=[long_note, "  trimmed note  ", "   "])
    assert len(draft.notes) == 2
    assert len(draft.notes[0]) == 200
    assert draft.notes[0] == "A" * 200
    assert draft.notes[1] == "trimmed note"
