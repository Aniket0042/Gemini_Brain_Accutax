import pytest
from typing import Any, Dict

from gemini_brain.bots.draft import from_sentence


def _fake_model_response(tool_input: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "toolUse": {
                            "name": "make_bot_draft",
                            "toolUseId": "tu_123",
                            "input": tool_input,
                        }
                    }
                ],
            }
        }
    }


def test_good_sentence_draft():
    fake_input = {
        "name": "Monday VAT Inspector",
        "schedules": [{"kind": "weekly", "day": "mon", "time": "08:00"}],
        "watches": ["duplicate_bill", "vat_rate_wrong"],
        "deliver": ["inbox", "email"],
        "notes": ["check vendor TRNs"],
    }

    def fake_call(**kwargs):
        return _fake_model_response(fake_input)

    draft, issues = from_sentence(
        "Check my VAT every Monday at 8am and email me",
        allowed_orgs={101: "AccuTax UAE"},
        current_org_ids=[101],
        call_model=fake_call,
    )

    assert draft.name == "Monday VAT Inspector"
    assert len(draft.schedules) == 1
    assert draft.schedules[0].day == "mon"
    assert draft.watches == ["duplicate_bill", "vat_rate_wrong"]
    assert "email" not in draft.deliver  # asked for, but email is not available yet
    assert any(iss.field == "deliver" for iss in issues)
    assert draft.org_ids == [101]


def test_no_tool_use_fallback():
    def fake_call_empty(**kwargs):
        return {"output": {"message": {"content": [{"text": "I could not parse this."}]}}}

    draft, issues = from_sentence(
        "Something random",
        allowed_orgs={101: "AccuTax UAE"},
        current_org_ids=[101],
        call_model=fake_call_empty,
    )

    assert draft.name == "VAT Watch"
    assert len(draft.schedules) == 1
    assert draft.org_ids == [101]


def test_org_name_matching_and_unmatched():
    fake_input = {
        "org_names": ["AccuTax UAE", "Ghost Enterprise"],
        "schedules": [{"kind": "weekly", "day": "tue", "time": "09:00"}],
    }

    def fake_call(**kwargs):
        return _fake_model_response(fake_input)

    draft, issues = from_sentence(
        "Run for AccuTax UAE and Ghost Enterprise every Tuesday",
        allowed_orgs={101: "AccuTax UAE"},
        current_org_ids=[101],
        call_model=fake_call,
    )

    assert draft.org_ids == [101]
    assert "Which organization is 'Ghost Enterprise'?" in draft.questions


def test_bad_schedule_time_dropped():
    fake_input = {
        "schedules": [{"kind": "weekly", "day": "mon", "time": "25:00"}],
    }

    def fake_call(**kwargs):
        return _fake_model_response(fake_input)

    draft, issues = from_sentence(
        "Run every Monday at 25:00",
        allowed_orgs={101: "AccuTax UAE"},
        current_org_ids=[101],
        call_model=fake_call,
    )

    # Bad schedule dropped, validate reports schedule empty issue
    assert len(draft.schedules) == 0
    assert any("Schedule ignored" in iss.message for iss in issues)


def test_text_length_and_empty_validation():
    with pytest.raises(ValueError):
        from_sentence("", allowed_orgs={}, current_org_ids=[])

    with pytest.raises(ValueError):
        from_sentence("   ", allowed_orgs={}, current_org_ids=[])

    with pytest.raises(ValueError):
        from_sentence("x" * 501, allowed_orgs={}, current_org_ids=[])
