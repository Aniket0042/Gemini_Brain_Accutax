"""VAT review: odd bills (vat_review.anomalies and the anomalies mode of the tool)."""
import datetime as dt
from types import SimpleNamespace

import pytest

from gemini_brain.vat_review import anomalies as an
from gemini_brain.vat_review import scan as scan_mod
from gemini_brain.vat_review import tool

TODAY = dt.date(2026, 10, 15)


def bill(number="B-1", vendor="Acme", date="2026-03-01", net=1000.0, vat=50.0, status="ACCEPTED", id_=None):
    return {"document_number": number, "document_id": id_ or number, "document_date": date, "document_type": "BILL",
            "status": status, "vendor_name": vendor, "net_purchases": net, "input_vat": vat, "organization_name": "Org A",
            "currency": "AED"}


def rules(findings):
    return sorted(f["rule"] for f in findings)


def clean(n=10):
    """Bills that raise nothing: 5% VAT, different suppliers, plain amounts."""
    return [bill(f"C{i}", vendor=f"Supplier {i}", net=1000.0 + 13 * i, vat=round((1000.0 + 13 * i) * 0.05, 2),
                 date=f"2026-02-{i + 1:02d}") for i in range(n)]


def test_clean_bills_raise_nothing():
    assert an.find_anomalies(clean(), TODAY) == ([], [])


def test_no_bills_raise_nothing():
    assert an.find_anomalies([], TODAY) == ([], [])


# --- VAT against the amount ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("net,vat", [(1000.0, 50.0), (1000.0, 50.04), (333.33, 16.67), (20001.0, 1000.3)])
def test_five_percent_within_rounding_is_fine(net, vat):
    findings, _ = an.find_anomalies(clean() + [bill("X", net=net, vat=vat)], TODAY)
    assert findings == []


def test_other_rates_are_high_severity_with_the_numbers():
    findings, _ = an.find_anomalies(clean() + [bill("X", net=7186.0, vat=1293.48)], TODAY)
    assert rules(findings) == ["vat_rate_not_standard"] and findings[0]["severity"] == "high"
    d = findings[0]["details"]
    assert d["expected_vat"] == 359.3 and d["rate_percent"] == 18.0 and d["difference"] == 934.18
    assert "18.0%" in findings[0]["why"] and "359.30" in findings[0]["why"]


def test_vat_that_is_five_over_one_hundred_five_of_the_amount_is_flagged():
    """VAT taken from a VAT-inclusive total (5/105) is not 5% of the net amount: someone should look."""
    findings, _ = an.find_anomalies(clean() + [bill("X", net=1050.0, vat=50.0)], TODAY)
    assert rules(findings) == ["vat_rate_not_standard"]


def test_opposite_signs_and_vat_without_amount():
    findings, _ = an.find_anomalies(clean() + [bill("X", net=-500.0, vat=25.0), bill("Y", net=0.0, vat=12.0),
                                               bill("Z", net=None, vat=7.0)], TODAY)
    assert rules(findings) == ["vat_without_amount", "vat_without_amount", "vat_wrong_sign"]


def test_a_credit_note_with_negative_vat_is_fine():
    findings, _ = an.find_anomalies(clean() + [bill("CN", net=-500.0, vat=-25.0)], TODAY)
    assert findings == []


def test_some_bills_with_no_vat_are_listed_as_low():
    findings, patterns = an.find_anomalies(clean() + [bill("X", net=900.0, vat=0.0)], TODAY)
    assert rules(findings) == ["vat_zero"] and findings[0]["severity"] == "low" and patterns == []


def test_when_almost_all_bills_have_no_vat_it_is_one_pattern_not_one_finding_each():
    bills = [bill(f"Z{i}", vendor=f"S{i}", net=1000.0 + 7 * i, vat=0.0) for i in range(20)]
    findings, patterns = an.find_anomalies(bills, TODAY)
    assert findings == [] and patterns[0]["rule"] == "vat_zero" and patterns[0]["share_percent"] == 100
    assert "20 of 20 bills" in patterns[0]["text"]


# --- missing details and dates ------------------------------------------------------------------------------------

def test_missing_supplier_and_number_and_future_date():
    findings, _ = an.find_anomalies(clean() + [bill("", vendor="  "), bill("F-1", date="2026-11-01")], TODAY)
    assert rules(findings) == ["future_date", "missing_number", "missing_supplier"]
    assert next(f for f in findings if f["rule"] == "future_date")["details"]["days_ahead"] == 17


def test_today_is_not_in_the_future():
    findings, _ = an.find_anomalies(clean() + [bill("T", date="2026-10-15")], TODAY)
    assert findings == []


# --- amounts out of line ------------------------------------------------------------------------------------------

def usual(vendor="Acme", n=8, net=1000.0):
    return [bill(f"U{i}", vendor=vendor, net=net + i * 10, vat=round((net + i * 10) * 0.05, 2), date=f"2026-01-{i + 1:02d}")
            for i in range(n)]


def test_a_bill_far_above_the_suppliers_usual_is_flagged():
    findings, _ = an.find_anomalies(usual() + [bill("BIG", net=9001.0, vat=450.05)], TODAY)
    assert rules(findings) == ["large_for_supplier"]
    assert findings[0]["details"]["times_usual"] >= 8 and "usual bill" in findings[0]["why"]


def test_a_supplier_with_few_bills_is_not_judged():
    assert an.find_anomalies(usual(n=3) + [bill("BIG", net=9001.0, vat=450.05)], TODAY) == ([], [])


def test_a_modest_rise_is_not_flagged():
    assert an.find_anomalies(usual() + [bill("UP", net=1400.0, vat=70.0)], TODAY) == ([], [])


def test_round_amounts_are_flagged_only_when_rare():
    findings, _ = an.find_anomalies(clean(20) + [bill("R", net=10000.0, vat=500.0)], TODAY)
    assert rules(findings) == ["round_amount"]
    many = [bill(f"R{i}", vendor=f"S{i}", net=5000.0 + 1000 * (i % 3), vat=250.0 + 50 * (i % 3), date="2026-02-01")
            for i in range(10)]
    assert an.find_anomalies(many, TODAY)[0] == []


def test_a_large_one_off_supplier_needs_enough_bills_to_judge():
    base = [bill(f"B{i}", vendor=f"V{i % 5}", net=1000.0 + i, vat=round((1000.0 + i) * 0.05, 2)) for i in range(60)]
    findings, _ = an.find_anomalies(base + [bill("ONE", vendor="Stranger", net=50000.0 + 1, vat=2500.05)], TODAY)
    assert "large_one_off_supplier" in rules(findings)
    findings, _ = an.find_anomalies(clean(10) + [bill("ONE", vendor="Stranger", net=50001.0, vat=2500.05)], TODAY)
    assert "large_one_off_supplier" not in rules(findings)


def test_findings_come_most_serious_first_then_largest():
    findings, _ = an.find_anomalies(clean() + [bill("M", vendor="", net=100.0, vat=5.0),
                                               bill("H1", net=100.0, vat=40.0), bill("H2", net=900.0, vat=300.0)], TODAY)
    assert [f["bill"]["document_number"] for f in findings] == ["H2", "H1", "M"]


# --- the tool -----------------------------------------------------------------------------------------------------

def fake_cube(monkeypatch, all_bills):
    def load(query, *, organization_ids, subject, deadline):
        a, b = query["timeDimensions"][0]["dateRange"]
        rows = [{f"purchases.{k}": v for k, v in x.items()} for x in all_bills if a <= x["document_date"] <= b][:500]
        return SimpleNamespace(rows=rows)

    monkeypatch.setattr(scan_mod.cube_client, "load", load)


def run_tool(monkeypatch, bills, **params):
    fake_cube(monkeypatch, bills)
    return tool.run({"period": {"start": "2026-01-01", "end": "2026-12-31"}, **params}, organization_ids=[7], subject="t",
                    deadline=1e12)


def test_the_anomalies_mode_reports_counts_by_rule_and_severity(monkeypatch):
    out = run_tool(monkeypatch, clean() + [bill("X", net=7186.0, vat=1293.48), bill("", net=100.0, vat=5.0)], mode="anomalies")
    assert out["mode"] == "anomalies" and "groups" not in out
    assert out["findings_found"] == 2 and out["findings_by_rule"] == {"vat_rate_not_standard": 1, "missing_number": 1}
    assert out["findings_by_severity"] == {"high": 1, "medium": 1, "low": 0}
    assert out["rows"][0]["kind"] == "finding" and out["rows"][0]["level"] == "high" and out["rows"][0]["why"]


def test_the_all_mode_runs_both_and_rows_share_columns(monkeypatch):
    out = run_tool(monkeypatch, clean() + [bill("D-1", id_="1"), bill("D-1", id_="2", date="2026-04-01"),
                                           bill("X", vendor="Odd", net=7186.0, vat=1293.48)])
    assert out["mode"] == "all" and out["groups_found"] == 1 and out["findings_found"] == 1
    assert {r["kind"] for r in out["rows"]} == {"duplicate", "finding"}
    assert len({tuple(sorted(r)) for r in out["rows"]}) == 1


def test_one_rule_does_not_fill_the_list_while_others_wait(monkeypatch):
    bills = clean(5) + [bill(f"V{i}", vendor=f"VS{i}", net=7001.0 + i, vat=1000.0 + i) for i in range(12)]
    bills.append(bill("", vendor="NoNumber"))
    out = run_tool(monkeypatch, bills, mode="anomalies", limit=4)
    shown = [f["rule"] for f in out["findings"]]
    assert len(shown) == 4 and "missing_number" in shown and shown.count("vat_rate_not_standard") == 3
    assert any("Showing 4 of 13" in n for n in out["notes"])


def test_a_pattern_is_reported_with_its_organization(monkeypatch):
    out = run_tool(monkeypatch, [bill(f"Z{i}", vendor=f"S{i}", net=1000.0 + 7 * i, vat=0.0) for i in range(20)], mode="anomalies")
    assert out["findings_found"] == 0 and out["patterns"][0]["organization_name"] == "Org A"
    assert not any("No odd bills" in n for n in out["notes"])


def test_nothing_found_says_which_rules_are_not_applied(monkeypatch):
    out = run_tool(monkeypatch, clean(), mode="anomalies")
    assert out["findings_found"] == 0 and any("TRN" in n and "reverse-charge" in n for n in out["notes"])


def test_the_result_is_json(monkeypatch):
    import json
    json.dumps(run_tool(monkeypatch, clean() + [bill("X", net=7186.0, vat=1293.48)]))
