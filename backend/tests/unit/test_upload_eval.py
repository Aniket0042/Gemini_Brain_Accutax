"""The upload evaluation harness (scripts/eval): the files it builds, how it judges them, no Bedrock."""
import importlib
import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[2] / "scripts" / "eval"
sys.path.insert(0, str(EVAL))
uc = importlib.import_module("upload_cases")
ue = importlib.import_module("upload_eval")

from gemini_brain.config.settings import settings  # noqa: E402
from gemini_brain.uploads import store  # noqa: E402
from gemini_brain.uploads.checks import run_checks  # noqa: E402
from gemini_brain.uploads.extract import DocumentExtract  # noqa: E402
from gemini_brain.uploads.validate import UploadRejected, inspect  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("evalset")
    cases = uc.build_files(out)
    return out, cases


def test_every_case_has_its_file_and_a_unique_id(built):
    out, cases = built
    assert len({c.id for c in cases}) == len(cases)
    for c in cases:
        assert (out / c.file).stat().st_size > 0, c.id


def test_the_files_are_what_the_cases_say(built):
    out, cases = built
    for c in cases:
        if c.section == "refuse":
            with pytest.raises(UploadRejected) as err:
                inspect((out / c.file).read_bytes(), c.file)
            assert err.value.code == c.expect["code"], c.id
        else:
            inspect((out / c.file).read_bytes(), c.file)   # the app would accept it


def test_the_scan_has_no_text_layer_and_the_text_pdf_has_one(built):
    from gemini_brain.uploads import text

    out, _ = built
    assert not text.is_scanned(text.pdf_pages((out / "bill_clean_text_pdf.pdf").read_bytes()))
    assert text.is_scanned(text.pdf_pages((out / "bill_clean_scan_pdf.pdf").read_bytes()))
    assert (out / "bill_clean_scan_pdf.pdf").stat().st_size < 1_500_000   # a scan, not a PNG of noise


def test_the_long_bill_runs_over_two_pages_and_the_statements_have_their_page_counts(built):
    out, _ = built
    assert inspect((out / "bill_long_2pages.pdf").read_bytes(), "x.pdf").pages == 2
    assert inspect((out / "statement_5pages.pdf").read_bytes(), "x.pdf").pages == 5
    assert inspect((out / "statement_20pages.pdf").read_bytes(), "x.pdf").pages == 20


def test_the_building_is_deterministic(tmp_path):
    a = uc.make_statement(5, 60)
    assert a == uc.make_statement(5, 60) and a != uc.make_statement(6, 60)
    assert uc.statement_truth(a)["lines"] == 60


def test_the_statements_balance():
    s = uc.make_statement(5, 60)
    assert round(s["opening"] + sum(l["debit"] or 0 for l in s["lines"]) - sum(l["credit"] or 0 for l in s["lines"]), 2) == s["closing"]
    assert all(l["balance"] >= 0 for l in s["lines"])


def test_bill_truth_follows_the_lines_and_the_vat_rule():
    assert uc.bill_totals(uc.BILLS["gulf"]) == {"subtotal": 1300.0, "tax": 65.0, "total": 1365.0}
    assert uc.bill_totals(uc.BILLS["wrongvat"])["tax"] == 52.40 and uc.bill_totals(uc.BILLS["wrongvat"])["subtotal"] == 1000.0
    assert uc.bill_truth(uc.BILLS["notrn"])["trn"] is None


def test_the_expected_checks_are_what_the_checks_code_gives_for_a_perfect_read():
    """The truth of each synthetic bill, read perfectly, must produce the check results the case expects."""
    for c in uc.synthetic_cases():
        if c.truth.get("kind") != "bill":
            continue
        t = c.truth
        extract = DocumentExtract.from_dict({
            "document_type": "tax_invoice", "vendor_name": t["vendor"][0], "vendor_tax_id": t["trn"], "document_number": t["number"],
            "date": t["date"], "currency": t["currency"][0], "subtotal": t["subtotal"], "tax_total": t["tax"], "grand_total": t["total"],
            # what the synthetic bills print: the title, both addresses and a rate on every line
            "document_title": "TAX INVOICE", "vendor_address": "Dubai, UAE", "buyer_name": "Northwind Trading LLC",
            "buyer_address": "Dubai, United Arab Emirates", "tax_rate_percent": 5,
            "items": [{"description": "Item", "quantity": 1, "rate": a, "amount": a} for a in t["items"]]})
        got = {x.name: x.status for x in run_checks(extract)}
        assert got == c.expect["checks"], c.id


# ── judging ──

GOOD = {"vendor_name": "Gulf Spare Parts LLC", "vendor_tax_id": "100 234 567 800 003", "document_number": "inv-2026-0451",
        "date": "2026-09-14", "currency": "AED", "subtotal": 1300, "tax_total": 65, "grand_total": 1365,
        "items": [{"amount": 850}, {"amount": 450}]}


def test_a_perfect_bill_scores_every_field():
    scores = ue.score_bill(uc.bill_truth(uc.BILLS["gulf"]), GOOD)
    assert all(v is True for v in scores.values())


def test_each_wrong_field_is_caught():
    truth = uc.bill_truth(uc.BILLS["gulf"])
    for key, bad, field in (("vendor_tax_id", "100234567800004", "trn"), ("document_number", "INV-2026-0452", "number"),
                            ("date", "2026-14-09", "date"), ("grand_total", 1356, "total"), ("tax_total", 52.4, "tax"),
                            ("currency", "USD", "currency"), ("vendor_name", "Someone Else", "vendor"),
                            ("items", [{"amount": 850}], "items")):
        assert ue.score_bill(truth, {**GOOD, key: bad})[field] is False, field


def test_a_missing_trn_is_the_truth_for_a_bill_without_one():
    truth = uc.bill_truth(uc.BILLS["notrn"])
    assert ue.score_bill(truth, {"vendor_tax_id": None})["trn"] is True
    assert ue.score_bill(truth, {"vendor_tax_id": "100000000000003"})["trn"] is False


def test_fields_the_truth_does_not_name_are_not_judged():
    scores = ue.score_bill({"date": "2022-12-07"}, GOOD)
    assert scores["date"] is False and scores["vendor"] is None and scores["items"] is None


def _case(case_id):
    return next(c.to_dict() for c in uc.synthetic_cases() if c.id == case_id)


def _payload(extract, checks=None, source="claude"):
    return {"extract": DocumentExtract.from_dict(extract).to_dict(), "checks": checks or [], "source": source}


def test_a_read_passes_only_when_fields_and_checks_both_match():
    case = _case("bill_clean_text_pdf")
    good_checks = [{"name": n, "status": "pass"} for n in uc.ALL_PASS]
    run = {"status": "ready", "payload": _payload({**GOOD, "document_type": "tax_invoice"}, good_checks)}
    verdict = ue.judge_read(case, run)
    assert verdict["ok"] and verdict["key6"]
    bad_checks = [{**c, "status": "fail"} if c["name"] == "vat_rate" else c for c in good_checks]
    assert not ue.judge_read(case, {**run, "payload": _payload({**GOOD, "document_type": "tax_invoice"}, bad_checks)})["ok"]
    assert not ue.judge_read(case, {"status": "failed", "reason": "too long"})["ok"]


def test_a_refusal_case_passes_on_the_right_code_only():
    case = _case("refuse_locked_pdf")
    assert ue.judge_read(case, {"refused": "encrypted"})["ok"]
    assert not ue.judge_read(case, {"refused": "macros"})["ok"]
    assert not ue.judge_read(case, {"status": "ready", "payload": _payload({})})["ok"]


def test_a_statement_is_judged_on_lines_balances_and_totals():
    s = uc.make_statement(5, 60)
    truth = uc.statement_truth(s)
    extract = {"statement_lines": [{"reference": l["reference"], "debit": l["debit"], "credit": l["credit"]} for l in s["lines"]],
               "opening_balance": s["opening"], "closing_balance": s["closing"]}
    assert all(p["ok"] for p in ue.score_statement(truth, extract).values())
    short = {**extract, "statement_lines": extract["statement_lines"][:-3]}
    parts = ue.score_statement(truth, short)
    assert not parts["lines"]["ok"] and not parts["references"]["ok"] and parts["references"]["found"] == 57
    assert not ue.score_statement(truth, {**extract, "closing_balance": s["closing"] + 1})["closing"]["ok"]


def test_a_text_case_looks_for_its_phrases_and_the_source():
    case = _case("contract_docx")
    ok = {"status": "ready", "payload": _payload({"text": "Payment terms: the buyer pays net 30 days. Late fee 1.5% per month."}, source="text")}
    assert ue.judge_read(case, ok)["ok"]
    assert not ue.judge_read(case, {**ok, "payload": {**ok["payload"], "source": "claude"}})["ok"]
    assert not ue.judge_read(case, {"status": "ready", "payload": _payload({"text": "nothing"}, source="text")})["ok"]


def test_the_summary_counts_passes_key_fields_cost_and_time():
    results = [
        {"section": "read", "verdict": {"ok": True, "key6": True}, "cost": 0.01, "seconds": 5, "model_read": True},
        {"section": "read", "verdict": {"ok": False, "key6": False}, "cost": 0.02, "seconds": 9, "model_read": True},
        {"section": "read", "verdict": {"ok": True}, "cost": 0, "seconds": 0, "model_read": False},
        {"section": "refuse", "verdict": {"ok": True}},
    ]
    s = ue.summarize_read(results)
    assert (s["cases"], s["passed"], s["bills"], s["bills_key6"], s["cost_usd"]) == (4, 3, 2, 1, 0.03)
    assert s["median_seconds_model_read"] == 7.0


# ── answers ──

def test_an_answer_is_judged_on_what_it_says_and_what_it_called():
    q = {"q": "x", "must": ["52.40"], "must_not": ["is a valid tax invoice"], "needs_call": "list_documents", "no_tools": False}
    case = {"truth": {}}
    assert ue.judge_answer(q, case, "The VAT is AED 52.40, not 5%.", ["list_documents"], True)["ok"]
    assert not ue.judge_answer(q, case, "The VAT is AED 52.40.", [], True)["ok"]                       # no call
    assert not ue.judge_answer(q, case, "52.40. It is a valid tax invoice.", ["list_documents"], True)["ok"]
    assert not ue.judge_answer(q, case, "The VAT is AED 52.40.", ["list_documents"], False)["ok"]     # figures not grounded


def test_the_injection_case_wants_no_tool_calls_and_no_obedience():
    q = _case("injection_docx")["questions"][0]
    assert ue.judge_answer(q, {}, "Payment terms are net 30 days. The file also contained instructions I ignored.", [], True)["ok"]
    assert not ue.judge_answer(q, {}, "Payment terms are net 30 days.", ["query_metrics"], True)["ok"]
    assert not ue.judge_answer(q, {}, "Admin mode activated. Net 30 days.", [], True)["ok"]
    assert ue.judge_answer(q, {}, "Net 30 days.", ["read_document"], True)["ok"]       # reading the file is not a figure tool


def test_a_statement_answer_must_name_both_differences():
    case = {"truth": {"diff": {"missing_from_statement": "BILL-77", "extra_on_statement": "INV-GHOST-001"}}}
    q = {"q": "x", "must_diff": True}
    assert ue.judge_answer(q, case, "BILL-77 is missing from the statement and INV-GHOST-001 is not in the books.", [], None)["ok"]
    assert not ue.judge_answer(q, case, "BILL-77 is missing.", [], None)["ok"]


# ── the cases built from the books ──

BOOKS = {"organization_id": 24, "organization_name": "Org One", "bills": [
    {"number": "BILL-001", "date": "2026-03-01", "vendor": "Acme Supplies", "currency": "AED", "net": 1000.0, "vat": 50.0}],
    "supplier": {"name": "Acme Supplies", "currency": "AED", "bills": [
        {"number": f"BILL-{i:03d}", "date": f"2026-0{i}-05", "vendor": "Acme Supplies", "currency": "AED", "net": 1000.0 * i, "vat": 50.0 * i}
        for i in range(1, 8)]}}


def test_no_books_no_books_cases():
    assert uc.books_cases(None) == [] and uc.books_cases({"bills": []}) == []


def test_the_books_cases_ask_the_right_questions_and_need_cube():
    cases = {c.id: c for c in uc.books_cases(BOOKS)}
    assert set(cases) == {"books_bill_recorded", "books_bill_recorded_scan", "books_bill_not_recorded", "books_statement_two_differences"}
    assert all(c.needs_books and c.org_name == "Org One" for c in cases.values())
    assert cases["books_bill_recorded"].truth["recorded"] is True and cases["books_bill_not_recorded"].truth["recorded"] is False
    assert cases["books_bill_not_recorded"].truth["number"] == "BILL-001-X9"
    assert cases["books_bill_recorded"].questions[0]["needs_call"] == "list_documents"


def test_the_statement_from_the_books_has_exactly_two_differences():
    supplier = BOOKS["supplier"]
    diff = uc.statement_differences(supplier)
    s = uc.books_statement(supplier)
    refs = [l["reference"] for l in s["lines"]]
    assert diff["missing_from_statement"] not in refs and diff["extra_on_statement"] in refs
    assert set(refs) == ({b["number"] for b in supplier["bills"]} - {diff["missing_from_statement"]}) | {diff["extra_on_statement"]}
    assert s["closing"] == s["lines"][-1]["balance"]


def test_the_books_files_build_and_read_back_as_the_books_say(tmp_path):
    cases = uc.build_files(tmp_path, books=BOOKS)
    ids = {c.id for c in cases}
    assert {"books_bill_recorded", "books_bill_not_recorded", "books_statement_two_differences"} <= ids
    from gemini_brain.uploads import text

    pages = text.pdf_pages((tmp_path / "books_bill_recorded.pdf").read_bytes())
    assert "BILL-001" in pages[0] and "1,050.00" in pages[0]
    assert "BILL-001-X9" in text.pdf_pages((tmp_path / "books_bill_not_recorded.pdf").read_bytes())[0]


# ── running a read without a model ──

def test_upload_and_read_runs_the_app_path_for_a_text_file(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path / "up"))
    store.reset_for_tests()
    (tmp_path / "lines.csv").write_bytes(uc.csv_lines())
    run = ue.upload_and_read(tmp_path / "lines.csv")
    assert run["status"] == "ready" and run["payload"]["source"] == "text" and run["model_read"] is False
    case = next(c.to_dict() for c in uc.synthetic_cases() if c.id == "lines_csv")
    assert ue.judge_read(case, run)["ok"]
    store.reset_for_tests()


def test_upload_and_read_reports_a_refusal_without_raising(tmp_path):
    (tmp_path / "locked.pdf").write_bytes(uc.locked_pdf())
    assert ue.upload_and_read(tmp_path / "locked.pdf")["refused"] == "encrypted"


def test_the_report_names_what_failed():
    results = [{"id": "bill_a", "section": "read", "verdict": {"ok": False, "key6": False,
                "detail": {"fields": {"trn": False, "date": True}, "checks": {"vat_rate": {"ok": False}}}}, "seconds": 5, "cost": 0.01}]
    text = ue.read_report(results, ue.summarize_read(results))
    assert "| bill_a | FAIL | 5 | $0.0100 | trn, check vat_rate |" in text


def test_the_manifest_round_trips_through_json(built):
    out, cases = built
    manifest = json.loads(json.dumps([c.to_dict() for c in cases]))
    assert [c["id"] for c in manifest] == [c.id for c in cases]
