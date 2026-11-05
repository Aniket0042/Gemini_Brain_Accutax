"""Found in the first VM test: the tax rate list is paged (10 by default), the VAT choice must not guess, and the
suggested number belongs to the organization of the token."""
import pytest

from gemini_brain.agent import tools
from gemini_brain.invoicing import lookups
from tests.unit.test_sales_draft import ASK, TOKEN, _ctx, _fake_jwt, accutax  # noqa: F401  (accutax is a fixture)
from gemini_brain.accutax_api.client import AccutaxCredential


def _rate(i, rate, tax_type="standard", rate_type="sales", name=None, default=False):
    return {"id": i, "name": name or f"Rate {i}", "tax_rate": rate, "tax_type": tax_type, "rate_type": rate_type,
            "is_active": True, "is_default": default}


def test_vat_found_beyond_the_first_ten_rates(accutax):
    # ten newer 0% rates, then the real 5% VAT: without pageSize only the first ten come back
    accutax.tax_rates = [_rate(i, 0, "zero_rated") for i in range(100, 110)] + [_rate(7, 5, name="VAT 5%")]
    out, ok = tools.execute("draft_sales_document", ASK, _ctx())
    assert ok and out["vat"] == "600.00" and out["lines"][0]["vat"] == "5%"
    assert out["warnings"] == [] or all("VAT rate" not in w for w in out["warnings"])


def test_several_standard_rates_are_not_guessed(accutax):
    accutax.tax_rates = [_rate(1, 18, name="Out of Scope Tax"), _rate(2, 0, name="Withholding Tax")]
    out, ok = tools.execute("draft_sales_document", ASK, _ctx())
    assert ok and out["vat"] == "0.00" and out["lines"][0]["vat"] == "No VAT rate"
    assert any("several standard rates" in w and "18%" in w for w in out["warnings"])


def test_the_person_names_the_rate(accutax):
    accutax.tax_rates = [_rate(1, 18, name="Out of Scope Tax"), _rate(2, 0, name="Withholding Tax")]
    ask = {**ASK, "lines": [{"description": "Consulting", "quantity": 40, "unit_price": 300, "vat_percent": 18}]}
    out, ok = tools.execute("draft_sales_document", ask, _ctx())
    assert ok and out["vat"] == "2,160.00" and out["total"] == "14,160.00"
    assert out["warnings"] == []
    out, _ = tools.execute("draft_sales_document", {**ask, "lines": [{**ask["lines"][0], "vat_percent": 7}]}, _ctx())
    assert any("no tax rate of that kind" in w for w in out["warnings"])


def test_pick_rate_prefers_default_then_five_percent():
    rates = [lookups.TaxRate(1, "A", 18.0, "standard", "sales", False), lookups.TaxRate(2, "B", 5.0, "standard", "sales", False)]
    assert lookups.pick_rate(rates).id == 2
    rates.append(lookups.TaxRate(3, "C", 10.0, "standard", "sales", True))
    assert lookups.pick_rate(rates).id == 3
    assert lookups.pick_rate(rates, percent=18).id == 1


def test_number_is_not_taken_from_another_organization(accutax):
    # the token is on organization 24; the invoice is for 25: Accutax would answer with 24's numbering
    ctx = _ctx(orgs=(25,))
    out, ok = tools.execute("draft_sales_document", ASK, ctx)
    assert ok and out["suggested_number"] is None
    assert any("another organization" in w for w in out["warnings"])
    assert not any("invoice_setting" in c[1] or "document-number" in c[1] for c in accutax.calls)


def test_number_is_read_when_token_matches(accutax):
    out, _ = tools.execute("draft_sales_document", ASK, _ctx())
    assert out["suggested_number"] == "INV-1048"
    other = tools.ToolContext(organization_ids=[24], subject="t", deadline=1e18, user_id=501, session_id="s1",
                              accutax=AccutaxCredential("opaque-token"), write_actions=True)
    out, _ = tools.execute("draft_sales_document", ASK, other)       # a token that names no organization
    assert out["suggested_number"] is None


def test_accutax_total_equals_the_card_total(accutax):
    # the VM showed 1,666.00 on the invoice page for a card total of 1,445.50: Accutax's amount due is
    # sum(line_amount + tax_amount), so line_amount must be the net
    accutax.tax_rates = [_rate(1, 18, name="Out of Scope Tax")]
    ask = {**ASK, "lines": [{"description": "Consulting", "quantity": 3.5, "unit_price": 350}]}
    out, _ = tools.execute("draft_sales_document", ask, _ctx())
    assert out["total"] == "1,445.50"
    from gemini_brain.actions import pending
    lines = pending.get_for(out["draft_id"], user_id=501).payload["line_items"]
    accutax_total = sum(l["line_amount"] + l["tax_amount"] for l in lines)
    assert round(accutax_total, 2) == 1445.50
