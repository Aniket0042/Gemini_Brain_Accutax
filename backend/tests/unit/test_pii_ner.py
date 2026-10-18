"""Names the books do not know, found in the user's questions (privacy/ner.py, red-team gap pr28/pr29)."""
import json

import pytest

from gemini_brain.agent import loop
from gemini_brain.privacy import dictionary, ner, policy
from gemini_brain.privacy.scrubber import Scrubber
from gemini_brain.privacy.vault import Vault

from tests.unit.test_pii_masking import FakeAdapter, META, ORGS, _all_sent_text, _final, _tool_use, agent  # noqa: F401

#: Ordinary finance questions: capitalized terms, acronyms, places, months, account names, public bodies, banks.
#: Nothing in them may be masked.
CLEAN = [
    "What is our VAT payable for Q3 2026?",
    "Show the P&L for FY 2025-26 by month.",
    "Compare revenue in January and February.",
    "What is the standard VAT rate in the UAE?",
    "How much input VAT can we reclaim this quarter?",
    "List overdue invoices above AED 10,000.",
    "Which customers are missing a TRN?",
    "What are DSO and DPO today?",
    "Show total revenue MTD, QTD and YTD.",
    "Give me the Balance Sheet as of 30 September.",
    "What is our Corporate Tax liability for this tax period?",
    "Do we need to register for Corporate Tax in Dubai?",
    "How is VAT charged on supplies to a Designated Zone in Jebel Ali?",
    "What is the penalty for late VAT return filing with the FTA?",
    "Show expenses under Office Rent for this year.",
    "How much did we spend on Professional Fees in Q2?",
    "Break down Cost of Goods Sold by month.",
    "Chart net sales by month this year.",
    "Rank top 5 customers by revenue.",
    "Export the Trial Balance to Excel.",
    "Give me a PDF report of top suppliers.",
    "Which bills are due next week?",
    "What did we pay to DEWA last month?",
    "How much did we pay Etisalat in August?",
    "Show all payments to the Federal Tax Authority.",
    "What is the balance in our Emirates NBD account?",
    "Total bank charges from ADCB this year?",
    "Is e-invoicing mandatory from July 2026?",
    "What is the reverse charge mechanism for imports from India?",
    "Do we charge VAT on exports to Saudi Arabia?",
    "What VAT applies to residential rent in Sharjah?",
    "Compare Abu Dhabi and Dubai branch sales.",
    "Show sales for the Ras Al Khaimah branch.",
    "What is our gross margin for Ramadan this year?",
    "How did EBITDA change from H1 to H2?",
    "Show the Accounts Receivable aging report.",
    "What is the Accounts Payable balance today?",
    "Draft a payment reminder for the oldest overdue invoice.",
    "Submit the VAT return.",
    "Explain the Profit Margin Scheme for used cars.",
    "What is the Tourist Refund Scheme?",
    "Can we claim input tax on Business Entertainment?",
    "Which entities have a VAT return due this month?",
    "Show Salaries and Wages for the last 3 months.",
    "What is the Gratuity provision this year?",
    "Show Depreciation on Fixed Assets.",
    "Which items in Inventory are below reorder level?",
    "How much Withholding Tax applies to non-residents?",
    "What is the Small Business Relief threshold?",
    "Show cash flow for September 2026.",
    "Compare Marketing Expenses to Revenue.",
    "What were our sales on Monday?",
    "Why did margins drop in Q3 compared with Q2?",
    "Are we VAT filing-ready for the current period?",
    "Show me Electricity and Water expenses.",
    "What is the Free Zone Person qualifying income rule?",
    "Do Qualifying Free Zone Persons pay 0% Corporate Tax?",
    "Show sales of Laptops and Printers this year.",
    "Which Projects are over budget?",
    "What is the GST rate in India for services?",
    "How do we convert USDT receipts to AED for VAT?",
    "How much did we spend on Google Ads and Microsoft licences?",
    "Show the General Ledger for Bank Charges.",
    "What does Article 48 of the VAT Decree-Law say?",
    "List Credit Notes issued in October.",
    "Show Journal Entries posted on 15 October.",
    "What is the VAT on Zero Rated supplies?",
    "Total purchases from Amazon this year?",
    "Show Opening Balance Equity.",
    "Please show me the Income Statement for Q1.",
    "Hello, what can you do?",
    "Thanks! Now break it down by Branch.",
    "Show Sales Returns for Dubai Mainland.",
    "What's our Net Profit after Tax?",
    "Which customers in Ajman owe us the most?",
    "When does e-invoicing become mandatory for a business with AED 60 million revenue?",
    "We receive payment in USDT. How do we convert it to AED for VAT?",
    "What are the current late payment penalties for VAT?",
    "Is the TRN 100234567800003 valid?",
    "Pay to IBAN AE070331234567890123456 (Emirates NBD)",
    "Is [SUPPLIER_1] overdue?",
    "من هم أكبر 5 عملاء من حيث المبيعات هذا العام؟",
]

#: Names the books do not know, and what must be hidden.
NAMES = [
    ("Do we have any bills from Al Noor Trading LLC?", ["Al Noor Trading LLC"]),
    ("Rajesh Kumar from our team asked for the sales total this month. What is it?", ["Rajesh Kumar"]),
    ("How much do we owe Zenith Foods FZE?", ["Zenith Foods FZE"]),
    ("Show invoices to Kumar & Sons Pvt Ltd this year.", ["Kumar & Sons Pvt Ltd"]),
    ("Priya Sharma approved the payment last week. Which bills did she approve?", ["Priya Sharma"]),
    ("Did we pay Mohammed Al Mansoori in September?", ["Mohammed Al Mansoori"]),
    ("Show bills from Gulf Star General Trading.", ["Gulf Star General Trading"]),
    ("Payments to Blue Ocean Contracting LLC this quarter?", ["Blue Ocean Contracting LLC"]),
    ("John Smith wants the VAT return summary.", ["John Smith"]),
    ("Is there a supplier called Falcon Eye Technologies FZCO?", ["Falcon Eye Technologies FZCO"]),
    ("What does customer Ahmed Hassan owe us?", ["Ahmed Hassan"]),
    ("List invoices for client Sunrise Hospitality Ltd.", ["Sunrise Hospitality Ltd"]),
    ("Check bills from Emirates Steel Industries PJSC.", ["Emirates Steel Industries PJSC"]),
    ("Our accountant Fatima Khan needs the aging report.", ["Fatima Khan"]),
    ("Send the statement to Ravi Menon.", ["Ravi Menon"]),
    ("Did Desert Rose Trading send a credit note?", ["Desert Rose Trading"]),
    ("How much did we pay Anil Kapoor for consulting?", ["Anil Kapoor"]),
    ("Compare purchases from Alpha Tech LLC and Beta Supplies FZE.", ["Alpha Tech LLC", "Beta Supplies FZE"]),
    ("Bills from Al Noor Trading L.L.C. this year", ["Al Noor Trading L.L.C."]),
]


@pytest.mark.parametrize("question", CLEAN)
def test_ordinary_questions_mask_nothing(question):
    assert ner.find(question) == []
    without = Scrubber(Vault(), dictionary.Dictionary()).text(question, partial=True)   # identifiers only
    s = Scrubber(Vault(), dictionary.Dictionary())
    assert s.learn([question]) == []
    assert s.text(question, partial=True) == without


def test_the_clean_list_is_long_enough():
    assert len(CLEAN) >= 50


@pytest.mark.parametrize("question,names", NAMES)
def test_names_not_in_the_books_are_found(question, names):
    assert [f.text for f in ner.find(question)] == names
    s = Scrubber(Vault(), dictionary.Dictionary())
    masked = s.text(question, partial=True) if s.learn([question]) else question
    for name in names:
        assert name not in masked
    # Back to the user's exact text; a full stop after "Ltd" may go into the match ("Ltd."), as for the books' names.
    assert s.vault.rehydrate(masked).rstrip(".") == question.rstrip(".")


def test_people_are_persons_and_companies_parties():
    assert [f.kind for f in ner.find("Rajesh Kumar asked about bills from Al Noor Trading LLC")] == ["PERSON", "PARTY"]


def test_the_selected_organizations_and_known_names_stay_with_the_dictionary():
    org = "Professional & Consulting Services_User1_Org4"
    names = dictionary.Dictionary([dictionary.Entry(org, "ORG", policy.normalize_name(org)),
                                   dictionary.Entry("Zenith Foods FZE", "SUPPLIER", "zenith foods")])
    s = Scrubber(Vault(), names)
    assert s.learn([f"Compare {org} with last year; bills from Zenith Foods FZE?"]) == []
    assert s.text(f"Compare {org} with last year; bills from Zenith Foods FZE?", partial=True) == \
        "Compare [ORG_1] with last year; bills from [SUPPLIER_1]?"


def test_public_bodies_places_and_months_are_never_names():
    for q in ("Payments to Emirates NBD and DEWA in March", "Bills from Dubai Municipality", "Sales in Sharjah in May",
              "Did we pay the Federal Tax Authority?", "invoices from Abu Dhabi", "bills from Etisalat"):
        assert ner.find(q) == [], q


def test_learned_names_are_hidden_everywhere_in_the_request():
    s = Scrubber(Vault(), dictionary.Dictionary())
    assert s.learn(["Rajesh Kumar asked: any bills from Al Noor Trading LLC?"]) == ["Rajesh Kumar", "Al Noor Trading LLC"]
    # An earlier answer, a tool result's free text, and a later short mention in the question.
    assert s.text("Rajesh Kumar has 2 bills; Al Noor Trading has none.") == "[PERSON_1] has 2 bills; [PARTY_1] has none."
    assert s.json({"description": "Paid AL NOOR TRADING LLC", "amount": "100"}) == \
        {"description": "Paid [PARTY_1]", "amount": "100"}
    assert s.text("and Rajesh?", partial=True) == "and [PERSON_1]?"
    assert s.vault.rehydrate("[PARTY_1] for [PERSON_1]") == "Al Noor Trading LLC for Rajesh Kumar"


def test_without_spacy_legal_forms_are_still_found(monkeypatch):
    monkeypatch.setattr(ner, "_nlp", None)
    monkeypatch.setattr(ner, "_failed", True)
    assert [f.text for f in ner.find("Bills from Al Noor Trading LLC for Rajesh Kumar")] == ["Al Noor Trading LLC"]
    assert ner.find("What is our VAT payable for Q3?") == []


# ── The agent ────────────────────────────────────────────────────────────────

def test_agent_hides_a_company_not_in_the_books_and_filters_on_the_typed_name(monkeypatch, agent):  # noqa: F811
    monkeypatch.setattr(loop.settings, "pii_mode", "on")
    FakeAdapter.script = [
        _tool_use("list_documents", {"type": "bills", "filters": [
            {"member": "purchases.vendor_name", "operator": "contains", "values": ["[PARTY_1]"]}]}),
        _final("I found no bills from [PARTY_1].")]
    result = loop.run_agent("Do we have any bills from Gulf Star General Trading?", ORGS, META)
    sent = _all_sent_text()
    assert "Gulf Star" not in sent and "[PARTY_1]" in sent
    assert agent[0]["filters"][0]["values"] == ["Gulf Star General Trading"]
    assert result.answer == "I found no bills from Gulf Star General Trading."
    assert result.privacy["unresolved"] == 0 and result.privacy["names_learned"] == 1


def test_agent_hides_a_person_typed_in_an_earlier_turn_and_repeated_in_its_answer(monkeypatch, agent):  # noqa: F811
    monkeypatch.setattr(loop.settings, "pii_mode", "on")
    FakeAdapter.script = [_final("[PERSON_1] can have the total: AED 1,000.")]
    history = [{"role": "user", "content": "Rajesh Kumar from our team asked for the sales total this month."},
               {"role": "assistant", "content": "Rajesh Kumar: sales this month are AED 1,000."}]
    result = loop.run_agent("Can you write that up for him?", ORGS, META, history=history)
    assert "Rajesh" not in _all_sent_text() and "Kumar" not in _all_sent_text()
    assert result.answer == "Rajesh Kumar can have the total: AED 1,000."


def test_agent_leaves_an_ordinary_question_as_written(monkeypatch, agent):  # noqa: F811
    monkeypatch.setattr(loop.settings, "pii_mode", "on")
    FakeAdapter.script = [_final("AED 5.")]
    q = "Compare VAT payable for Q2 2026 in Dubai and Sharjah, and the P&L YTD."
    loop.run_agent(q, ORGS, META)
    sent = json.loads(_all_sent_text())
    assert sent[0]["messages"][-1]["content"][0]["text"] == q
