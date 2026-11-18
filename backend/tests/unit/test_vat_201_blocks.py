"""Unit tests for vat_201 blocks under chat answers (VAT 201 Card)."""
import json

from gemini_brain.agent import preview
from gemini_brain.vat_return import blocks as fb


def sample_tool_result():
    return {
        "today": "2026-10-16",
        "organizations": [
            {
                "organization_id": 24,
                "emirate": "Dubai",
                "period": {"start": "2026-07-01", "end": "2026-09-30"},
                "frequency": "quarterly",
                "frequency_source": "assumed",
                "due_date": "2026-10-28",
                "boxes": [
                    {"box": "1b", "label": "Standard-rated supplies in Dubai", "net": 10000.0, "vat": 500.0, "status": "from_books"},
                    {"box": "2", "label": "Tax refunds provided to tourists", "net": None, "vat": None, "status": "not_in_books"},
                    {"box": "3", "label": "Supplies subject to the reverse charge provisions", "net": 0.0, "vat": 0.0, "status": "candidates_only"},
                    {"box": "4", "label": "Zero-rated supplies", "net": 2000.0, "vat": None, "status": "from_books"},
                    {"box": "5", "label": "Exempt supplies", "net": 1000.0, "vat": None, "status": "from_books"},
                    {"box": "6", "label": "Goods imported into the UAE", "net": None, "vat": None, "status": "not_in_books"},
                    {"box": "7", "label": "Adjustments to goods imported into the UAE", "net": None, "vat": None, "status": "not_in_books"},
                    {"box": "8", "label": "Totals", "net": None, "vat": 500.0, "status": "from_books"},
                    {"box": "9", "label": "Standard-rated expenses", "net": 4000.0, "vat": 200.0, "status": "approximate"},
                    {"box": "10", "label": "Supplies subject to the reverse charge provisions", "net": 0.0, "vat": 0.0, "status": "candidates_only"},
                    {"box": "11", "label": "Totals", "net": None, "vat": 200.0, "status": "approximate"},
                    {"box": "12", "label": "Total value of due tax for the period", "net": None, "vat": 500.0, "status": "from_books"},
                    {"box": "13", "label": "Total value of recoverable tax for the period", "net": None, "vat": 200.0, "status": "approximate"},
                    {"box": "14", "label": "Payable tax for the period", "net": None, "vat": 300.0, "status": "approximate"},
                ],
                "reverse_charge": {
                    "candidates": [
                        {"document_number": "AWS-01", "document_id": "101", "document_date": "2026-08-15",
                         "vendor_name": "Amazon", "country": "US", "net": 1000.0, "self_assessed_vat": 50.0}
                    ],
                    "candidate_count": 1,
                    "if_confirmed": {
                        "box3_net": 1000.0, "box3_vat": 50.0, "box10_net": 1000.0, "box10_vat": 50.0, "net_change": 0.0
                    },
                    "note": "Needs confirmation."
                },
                "unclassified_sales": 0.0,
                "notes": [],
            }
        ],
        "rows": [],
        "row_count": 0,
        "notes": [],
    }


def test_block_built_from_sample_result():
    data = [{"tool": "vat_201_draft", "result": sample_tool_result()}]
    urls = {"AWS-01": "https://app/bills/101"}

    blocks = fb.form201_blocks(data, urls)
    assert len(blocks) == 1
    b = blocks[0]
    assert b["type"] == "vat_201"
    assert "VAT 201 draft" in b["title"]
    assert "2026-07-01 to 2026-09-30" in b["title"]
    assert "Draft prepared by Accutax from your books" in b["disclaimer"]

    orgs = b["organizations"]
    assert len(orgs) == 1
    org = orgs[0]
    assert org["organization_id"] == 24
    assert len(org["boxes"]) == 14
    cand = org["reverse_charge"]["candidates"][0]
    assert cand["document_number"] == "AWS-01"
    assert cand["url"] == "https://app/bills/101"

    # Must be valid json
    json.dumps(blocks)


def test_no_block_when_tool_did_not_run():
    data = [{"tool": "query_metrics", "result": {"rows": []}}]
    assert fb.form201_blocks(data) == []
    assert fb.form201_blocks([]) == []
    assert fb.form201_blocks(None) == []


def test_broken_result_does_not_raise():
    bad_data = [
        {"tool": "vat_201_draft", "result": None},
        {"tool": "vat_201_draft", "result": "not a dict"},
        {"tool": "vat_201_draft", "result": {"organizations": "not a list"}},
        {"tool": "vat_201_draft", "result": {"organizations": [{"boxes": "bad"}]}},
        {"tool": "vat_201_draft", "result": {"organizations": [None]}},
    ]
    for d in bad_data:
        res = fb.form201_blocks([d])
        assert isinstance(res, list)


def test_preview_form201_cards_wrapper_never_raises(monkeypatch):
    assert preview._form201_cards([]) == []

    # If form201_blocks raises an unexpected exception, preview wrapper catches it and returns []
    def boom(*a, **kw):
        raise RuntimeError("simulated error")

    monkeypatch.setattr(fb, "form201_blocks", boom)
    assert preview._form201_cards([{"tool": "vat_201_draft"}]) == []
