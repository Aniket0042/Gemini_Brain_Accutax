"""Fixes from the 19 Nov manual test of the easy reverse-charge questions: short organization names in the privacy
masking, box and article numbers in the figure check, the VAT 201 table under its card, the review's model-only note,
and reverse charge in the VAT return figures."""
from types import SimpleNamespace

import pytest

from gemini_brain.policy.verifier import _numbers_in
from gemini_brain.privacy.dictionary import Dictionary, Entry
from gemini_brain.privacy.policy import normalize_name
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault
from gemini_brain.vat_review.blocks import without_tables

ORGS = ["Al Noor Electronics Trading LLC", "Desert Rose Contracting LLC", "Pixel Forge Technologies FZ-LLC",
        "Al Shifa Medical Centre LLC", "Summit Advisory Partners LLC", "Emirates Fresh Foods Trading LLC"]


def _scrubber(extra=()):
    return Scrubber(Vault(), Dictionary([Entry(n, "ORG", normalize_name(n)) for n in ORGS] + list(extra)))


# --- privacy masking: a shortened name of a selected organization is that organization -----------------------------

@pytest.mark.parametrize("question,org", [
    ("Draft our VAT 201 return for last quarter for Al Noor", "Al Noor Electronics Trading LLC"),
    ("for AL Noor", "Al Noor Electronics Trading LLC"),
    ("fo Al Noor electronics", "Al Noor Electronics Trading LLC"),
    ("for Desert Rose", "Desert Rose Contracting LLC"),
    ("for Al Shifa", "Al Shifa Medical Centre LLC"),
    ("for pixel forge", "Pixel Forge Technologies FZ-LLC"),
    ("for Emirates Fresh", "Emirates Fresh Foods Trading LLC"),
])
def test_a_short_organization_name_becomes_that_organizations_placeholder(question, org):
    sc = _scrubber()
    sc.learn([question])
    masked = sc.text(question, partial=True)
    tokens = [t for t in masked.split() if t.startswith("[")]
    assert len(tokens) == 1 and tokens[0].startswith("[ORG_")
    assert sc.vault.rehydrate(tokens[0]) == org
    assert "Noor" not in masked and "Rose" not in masked and "Shifa" not in masked


def test_a_contact_is_still_a_contact():
    sc = _scrubber([Entry("Noor Ahmed", "CUSTOMER", "noor ahmed")])
    assert sc.text("pay Noor Ahmed today", partial=True).startswith("pay [CUSTOMER_")


def test_ordinary_words_are_left_alone():
    sc = _scrubber()
    assert sc.text("the al and the rose", partial=True) == "the al and the rose"


def test_a_run_two_organizations_share_is_not_matched():
    sc = Scrubber(Vault(), Dictionary([Entry(n, "ORG", normalize_name(n)) for n in
                                       ("Al Noor Electronics LLC", "Al Noor Foods LLC")]))
    masked = sc.text("for Al Noor", partial=True)
    assert "[ORG_" not in masked  # ambiguous: left for the model to ask which one


# --- figure check ---------------------------------------------------------------------------------------------------

def test_box_names_and_article_numbers_are_not_figures():
    assert _numbers_in("| 1b | Standard-rated supplies in Dubai | 541,043.00 | 27,052.15 |") == [
        ("541,043.00", 541043.0), ("27,052.15", 27052.15)]
    assert _numbers_in("blocked under Executive Regulation Article 53, in Box 3 and Box 10") == []
    assert _numbers_in("Article 59(5) of Cabinet Decision No. 52 and Decree-Law No. 8") == []
    assert ("1.2b", 1_200_000_000.0) in _numbers_in("sales of 1.2b this year")  # a real figure still counts


# --- the VAT 201 table under its card -------------------------------------------------------------------------------

def test_a_table_of_the_vat_201_boxes_is_removed():
    data = [{"tool": "vat_201_draft", "result": {"rows": [
        {"box": "1b", "net": 541043.0, "vat": 27052.15}, {"box": "14", "net": None, "vat": 17370.66}]}}]
    answer = ("Here is the draft.\n\n| Box | Net | VAT |\n|---|---|---|\n| 1b | 541,043.00 | 27,052.15 |\n"
              "| 14 | – | 17,370.66 |\n\nDue 28 Oct 2026.")
    out = without_tables(answer, data)
    assert "|" not in out and "Here is the draft." in out and "Due 28 Oct 2026." in out


# --- the review's note for the model is not on the card -------------------------------------------------------------

def test_the_reverse_charge_note_is_not_among_the_card_notes(monkeypatch):
    from gemini_brain.vat_review import tool as review_tool
    from gemini_brain.vat_review.scan import Scan
    monkeypatch.setattr(review_tool, "_has_trn", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_country", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_rc", lambda *a, **k: True)
    bill = {"document_number": "F-1", "document_id": "F-1", "document_date": "2026-08-01", "document_type": "BILL",
            "status": "ACCEPTED", "vendor_name": "Overseas Ltd", "net_purchases": 100.0, "input_vat": 0.0,
            "vendor_country_code": "GB", "vendor_trn": ""}
    monkeypatch.setattr(review_tool, "scan_bills", lambda *a, **k: Scan(bills=[dict(bill)]))
    res = review_tool.run({"mode": "anomalies", "rules": ["rcm_candidate"],
                           "period": {"start": "2026-07-01", "end": "2026-09-30"}},
                          organization_ids=[7], subject="t", deadline=1e12)
    assert "reverse_charge_note" in res
    assert not any("reverse_charge holds" in n for n in res["notes"])


# --- reverse charge in the VAT return figures ---------------------------------------------------------------------

def test_the_vat_return_figures_include_reverse_charge_and_keep_the_net(monkeypatch):
    import datetime as dt
    from gemini_brain.vat_return import tool as vr

    def load(query, *, organization_ids, subject, deadline):
        if "purchases.reverse_charge_net" in query.get("measures", []):
            return SimpleNamespace(rows=[{"purchases.reverse_charge_net": 75965.0}])
        return SimpleNamespace(rows=[{"vat.output_vat": 27052.15, "vat.input_vat": 9681.49,
                                      "vat.net_vat_payable": 17370.66, "vat.standard_rated_sales": 541043.0}])

    monkeypatch.setattr(vr.cube_client, "load", load)
    f = vr._figures(10176, dt.date(2026, 7, 1), dt.date(2026, 9, 30), "t", 1e12)
    assert f["reverse_charge_vat"] == 3798.25
    assert f["output_vat"] == 30850.40 and f["input_vat"] == 13479.74
    assert f["net_vat_payable"] == 17370.66


def test_the_vat_return_figures_without_the_measure_are_unchanged(monkeypatch):
    import datetime as dt
    from gemini_brain.vat_return import tool as vr

    def load(query, *, organization_ids, subject, deadline):
        if "purchases.reverse_charge_net" in query.get("measures", []):
            raise vr.CubeError("unknown member")
        return SimpleNamespace(rows=[{"vat.output_vat": 100.0, "vat.input_vat": 40.0, "vat.net_vat_payable": 60.0,
                                      "vat.standard_rated_sales": 2000.0}])

    monkeypatch.setattr(vr.cube_client, "load", load)
    f = vr._figures(7, dt.date(2026, 7, 1), dt.date(2026, 9, 30), "t", 1e12)
    assert (f["output_vat"], f["input_vat"], f["reverse_charge_vat"]) == (100.0, 40.0, 0.0)
