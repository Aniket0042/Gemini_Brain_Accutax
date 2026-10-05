"""Unit tests for the VAT knowledge base (gemini_brain.vat_kb). No network, no model downloads:
search tests use a small fake embedder over a hand-built build directory."""
import json
from decimal import Decimal
from pathlib import Path

import numpy as np
import pytest

from gemini_brain.vat_kb import calc, documents, expansion, text
from gemini_brain.vat_kb.fta_crawler import FtaListing, absolute_url, file_name
from gemini_brain.vat_kb.index import Chunk
from gemini_brain.vat_kb.retrieval import KnowledgeBase, rrf, tokenize


# ── R1 / R2: tiers and superseded documents ───────────────────────────────────
@pytest.mark.parametrize("title,category,legislation,tier", [
    ("Federal Decree-Law No. 8 of 2017 and amendments", "VAT", True, "core"),
    ("Directive on Tax Transactions No. 5 of 2026 for Value Added Tax", "VAT", True, "core"),
    ("Federal Decree-Law No. 7 of 2017 and amendments", "Federal Tax Procedures", True, "skip"),
    ("Federal Tax Authority Decision No. 15 of 2023 on Professional Development Requirements for Natural Person Tax Agents",
     "Federal Tax Authority", True, "extra"),
    ("Cabinet Decision No. 63 of 2025 on Unincorporated Partnership", "Federal Tax Procedures", True, "core"),
    ("FTA Decision No. 3 of 2021 and its amendments on Implementing the Marking of Tobacco", "Federal Tax Authority", True, "skip"),
    ("Transfer of a Business as a Going Concern", "Public Clarifications", False, "core"),
    ("Real Estate Guide", "VAT Guides", False, "core"),
    ("Profit Margin Scheme | VATGPM1", "VAT Guides", False, "core"),
    ("VAT Registration", "VAT Guides", False, "extra"),
    ("Taxpayers Bulletin – March 2025", "Tax payer Bulletin", False, "extra"),
    ("VAT Refund for UAE Nationals Building New Residences | VATGRH1", "Archive VAT Guides", False, "skip"),
    ("Amendment of Penalties", "Public Clarifications", False, "skip"),
    ("Cabinet Decision No. 49 of 2021 on Amending some Provisions of Cabinet Decision No. 40 of 2017",
     "Federal Tax Procedures", True, "skip"),
    ("Performing the function of Director on a Board of Directors by a Natural person", "Public Clarifications", False, "skip"),
    ("Performing the function of Director on a Board of Directors by a Natural person - VATP037",
     "Public Clarifications", False, "core"),
])
def test_classify(title, category, legislation, tier):
    assert documents.classify(title, category, legislation) == tier


def test_documents_round_trip(tmp_path):
    doc = documents.Document("Real Estate Guide", "VAT Guides", "guides", "Apr 19, 2021",
                             "https://tax.gov.ae/x.pdf", "real-estate-guide-abc123.pdf", "core")
    documents.write_documents(tmp_path / "documents.csv", [doc])
    assert documents.read_documents(tmp_path / "documents.csv") == [doc]
    assert doc.doc_type == "guidance"


# ── R1: FTA listing parsing ───────────────────────────────────────────────────
LISTING_HTML = """
<form><input type="hidden" name="__VIEWSTATE" value="abc">
<select name="ctl00$x$ddlCategory"><option value="0" selected>All</option><option value="113">VAT</option></select>
<input type="submit" name="ctl00$x$btnSearch" value="Search">
<div class="commonTableNew">
  <div class="row headerTable"><div class="col-md-7">Name</div></div>
  <div class="row"><div class="col-md-7"><div class="d-flex">
      Directive on Tax Transactions No. 3 of 2026 <div class="newIconBlock"><span>New</span></div></div>
      <span class="lastmodifiedDate">Jul 14, 2026</span><span class="tag_category">VAT</span></div>
    <div class="col-md-5"><a href="https://tax.gov.ae//Datafolder/Files/Legislation/2026/dir 3.pdf">pdf</a>
      <a href="javascript:void(0);">Comments</a></div></div>
</div>
<a aria-label="Next page" href="javascript:__doPostBack('ctl00$x$pgList$ctl00$Next','')">&gt;</a>
</form>"""


def test_listing_parse_and_pager():
    from bs4 import BeautifulSoup
    page = BeautifulSoup(LISTING_HTML, "html.parser")
    items = FtaListing._parse(page)
    assert len(items) == 1
    assert items[0].title == "Directive on Tax Transactions No. 3 of 2026"
    assert items[0].issue_date == "Jul 14, 2026" and items[0].category == "VAT"
    assert items[0].links == ["https://tax.gov.ae//Datafolder/Files/Legislation/2026/dir 3.pdf"]
    assert FtaListing._next_page_target(page) == "ctl00$x$pgList$ctl00$Next"
    state = FtaListing._form_state(page)
    assert state["__VIEWSTATE"] == "abc" and state["ctl00$x$ddlCategory"] == "0"
    assert "ctl00$x$btnSearch" not in state


def test_absolute_url_and_file_name():
    url = absolute_url("https://tax.gov.ae//Datafolder/Files/Legislation/2026/dir 3.pdf")
    assert url == "https://tax.gov.ae/Datafolder/Files/Legislation/2026/dir%203.pdf"
    assert absolute_url("/DataFolder/Files/Pdf/x.pdf") == "https://tax.gov.ae/DataFolder/Files/Pdf/x.pdf"
    name = file_name("Charities Guide", url, 1, 3)
    assert name.startswith("charities-guide-2-") and name.endswith(".pdf")


# ── R3 / R4: cleaning, effective date, chunking ───────────────────────────────
def test_clean_removes_arabic_and_rejoins_lines():
    raw = "توجيه رقم 5\nA Taxable Person making a Deemed Supply\nمن الخدمات\nof Services shall determine\n( 37 )\n"
    assert text.clean(raw) == "A Taxable Person making a Deemed Supply of Services shall determine"


def test_effective_date():
    assert text.effective_date("Issued 4 Nov 2025 – (Effective from 14 January 2026) The Cabinet") == "14 January 2026"
    assert text.effective_date("amended with effect from 1 January 2026.") == "1 January 2026"
    assert text.effective_date("no date here") == ""


def test_chunk_sizes_and_overlap():
    sentence = "The supplier shall not account for the Tax on the supply. "
    chunks = text.chunk(sentence * 60, size=1000, overlap=150)
    assert len(chunks) > 1
    assert all(len(c) <= 1000 + 160 for c in chunks)
    # each later chunk starts with text from the end of the previous one
    assert chunks[1][:40] in chunks[0][-200:]


def test_chunk_hard_splits_very_long_sentence():
    chunks = text.chunk("x" * 2500, size=1000, overlap=150)
    assert [len(c) for c in chunks][:2] == [1000, 1000]


def test_header():
    assert text.header("Cabinet Decision No. 153 of 2025", "Nov 04, 2025", "14 January 2026") == \
        "Cabinet Decision No. 153 of 2025 | issued Nov 04, 2025 | effective 14 January 2026"
    assert text.header("Directive No. 5", "NA", "") == "Directive No. 5"


# ── R6: query expansion ───────────────────────────────────────────────────────
@pytest.mark.parametrize("question,expected", [
    ("We receive payment in USDT. How do we convert it to AED for VAT?", "digital currency"),
    ("We sell smartphones to a VAT-registered reseller", "smart phones"),
    ("What are the current late payment penalties for VAT?", "settle the Payable Tax"),
    ("We bought an entire business, including staff and stock", "going concern"),
    ("We re-invoice courier fees to a client at cost", "disbursement"),
    ("What is the criteria for e-invoicing currently?", "implementation phases"),
    ("We reported zero-rated sales in the exempt box of our VAT return", "error or omission"),
])
def test_expand_adds_legal_terms(question, expected):
    expanded = expansion.expand(question)
    assert expanded.startswith(question) and expected in expanded


def test_expand_leaves_other_questions_alone():
    assert expansion.expand("What is the VAT rate in the UAE?") == "What is the VAT rate in the UAE?"


# ── R10: calculations ─────────────────────────────────────────────────────────
def test_bad_debt_question_uses_tax_fraction():
    q = ("An invoice for AED 105,000 including VAT; the customer paid 40%, and the invoice is now "
         "8 months old. How much output tax can we adjust under bad debt relief?")
    lines = calc.calculations_for(q)
    assert any("Unpaid part = AED 105,000.00 x 60% = AED 63,000.00" in l and "AED 3,000.00" in l for l in lines)
    assert any("VAT included in AED 105,000.00" in l and "AED 5,000.00" in l for l in lines)


def test_apportionment_question():
    q = ("Taxable supplies AED 6,000,000, exempt supplies AED 4,000,000, residual input tax AED 100,000. "
         "How much can we recover under the standard method?")
    lines = calc.calculations_for(q)
    assert any("= 60%" in l for l in lines)
    assert any("Recoverable residual input tax = AED 100,000.00 x 60% = AED 60,000.00" in l for l in lines)


def test_amounts_ignore_words_ending_in_d():
    # "zero-rated, such" once matched as "Dh" + "," and crashed the calculation
    q = "Do we have to issue a tax invoice for a supply that is wholly zero-rated, such as an export of goods?"
    assert calc.calculations_for(q) == []
    assert calc._amounts("AED 105,000 and Dhs 2,500 and 60 million AED and 1,000 dirhams") == [
        Decimal("105000"), Decimal("2500"), Decimal("60000000"), Decimal("1000")]


def test_calc_helpers():
    assert calc.vat_in_gross(Decimal("105")) == Decimal("5")
    assert calc.vat_on_net(Decimal("100")) == Decimal("5.00")
    assert calc.recovery_ratio(Decimal("2"), Decimal("1")) == Decimal("67")
    assert calc.calculations_for("What is the VAT rate?") == []


def test_monthly_equivalents_only_for_rates_charged_monthly():
    assert calc.monthly_equivalents(["A monthly penalty of (14%) per annum, for each month or part thereof"]) == [
        "14% per annum charged monthly = 1.17% per month (14 / 12 = 1.1667)"]
    assert calc.monthly_equivalents(["Interest of 5% per annum."]) == []
    assert calc.monthly_equivalents(["monthly 14% per annum", "each month 14% per annum"]) == [
        "14% per annum charged monthly = 1.17% per month (14 / 12 = 1.1667)"]   # once per rate


# ── R5 / R7: search over a tiny build ─────────────────────────────────────────
VOCAB = ["digital", "currency", "penalty", "payable", "settle", "residence", "leased", "director", "invoice", "group"]


class FakeEmbedder:
    """Bag-of-words vectors over VOCAB: deterministic and offline."""
    name = "fake"

    def _vec(self, s):
        words = tokenize(s)
        v = np.array([float(words.count(w)) for w in VOCAB], dtype="float32") + 1e-3
        return v / np.linalg.norm(v)

    def embed_documents(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


def _make_build(tmp_path: Path, rows) -> Path:
    build = tmp_path / "kb" / "builds" / "b1"
    build.mkdir(parents=True)
    chunks = []
    for i, (title, position, body) in enumerate(rows):
        chunks.append(Chunk(i, position, title, "VAT", "statute", "", "", "https://tax.gov.ae/" + title.lower().replace(" ", "-") + ".pdf",
                            f"{title}\n{body}"))
    with open(build / "chunks.jsonl", "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c.__dict__) + "\n")
    np.save(build / "embeddings.npy", FakeEmbedder().embed_documents([c.text for c in chunks]))
    (tmp_path / "kb" / "CURRENT").write_text("b1")
    return build


ROWS = [
    ("Directive 3 digital currency", 0, "Convert the value of digital currency into dirham using three platforms."),
    ("Directive 3 digital currency", 1, "Use the average of the exchange rates published by the platforms."),
    ("Cabinet 129 penalties", 0, "Failure to settle the Payable Tax: monthly penalty of 14% per annum."),
    ("New residence guide", 0, "The refund is for a newly built residence of a UAE national."),
    ("New residence guide", 1, "Expenses must relate to a building used solely as a residence."),
    ("New residence guide", 2, "A request may not be submitted where the building is leased to another person."),
    ("Directors clarification", 0, "Director services by a natural person are not a supply."),
    ("E-invoicing decision", 0, "Electronic invoice issued through the Electronic Invoicing System."),
]


def test_search_finds_expanded_term_and_neighbours(tmp_path):
    kb = KnowledgeBase(_make_build(tmp_path, ROWS), embedder=FakeEmbedder())
    result = kb.search("We receive payment in USDT, how do we convert it?")
    assert result.confident
    assert result.hits[0].chunk.title.startswith("Directive 3")
    assert "digital currency" in result.expanded
    titles = [h.chunk.title for h in result.hits]
    assert titles.count("Directive 3 digital currency") == 2      # matched chunk + its neighbour


def test_search_brings_neighbouring_chunk(tmp_path):
    kb = KnowledgeBase(_make_build(tmp_path, ROWS), embedder=FakeEmbedder())
    result = kb.search("Is a residence still eligible for the refund if solely used as a residence?")
    texts = " ".join(h.chunk.text for h in result.hits)
    assert "leased to another person" in texts


def test_search_reports_weak_match(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import retrieval
    monkeypatch.setattr(retrieval, "STRONG_DENSE", 0.99)
    monkeypatch.setattr(retrieval, "MIN_DENSE", 0.99)
    kb = KnowledgeBase(_make_build(tmp_path, ROWS), embedder=FakeEmbedder())
    result = kb.search("What is the weather tomorrow?")
    assert not result.confident and result.hits == [] and "weak match" in result.reason


def test_sources_are_unique_documents_in_order(tmp_path):
    kb = KnowledgeBase(_make_build(tmp_path, ROWS), embedder=FakeEmbedder())
    result = kb.search("digital currency conversion")
    urls = [s.url for s in result.sources()]
    assert len(urls) == len(set(urls)) and urls[0] == "https://tax.gov.ae/directive-3-digital-currency.pdf"


def test_rerank_keeps_top_fused_results(tmp_path):
    class ReverseReranker:
        def rerank(self, query, docs):
            return list(range(len(docs)))          # prefers the last candidates
    kb = KnowledgeBase(_make_build(tmp_path, ROWS), embedder=FakeEmbedder(), reranker=ReverseReranker(), rerank=True)
    plain = KnowledgeBase(kb.build_dir, embedder=FakeEmbedder())
    q = "settle the payable tax penalty"
    top_plain = [h.chunk.id for h in plain.search(q).hits][:1]
    assert [h.chunk.id for h in kb.search(q).hits][:1] == top_plain


def test_stale_passage_is_never_returned(tmp_path):
    stale = "Late payment penalty: 2% immediately; 4% is due on the seventh day; 1% daily penalty after a month."
    rows = ROWS + [("Owners associations guide", 0, "Owners associations collect service charges."),
                   ("Owners associations guide", 1, stale),
                   ("Owners associations guide", 2, "Service charges are standard rated.")]
    assert documents.stale_passage_reason(stale) and not documents.stale_passage_reason(ROWS[2][2])
    kb = KnowledgeBase(_make_build(tmp_path, rows), embedder=FakeEmbedder())
    for q in ("late payment penalty seventh day daily penalty", "owners associations service charges"):
        texts = [h.chunk.text for h in kb.search(q).hits]
        assert texts and not any("seventh day" in t for t in texts)   # not as a hit, nor as a neighbour


def test_rrf_and_tokenize():
    assert rrf([[5, 6], [5, 7]])[0] == 5            # top of both lists wins
    assert set(rrf([[1, 2], [3]])) == {1, 2, 3}
    assert tokenize("The VAT on a Tax Invoice") == ["vat", "tax", "invoice"]


def test_load_knowledge_base_without_build(tmp_path):
    from gemini_brain.vat_kb.retrieval import load_knowledge_base
    assert load_knowledge_base(tmp_path) is None


def test_one_document_cannot_fill_every_slot(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import retrieval
    monkeypatch.setattr(retrieval, "MAX_PER_DOC", 2)
    monkeypatch.setattr(retrieval, "NEIGHBOURS", ())
    rows = [("Big clarification", i, f"digital currency rule number {i}.") for i in range(6)]
    rows.append(("Small decision", 0, "Digital currency conversion uses an average rate."))
    kb = KnowledgeBase(_make_build(tmp_path, rows), embedder=FakeEmbedder())
    titles = [h.chunk.title for h in kb.search("digital currency", k=3).hits]
    assert titles.count("Big clarification") <= 3          # 2 ranked + its opening chunk
    assert "Small decision" in titles


def test_top_keyword_match_is_always_kept(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import retrieval
    monkeypatch.setattr(retrieval, "NEIGHBOURS", ())
    rows = [(f"Doc {i}", 0, "penalty payable settle tax") for i in range(5)]
    rows.append(("Invoice rule", 0, "A Registrant is not required to issue a tax invoice for a wholly zero-rated supply."))
    kb = KnowledgeBase(_make_build(tmp_path, rows), embedder=FakeEmbedder())
    titles = [h.chunk.title for h in kb.search("wholly zero-rated invoice", k=2).hits]
    assert "Invoice rule" in titles


def test_opening_chunk_of_top_document_is_included(tmp_path, monkeypatch):
    from gemini_brain.vat_kb import retrieval
    monkeypatch.setattr(retrieval, "NEIGHBOURS", ())
    rows = [("Scrap clarification", 0, "Cabinet Decision applies with effect from 14 January 2026.")]
    rows += [("Scrap clarification", i, f"filler text {i} about declarations.") for i in range(1, 8)]
    rows.append(("Scrap clarification", 8, "The recipient accounts for the digital currency tax on scrap."))
    kb = KnowledgeBase(_make_build(tmp_path, rows), embedder=FakeEmbedder())
    texts = " ".join(h.chunk.text for h in kb.search("digital currency recipient", k=1).hits)
    assert "14 January 2026" in texts
