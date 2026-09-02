"""
test_window_widening.py — Stage 4: retry an empty recent window over a longer one.

The behavioural risk here is not the retry, it is retrying the *wrong* thing.
Widening answers a slightly different question, so a deliberate historical period
("Q1 2025", "last month") must never be silently replaced with a different one.
The guard is that the window's end date is at/near today; several tests below
exist purely to hold that line.
"""
import datetime
from unittest.mock import MagicMock, patch

import pytest

from gemini_brain.endpoints.window_widener import (
    MAX_WIDENING_ATTEMPTS,
    describe_window,
    plan_widenings,
    widened_params,
)
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved
from gemini_brain.router.dates import today as org_today


#: Fixed anchor for the pure planning tests — those pass an explicit anchor, so a
#: literal date keeps them readable and deterministic.
TODAY = datetime.date(2026, 8, 28)


# ── Planning ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name,params,expect_retries", [
    ("this month",        {"start_date": "2026-08-01", "end_date": "2026-08-31"}, True),
    ("this year",         {"start_date": "2026-01-01", "end_date": "2026-08-28"}, True),
    ("last 30 days",      {"start_date": "2026-07-29", "end_date": "2026-08-28"}, True),
    ("ends yesterday",    {"start_date": "2026-08-01", "end_date": "2026-08-27"}, True),
    ("Q1 2025",           {"start_date": "2025-01-01", "end_date": "2025-03-31"}, False),
    ("last month",        {"start_date": "2026-07-01", "end_date": "2026-07-31"}, False),
    ("bare year 2024",    {"start_date": "2024-01-01", "end_date": "2024-12-31"}, False),
    ("no date range",     {"organization_id": 199},                               False),
    ("as_of only",        {"as_of_date": "2026-08-28"},                           False),
    ("unparseable start", {"start_date": "nope", "end_date": "2026-08-28"},       False),
    ("not a dict",        None,                                                    False),
])
def test_plan_widenings_applicability(name, params, expect_retries):
    plans = plan_widenings(params, anchor=TODAY)
    assert bool(plans) is expect_retries, name


def test_historical_windows_are_never_widened():
    """The core correctness guard, stated on its own so it can't be lost in a table.

    Answering "revenue in Q1 2025" with last year's numbers is worse than
    answering "there is nothing there".
    """
    for end in ("2025-03-31", "2024-12-31", "2026-07-31"):
        assert plan_widenings({"start_date": "2020-01-01", "end_date": end}, anchor=TODAY) == []


def test_widenings_are_bounded():
    plans = plan_widenings({"start_date": "2026-08-01", "end_date": "2026-08-31"}, anchor=TODAY)
    assert len(plans) <= MAX_WIDENING_ATTEMPTS


def test_widenings_are_strictly_longer_and_ascending():
    """Re-running the same span would spend a call to get the same empty answer."""
    params = {"start_date": "2026-08-01", "end_date": "2026-08-31"}
    original_span = 30
    spans = []
    for start, end in plan_widenings(params, anchor=TODAY):
        span = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(start)).days
        assert span > original_span
        spans.append(span)
    assert spans == sorted(spans)


def test_widened_windows_end_today():
    for start, end in plan_widenings({"start_date": "2026-08-01", "end_date": "2026-08-31"}, anchor=TODAY):
        assert end == TODAY.isoformat()


def test_describe_window_phrasing():
    assert describe_window("2026-05-30", "2026-08-28") == "the last 3 months"
    assert describe_window("2026-02-28", "2026-08-28") == "the last 6 months"
    assert describe_window("2025-08-28", "2026-08-28") == "the last 12 months"
    assert describe_window("2024-08-28", "2026-08-28") == "the last two years"
    assert describe_window("bad", "worse") == "a longer period"


def test_widened_params_does_not_mutate_the_original():
    original = {"organization_id": 199, "start_date": "2026-08-01", "end_date": "2026-08-31"}
    out = widened_params(original, "2026-05-30", "2026-08-28")
    assert original["start_date"] == "2026-08-01", "input was mutated"
    assert out["start_date"] == "2026-05-30"
    assert out["organization_id"] == 199, "unrelated params must be carried through"


# ── Runner integration ───────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def no_live_db_verification(monkeypatch):
    """These tests exercise widening/self-correction, not the SQL cross-check.

    _empty_result() calls _verify_empty_via_sql(), which reaches a real database
    connection outside of runner._retrieve — the mock these tests set on
    `runner._retrieve` does not cover it. With a real (or even reachable-but-empty)
    database configured, that call can silently flip an EMPTY outcome to OK/PARTIAL,
    which has nothing to do with what these tests are checking. Force it off so
    these tests are hermetic regardless of what .env points at.
    """
    monkeypatch.setattr(
        "gemini_brain.orchestrator.gemini_brain_runner._verify_empty_via_sql",
        lambda *a, **k: None,
    )


def _make_runner():
    with patch("gemini_brain.orchestrator.gemini_brain_runner.settings") as mock_settings:
        mock_settings.gemini_api_key = "dummy"
        mock_settings.accutax_base_url = "http://dummy"
        mock_settings.accutax_auth_token = "dummy"
        runner = GeminiBrainRunner(api_key="test-api-key")
        runner._call_llm = MagicMock(return_value=('{"intent": 4, "reason": "data"}', 10, 5))
        return runner


# Built relative to the real clock: the runner calls plan_widenings() without an
# anchor, so a hardcoded "recent" window would quietly stop being recent and these
# tests would start passing for the wrong reason (no retry, because the window
# aged into the historical branch).
_REAL_TODAY = org_today()
RECENT_WINDOW = {
    "organization_id": 1,
    "start_date": (_REAL_TODAY - datetime.timedelta(days=27)).isoformat(),
    "end_date": _REAL_TODAY.isoformat(),
}
HISTORICAL_WINDOW = {
    "organization_id": 1,
    "start_date": (_REAL_TODAY - datetime.timedelta(days=540)).isoformat(),
    "end_date": (_REAL_TODAY - datetime.timedelta(days=450)).isoformat(),
}

ROWS = [{"customer": f"C{i}", "revenue": float(i)} for i in range(5)]


def _empty():
    return Retrieved(Outcome.EMPTY, tier="live_api", endpoint="/report/sales-by-customer",
                     payload=[], row_count=0)


def _usable():
    return Retrieved(Outcome.OK, tier="live_api", endpoint="/report/sales-by-customer",
                     payload=ROWS, row_count=len(ROWS))


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_empty_recent_window_is_retried_and_recovers(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/sales-by-customer", "path_params": {},
                              "query_params": dict(RECENT_WINDOW)}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(side_effect=[_empty(), _usable()])
    runner._db_fallback = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
               return_value=("Revenue came mostly from C4.", "Claude Haiku 4.5", 10, 5)):
        res = runner.run("sales by customer this month", organization_id=1)

    assert runner._retrieve.call_count == 2, "expected exactly one widened retry"
    assert res["results"] == ROWS
    assert res["status"] == "partial"
    assert res["notice"]["code"] == "WIDENED_WINDOW"
    # The caveat must be in the answer text, not only the notice — API consumers
    # that ignore `notice` still need to know the period changed.
    assert "no records for the period you asked about" in res["answer"].lower()
    assert "Revenue came mostly from C4." in res["answer"]
    assert runner._db_fallback.call_count == 0


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_historical_window_is_not_retried(mock_intent, mock_sel):
    """A deliberate period stays a confirmed-zero answer."""
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/sales-by-customer", "path_params": {},
                              "query_params": dict(HISTORICAL_WINDOW)}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(return_value=_empty())
    runner._db_fallback = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
               return_value=("No sales recorded in that quarter.", "Claude Haiku 4.5", 10, 5)):
        res = runner.run("sales by customer in Q1 2025", organization_id=1)

    assert runner._retrieve.call_count == 1, "a historical window must not be widened"
    assert res["status"] == "empty"
    assert res["notice"]["code"] == "NO_ROWS"


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_all_widenings_empty_falls_back_to_confirmed_zero(mock_intent, mock_sel):
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/sales-by-customer", "path_params": {},
                              "query_params": dict(RECENT_WINDOW)}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(side_effect=[_empty(), _empty(), _empty()])
    runner._db_fallback = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
               return_value=("Nothing recorded.", "Claude Haiku 4.5", 10, 5)):
        res = runner.run("sales by customer this month", organization_id=1)

    assert runner._retrieve.call_count == 1 + MAX_WIDENING_ATTEMPTS
    assert res["status"] == "empty"
    assert res["notice"]["code"] == "NO_ROWS"
    assert "no records for the period" not in res["answer"].lower()


@patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint")
@patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent")
def test_widening_failure_degrades_to_the_empty_answer(mock_intent, mock_sel):
    """A crash inside the retry must not break a query that already had an answer."""
    mock_intent.return_value = ({"type": 4, "reason": "fetch"}, 10, 5)
    mock_sel.return_value = ({"endpoint": "/report/sales-by-customer", "path_params": {},
                              "query_params": dict(RECENT_WINDOW)}, 10, 5)

    runner = _make_runner()
    runner._retrieve = MagicMock(side_effect=[_empty(), RuntimeError("upstream exploded")])
    runner._db_fallback = MagicMock()

    with patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
               return_value=("Nothing recorded.", "Claude Haiku 4.5", 10, 5)):
        res = runner.run("sales by customer this month", organization_id=1)

    assert res["status"] == "empty"
    assert res["notice"]["code"] == "NO_ROWS"
