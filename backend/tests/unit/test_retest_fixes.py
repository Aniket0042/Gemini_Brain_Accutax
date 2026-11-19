"""Fixes from the 19 Nov re-test of the easy and medium VAT questions: the review card names every reverse-charge
supplier, a bill list carries each bill's reverse-charge VAT, and the VAT 201 export says its statuses in words."""
import datetime as dt
from types import SimpleNamespace

from gemini_brain.agent import preview
from gemini_brain.semantic import list_documents, query_metrics
from gemini_brain.semantic.catalog import parse_meta
from gemini_brain.vat_review.blocks import review_blocks

_AUTO = ["organization_id", "organization_name", "currency"]
CATALOG = parse_meta({"cubes": [{
    "name": "purchases", "type": "view", "meta": {"kind": "flow", "time_dimension": "purchases.document_date"},
    "measures": [{"name": f"purchases.{m}"} for m in ("net_purchases", "input_vat", "reverse_charge_vat")],
    "dimensions": [{"name": f"purchases.{d}"} for d in _AUTO + ["document_number", "document_date", "vendor_name"]],
}]})


def _review(reverse_charge):
    return [{"tool": "review_vat", "result": {"side": "purchases", "mode": "anomalies", "groups": [], "findings": [],
                                              "notes": [], "reverse_charge": reverse_charge}}]


def test_the_review_card_names_every_reverse_charge_supplier():
    rc = [{"organization_id": 10182, "organization_name": "Summit Advisory Partners LLC", "currency": "AED",
           "candidates": {"bills": 1, "net": 500.0, "self_assessed_vat": 25.0,
                          "by_supplier": [{"vendor_name": "New Overseas Ltd", "country": "GB", "bills": 1, "net": 500.0,
                                           "self_assessed_vat": 25.0}]},
           "already_reverse_charged": {"bills": 11, "net": 253508.0, "self_assessed_vat": 12675.4, "by_supplier": [
               {"vendor_name": "Harrington Consulting Ltd (UK)", "country": "IN", "bills": 5, "net": 191492.0,
                "self_assessed_vat": 9574.6},
               {"vendor_name": "LinkedIn Ireland Unlimited Company", "country": "IN", "bills": 6, "net": 62016.0,
                "self_assessed_vat": 3100.8}]}}]
    card = review_blocks(_review(rc))[0]["reverse_charge"][0]
    assert [(r["supplier"], r["bills"], r["state"]) for r in card["rows"]] == [
        ("New Overseas Ltd", 1, "to_confirm"), ("Harrington Consulting Ltd (UK)", 5, "already"),
        ("LinkedIn Ireland Unlimited Company", 6, "already")]
    assert (card["net"], card["vat"]) == (254008.0, 12700.4)


def test_no_reverse_charge_section_when_there_is_none():
    rc = [{"organization_id": 1, "candidates": {"by_supplier": []}, "already_reverse_charged": None}]
    assert review_blocks(_review(rc))[0]["reverse_charge"] == []
    assert review_blocks(_review(None))[0]["reverse_charge"] == []


def _run(monkeypatch, rows):
    monkeypatch.setattr(list_documents, "get_catalog", lambda *a, **k: CATALOG)
    monkeypatch.setattr(query_metrics.periods, "today_in", lambda tz: dt.date(2026, 11, 19))
    monkeypatch.setattr(list_documents.cube_client, "load", lambda *a, **k: SimpleNamespace(rows=rows, last_refresh_time=None))
    return list_documents.run({"type": "bills"}, organization_ids=[10182], subject="t", deadline=1e12)


def test_a_bill_list_carries_each_bills_reverse_charge_vat(monkeypatch):
    out = _run(monkeypatch, [
        {"purchases.document_number": "LINK-2600643", "purchases.net_purchases": 7052.0, "purchases.input_vat": 0.0,
         "purchases.reverse_charge_vat": 352.6},
        {"purchases.document_number": "B-1", "purchases.net_purchases": 1000.0, "purchases.input_vat": 50.0,
         "purchases.reverse_charge_vat": 0.0}])
    assert [r.get("reverse_charge_vat") for r in out["rows"]] == ["352.6", "0.0"] or \
        [float(r["reverse_charge_vat"]) for r in out["rows"]] == [352.6, 0.0]


def test_a_list_of_ordinary_bills_has_no_reverse_charge_column(monkeypatch):
    out = _run(monkeypatch, [{"purchases.document_number": "B-1", "purchases.net_purchases": 1000.0,
                              "purchases.input_vat": 50.0, "purchases.reverse_charge_vat": 0.0}])
    assert "reverse_charge_vat" not in out["rows"][0]


def test_the_vat_201_export_says_statuses_in_words():
    data = [{"tool": "vat_201_draft", "result": {"rows": [
        {"organization_id": 1, "box": "1b", "label": "x", "net": 1.0, "vat": 0.05, "status": "from_books"},
        {"organization_id": 1, "box": "3", "label": "y", "net": 0.0, "vat": 0.0, "status": "candidates_only"}]}}]
    assert [r["status"] for r in preview.export_rows(data)] == ["From books", "To confirm"]
