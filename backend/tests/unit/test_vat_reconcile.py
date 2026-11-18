"""The ledger-versus-documents VAT gap is explained invoice by invoice, and the causes add up to the gap."""
from types import SimpleNamespace

from gemini_brain.vat_return import reconcile


def _cube(ledger, docs, docs_anywhere, ledger_anywhere):
    """A fake cube_client.load. ledger/docs: {number: vat} for the period; *_anywhere: all time."""
    def load(q, organization_ids, subject, deadline):
        dims = q.get("dimensions") or []
        if dims == ["ledger.journal_description"]:
            data = ledger_anywhere if any(f["member"] == "ledger.journal_description" for f in q["filters"]) else ledger
            rows = [{"ledger.journal_description": f"Invoice {n} - X", "ledger.credit": max(v, 0), "ledger.debit": max(-v, 0)}
                    for n, v in data.items()]
            wanted = [f for f in q["filters"] if f["member"] == "ledger.journal_description"]
            if wanted:
                rows = [r for r in rows if any(w in r["ledger.journal_description"] for w in wanted[0]["values"])]
            return SimpleNamespace(rows=rows)
        data = docs
        flt = q.get("filters")
        if flt:
            data = {n: v for n, v in docs_anywhere.items() if n in flt[0]["values"]}
        return SimpleNamespace(rows=[{"sales.document_number": n, "sales.output_vat": v} for n, v in data.items()])
    return load


def test_causes_add_up_to_the_gap(monkeypatch):
    ledger = {"A1": 100.0, "A2": 50.0, "C1": 30.0, "D1": 10.0, "S1": -40.0}   # A1, A2 cancelled; C1 dated elsewhere
    docs = {"B1": 20.0, "B2": 5.0, "C1": 30.0, "D1": 0.0, "S1": 0.0, "Z0": 0.0}  # B1 never posted; B2 posted elsewhere
    monkeypatch.setattr(reconcile.cube_client, "load", _cube(
        ledger, docs, docs_anywhere={"C2": 7.0, "D1": 0.0},
        ledger_anywhere={"B2": 5.0}))
    # C1 is in both with equal VAT, so it is no cause; A1/A2 vanished, D1 has VAT only in the ledger, S1 wrong side
    r = reconcile.explain_side(24, "output", "2026-01-01", "2026-03-31", "t", 1e12)
    assert r["gap"] == round(sum(ledger.values()) - sum(docs.values()), 2)
    assert r["unexplained"] == 0.0
    causes = {c["cause"].split(" ")[0]: c for c in r["causes"]}
    gone = next(c for c in r["causes"] if "no longer issued" in c["cause"])
    assert gone["documents"] == 2 and gone["effect_on_gap"] == 150.0
    never = next(c for c in r["causes"] if "no VAT journal" in c["cause"])
    assert never["documents"] == 1 and never["effect_on_gap"] == -20.0
    wrong = next(c for c in r["causes"] if "opposite side" in c["cause"])
    assert wrong["effect_on_gap"] == -40.0
    assert causes  # at least the cause list is keyed


def test_zero_vat_leftovers_are_not_causes(monkeypatch):
    monkeypatch.setattr(reconcile.cube_client, "load", _cube({"A1": 10.0}, {"A1": 10.0, "Z1": 0.0, "Z2": 0.0}, {}, {}))
    r = reconcile.explain_side(24, "output", "2026-01-01", "2026-03-31", "t", 1e12)
    assert r["causes"] == [] and r["gap"] == 0.0


def test_too_many_leftovers_gives_no_explanation(monkeypatch):
    many = {f"N{i}": 1.0 for i in range(reconcile.MAX_CLASSIFIED + 1)}
    monkeypatch.setattr(reconcile.cube_client, "load", _cube(many, {}, {}, {}))
    assert reconcile.explain_side(24, "output", "2026-01-01", "2026-03-31", "t", 1e12) is None


def test_a_cube_error_leaves_no_explanation(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cube down")
    monkeypatch.setattr(reconcile.cube_client, "load", boom)
    assert reconcile.explain(24, "2026-01-01", "2026-03-31", "t", 1e12, ["output"]) is None


def test_journals_that_name_no_document_are_their_own_cause(monkeypatch):
    def load(q, organization_ids, subject, deadline):
        if q.get("dimensions") == ["ledger.journal_description"]:
            return SimpleNamespace(rows=[
                {"ledger.journal_description": "Reversal of void entry 7", "ledger.credit": 0, "ledger.debit": 40.0},
                {"ledger.journal_description": "Invoice A1 - X", "ledger.credit": 10.0, "ledger.debit": 0}])
        return SimpleNamespace(rows=[{"sales.document_number": "A1", "sales.output_vat": 10.0}])
    monkeypatch.setattr(reconcile.cube_client, "load", load)
    r = reconcile.explain_side(24, "output", "2026-07-01", "2026-09-30", "t", 1e12)
    assert r["gap"] == -40.0 and r["unexplained"] == 0.0
    assert r["causes"][0]["effect_on_gap"] == -40.0 and r["causes"][0]["examples"] == ["Reversal of void entry 7"]
