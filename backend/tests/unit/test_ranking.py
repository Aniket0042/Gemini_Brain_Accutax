"""
test_ranking.py — shared count + direction resolution for "top N" / "bottom N"
ranked-list queries (gemini_brain.utils.ranking).

This module exists specifically because two separate bugs shipped from the
same root cause: ranked reports either ignored a user-requested row count
entirely, or (even once count was fixed) had no way to honor "bottom N" /
"least" phrasing since every ORDER BY was hardcoded DESC. These tests pin
down the shared helper's behavior so neither mistake can silently return.
"""
import re

import pytest

from gemini_brain.utils.ranking import (
    extract_direction_from_text,
    extract_limit_from_text,
    order_sql,
    resolve_direction,
    resolve_limit,
    resolve_limit_and_direction,
)


# ── resolve_limit ────────────────────────────────────────────────────────────

def test_resolve_limit_passes_through_a_valid_value():
    assert resolve_limit({"limit": 5}) == 5


def test_resolve_limit_clamps_to_the_ceiling():
    assert resolve_limit({"limit": 9999}) == 50


def test_resolve_limit_clamps_to_at_least_one():
    assert resolve_limit({"limit": -3}) == 1


def test_resolve_limit_falls_back_to_default_on_junk():
    assert resolve_limit({"limit": "not a number"}) == 20


def test_resolve_limit_falls_back_to_default_when_absent():
    assert resolve_limit({}) == 20


def test_resolve_limit_honors_custom_default_and_ceiling():
    assert resolve_limit({}, default=10, ceiling=500) == 10
    assert resolve_limit({"limit": 1000}, default=10, ceiling=500) == 500


# ── resolve_direction ────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["asc", "ascending", "bottom", "lowest", "least", "smallest", "worst"])
def test_resolve_direction_recognizes_ascending_sort_order_values(value):
    assert resolve_direction({"sort_order": value}) is True


@pytest.mark.parametrize("value", ["desc", "descending", "top", "highest", "most", "largest", "best"])
def test_resolve_direction_recognizes_descending_sort_order_values(value):
    assert resolve_direction({"sort_order": value}) is False


def test_resolve_direction_defaults_to_descending_with_no_signal():
    assert resolve_direction({}) is False


def test_explicit_sort_order_wins_over_conflicting_query_text():
    """A structured param, once set, should never be second-guessed by sniffing text."""
    assert resolve_direction({"sort_order": "desc"}, "show me the bottom 5 customers") is False
    assert resolve_direction({"sort_order": "asc"}, "top 5 customers") is True


@pytest.mark.parametrize("phrase", [
    "bottom 5 customers overdue 90 days",
    "who are our least profitable projects",
    "show the lowest 3 vendors by spend",
    "worst performing cost centers",
    "smallest invoices this month",
])
def test_resolve_direction_sniffs_ascending_from_free_text(phrase):
    assert resolve_direction({}, phrase) is True


@pytest.mark.parametrize("phrase", [
    "top 5 customers overdue 90 days",
    "highest grossing vendors",
    "our most profitable projects",
    "show the largest invoices",
    "best customers this year",
    "who are our customers",  # no ranking hint at all — must default, not guess
])
def test_resolve_direction_sniffs_descending_or_default_from_free_text(phrase):
    assert resolve_direction({}, phrase) is False


def test_resolve_limit_and_direction_combines_both():
    limit, ascending = resolve_limit_and_direction({"limit": 5, "sort_order": "asc"})
    assert (limit, ascending) == (5, True)


# ── order_sql ────────────────────────────────────────────────────────────────

def test_order_sql_maps_bool_to_keyword():
    assert order_sql(True) == "ASC"
    assert order_sql(False) == "DESC"


# ── extract_limit_from_text / extract_direction_from_text ──────────────────────
# These back the empty-result SQL verifier (router.rules), which has no
# structured params dict — only the user's raw query text — so they must work
# directly off text using a rule's own compiled patterns.

_TOP_N_PATTERNS = [re.compile(r"(?:top|bottom|least|lowest|worst)\s+(\d+)\s+vendors?", re.IGNORECASE)]


def test_extract_limit_from_text_recovers_the_count():
    assert extract_limit_from_text("show me the top 5 vendors", _TOP_N_PATTERNS, default=10) == 5


def test_extract_limit_from_text_recovers_count_for_bottom_phrasing_too():
    assert extract_limit_from_text("show me the bottom 3 vendors", _TOP_N_PATTERNS, default=10) == 3


def test_extract_limit_from_text_falls_back_to_default_without_a_match():
    assert extract_limit_from_text("show me our vendors", _TOP_N_PATTERNS, default=10) == 10


def test_extract_limit_from_text_falls_back_to_default_on_non_string():
    assert extract_limit_from_text(None, _TOP_N_PATTERNS, default=10) == 10


def test_extract_direction_from_text_detects_ascending_phrasing():
    assert extract_direction_from_text("bottom 5 vendors") is True
    assert extract_direction_from_text("least profitable vendors") is True


def test_extract_direction_from_text_defaults_to_descending():
    assert extract_direction_from_text("top 5 vendors") is False
    assert extract_direction_from_text("show me our vendors") is False
    assert extract_direction_from_text(None) is False
