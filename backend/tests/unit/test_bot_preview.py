import pytest
from unittest.mock import patch

from gemini_brain.bots.preview import try_period
from gemini_brain.bots.schema import BotDraft, Schedule


def test_try_period_mocked():
    draft = BotDraft(
        org_ids=[101],
        watches=["duplicate_bill", "vat_rate_wrong", "sales_vat_wrong"],
        schedules=[Schedule(kind="weekly", day="mon", time="08:00")],
    )

    fake_review_purchases = {
        "side": "purchases",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "bills_checked": 45,
        "groups_found": 2,
        "findings_found": 3,
        "groups_by_rule": {"same_number": 2},
        "findings_by_rule": {"vat_rate_not_standard": 3},
    }

    fake_review_sales = {
        "side": "sales",
        "period": {"start": "2026-09-01", "end": "2026-09-30"},
        "bills_checked": 12,
        "groups_found": 0,
        "findings_found": 1,
        "groups_by_rule": {},
        "findings_by_rule": {"vat_zero": 1},
    }

    def fake_review_run(req, **kwargs):
        if req["side"] == "purchases":
            assert kwargs.get("exclude") == frozenset({"ex_1"})
            return fake_review_purchases
        return fake_review_sales

    def fake_return_run(req, **kwargs):
        return {"organizations": [{"organization_id": 101, "net_vat": 1500.0}]}

    with patch("gemini_brain.vat_review.tool.run", side_effect=fake_review_run) as mock_rev, \
         patch("gemini_brain.vat_return.tool.run", side_effect=fake_return_run) as mock_ret:

        result = try_period(
            draft,
            preset="last_month",
            subject="bot-preview:18",
            exclude=frozenset({"ex_1"}),
        )

        assert mock_rev.call_count == 2
        assert mock_ret.call_count == 1

        assert result["bills_checked"] == 45
        assert result["invoices_checked"] == 12
        assert result["groups_found"] == 2
        assert result["findings_found"] == 4
        assert result["period"] == {"start": "2026-09-01", "end": "2026-09-30"}

        # Check by_watch breakdown
        assert result["by_watch"]["duplicate_bill"] == 2
        assert result["by_watch"]["vat_rate_wrong"] == 3
        assert result["by_watch"]["sales_vat_wrong"] == 1

        assert result["would_send"]["alerts"] == 6  # 2 groups + 4 findings
        assert result["would_send"]["digests"] == 5  # weekly draft = 5 runs/month


def test_try_period_purchases_only_skips_sales():
    draft = BotDraft(
        org_ids=[101],
        watches=["duplicate_bill"],
        schedules=[],
    )

    fake_review = {
        "side": "purchases",
        "period": {"start": "2026-10-01", "end": "2026-10-31"},
        "bills_checked": 10,
        "groups_found": 1,
        "findings_found": 0,
        "groups_by_rule": {"same_number": 1},
        "findings_by_rule": {},
    }

    with patch("gemini_brain.vat_review.tool.run", return_value=fake_review) as mock_rev, \
         patch("gemini_brain.vat_return.tool.run", return_value={}):

        result = try_period(draft, preset="this_quarter", subject="bot-preview:18")

        assert mock_rev.call_count == 1
        assert result["bills_checked"] == 10
        assert result["invoices_checked"] == 0
        assert result["by_watch"]["duplicate_bill"] == 1
