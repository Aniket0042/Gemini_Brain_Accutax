"""Package 5: tax periods, due dates, the VAT return position from the books, and the penalty calculator."""
import datetime as dt
import json
from types import SimpleNamespace

import pytest

from gemini_brain.agent import tools
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.cube_client import CubeError
from gemini_brain.vat_return import penalty
from gemini_brain.vat_return import periods as p
from gemini_brain.vat_return import tool

D = dt.date


# --- tax periods and due dates --------------------------------------------------------------------------------------

@pytest.mark.parametrize("day,group,expected", [
    (D(2026, 5, 10), 3, (D(2026, 4, 1), D(2026, 6, 30))),     # calendar quarters
    (D(2026, 6, 30), 3, (D(2026, 4, 1), D(2026, 6, 30))),
    (D(2026, 1, 15), 3, (D(2026, 1, 1), D(2026, 3, 31))),
    (D(2026, 1, 15), 1, (D(2025, 11, 1), D(2026, 1, 31))),    # quarters ending January, April, July, October
    (D(2026, 2, 1), 1, (D(2026, 2, 1), D(2026, 4, 30))),
    (D(2026, 12, 5), 2, (D(2026, 12, 1), D(2027, 2, 28))),    # quarters ending February, May, August, November
])
def test_the_quarter_holding_a_day(day, group, expected):
    assert p.period_containing(day, "quarterly", group) == expected


def test_a_monthly_period_and_february_in_a_leap_year():
    assert p.period_containing(D(2028, 2, 10), "monthly") == (D(2028, 2, 1), D(2028, 2, 29))


def test_the_last_complete_period():
    assert p.last_complete_period(D(2026, 10, 16), "quarterly") == (D(2026, 7, 1), D(2026, 9, 30))
    assert p.last_complete_period(D(2026, 10, 1), "monthly") == (D(2026, 9, 1), D(2026, 9, 30))
    assert p.last_complete_period(D(2026, 1, 5), "quarterly") == (D(2025, 10, 1), D(2025, 12, 31))


def test_the_return_is_due_28_days_after_the_period_ends():
    assert p.due_date(D(2026, 9, 30)) == D(2026, 10, 28)
    assert p.due_date(D(2026, 12, 31)) == D(2027, 1, 28)


def test_an_unknown_quarter_group_is_refused():
    with pytest.raises(ValueError):
        p.period_containing(D(2026, 1, 1), "quarterly", 4)


# --- penalties (Cabinet Decision No. 129 of 2025, in force from 14 April 2026) ---------------------------------------

def test_late_payment_is_charged_for_each_month_or_part_from_the_day_after_the_due_date():
    one_day = penalty.late_payment(12_000.0, D(2026, 7, 28), D(2026, 7, 29))
    assert one_day["months"] == 1 and one_day["monthly_penalty"] == 140.0 and one_day["penalty"] == 140.0
    assert penalty.late_payment(12_000.0, D(2026, 7, 28), D(2026, 8, 28))["months"] == 1
    assert penalty.late_payment(12_000.0, D(2026, 7, 28), D(2026, 8, 29))["months"] == 2
    paid_on_time = penalty.late_payment(12_000.0, D(2026, 7, 28), D(2026, 7, 28))
    assert paid_on_time["months"] == 0 and paid_on_time["penalty"] == 0.0


def test_a_day_the_month_lacks_moves_to_the_first_of_the_next_month():
    assert penalty.penalty_dates(D(2026, 12, 30), D(2027, 4, 2)) == [
        D(2026, 12, 31), D(2027, 1, 31), D(2027, 3, 1), D(2027, 3, 31), D(2027, 5, 1)][:4]


def test_a_due_date_before_the_current_rules_is_not_calculated():
    out = penalty.late_payment(1000.0, D(2026, 3, 28), D(2026, 6, 1))
    assert out["calculated"] is False and "14 April 2026" in out["reason"]


def test_a_late_return_costs_1000_or_2000_when_repeated():
    assert penalty.late_return()["penalty"] == 1000.0 and penalty.late_return(True)["penalty"] == 2000.0


def test_a_voluntary_disclosure_costs_1_percent_a_month_or_part():
    out = penalty.voluntary_disclosure(-5_000.0, D(2026, 7, 28), D(2026, 9, 10))
    assert out["months"] == 2 and out["monthly_penalty"] == 50.0 and out["penalty"] == 100.0 and out["difference"] == 5000.0


# --- the vat_penalty tool -------------------------------------------------------------------------------------------

def test_the_penalty_tool_takes_today_when_no_payment_date_is_given():
    out = tool.penalty_run({"kind": "late_payment", "amount": 12000, "due_date": "2026-07-28"}, today=D(2026, 10, 16))
    assert out["months"] == 3 and out["penalty"] == 420.0 and "Cabinet Decision" in out["source"]


@pytest.mark.parametrize("params", [
    {"kind": "fine"}, {"kind": "late_payment", "amount": "x", "due_date": "2026-07-28"},
    {"kind": "late_payment", "amount": 10, "due_date": "28/07/2026"},
    {"kind": "late_payment", "amount": 10, "due_date": "2026-07-28", "paid_on": "2026-07-01"},
])
def test_the_penalty_tool_refuses_bad_input(params):
    with pytest.raises(ToolInputError):
        tool.penalty_run(params, today=D(2026, 10, 16))


def test_the_agent_offers_and_runs_the_penalty_tool():
    ctx = tools.ToolContext(organization_ids=[7], subject="t", deadline=1e12)
    result, ok = tools.execute("vat_penalty", {"kind": "late_return"}, ctx)
    assert ok and result["penalty"] == 1000.0


# --- the vat_return tool --------------------------------------------------------------------------------------------

def fake_cube(monkeypatch, settings_rows=None, figures=None, settings_error=False):
    seen = []

    def load(query, *, organization_ids, subject, deadline):
        seen.append((query, list(organization_ids)))
        if any(m.startswith("vat_settings.") for m in query.get("dimensions", [])):
            if settings_error:
                raise CubeError("Forbidden: 'vat_settings' is not queryable")
            return SimpleNamespace(rows=settings_rows or [])
        org = organization_ids[0]
        return SimpleNamespace(rows=[(figures or {}).get(org, {})])

    monkeypatch.setattr(tool.cube_client, "load", load)
    monkeypatch.setattr(tool.semantic_periods, "today_in", lambda tz: D(2026, 10, 16))
    return seen


FIG = {"vat.output_vat": "12500.00", "vat.input_vat": "4500.00", "vat.net_vat_payable": "8000.00",
       "vat.standard_rated_sales": "250000.00"}


def test_the_last_quarter_with_its_due_date_and_penalties_once_overdue(monkeypatch):
    seen = fake_cube(monkeypatch, figures={25: FIG})
    out = tool.run({}, organization_ids=[25], subject="t", deadline=1e12)
    [org] = out["organizations"]
    assert org["period"] == {"start": "2026-07-01", "end": "2026-09-30"} and org["due_date"] == "2026-10-28"
    assert org["days_to_due"] == 12 and org["position"] == "payable" and org["net_vat_payable"] == 8000.0
    assert org["frequency_source"].startswith("assumed") and "if_still_unfiled_and_unpaid_today" not in org
    q = next(q for q, _ in seen if "measures" in q)
    assert q["timeDimensions"][0]["dateRange"] == ["2026-07-01", "2026-09-30"]
    assert any("not a filed return" in n for n in out["notes"]) and any("calendar quarters" in n for n in out["notes"])
    json.dumps(out)


def test_an_overdue_payable_return_shows_both_penalties(monkeypatch):
    fake_cube(monkeypatch, figures={25: FIG})
    out = tool.run({"which": "period", "period": {"start": "2026-04-01", "end": "2026-06-30"}},
                   organization_ids=[25], subject="t", deadline=1e12)
    pen = out["organizations"][0]["if_still_unfiled_and_unpaid_today"]
    assert pen["late_return"]["penalty"] == 1000.0
    assert pen["late_payment"]["months"] == 3 and pen["late_payment"]["penalty"] == round(round(8000 * 0.14 / 12, 2) * 3, 2)


def test_the_frequency_comes_from_the_vat_settings(monkeypatch):
    fake_cube(monkeypatch, settings_rows=[{"vat_settings.organization_id": "25", "vat_settings.filing_frequency": "monthly"}],
              figures={25: FIG, 26: FIG})
    out = tool.run({}, organization_ids=[25, 26], subject="t", deadline=1e12)
    by_org = {o["organization_id"]: o for o in out["organizations"]}
    assert by_org[25]["frequency"] == "monthly" and by_org[25]["period"] == {"start": "2026-09-01", "end": "2026-09-30"}
    assert by_org[25]["frequency_source"] == "from the organization's VAT settings"
    assert by_org[26]["frequency"] == "quarterly" and by_org[26]["frequency_source"].startswith("assumed")


def test_a_cube_without_the_settings_view_still_answers(monkeypatch):
    fake_cube(monkeypatch, figures={25: FIG}, settings_error=True)
    out = tool.run({}, organization_ids=[25], subject="t", deadline=1e12)
    assert out["organizations"][0]["frequency"] == "quarterly"


def test_a_refund_has_no_penalty(monkeypatch):
    fake_cube(monkeypatch, figures={25: {**FIG, "vat.net_vat_payable": "-300.00"}})
    out = tool.run({"which": "period", "period": {"start": "2026-04-01", "end": "2026-06-30"}},
                   organization_ids=[25], subject="t", deadline=1e12)
    org = out["organizations"][0]
    assert org["position"] == "refundable" and "if_still_unfiled_and_unpaid_today" not in org


@pytest.mark.parametrize("params", [{"which": "next"}, {"quarter_group": 5}, {"frequency": "yearly"},
                                    {"which": "period", "period": {"start": "2026-07-01", "end": "2026-06-01"}}])
def test_bad_input_is_a_tool_error(monkeypatch, params):
    fake_cube(monkeypatch, figures={25: FIG})
    with pytest.raises(ToolInputError):
        tool.run(params, organization_ids=[25], subject="t", deadline=1e12)


def test_the_agent_runs_vat_return_on_the_chats_organizations_only(monkeypatch):
    fake_cube(monkeypatch, figures={25: FIG})
    ctx = tools.ToolContext(organization_ids=[25], subject="t", deadline=1e12)
    result, ok = tools.execute("vat_return", {}, ctx)
    assert ok and result["organizations"][0]["organization_id"] == 25
    result, ok = tools.execute("vat_return", {"organization_ids": [99]}, ctx)
    assert not ok and "not selected" in result["error"]
