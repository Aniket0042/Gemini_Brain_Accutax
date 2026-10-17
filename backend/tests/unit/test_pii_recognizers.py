"""PII plan phase 1: the recognizers, the field policy and the contact dictionary."""
import collections
import json
import time
from pathlib import Path

import pytest

from gemini_brain.privacy import dictionary, policy, recognizers

LABELLED = Path(__file__).resolve().parents[1] / "data" / "pii_labelled.json"


def _score():
    cases = json.loads(LABELLED.read_text(encoding="utf-8"))["cases"]
    tp = fp = fn = 0
    misses, negative_hits = [], []
    for case in cases:
        expected = collections.Counter((e["kind"], e["text"]) for e in case["entities"])
        got = collections.Counter((s.kind, s.text) for s in recognizers.find(case["text"]))
        hit = sum((expected & got).values())
        tp, fp, fn = tp + hit, fp + sum(got.values()) - hit, fn + sum(expected.values()) - hit
        if expected != got:
            misses.append((case["text"], dict(expected), dict(got)))
        if case["group"] == "negative" and got:
            negative_hits.append((case["text"], dict(got)))
    return len(cases), tp / (tp + fp), tp / (tp + fn), misses, negative_hits


def test_labelled_set_meets_the_phase_1_gate():
    n, precision, recall, misses, negative_hits = _score()
    assert n >= 300
    assert precision >= 0.98 and recall >= 0.95, misses[:10]
    assert negative_hits == []                     # no figure, date or document number is ever flagged


@pytest.mark.parametrize("text", [
    "Net AED 18,450.00 plus VAT AED 922.50", "bill INV-BULK-26-20241216180347-114231", "journal JE-24-2026-00000773",
    "document BILL0000123 dated 2026-09-12", "Order 9876543210 shipped", "Batch 2345 6789 0123 4567",
])
def test_figures_and_document_numbers_are_never_flagged(text):
    assert recognizers.find(text) == []


def test_context_words_and_check_digits():
    assert [s.kind for s in recognizers.find("call me on 9876543210")] == ["PHONE"]
    assert recognizers.find("Order 9876543210") == []                         # a bare 10-digit number needs context
    assert [s.kind for s in recognizers.find("Passport A1234567")] == ["PASSPORT"]
    assert recognizers.find("Item A1234567") == []
    [eid] = recognizers.find("EID 784-1990-1234567-0")                       # a failed check digit lowers the score
    assert eid.kind == "EMIRATES_ID" and eid.score < 0.95


def test_overlaps_keep_the_stronger_reading():
    [span] = recognizers.find("GSTIN 27AAPFU0939F1ZV")
    assert span.kind == "GSTIN"                                               # not also its PAN
    spans = recognizers.find("TRN 100234567800003, email a.b@x.ae.")
    assert [(s.kind, s.text) for s in spans] == [("TRN", "100234567800003"), ("EMAIL", "a.b@x.ae")]


@pytest.mark.parametrize("key,cls,kind", [
    ("purchases.vendor_name", policy.TOKEN, "SUPPLIER"), ("sales.customer_name", policy.TOKEN, "CUSTOMER"),
    ("payments.counterparty_name", policy.TOKEN, "PARTY"), ("organization_name", policy.TOKEN, "ORG"),
    ("vendor_trn", policy.TOKEN, "TRN"), ("buyer_tax_id", policy.TOKEN, "TRN"), ("vendor_address", policy.TOKEN, "ADDRESS"),
    ("gl.journal_description", policy.FREE_TEXT, None), ("reference", policy.FREE_TEXT, None),
    ("net_sales", policy.KEEP, None), ("output_vat", policy.KEEP, None), ("document_number", policy.KEEP, None),
    ("document_date", policy.KEEP, None), ("organization_id", policy.KEEP, None), ("account_name", policy.KEEP, None),
    ("item_name", policy.KEEP, None), ("status", policy.KEEP, None), ("finding", policy.KEEP, None),
    ("label", policy.SCAN, None),
])
def test_field_policy(key, cls, kind):
    rule = policy.field_rule(key)
    assert (rule.cls, rule.kind) == (cls, kind)


def test_name_normalization_and_public_entities():
    assert policy.normalize_name("Al Noor Trading L.L.C.") == policy.normalize_name("AL NOOR TRADING LLC") == \
        "al noor trading"
    assert policy.normalize_name("The Smith & Sons Co.") == "smith and sons"
    assert policy.public_entity("Dubai Electricity & Water Authority") == "utility"
    assert policy.public_entity("Emirates NBD") == "bank"
    assert policy.public_entity("Al Noor Trading LLC") is None


def _dict():
    entries = [dictionary.Entry(n, k, policy.normalize_name(n)) for n, k in [
        ("Al Noor Trading L.L.C", "SUPPLIER"), ("Al Futtaim Motors LLC", "SUPPLIER"), ("Zenith Foods FZE", "CUSTOMER"),
        ("Zenith Foods FZE", "SUPPLIER"), ("Cash Customer", "CUSTOMER"), ("DEWA", "SUPPLIER"),
        ("Acme Holdings", "ORG")]]
    return dictionary.Dictionary(entries)


def test_dictionary_finds_names_in_any_spelling():
    d = _dict()
    spans = d.find("Pay AL NOOR TRADING LLC and al-noor trading; also the Zenith Foods. Al Noor Trading L.L.C. paid.")
    assert [(s.kind, s.text) for s in spans] == [("SUPPLIER", "AL NOOR TRADING LLC"), ("SUPPLIER", "al-noor trading"),
                                                 ("PARTY", "Zenith Foods"), ("SUPPLIER", "Al Noor Trading L.L.C.")]
    assert d.find("Acme Holdings revenue")[0].kind == "ORG"


def test_dictionary_leaves_out_generic_and_public_names():
    d = _dict()
    assert "cash customer" not in d.entries and "dewa" not in d.entries
    assert d.find("Cash Customer paid DEWA") == []


def test_partial_names_only_when_asked_and_unique():
    d = _dict()
    assert d.find("bills from futtaim") == []
    [span] = d.find("bills from futtaim", partial=True)
    assert span.partial and span.key == "al futtaim motors" and span.score < 0.95
    assert d.find("trading and foods", partial=True) == []            # common words stand for no one name


def test_for_orgs_caches_per_organization(monkeypatch):
    dictionary.clear()
    calls = []

    def fake_load(org_ids, *, subject):
        calls.append(sorted(org_ids))
        return {o: [dictionary.Entry(f"Vendor Number {o}", "SUPPLIER", f"vendor number {o}")] for o in org_ids}

    monkeypatch.setattr(dictionary, "load_many", fake_load)
    assert len(dictionary.for_orgs([24, 25], org_names=["Acme Holdings"])) == 1   # nothing cached: org names only
    d = dictionary.for_orgs([24, 25], subject="t", fetch=True)
    assert calls == [[24, 25]] and len(d) == 2                                # one load for both organizations
    dictionary.for_orgs([25, 24], fetch=True)
    assert calls == [[24, 25]]                                                # cached
    dictionary.clear()


def test_warm_loads_in_the_background(monkeypatch):
    dictionary.clear()
    monkeypatch.setattr(dictionary, "load_many", lambda org_ids, *, subject: {
        o: [dictionary.Entry("Gamma Supplies", "SUPPLIER", "gamma supplies")] for o in org_ids})
    dictionary.warm([30], subject="t")
    for _ in range(100):
        if len(dictionary.for_orgs([30])):
            break
        time.sleep(0.01)
    assert len(dictionary.for_orgs([30])) == 1
    dictionary.clear()


def test_one_query_per_source_for_all_orgs_and_a_failing_source_is_skipped(monkeypatch):
    from gemini_brain.semantic import cube_client

    class Result:
        def __init__(self, rows):
            self.rows = rows

    seen = []

    def fake(query, *, organization_ids, **kw):
        member = query["dimensions"][0]
        seen.append((member, list(organization_ids)))
        if member.startswith("bank."):
            raise cube_client.CubeError("view missing")
        org_key = member.split(".")[0] + ".organization_id"
        return Result([{org_key: 24, member: "Delta Traders"}, {org_key: "25", member: "Echo Supplies"},
                       {org_key: 24, member: ""}, {org_key: 99, member: "Not Asked For"}])

    monkeypatch.setattr(cube_client, "load", fake)
    loaded = dictionary.load_many([24, 25], subject="t")
    assert len(seen) == len(dictionary.SOURCES) and all(orgs == [24, 25] for _, orgs in seen)
    assert {e.key for e in loaded[24]} == {"delta traders"} and {e.key for e in loaded[25]} == {"echo supplies"}
    assert len(loaded[24]) == len(dictionary.SOURCES) - 1


def test_many_organizations_never_ask_cube_for_more_rows_than_it_allows(monkeypatch):
    # Cube refuses a limit above 50,000 ("The query limit has been exceeded"): with 10 organizations the
    # dictionary once asked for 200,000 and got no names at all.
    from gemini_brain.semantic import cube_client

    class Result:
        rows = []

    limits = []

    def fake(query, **kw):
        limits.append(query["limit"])
        if query["limit"] > dictionary.CUBE_MAX_ROWS:
            raise cube_client.CubeError("Cube refused the query (HTTP 500): Error: The query limit has been exceeded.")
        return Result()

    monkeypatch.setattr(cube_client, "load", fake)
    dictionary.load_many(list(range(24, 34)), subject="t")
    assert limits and max(limits) == dictionary.CUBE_MAX_ROWS
