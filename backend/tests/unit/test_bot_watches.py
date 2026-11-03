import pytest

from gemini_brain.bots.watches import all_watches, get, default_ids, review_calls
from gemini_brain.vat_review.tool import RULE_TEXT


def test_all_rules_exist_in_rule_text():
    for w in all_watches():
        for rule in w.rules:
            assert rule in RULE_TEXT, f"Rule {rule} in watch {w.id} not found in vat_review.tool.RULE_TEXT"


def test_get_and_unknown_key():
    w = get("duplicate_bill")
    assert w.id == "duplicate_bill"
    assert w.side == "purchases"

    with pytest.raises(KeyError):
        get("non_existent_watch")


def test_default_ids():
    defaults = default_ids()
    assert "duplicate_bill" in defaults
    assert "vat_rate_wrong" in defaults
    assert "sales_vat_wrong" in defaults
    assert "bill_missing_details" not in defaults
    assert "unusual_bill" not in defaults


def test_review_calls_standard_example():
    chosen = ["duplicate_bill", "vat_rate_wrong", "sales_vat_wrong"]
    calls = review_calls(chosen, sensitivity="balanced")
    assert len(calls) == 2

    purchases = next(c for c in calls if c["side"] == "purchases")
    assert purchases["mode"] == "all"
    expected_purchases = sorted([
        "same_number", "same_total_near_date", "similar_vendor_same_day",
        "vat_rate_not_standard", "vat_wrong_sign", "vat_without_amount"
    ])
    assert purchases["rules"] == expected_purchases

    sales = next(c for c in calls if c["side"] == "sales")
    assert sales["mode"] == "all"
    expected_sales = sorted(["vat_rate_not_standard", "vat_zero", "vat_wrong_sign"])
    assert sales["rules"] == expected_sales


def test_review_calls_fewer_sensitivity():
    chosen = ["duplicate_bill", "unusual_bill"]
    calls = review_calls(chosen, sensitivity="fewer")
    assert len(calls) == 1
    purchases = calls[0]
    # "similar_vendor_same_day" and "large_one_off_supplier" must be removed
    assert "similar_vendor_same_day" not in purchases["rules"]
    assert "large_one_off_supplier" not in purchases["rules"]
    assert "same_number" in purchases["rules"]
    assert "large_for_supplier" in purchases["rules"]


def test_review_calls_more_sensitivity():
    chosen = ["duplicate_bill"]
    calls = review_calls(chosen, sensitivity="more")
    assert len(calls) == 1
    purchases = calls[0]
    assert "round_amount" in purchases["rules"]


def test_review_calls_empty_or_one_side():
    assert review_calls([]) == []

    sales_only = review_calls(["sales_vat_wrong"])
    assert len(sales_only) == 1
    assert sales_only[0]["side"] == "sales"
