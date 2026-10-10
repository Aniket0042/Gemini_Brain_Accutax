"""Tests for the App Guidance guide: section matching, prompt context and route
sanitizing."""
import re

import pytest

from gemini_brain.config.settings import settings
from gemini_brain.knowledge import guide_loader as gl

GUIDE = gl.load_app_guide()
HEADINGS = [s.heading for s in gl._parse_sections(GUIDE)]
PATH_RE = re.compile(r"`/[^`]*`")


def test_guide_loads_with_sections():
    assert GUIDE
    assert len(HEADINGS) >= 50


def test_every_section_is_verified():
    stubs = [s.heading for s in gl._parse_sections(GUIDE) if s.status != "verified"]
    assert stubs == []


@pytest.mark.parametrize(
    "name,mapping",
    [
        ("SECTION_ROUTES", gl.SECTION_ROUTES),
        ("SECTION_KEYWORDS", gl.SECTION_KEYWORDS),
        ("SECTION_EXCLUDE_KEYWORDS", gl.SECTION_EXCLUDE_KEYWORDS),
        ("SECTION_REQUIRE_KEYWORDS", gl.SECTION_REQUIRE_KEYWORDS),
    ],
)
def test_mapping_keys_are_real_headings(name, mapping):
    unknown = [k for k in mapping if k not in HEADINGS]
    assert unknown == [], f"{name} keys missing from accutax_guide.md: {unknown}"


def test_every_heading_has_route_and_keywords():
    assert [h for h in HEADINGS if h not in gl.SECTION_ROUTES] == []
    assert [h for h in HEADINGS if h not in gl.SECTION_KEYWORDS] == []


def test_routes_are_relative_paths():
    assert all(route.startswith("/") for route in gl.SECTION_ROUTES.values())


MATCH_CASES = [
    ("how do I create an invoice", "Creating an Invoice"),
    ("how do I raise a tax invoice for a customer", "Creating an Invoice"),
    ("how do I send an invoice", "Creating an Invoice"),
    ("how do I create a quote", "Creating a Quote"),
    ("how do I convert a quote to an invoice", "Creating a Quote"),
    ("how do I create a proforma invoice", "Creating a Proforma Invoice"),
    ("how do I make a cash invoice", "Creating a Cash Invoice"),
    ("how do I record cash sales", "Creating a Cash Invoice"),
    ("how do I set up a recurring invoice", "Setting Up a Recurring Invoice"),
    ("how do I schedule monthly invoices", "Setting Up a Recurring Invoice"),
    ("how do I create a credit note", "Creating a Credit Note"),
    ("how do I issue a refund to a customer", "Creating a Credit Note"),
    ("how do I add a new client", "Adding a Customer"),
    ("how do I edit a customer", "Adding a Customer"),
    ("how do I delete a customer", "Adding a Customer"),
    ("how do I add tax to an invoice", "Adding / Applying Tax (VAT)"),
    ("how do I set up a VAT rate", "Adding / Applying Tax (VAT)"),
    ("how do I create a tax rule", "Creating a Tax Rule"),
    ("how do I apply reverse charge automatically", "Creating a Tax Rule"),
    ("how do I use the tax calculator", "Using the Tax Calculator"),
    ("how do I register for VAT in accutax", "Setting Up VAT Registration (VAT Configuration)"),
    ("how do I change my VAT filing frequency", "Setting Up VAT Registration (VAT Configuration)"),
    ("where can I see the VAT summary", "Viewing the VAT Summary"),
    ("how do I file my VAT return", "Filing a VAT Return (FTA Form 201)"),
    ("how do I submit VAT to FTA", "Filing a VAT Return (FTA Form 201)"),
    ("show me the P&L", "Viewing the Profit & Loss Report"),
    ("where is the balance sheet", "Viewing the Balance Sheet"),
    ("how do I see the cash flow statement", "Viewing the Cash Flow Statement"),
    ("how do I see AR aging", "Viewing the Aged Receivables (AR Aging) Report"),
    ("how do I check overdue invoices", "Viewing the Aged Receivables (AR Aging) Report"),
    ("how do I see the trial balance", "Viewing Other Financial Reports"),
    ("how do I record a payment against an invoice", "Recording a Customer Payment"),
    ("how do I record an advance payment from a customer", "Recording a Customer Payment"),
    ("how do I record an expense", "Recording an Expense or Vendor Bill"),
    ("how do I enter a vendor bill", "Recording an Expense or Vendor Bill"),
    ("how do I record a vendor invoice", "Recording an Expense or Vendor Bill"),
    ("how do I scan a receipt", "Recording an Expense or Vendor Bill"),
    ("how do I create a purchase order", "Creating a Purchase Order"),
    ("how do I convert a PO to an expense", "Creating a Purchase Order"),
    ("how do I record petty cash", "Recording a Cash Expense"),
    ("how do I create a debit note", "Recording a Vendor Credit (Debit Note)"),
    ("how do I return goods to a supplier", "Recording a Vendor Credit (Debit Note)"),
    ("how do I pay a supplier", "Recording a Supplier Payment"),
    ("how do I record an advance payment from a vendor", "Recording a Supplier Payment"),
    ("how do I add a new vendor", "Adding a Vendor / Supplier"),
    ("how do I update supplier details", "Adding a Vendor / Supplier"),
    ("how do I reconcile my bank account", "Bank Reconciliation"),
    ("how do I create a service in accutax", "Creating an Item / Product"),
    ("how do I create a journal entry", "Creating a Manual Journal Entry"),
    ("how do I make an adjusting entry", "Creating a Manual Journal Entry"),
    ("how do I reverse a journal entry", "Viewing & Reversing Journal Entries"),
    ("where do I enter my TRN", "Setting Up the Company Profile"),
    ("how do I invite a user", "Inviting Users and Managing Roles"),
    ("how do I add my logo to invoices", "Customizing Document Templates"),
    ("how do I add a custom field", "Adding Custom Fields"),
    ("how do I add an exchange rate", "Managing Currencies and Exchange Rates"),
    ("how do I import data from quickbooks", "Importing Data"),
    ("how do I add a branch", "Adding a Branch"),
    ("how do I create a project", "Adding a Project"),
    ("how do I add a cost center", "Adding a Cost Center"),
    ("how do I connect google drive", "Connecting Google Drive, Dropbox or SharePoint"),
    ("how do I see my invoices", "Creating an Invoice"),
    ("how do I create a delivery note", "Creating a Delivery Note"),
    ("how do I convert a delivery note to an invoice", "Creating a Delivery Note"),
    ("how do I add an account to the chart of accounts", "Managing the Chart of Accounts"),
    ("where is the COA", "Managing the Chart of Accounts"),
    ("how do I add an expense category", "Adding Expense Categories"),
    ("how do I adjust stock after a physical count", "Recording an Inventory Adjustment"),
    ("how do I record damaged inventory", "Recording an Inventory Adjustment"),
    ("how do I process invoices emailed by suppliers", "Processing Supplier Invoices in the Email Inbox"),
    ("what is my accutax inbox email", "Processing Supplier Invoices in the Email Inbox"),
    ("how do I upload a document", "Uploading and Organizing Documents"),
    ("how do I create a folder for files", "Uploading and Organizing Documents"),
    ("how do I set up bank rules", "Setting Up Bank Auto-Match Rules"),
    ("how do I auto match bank transactions", "Setting Up Bank Auto-Match Rules"),
    ("how do I change invoice numbering", "Setting Invoice Numbering"),
    ("how do I set the invoice number prefix", "Setting Invoice Numbering"),
    ("how do I see sales by customer", "Viewing Sales by Customer and Statements of Account"),
    ("how do I get a customer statement", "Viewing Sales by Customer and Statements of Account"),
    ("how do I change my password", "Changing Your Password"),
    ("how do I enable two factor authentication", "Enabling Two-Factor Authentication (MFA)"),
    ("how do I set up MFA with google authenticator", "Enabling Two-Factor Authentication (MFA)"),
]


@pytest.mark.parametrize("query,expected", MATCH_CASES)
def test_query_matches_expected_section(query, expected):
    coverage = gl.guide_coverage_for(query)
    assert coverage["matched_section"] == expected
    assert coverage["status"] == "verified"


@pytest.mark.parametrize(
    "query",
    ["what is EBITDA", "asdkjaslkdj random gibberish", "how do I add something", ""],
)
def test_unrelated_or_verb_only_query_is_no_match(query):
    coverage = gl.guide_coverage_for(query)
    assert coverage["status"] == "no_match"
    assert coverage["matched_section"] is None
    assert coverage["app_url"] is None


def test_vendor_context_never_matches_customer_sections():
    for query in ["how do I record a payment to a vendor", "how do I create a credit note from a supplier"]:
        matched = gl.guide_coverage_for(query)["matched_section"]
        assert matched not in {"Recording a Customer Payment", "Creating a Credit Note", "Creating an Invoice"}


def test_app_url_uses_configured_base(monkeypatch):
    monkeypatch.setattr(settings, "accutax_app_url", "https://app.example.com/")
    coverage = gl.guide_coverage_for("how do I create a purchase order")
    assert coverage["app_url"] == "https://app.example.com/create-expense/purchase-order"


def test_app_url_is_none_without_base(monkeypatch):
    monkeypatch.setattr(settings, "accutax_app_url", "")
    assert gl.guide_coverage_for("how do I create an invoice")["app_url"] is None


def test_prompt_guide_has_no_internal_paths():
    prompt = gl.load_app_guide_for_prompt()
    assert PATH_RE.findall(prompt) == []
    assert "Route:" not in prompt


def test_context_contains_matched_section_only():
    context = gl.guide_context_for("how do I create a purchase order")
    assert "## Creating a Purchase Order" in context
    assert "## Bank Reconciliation" not in context
    assert "Sidebar layout" in context
    assert PATH_RE.findall(context) == []
    assert "Route:" not in context


def test_context_is_much_smaller_than_full_guide():
    context = gl.guide_context_for("how do I create a purchase order")
    assert len(context) < len(gl.load_app_guide_for_prompt()) / 3


def test_context_caps_section_count():
    context = gl.guide_context_for("how do I record a payment for an invoice to a customer")
    assert context.count("\n## ") + context.startswith("## ") <= gl.CONTEXT_SECTIONS


def test_no_match_context_is_orientation_only():
    context = gl.guide_context_for("what is EBITDA")
    assert "Sidebar layout" in context
    assert "## " not in context
