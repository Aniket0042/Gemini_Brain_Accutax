import datetime as dt

import pytest

from gemini_brain.semantic import periods

D = dt.date


@pytest.mark.parametrize("today,preset,expected", [
    (D(2026, 10, 6), "ytd", (D(2026, 1, 1), D(2026, 10, 6))),
    (D(2026, 10, 6), "today", (D(2026, 10, 6), D(2026, 10, 6))),
    (D(2026, 10, 6), "this_month", (D(2026, 10, 1), D(2026, 10, 6))),
    (D(2026, 10, 6), "last_month", (D(2026, 9, 1), D(2026, 9, 30))),
    (D(2026, 1, 1), "last_month", (D(2025, 12, 1), D(2025, 12, 31))),
    (D(2026, 3, 31), "last_month", (D(2026, 2, 1), D(2026, 2, 28))),
    (D(2024, 3, 1), "last_month", (D(2024, 2, 1), D(2024, 2, 29))),
    (D(2026, 10, 6), "this_quarter", (D(2026, 10, 1), D(2026, 10, 6))),
    (D(2026, 10, 6), "last_quarter", (D(2026, 7, 1), D(2026, 9, 30))),
    (D(2026, 2, 15), "last_quarter", (D(2025, 10, 1), D(2025, 12, 31))),
    (D(2026, 10, 6), "last_year", (D(2025, 1, 1), D(2025, 12, 31))),
    (D(2026, 10, 6), "last_12_months", (D(2025, 11, 1), D(2026, 10, 6))),
    (D(2026, 1, 1), "ytd", (D(2026, 1, 1), D(2026, 1, 1))),
])
def test_presets(today, preset, expected):
    assert periods.resolve({"preset": preset}, today) == expected


def test_no_period_means_year_to_date():
    assert periods.resolve(None, D(2026, 10, 6)) == (D(2026, 1, 1), D(2026, 10, 6))


def test_custom_range():
    assert periods.resolve({"start": "2025-01-01", "end": "2025-12-31T00:00:00Z"}, D(2026, 10, 6)) == \
        (D(2025, 1, 1), D(2025, 12, 31))


@pytest.mark.parametrize("period", [
    {"preset": "fiscal_year"},
    {"start": "2025-12-31", "end": "2025-01-01"},
    {"start": "2025-01-01"},
    {"start": "not a date", "end": "2025-01-01"},
])
def test_bad_periods_raise(period):
    with pytest.raises(ValueError):
        periods.resolve(period, D(2026, 10, 6))
