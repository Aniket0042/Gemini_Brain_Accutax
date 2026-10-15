"""VAT review: the card under the answer, the law questions, and the Excel export of findings."""
import json
import zlib
from types import SimpleNamespace

import pytest

from gemini_brain.agent import links, preview
from gemini_brain.vat_review import blocks as vb
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review import tool


@pytest.fixture(autouse=True)
def _no_cube_catalog(monkeypatch):
    monkeypatch.setattr(tool, "_has_trn", lambda *a, **k: False)  # no Cube in unit tests


def bill(number, vendor="Acme", date="2026-03-01", net=1000.0, vat=50.0, id_=None):
    return {"document_number": number, "document_id": id_ or str(zlib.crc32(number.encode()) % 100000), "document_date": date, "document_type": "BILL",
            "status": "ACCEPTED", "vendor_name": vendor, "net_purchases": net, "input_vat": vat, "organization_name": "Org A",
            "currency": "AED"}


def fake_cube(monkeypatch, all_bills):
    def load(query, *, organization_ids, subject, deadline):
        a, b = query["timeDimensions"][0]["dateRange"]
        rows = [{f"purchases.{k}": v for k, v in x.items()} for x in all_bills if a <= x["document_date"] <= b][:500]
        return SimpleNamespace(rows=rows)

    monkeypatch.setattr(scan_mod.cube_client, "load", load)


def review(monkeypatch, bills, **params):
    fake_cube(monkeypatch, bills)
    return tool.run({"period": {"start": "2026-01-01", "end": "2026-12-31"}, **params}, organization_ids=[7], subject="t",
                    deadline=1e12)


BILLS = ([bill(f"C{i}", vendor=f"S{i}", net=1000.0 + 13 * i, vat=round((1000.0 + 13 * i) * 0.05, 2), date=f"2026-02-{i + 1:02d}")
          for i in range(8)]
         + [bill("D-1", id_="1"), bill("D-1", id_="2", date="2026-04-01"), bill("X-1", vendor="Odd", net=7186.0, vat=1293.48)])


def test_the_card_lists_groups_and_findings_in_plain_words(monkeypatch):
    result = review(monkeypatch, BILLS)
    [card] = vb.review_blocks([{"tool": "review_vat", "input": {}, "result": result}], {"X-1": "https://app/bills/9"})
    assert card["type"] == "vat_review" and card["bills_checked"] == 11
    assert card["summary"]["duplicate_groups"] == 1 and card["summary"]["findings"] == 1 and card["summary"]["high"] == 1
    dup = next(i for i in card["items"] if i["kind"] == "duplicate")
    assert dup["title"] == "Same bill number twice" and dup["level_word"] == "Sure" and dup["count"] == 2
    assert dup["extra_amount"] == 1000.0 and [b["number"] for b in dup["bills"]] == ["D-1", "D-1"]
    odd = next(i for i in card["items"] if i["kind"] == "finding")
    assert odd["title"] == "VAT is not 5% or 0%" and odd["level_word"] == "High" and "18.0%" in odd["why"]
    assert odd["bills"][0]["url"] == "https://app/bills/9"
    assert "vat_rate_not_standard" not in json.dumps(card)  # no rule names on the card
    json.dumps(card)


def test_a_long_group_lists_a_few_bills_and_counts_the_rest():
    group = {"rule": "same_total_near_date", "confidence": "medium", "why": "w", "organization_name": "O", "extra_amount": 5.0,
             "bills": [{"document_number": f"N{i}", "document_date": "2026-03-01", "net_purchases": 1.0} for i in range(9)]}
    [card] = vb.review_blocks([{"tool": "review_vat", "result": {"groups": [group], "mode": "duplicates"}}])
    item = card["items"][0]
    assert item["count"] == 9 and len(item["bills"]) == vb.BILLS_PER_ITEM and item["more"] == 3


def test_other_tools_give_no_card():
    assert vb.review_blocks([{"tool": "list_documents", "result": {"rows": []}}]) == []
    assert vb.review_blocks([]) == []


def test_a_pattern_is_on_the_card(monkeypatch):
    zero = [bill(f"Z{i}", vendor=f"S{i}", net=1000.0 + 7 * i, vat=0.0) for i in range(20)]
    [card] = vb.review_blocks([{"tool": "review_vat", "result": review(monkeypatch, zero, mode="anomalies")}])
    assert card["items"] == [] and "20 of 20 bills" in card["patterns"][0]


def test_the_preview_adds_the_card_and_survives_a_bad_result(monkeypatch):
    result = review(monkeypatch, BILLS)
    cards = preview._review_cards([{"tool": "review_vat", "input": {}, "result": result}])
    assert [c["type"] for c in cards] == ["vat_review"]
    assert preview._review_cards([{"tool": "review_vat", "result": {"groups": "not a list"}}]) == []


# --- law questions ------------------------------------------------------------------------------------------------

def test_law_questions_follow_the_rules_that_fired(monkeypatch):
    out = review(monkeypatch, BILLS)
    questions = [q["question"] for q in out["law_questions"]]
    assert len(questions) == len(set(questions)) and 1 <= len(questions) <= tool.MAX_LAW_QUESTIONS
    assert "standard rate of tax" in questions[0]  # the high-severity finding comes first
    assert any("recovery of input tax" in q for q in questions)  # the duplicate


def test_rules_that_are_not_law_add_no_question(monkeypatch):
    base = [bill(f"U{i}", net=1000.0 + i * 10, vat=round((1000.0 + i * 10) * 0.05, 2), date=f"2026-01-{i + 1:02d}") for i in range(8)]
    out = review(monkeypatch, base + [bill("BIG", net=9001.0, vat=450.05)], mode="anomalies")
    assert out["findings_by_rule"] == {"large_for_supplier": 1} and out["law_questions"] == []


def test_no_findings_no_law_questions(monkeypatch):
    clean = [bill(f"C{i}", vendor=f"S{i}", net=1000.0 + 13 * i, vat=round((1000.0 + 13 * i) * 0.05, 2)) for i in range(8)]
    assert review(monkeypatch, clean)["law_questions"] == []


# --- Excel export of findings -------------------------------------------------------------------------------------

def test_findings_export_to_excel_and_chart_from_the_rows(monkeypatch):
    from gemini_brain.artifacts.attach import attach_delivery
    result = review(monkeypatch, BILLS)
    data = [{"tool": "review_vat", "input": {}, "result": result}]
    rows = preview.export_rows(data)
    assert rows and {"kind", "rule", "level", "supplier", "document_number", "amount", "why"} <= set(rows[0])
    delivery = preview.delivery_for("Export these findings to Excel")
    blocks = attach_delivery("Export these findings to Excel", rows, [], status="ok", user_id=1, organization_id=7,
                             session_id=None, org_name="Org A", answer_text="x", delivery=delivery)
    assert any(b.get("type") == "artifact" for b in blocks)


def test_bill_links_cover_findings_too(monkeypatch):
    monkeypatch.setattr(links.settings, "accutax_app_url", "https://app.example")
    result = review(monkeypatch, BILLS)
    found = links.document_links([{"tool": "review_vat", "result": result}])
    assert "X-1" in found and found["X-1"].startswith("https://app.example/")


# --- the answer without a table -----------------------------------------------------------------------------------

def test_tables_are_taken_out_of_a_review_answer():
    text = "Three groups.\n\n| Group | Amount |\n|---|---|\n| 1 | 3,300.00 |\n| 2 | 1,850.00 |\n\n- Group 1 is sure.\n\nNext step: check."
    assert vb.without_tables(text) == "Three groups.\n\n- Group 1 is sure.\n\nNext step: check."


def test_text_without_a_table_is_unchanged_and_never_empty():
    assert vb.without_tables("Found 3.\n\n- one\n- two") == "Found 3.\n\n- one\n- two"
    assert vb.without_tables("") == ""
    assert preview._without_tables("| a |\n|---|") == "| a |\n|---|"  # nothing left: keep the answer rather than blank it


def test_rule_names_are_put_into_words():
    text = 'Found 50 findings, all "future_date" (medium). Also vat_zero and `large_for_supplier` ones, not vat_zero_x.'
    out = vb.plain_rule_names(text)
    assert out == "Found 50 findings, all dated in the future (medium). Also no VAT recorded and far above the supplier's usual bill ones, not vat_zero_x."
    assert vb.plain_rule_names("") == "" and vb.plain_rule_names("Nothing to change.") == "Nothing to change."
    assert set(vb.PLAIN) == set(vb.TITLES) - set()  # every rule has a plain phrase
