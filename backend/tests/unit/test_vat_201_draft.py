"""Tests for vat_201_draft tool (Form VAT 201 draft from books)."""
import datetime as dt
from types import SimpleNamespace

import pytest

from gemini_brain.agent import preview, tools
from gemini_brain.semantic.catalog import Catalog, ToolInputError
from gemini_brain.vat_return import form201
from gemini_brain.vat_review.scan import Scan

D = dt.date


@pytest.fixture(autouse=True)
def _cube_members(monkeypatch):
    """No live Cube catalog in unit tests: the supplier's TRN and country are there unless a test says otherwise."""
    from gemini_brain.vat_review import tool as review_tool
    monkeypatch.setattr(review_tool, "_has_trn", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_country", lambda *a, **k: True)
    monkeypatch.setattr(review_tool, "_has_rc", lambda *a, **k: False)


def mock_env(monkeypatch, sales_rows=None, bills=None, scan_complete=True, why_incomplete=None):
    def fake_load(query, *, organization_ids, subject, deadline):
        if any(m.startswith("vat_settings.") for m in query.get("dimensions", [])):
            return SimpleNamespace(rows=[])
        return SimpleNamespace(rows=sales_rows or [])

    fake_scan = Scan(bills=bills or [], complete=scan_complete, why_incomplete=why_incomplete)

    monkeypatch.setattr(form201.cube_client, "load", fake_load)
    monkeypatch.setattr(form201, "scan_bills", lambda *a, **kw: fake_scan)
    monkeypatch.setattr(form201.semantic_periods, "today_in", lambda tz: D(2026, 10, 16))


def test_dubai_org_form201_boxes_and_arithmetic(monkeypatch):
    """Case 1: Dubai organization: standard sales go to 1b; 4 and 5 from zero_rated/exempt; 8/11/12/13/14 add up; 14 = 12 − 13."""
    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 10000.0, "sales.output_vat": 500.0},
        {"sales.vat_category": "zero_rated", "sales.emirate": "Dubai", "sales.net_sales": 2000.0, "sales.output_vat": 0.0},
        {"sales.vat_category": "exempt", "sales.emirate": "Dubai", "sales.net_sales": 1000.0, "sales.output_vat": 0.0},
    ]
    bills = [
        {"document_number": "B1", "document_id": 1, "document_date": "2026-08-01", "vendor_name": "Local Supplier",
         "net_purchases": 4000.0, "input_vat": 200.0, "vendor_country_code": "AE", "vendor_trn": "100123456700003"}
    ]
    mock_env(monkeypatch, sales_rows=sales, bills=bills)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    assert len(res["organizations"]) == 1
    org = res["organizations"][0]
    assert org["organization_id"] == 24
    assert org["emirate"] == "Dubai"

    boxes = {b["box"]: b for b in org["boxes"]}
    assert "1b" in boxes
    assert boxes["1b"]["net"] == 10000.0
    assert boxes["1b"]["vat"] == 500.0
    assert boxes["1b"]["status"] == "from_books"
    assert boxes["1b"]["label"] == "Standard-rated supplies in Dubai"

    assert boxes["4"]["net"] == 2000.0
    assert boxes["4"]["vat"] is None
    assert boxes["4"]["status"] == "from_books"

    assert boxes["5"]["net"] == 1000.0
    assert boxes["5"]["vat"] is None
    assert boxes["5"]["status"] == "from_books"

    assert boxes["8"]["vat"] == 500.0
    assert boxes["9"]["net"] == 4000.0
    assert boxes["9"]["vat"] == 200.0
    assert boxes["11"]["vat"] == 200.0

    assert boxes["12"]["vat"] == boxes["8"]["vat"]
    assert boxes["13"]["vat"] == boxes["11"]["vat"]
    assert boxes["14"]["vat"] == round(boxes["12"]["vat"] - boxes["13"]["vat"], 2)
    assert boxes["14"]["vat"] == 300.0


def test_emirate_string_unassigned(monkeypatch):
    """Case 2: Emirate "string" → figures in unassigned, status needs_emirate, a note."""
    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "string", "sales.net_sales": 5000.0, "sales.output_vat": 250.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    assert "unassigned" in boxes
    assert boxes["unassigned"]["net"] == 5000.0
    assert boxes["unassigned"]["vat"] == 250.0
    assert boxes["unassigned"]["status"] == "needs_emirate"

    assert any("not one of the 7 UAE emirates" in n for n in org["notes"])
    assert any("not one of the 7 UAE emirates" in n for n in res["notes"])


def test_unknown_category_handling(monkeypatch):
    """Case 3: unknown category with VAT → in Box 1; unknown without VAT → unclassified_sales."""
    sales = [
        {"sales.vat_category": "unknown", "sales.emirate": "Abu Dhabi", "sales.net_sales": 1000.0, "sales.output_vat": 50.0},
        {"sales.vat_category": "unknown", "sales.emirate": "Abu Dhabi", "sales.net_sales": 800.0, "sales.output_vat": 0.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    assert boxes["1a"]["net"] == 1000.0
    assert boxes["1a"]["vat"] == 50.0
    assert org["unclassified_sales"] == 800.0
    assert any("no tax code and no VAT" in n for n in org["notes"])


def test_out_of_scope_lines_in_no_box(monkeypatch):
    """Case 4: out_of_scope lines are in no box."""
    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 1000.0, "sales.output_vat": 50.0},
        {"sales.vat_category": "out_of_scope", "sales.emirate": "Dubai", "sales.net_sales": 9999.0, "sales.output_vat": 0.0},
        {"sales.vat_category": "no_vat", "sales.emirate": "Dubai", "sales.net_sales": 8888.0, "sales.output_vat": 0.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    assert boxes["1b"]["net"] == 1000.0
    assert boxes["1b"]["vat"] == 50.0
    assert org["unclassified_sales"] == 0.0
    total_net = sum(b["net"] for b in org["boxes"] if b.get("net") is not None)
    assert total_net == 1000.0


def test_bills_foreign_vat_excluded_and_uae_vat_included(monkeypatch):
    """Case 5: Bills: a foreign bill with VAT and no TRN is not in Box 9; a UAE bill with VAT is."""
    bills = [
        {"document_number": "FOREIGN-1", "document_id": 1, "document_date": "2026-08-01", "vendor_name": "Foreign Corp",
         "net_purchases": 500.0, "input_vat": 50.0, "vendor_country_code": "GB", "vendor_trn": None},
        {"document_number": "LOCAL-1", "document_id": 2, "document_date": "2026-08-02", "vendor_name": "Local Supplier",
         "net_purchases": 2000.0, "input_vat": 100.0, "vendor_country_code": "AE", "vendor_trn": "100123456700003"},
    ]
    mock_env(monkeypatch, bills=bills)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    assert boxes["9"]["net"] == 2000.0
    assert boxes["9"]["vat"] == 100.0


def test_two_rcm_candidates(monkeypatch):
    """Case 6: Two rcm_candidate bills → candidate_count == 2, if_confirmed sums match, Box 3 and Box 10 stay 0."""
    bills = [
        {"document_number": "RCM-1", "document_id": 10, "document_date": "2026-08-10", "vendor_name": "AWS",
         "net_purchases": 1000.0, "input_vat": 0.0, "vendor_country_code": "US", "vendor_trn": None},
        {"document_number": "RCM-2", "document_id": 11, "document_date": "2026-08-12", "vendor_name": "Google",
         "net_purchases": 2000.0, "input_vat": 0.0, "vendor_country_code": "IE", "vendor_trn": None},
    ]
    mock_env(monkeypatch, bills=bills)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    rc = org["reverse_charge"]

    assert rc["candidate_count"] == 2
    assert len(rc["candidates"]) == 2
    assert rc["if_confirmed"]["box3_net"] == 3000.0
    assert rc["if_confirmed"]["box3_vat"] == 150.0
    assert rc["if_confirmed"]["box10_net"] == 3000.0
    assert rc["if_confirmed"]["box10_vat"] == 150.0
    assert rc["if_confirmed"]["net_change"] == 0.0

    boxes = {b["box"]: b for b in org["boxes"]}
    assert boxes["3"]["net"] == 0.0 and boxes["3"]["vat"] == 0.0 and boxes["3"]["status"] == "candidates_only"
    assert boxes["10"]["net"] == 0.0 and boxes["10"]["vat"] == 0.0 and boxes["10"]["status"] == "candidates_only"


def test_incomplete_scan_status_and_note(monkeypatch):
    """Case 7: Incomplete scan → Box 9 status incomplete, a note."""
    mock_env(monkeypatch, scan_complete=False, why_incomplete="time ran out")

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    assert boxes["9"]["status"] == "incomplete"
    assert any("Box 9 is incomplete: time ran out" in n for n in org["notes"])
    assert any("Box 9 is incomplete: time ran out" in n for n in res["notes"])


def test_boxes_2_6_7_not_in_books(monkeypatch):
    """Case 8: Boxes 2, 6, 7 are not_in_books."""
    mock_env(monkeypatch)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    org = res["organizations"][0]
    boxes = {b["box"]: b for b in org["boxes"]}

    for b_id in ("2", "6", "7"):
        assert boxes[b_id]["status"] == "not_in_books"
        assert boxes[b_id]["net"] is None
        assert boxes[b_id]["vat"] is None


def test_bad_which_raises_tool_input_error(monkeypatch):
    """Case 9: Bad which → ToolInputError."""
    mock_env(monkeypatch)
    with pytest.raises(ToolInputError):
        form201.run({"which": "invalid_choice"}, organization_ids=[24], subject="t", deadline=1e12)


def test_rows_has_one_row_per_box_with_fixed_columns(monkeypatch):
    """Case 10: rows has one row per box with the fixed column names."""
    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "Sharjah", "sales.net_sales": 3000.0, "sales.output_vat": 150.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)

    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    assert res["row_count"] == len(res["rows"])
    assert res["row_count"] == 14  # 14 boxes for one org

    expected_cols = {"organization_id", "box", "label", "net", "vat", "status"}
    for r in res["rows"]:
        assert set(r.keys()) == expected_cols
        assert r["organization_id"] == 24


def test_agent_registration_and_catalog_checks(monkeypatch):
    """Verifies vat_201_draft tool offering, execution, and preview label."""
    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 1000.0, "sales.output_vat": 50.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)

    ctx = tools.ToolContext(organization_ids=[24], subject="t", deadline=1e12)

    # 1. Preview label
    assert preview.step_label("vat_201_draft", {}) == "Drafting the VAT 201 return…"

    # 2. Offer tool when both sales and purchases are in catalog
    def _v(name):
        return {
            "name": name,
            "type": "view",
            "meta": {"kind": "flow", "time_dimension": f"{name}.document_date"},
            "measures": [{"name": f"{name}.amount"}],
            "dimensions": [{"name": f"{name}.{d}"} for d in ("organization_id", "organization_name", "currency", "document_date", "document_number", "status")],
        }

    from gemini_brain.semantic.catalog import parse_meta
    cat_both = parse_meta({"cubes": [_v("sales"), _v("purchases")]})
    monkeypatch.setattr(tools, "get_catalog", lambda *a, **kw: cat_both)
    specs = [s["toolSpec"]["name"] for s in tools.specs(ctx)]
    assert "vat_201_draft" in specs

    # 3. Do not offer when sales or purchases view is missing
    cat_no_p = parse_meta({"cubes": [_v("sales")]})
    monkeypatch.setattr(tools, "get_catalog", lambda *a, **kw: cat_no_p)
    specs_no_p = [s["toolSpec"]["name"] for s in tools.specs(ctx)]
    assert "vat_201_draft" not in specs_no_p

    # 4. Execute via agent tools
    result, ok = tools.execute("vat_201_draft", {}, ctx)
    assert ok is True
    assert "organizations" in result
    assert result["organizations"][0]["boxes"][0]["box"] == "1b"


def test_export_vat_201_rows_to_excel(monkeypatch):
    """Case A7: export vat_201_draft rows to Excel preserves columns and renders valid XLSX."""
    import io
    from openpyxl import load_workbook
    from gemini_brain.artifacts.generate import render_xlsx
    from gemini_brain.artifacts.report_spec import build_report_spec

    sales = [
        {"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 1000.0, "sales.output_vat": 50.0},
    ]
    mock_env(monkeypatch, sales_rows=sales)
    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    turn_data = [{"tool": "vat_201_draft", "result": res}]

    rows = preview.export_rows(turn_data)
    assert len(rows) == 14
    expected_cols = {"organization_id", "box", "label", "net", "vat", "status"}
    for r in rows:
        assert set(r.keys()) == expected_cols

    spec = build_report_spec(rows, query="export the VAT 201 draft as excel")
    excel_bytes = render_xlsx(spec)
    assert len(excel_bytes) > 0

    wb = load_workbook(io.BytesIO(excel_bytes))
    sheet_names = wb.sheetnames
    data_sheet_name = next(name for name in sheet_names if name != "Summary")
    ws = wb[data_sheet_name]
    header_cells = [str(cell.value or "").strip().lower().replace(" ", "_") for cell in ws[1] if cell.value is not None]
    # report_spec intentionally filters technical _id columns from report presentation, keeping all business columns
    assert {"box", "label", "net", "vat", "status"}.issubset(set(header_cells))


# --- review fixes (17 Nov 2026) -------------------------------------------------------------------------------------

def _bill(number, net, vat, country="AE", trn="100123456700003", status="ACCEPTED", doc_type="BILL"):
    return {"document_number": number, "document_id": number, "document_date": "2026-08-01", "document_type": doc_type,
            "status": status, "vendor_name": f"Supplier {number}", "net_purchases": net, "input_vat": vat,
            "vendor_country_code": country, "vendor_trn": trn}


def _boxes(res):
    return {b["box"]: b for b in res["organizations"][0]["boxes"]}


DUBAI_SALES = [{"sales.vat_category": "standard", "sales.emirate": "Dubai", "sales.net_sales": 10000.0,
                "sales.output_vat": 500.0}]


def test_vendor_credits_reduce_box_9(monkeypatch):
    bills = [_bill("B-100", 4000.0, 200.0), _bill("VC-100", -1000.0, -50.0, doc_type="VENDOR_CREDIT")]
    mock_env(monkeypatch, sales_rows=DUBAI_SALES, bills=bills)
    boxes = _boxes(form201.run({}, organization_ids=[24], subject="t", deadline=1e12))
    assert boxes["9"]["net"] == 3000.0 and boxes["9"]["vat"] == 150.0
    assert boxes["14"]["vat"] == 350.0


def test_rejected_and_deleted_bills_are_not_in_box_9(monkeypatch):
    bills = [_bill("B-100", 4000.0, 200.0), _bill("B-101", 2000.0, 100.0, status="REJECTED"),
             _bill("B-102", 2000.0, 100.0, status="DELETED")]
    mock_env(monkeypatch, sales_rows=DUBAI_SALES, bills=bills)
    assert _boxes(form201.run({}, organization_ids=[24], subject="t", deadline=1e12))["9"]["vat"] == 200.0


def test_a_cube_error_on_sales_shows_unavailable_not_zero(monkeypatch):
    mock_env(monkeypatch, bills=[_bill("B-100", 4000.0, 200.0)])

    def broken(query, *, organization_ids, subject, deadline):
        if any(m.startswith("vat_settings.") for m in query.get("dimensions", [])):
            return SimpleNamespace(rows=[])
        raise form201.cube_client.CubeError("permission denied for table tax_rates")

    monkeypatch.setattr(form201.cube_client, "load", broken)
    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    boxes = _boxes(res)
    for box in ("1", "4", "5", "8", "12", "14"):
        assert boxes[box]["status"] == "unavailable", box
        assert boxes[box]["vat"] is None and boxes[box].get("net") is None, box
    assert boxes["9"]["vat"] == 200.0  # bills are still read
    assert any("could not be read" in n for n in res["notes"])


def test_no_sales_in_the_period_is_zero_from_the_books_not_a_missing_emirate(monkeypatch):
    mock_env(monkeypatch, sales_rows=[], bills=[_bill("B-100", 4000.0, 200.0)])
    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    boxes = _boxes(res)
    assert boxes["1"]["status"] == "from_books" and boxes["1"]["vat"] == 0.0
    assert not any("emirate" in n for n in res["notes"])
    assert boxes["14"]["vat"] == -200.0


def test_missing_emirate_carries_into_box_8(monkeypatch):
    sales = [{**DUBAI_SALES[0], "sales.emirate": "string"}]
    mock_env(monkeypatch, sales_rows=sales, bills=[])
    boxes = _boxes(form201.run({}, organization_ids=[24], subject="t", deadline=1e12))
    assert boxes["unassigned"]["status"] == "needs_emirate"
    assert boxes["8"]["status"] == "needs_emirate" and boxes["14"]["status"] == "needs_emirate"


def test_candidates_are_all_listed_even_when_the_review_would_fold_them_into_a_pattern(monkeypatch):
    bills = [_bill(f"F-{i}", 1000.0, 0.0, country="IN", trn="") for i in range(9)] + [_bill("B-1", 1000.0, 50.0)]
    mock_env(monkeypatch, sales_rows=DUBAI_SALES, bills=bills)
    rc = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)["organizations"][0]["reverse_charge"]
    assert rc["candidate_count"] == 9
    assert rc["if_confirmed"]["box3_vat"] == 450.0


def test_candidates_use_the_review_rule(monkeypatch):
    from gemini_brain.vat_review import anomalies
    bills = [_bill("F-1", 1000.0, 0.0, country="IN", trn=""), _bill("F-2", 0.0, 0.0, country="IN", trn=""),
             _bill("A-1", 1000.0, 0.0), {**_bill("X-1", 500.0, 0.0), "vendor_country_code": None}]
    assert [f["bill"]["document_number"] for f in anomalies.rcm_candidates(bills)] == ["F-1"]
    found, _ = anomalies.find_anomalies(bills, D(2026, 10, 16))
    assert [f["bill"]["document_number"] for f in found if f["rule"] == "rcm_candidate"] == ["F-1"]


def test_box_7_has_its_own_note(monkeypatch):
    mock_env(monkeypatch, sales_rows=DUBAI_SALES, bills=[])
    boxes = _boxes(form201.run({}, organization_ids=[24], subject="t", deadline=1e12))
    assert boxes["7"]["note"] != boxes["6"]["note"]


def test_subject_is_required():
    with pytest.raises(TypeError):
        form201.run({}, organization_ids=[24])


def test_candidate_bills_get_app_links(monkeypatch):
    from gemini_brain.agent import links
    monkeypatch.setattr(links.settings, "accutax_app_url", "https://app.example")
    data = [{"tool": "vat_201_draft", "result": {"organizations": [{"reverse_charge": {"candidates": [
        {"document_number": "F-100", "document_id": 77}]}}], "rows": []}}]
    urls = links.document_links(data)
    assert "F-100" in urls and urls["F-100"].startswith("https://app.example")


def test_a_cube_without_the_supplier_country_still_drafts_the_return(monkeypatch):
    from gemini_brain.vat_review import tool as review_tool
    monkeypatch.setattr(review_tool, "_has_country", lambda *a, **k: False)
    mock_env(monkeypatch, sales_rows=DUBAI_SALES, bills=[_bill("B-100", 4000.0, 200.0)])
    seen = {}

    def fake_scan(*a, **kw):
        seen.update(kw)
        return Scan(bills=[{k: v for k, v in _bill("B-100", 4000.0, 200.0).items() if k != "vendor_country_code"}])

    monkeypatch.setattr(form201, "scan_bills", fake_scan)
    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    assert seen["with_country"] is False
    assert _boxes(res)["9"]["vat"] == 200.0
    assert res["organizations"][0]["reverse_charge"]["candidate_count"] == 0
    assert any("country is not available" in n for n in res["notes"])


def test_a_cube_error_on_bills_shows_boxes_9_to_14_unavailable(monkeypatch):
    mock_env(monkeypatch, sales_rows=DUBAI_SALES)

    def broken(*a, **kw):
        raise form201.cube_client.CubeError("Cube refused the query (HTTP 400)")

    monkeypatch.setattr(form201, "scan_bills", broken)
    res = form201.run({}, organization_ids=[24], subject="t", deadline=1e12)
    boxes = _boxes(res)
    for box in ("9", "11", "13", "14"):
        assert boxes[box]["status"] == "unavailable" and boxes[box]["vat"] is None, box
    assert boxes["1b"]["vat"] == 500.0
    assert any("bills could not be read" in n for n in res["notes"])
