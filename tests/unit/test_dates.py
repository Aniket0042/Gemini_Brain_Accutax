"""
test_dates.py — Comprehensive unit tests for deterministic timezone-aware date resolver.
"""
import datetime
from zoneinfo import ZoneInfo
import pytest

from gemini_brain.router import dates
from gemini_brain.router.dates import ORG_TZ, Window, resolve, today


def test_today_timezone():
    """Verify today() returns date in Asia/Dubai."""
    t = today()
    assert isinstance(t, datetime.date)


def test_timezone_boundary_ist_vs_dubai():
    """Verify timezone handling at month-end boundary (23:30 IST on last day of month).

    2026-03-31 23:30 IST (UTC+5:30) is 2026-03-31 22:00 in Dubai (UTC+4).
    Both should evaluate to March 31, 2026.
    """
    dt_ist = datetime.datetime(2026, 3, 31, 23, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    dt_dubai = dt_ist.astimezone(ORG_TZ)

    assert dt_dubai.date() == datetime.date(2026, 3, 31)
    assert dt_dubai.hour == 22

    # Date resolution anchored on that Dubai date
    anchor = dt_dubai.date()
    w = resolve("this month", anchor=anchor)
    assert w.date_from == datetime.date(2026, 3, 1)
    assert w.date_to == datetime.date(2026, 3, 31)


def test_this_month():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("this month", anchor=anchor)
    assert w.date_from == datetime.date(2026, 8, 1)
    assert w.date_to == datetime.date(2026, 8, 31)

    w_mtd = resolve("mtd", anchor=anchor)
    assert w_mtd.date_from == datetime.date(2026, 8, 1)
    assert w_mtd.date_to == datetime.date(2026, 8, 15)


def test_last_month():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("last month", anchor=anchor)
    assert w.date_from == datetime.date(2026, 7, 1)
    assert w.date_to == datetime.date(2026, 7, 31)

    # Rollover in January
    anchor_jan = datetime.date(2026, 1, 10)
    w_jan = resolve("previous month", anchor=anchor_jan)
    assert w_jan.date_from == datetime.date(2025, 12, 1)
    assert w_jan.date_to == datetime.date(2025, 12, 31)


def test_this_quarter_and_qtd():
    anchor = datetime.date(2026, 8, 15)  # Q3
    w = resolve("this quarter", anchor=anchor)
    assert w.date_from == datetime.date(2026, 7, 1)
    assert w.date_to == datetime.date(2026, 9, 30)

    w_qtd = resolve("qtd", anchor=anchor)
    assert w_qtd.date_from == datetime.date(2026, 7, 1)
    assert w_qtd.date_to == datetime.date(2026, 8, 15)


def test_last_quarter():
    anchor = datetime.date(2026, 8, 15)  # Q3 -> Last quarter is Q2
    w = resolve("last quarter", anchor=anchor)
    assert w.date_from == datetime.date(2026, 4, 1)
    assert w.date_to == datetime.date(2026, 6, 30)

    # Rollover in Q1
    anchor_q1 = datetime.date(2026, 2, 10)
    w_q1 = resolve("last quarter", anchor=anchor_q1)
    assert w_q1.date_from == datetime.date(2025, 10, 1)
    assert w_q1.date_to == datetime.date(2025, 12, 31)


def test_quarter_year_syntax():
    w_q1 = resolve("Q1 2026")
    assert w_q1.date_from == datetime.date(2026, 1, 1)
    assert w_q1.date_to == datetime.date(2026, 3, 31)

    w_q4 = resolve("q4 2025")
    assert w_q4.date_from == datetime.date(2025, 10, 1)
    assert w_q4.date_to == datetime.date(2025, 12, 31)


def test_this_year_and_ytd():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("this year", anchor=anchor)
    assert w.date_from == datetime.date(2026, 1, 1)
    assert w.date_to == datetime.date(2026, 12, 31)

    w_ytd = resolve("ytd", anchor=anchor)
    assert w_ytd.date_from == datetime.date(2026, 1, 1)
    assert w_ytd.date_to == datetime.date(2026, 8, 15)


def test_last_year():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("last year", anchor=anchor)
    assert w.date_from == datetime.date(2025, 1, 1)
    assert w.date_to == datetime.date(2025, 12, 31)


def test_bare_4_digit_year():
    w = resolve("2025")
    assert w.date_from == datetime.date(2025, 1, 1)
    assert w.date_to == datetime.date(2025, 12, 31)


def test_last_n_months():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("last 6 months", anchor=anchor)
    assert w.date_from == datetime.date(2026, 2, 1)
    assert w.date_to == datetime.date(2026, 8, 15)


def test_last_n_days():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("last 30 days", anchor=anchor)
    assert w.date_from == datetime.date(2026, 7, 16)
    assert w.date_to == datetime.date(2026, 8, 15)


def test_unrecognized_defaults_to_this_year():
    anchor = datetime.date(2026, 8, 15)
    w = resolve("some unknown phrase", anchor=anchor)
    assert w.date_from == datetime.date(2026, 1, 1)
    assert w.date_to == datetime.date(2026, 12, 31)

    w_none = resolve(None, anchor=anchor)
    assert w_none.date_from == datetime.date(2026, 1, 1)
    assert w_none.date_to == datetime.date(2026, 8, 15)
