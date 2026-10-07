"""cash_forecast: weekly closing cash worked out in code from faked Cube rows."""
import datetime as dt

import pytest

from gemini_brain.semantic import cash_forecast
from gemini_brain.semantic.catalog import ToolInputError
from gemini_brain.semantic.cube_client import CubeResult

TODAY = dt.date(2026, 10, 8)  # a Thursday; this week starts Monday 5 October


def _org(view, oid=24, name="Arjun Trading", currency="AED"):
    return {f"{view}.organization_id": oid, f"{view}.organization_name": name, f"{view}.currency": currency}


def _fake_cube(queries):
    """Answers each forecast query by shape, and records it."""
    def load(query, *, organization_ids, subject, deadline):
        queries.append(query)
        view = query["measures"][0].split(".")[0]
        if view == "balance_sheet":
            rows = [{**_org("balance_sheet"), "balance_sheet.account_currency": "AED", "balance_sheet.cash_and_bank": "10000"},
                    {**_org("balance_sheet"), "balance_sheet.account_currency": "USD", "balance_sheet.cash_and_bank": "500"}]
        elif query.get("filters"):  # undated
            rows = [{**_org(view), f"{view}.outstanding": "300" if view == "receivables" else "100"}]
        elif query.get("timeDimensions"):  # weekly
            if view == "receivables":
                rows = [{**_org(view), "receivables.due_date.week": "2026-10-05T00:00:00.000", "receivables.outstanding": "2000"},
                        {**_org(view), "receivables.due_date.week": "2026-10-19T00:00:00.000", "receivables.outstanding": "1000"}]
            else:
                rows = [{**_org(view), "payables.due_date.week": "2026-10-12T00:00:00.000", "payables.outstanding": "15000"}]
        else:  # totals
            rows = [{**_org(view), f"{view}.outstanding": "5300" if view == "receivables" else "16100",
                     f"{view}.overdue_amount": "1500" if view == "receivables" else "1000"}]
        return CubeResult(rows=rows, query=query, elapsed_ms=1)
    return load


@pytest.fixture
def cube(monkeypatch):
    queries = []
    monkeypatch.setattr(cash_forecast.periods, "today_in", lambda tz: TODAY)
    monkeypatch.setattr(cash_forecast.cube_client, "load", _fake_cube(queries))
    return queries


def test_weekly_closing_cash_is_worked_out_in_code(cube):
    out = cash_forecast.run({"weeks": 3}, organization_ids=[24], subject="t", deadline=1e12)
    org = out["organizations"][0]
    assert org["opening_cash"] == "10000"
    assert [(w["week_start"], w["collections_due"], w["payments_due"], w["closing_cash"]) for w in org["weeks"]] == [
        ("2026-10-08", "2000", "0", "12000"),
        ("2026-10-12", "0", "15000", "-3000"),
        ("2026-10-19", "1000", "0", "-2000"),
    ]
    assert org["closing_cash"] == "-2000" and org["lowest_closing_cash"] == "-3000"
    assert out["period"].startswith("2026-10-05 to 2026-10-25")


def test_overdue_undated_and_later_amounts_are_reported_beside_the_weeks(cube):
    org = cash_forecast.run({"weeks": 3}, organization_ids=[24], subject="t", deadline=1e12)["organizations"][0]
    assert org["overdue_receivables"] == "1500" and org["overdue_payables"] == "1000"
    assert org["closing_cash_if_overdue_settled"] == "-1500"
    assert org["receivables_without_due_date"] == "300" and org["payables_without_due_date"] == "100"
    # 5300 open = 1500 overdue + 300 undated + 3000 in the weeks + 500 later
    assert org["receivables_due_after_horizon"] == "500" and org["payables_due_after_horizon"] == "0"


def test_cash_in_another_currency_is_kept_apart(cube):
    org = cash_forecast.run({}, organization_ids=[24], subject="t", deadline=1e12)["organizations"][0]
    assert org["cash_in_other_currencies"] == {"USD": "500"}
    assert len(org["weeks"]) == cash_forecast.DEFAULT_WEEKS


def test_queries_never_need_more_rows_than_cube_returns(cube):
    cash_forecast.run({"weeks": 26}, organization_ids=list(range(1, 18)), subject="t", deadline=1e12)
    weekly = [q for q in cube if q.get("timeDimensions") and q["timeDimensions"][0].get("granularity")]
    assert weekly and all(q["timeDimensions"][0]["dateRange"] == ["2026-10-08", "2027-04-04"] for q in weekly)


@pytest.mark.parametrize("params, orgs", [({"weeks": 0}, [24]), ({"weeks": 27}, [24]), ({"weeks": "x"}, [24]),
                                          ({"weeks": 26}, list(range(1, 25)))])
def test_bad_requests_go_back_to_the_model(cube, params, orgs):
    with pytest.raises(ToolInputError):
        cash_forecast.run(params, organization_ids=orgs, subject="t", deadline=1e12)


def test_the_tool_is_offered_only_when_its_views_exist():
    assert cash_forecast.available(["pnl", "balance_sheet", "receivables", "payables"])
    assert not cash_forecast.available(["pnl", "balance_sheet", "receivables"])
