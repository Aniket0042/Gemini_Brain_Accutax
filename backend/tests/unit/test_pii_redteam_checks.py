"""The PII red-team runner's checks (scripts/eval/pii_redteam.py) and its case file, without Bedrock."""
import importlib.util
import json
from pathlib import Path

from gemini_brain.privacy import dictionary, policy

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("pii_redteam", ROOT / "scripts" / "eval" / "pii_redteam.py")
rt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rt)

KNOWN = dictionary.Dictionary([dictionary.Entry(n, "SUPPLIER", policy.normalize_name(n))
                               for n in ("Michael", "Theodore", "Vidhi")])


def test_masked_payload_has_no_leaks():
    sent = rt.flatten("Organizations: [ORG_1]", [{"role": "user", "content": [
        {"text": "Bills from [SUPPLIER_1], TRN [TRN_1]? AED 18,450.00, BILL-2041"}]}])
    assert rt.leaks([sent], known=KNOWN, values=["Michael", "100234567800003"], typed=["100234567800003"]) == []


def test_each_kind_of_leak_is_found_in_any_spacing():
    sent = rt.flatten("", [{"role": "user", "content": [
        {"text": "Theodore paid; call +971501234567; TRN 100 2345 6780 0003; mail a.b@x.ae; FTA info@tax.gov.ae"}]}])
    found = rt.leaks([sent], known=KNOWN, values=["Org Two"], typed=["+971 50 123 4567"])
    whys = {(f["why"], f["value"].lower()) for f in found}
    assert ("known contact name", "theodore") in whys
    assert ("typed by user", "+971501234567") in whys
    assert any(f["kind"] == "TRN" for f in found) and any(f["kind"] == "EMAIL" for f in found)
    assert not any("tax.gov.ae" in f["value"] for f in found)                       # public contact, on purpose


def test_answer_checks():
    case = {"answer_has": ["Michael"], "min_names": 2}
    ok = rt.answer_checks("Michael and Vidhi lead purchases.", case, known=KNOWN)
    assert ok["ok"] and ok["names"] == 2
    left = rt.answer_checks("[SUPPLIER_1] leads.", case, known=KNOWN)
    assert not left["ok"] and left["placeholders_left"] and left["missing"] == ["Michael"]
    assert not rt.answer_checks("Michael leads.", case, known=KNOWN)["names_ok"]
    assert not rt.answer_checks("Michael and Vidhi.", case, known=KNOWN, unresolved=1)["ok"]


def test_case_file_is_well_formed():
    cases = json.loads((ROOT / "tests" / "data" / "pii_redteam.json").read_text(encoding="utf-8"))["cases"]
    ids = [c["id"] for c in cases]
    assert len(cases) >= 40 and len(ids) == len(set(ids))
    for c in cases:
        assert c["orgs"] and all(24 <= o <= 33 for o in c["orgs"]) and c["question"].strip()
        assert all(cid in ids[:ids.index(c["id"])] for cid in c.get("context") or [])  # context runs first
    assert {c["group"] for c in cases} >= {"typed name", "many names", "follow-up", "typed id", "not in books",
                                           "arabic", "law", "injection", "report", "free text"}
