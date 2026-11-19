"""Tests for reverse charge (rcm_candidate) and foreign VAT (foreign_vat_charged) review rules."""
import datetime as dt
from types import SimpleNamespace

import pytest

from gemini_brain.vat_review import anomalies as an
from gemini_brain.vat_review import tool

TODAY = dt.date(2026, 10, 15)
VALID_TRN = "100234567800003"


def bill(number="B-1", vendor="Acme", date="2026-03-01", net=1000.0, vat=50.0, status="ACCEPTED",
         id_=None, country=None, trn=None):
    b = {
        "document_number": number,
        "document_id": id_ or number,
        "document_date": date,
        "document_type": "BILL",
        "status": status,
        "vendor_name": vendor,
        "net_purchases": net,
        "input_vat": vat,
        "organization_name": "Org A",
        "currency": "AED",
    }
    if country is not None:
        b["vendor_country_code"] = country
    if trn is not None:
        b["vendor_trn"] = trn
    return b


def clean(n=10):
    return [bill(f"C{i}", vendor=f"Supplier {i}", net=1000.0 + 13 * i, vat=round((1000.0 + 13 * i) * 0.05, 2),
                 date=f"2026-02-{i + 1:02d}", country="AE", trn=VALID_TRN) for i in range(n)]


def rules(findings):
    return sorted(f["rule"] for f in findings)


def test_uae_supplier_no_vat_raises_vat_zero_not_rcm_candidate():
    b = bill("B-1", country="AE", net=1000.0, vat=0.0)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="purchases")
    assert rules(findings) == ["vat_zero"]
    assert not patterns


def test_foreign_supplier_no_vat_raises_rcm_candidate_not_vat_zero():
    b = bill("B-1", country="IN", net=1000.0, vat=0.0)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="purchases")
    assert rules(findings) == ["rcm_candidate"]
    assert not patterns
    f = findings[0]
    assert f["details"]["self_assessed_vat"] == 50.0
    assert f["details"]["country"] == "IN"
    assert f["details"]["net"] == 1000.0
    assert "Box 3" in f["why"] and "Box 10" in f["why"]


def test_foreign_supplier_with_vat_and_no_trn_raises_foreign_vat_charged():
    b = bill("B-1", country="IN", net=1000.0, vat=50.0, trn=None)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="purchases")
    assert rules(findings) == ["foreign_vat_charged"]
    assert "vat_rate_not_standard" not in rules(findings)
    assert "supplier_trn_invalid" not in rules(findings)
    f = findings[0]
    assert f["details"]["country"] == "IN"
    assert f["details"]["vat"] == 50.0
    assert "Foreign VAT is not UAE input VAT" in f["why"]


def test_foreign_supplier_with_vat_and_valid_trn_has_no_foreign_vat_charged():
    b = bill("B-1", country="IN", net=1000.0, vat=50.0, trn=VALID_TRN)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="purchases")
    assert "foreign_vat_charged" not in rules(findings)
    assert not findings


def test_bill_without_vendor_country_code_matches_old_behaviour():
    b_without = bill("B-1", country=None, net=1000.0, vat=0.0)
    b_ae = bill("B-1", country="AE", net=1000.0, vat=0.0)
    findings_without, patterns_without = an.find_anomalies(clean() + [b_without], TODAY, side="purchases")
    findings_ae, patterns_ae = an.find_anomalies(clean() + [b_ae], TODAY, side="purchases")
    assert rules(findings_without) == rules(findings_ae) == ["vat_zero"]


def test_vendor_credit_foreign_supplier_no_vat_negative_amount_no_crash():
    b = bill("VC-1", country="IN", net=-500.0, vat=0.0)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="purchases")
    assert rules(findings) == ["rcm_candidate"]
    f = findings[0]
    assert f["details"]["net"] == -500.0
    assert f["details"]["self_assessed_vat"] == -25.0


def test_systemic_rcm_candidate_pattern_when_most_bills_foreign_no_vat():
    bills = [bill(f"B-{i}", country="IN", net=1000.0 + i, vat=0.0) for i in range(9)]
    bills.append(bill("B-9", country="AE", net=500.0, vat=25.0, trn=VALID_TRN))
    findings, patterns = an.find_anomalies(bills, TODAY, side="purchases")
    assert not findings
    assert len(patterns) == 1
    assert patterns[0]["rule"] == "rcm_candidate"
    assert patterns[0]["bills"] == 9
    assert patterns[0]["of"] == 10
    assert "imported services, reverse charge" in patterns[0]["text"]


def test_sales_side_with_foreign_customer_no_rcm_candidate():
    b = bill("INV-1", country="IN", net=1000.0, vat=0.0)
    findings, patterns = an.find_anomalies(clean() + [b], TODAY, side="sales")
    assert "rcm_candidate" not in rules(findings)
    assert not any(p.get("rule") == "rcm_candidate" for p in patterns)


def test_tool_run_passes_with_country_to_scan_bills(monkeypatch):
    seen = {}

    def fake_scan_bills(org, start, end, *, subject, deadline, with_trn=False, with_country=False, side="purchases",
                        with_rc=False):
        seen["with_country"] = with_country
        seen["with_trn"] = with_trn
        return SimpleNamespace(bills=[], complete=True, queries=1, why_incomplete=None)

    monkeypatch.setattr(tool, "_has_trn", lambda *a, **k: False)
    monkeypatch.setattr(tool, "_has_country", lambda *a, **k: True)
    monkeypatch.setattr(tool, "_has_rc", lambda *a, **k: False)
    monkeypatch.setattr(tool, "scan_bills", fake_scan_bills)

    tool.run({"period": {"start": "2026-01-01", "end": "2026-03-31"}, "side": "purchases"},
             organization_ids=[7], subject="test", deadline=1e12)
    assert seen["with_country"] is True
