"""Bills with a reverse-charge tax code (already reverse-charged in the books) and the review's reverse-charge totals."""
import datetime as dt
from types import SimpleNamespace

from gemini_brain.vat_return import form201
from gemini_brain.vat_review import anomalies
from gemini_brain.vat_review import tool as review_tool
from gemini_brain.vat_review.scan import Scan, _query

D = dt.date
TODAY = D(2026, 10, 16)


def bill(number, net, vat=0.0, country="GB", trn="", rc_net=None, rc_vat=0.0, vendor=None):
    b = {"document_number": number, "document_id": number, "document_date": "2026-08-01", "document_type": "BILL",
         "status": "ACCEPTED", "vendor_name": vendor or f"Supplier {number}", "net_purchases": net, "input_vat": vat,
         "vendor_country_code": country, "vendor_trn": trn}
    if rc_net is not None:
        b["reverse_charge_net"], b["reverse_charge_vat_recorded"] = rc_net, rc_vat
    return b


# --- rules ----------------------------------------------------------------------------------------------------------

def test_a_coded_bill_is_not_a_candidate_and_raises_nothing():
    coded = bill("RC-1", 1000.0, rc_net=1000.0)
    assert anomalies.reverse_charged(coded)
    assert anomalies.rcm_candidates([coded]) == []
    found, patterns = anomalies.find_anomalies([coded], TODAY)
    assert not [f for f in found if f["rule"] in ("rcm_candidate", "vat_zero", "foreign_vat_charged")]
    assert not patterns


def test_an_uncoded_foreign_bill_is_still_a_candidate():
    assert [f["bill"]["document_number"] for f in anomalies.rcm_candidates([bill("F-1", 500.0, rc_net=0.0)])] == ["F-1"]


def test_rcm_totals_count_every_candidate_per_supplier():
    bills = ([bill(f"A-{i}", 1000.0, vendor="Alpha") for i in range(8)]
             + [bill(f"B-{i}", 300.0, vendor="Beta") for i in range(4)])
    t = anomalies.rcm_totals(anomalies.rcm_candidates(bills))
    assert t["bills"] == 12 and t["net"] == 9200.0 and t["self_assessed_vat"] == 460.0
    assert [(s["vendor_name"], s["bills"], s["net"]) for s in t["by_supplier"]] == [("Alpha", 8, 8000.0),
                                                                                   ("Beta", 4, 1200.0)]


def test_the_scan_asks_for_the_reverse_charge_measures_only_when_told():
    assert "purchases.reverse_charge_net" in _query(TODAY, TODAY, with_rc=True)["measures"]
    assert "purchases.reverse_charge_net" not in _query(TODAY, TODAY)["measures"]
    assert "reverse_charge_net" not in str(_query(TODAY, TODAY, side="sales", with_rc=True)["measures"])


# --- the review's totals --------------------------------------------------------------------------------------------

def _review_env(monkeypatch, bills):
    monkeypatch.setattr(review_tool, "_has_trn", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_country", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_rc", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "scan_bills", lambda *a, **k: Scan(bills=[dict(b) for b in bills]))


def test_the_review_gives_totals_of_all_candidates_beyond_the_listed_rows(monkeypatch):
    bills = [bill(f"F-{i}", 1000.0 + i) for i in range(11)] + [bill("RC-1", 2000.0, rc_net=2000.0)]
    bills += [bill(f"L-{i}", 100.0, 5.0, country="AE", trn="100123456700003") for i in range(30)]
    _review_env(monkeypatch, bills)
    res = review_tool.run({"mode": "anomalies", "rules": ["rcm_candidate", "foreign_vat_charged"],
                           "period": {"start": "2026-07-01", "end": "2026-09-30"}},
                          organization_ids=[7], subject="t", deadline=1e12)
    rc = res["reverse_charge"][0]
    assert rc["candidates"]["bills"] == 11
    assert rc["candidates"]["net"] == round(sum(1000.0 + i for i in range(11)), 2)
    done = rc["already_reverse_charged"]
    assert (done["bills"], done["net"], done["self_assessed_vat"]) == (1, 2000.0, 100.0)
    assert [(s["vendor_name"], s["bills"], s["net"]) for s in done["by_supplier"]] == [("Supplier RC-1", 1, 2000.0)]


def test_no_reverse_charge_totals_on_a_review_of_other_rules(monkeypatch):
    _review_env(monkeypatch, [bill("F-1", 100.0)])
    res = review_tool.run({"mode": "anomalies", "rules": ["vat_rate_not_standard"],
                           "period": {"start": "2026-07-01", "end": "2026-09-30"}},
                          organization_ids=[7], subject="t", deadline=1e12)
    assert "reverse_charge" not in res


# --- the VAT 201 draft ----------------------------------------------------------------------------------------------

def _env(monkeypatch, bills, has_rc=True):
    sales = [{"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 10000.0,
              "sales.output_vat": 500.0}]

    def load(query, *, organization_ids, subject, deadline):
        if any(m.startswith("vat_settings.") for m in query.get("dimensions", [])):
            return SimpleNamespace(rows=[])
        return SimpleNamespace(rows=sales)

    monkeypatch.setattr(form201.cube_client, "load", load)
    monkeypatch.setattr(form201, "scan_bills", lambda *a, **k: Scan(bills=bills))
    monkeypatch.setattr(form201.semantic_periods, "today_in", lambda tz: TODAY)
    monkeypatch.setattr(review_tool, "_has_trn", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_country", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_rc", lambda *a, **k: has_rc)


def _boxes(res):
    return {b["box"]: b for b in res["organizations"][0]["boxes"]}


def test_coded_bills_fill_box_3_and_box_10_from_the_books(monkeypatch):
    bills = [bill("RC-1", 75965.0, rc_net=75965.0),
             bill("L-1", 193629.72, 9681.49, country="AE", trn="100123456700003", rc_net=0.0),
             bill("F-1", 1000.0, rc_net=0.0)]
    _env(monkeypatch, bills)
    res = form201.run({}, organization_ids=[10176], subject="t", deadline=1e12)
    b = _boxes(res)
    assert (b["3"]["net"], b["3"]["vat"], b["3"]["status"]) == (75965.0, 3798.25, "from_books")
    assert (b["10"]["net"], b["10"]["vat"], b["10"]["status"]) == (75965.0, 3798.25, "from_books")
    assert b["8"]["vat"] == 4298.25 and b["9"]["vat"] == 9681.49 and b["11"]["vat"] == 13479.74
    assert b["14"]["vat"] == round(4298.25 - 13479.74, 2)
    assert res["organizations"][0]["reverse_charge"]["candidate_count"] == 1  # only the uncoded foreign bill
    assert any("reverse-charge tax code" in n for n in res["notes"])


def test_vat_recorded_on_a_coded_line_goes_to_box_10_not_box_9(monkeypatch):
    _env(monkeypatch, [bill("RC-1", 1000.0, 50.0, rc_net=1000.0, rc_vat=50.0)])
    b = _boxes(form201.run({}, organization_ids=[7], subject="t", deadline=1e12))
    assert b["9"]["vat"] == 0.0 and b["10"]["vat"] == 50.0


def test_a_cube_without_reverse_charge_codes_keeps_box_3_as_candidates(monkeypatch):
    _env(monkeypatch, [bill("F-1", 1000.0)], has_rc=False)
    b = _boxes(form201.run({}, organization_ids=[7], subject="t", deadline=1e12))
    assert (b["3"]["net"], b["3"]["status"]) == (0.0, "candidates_only")
