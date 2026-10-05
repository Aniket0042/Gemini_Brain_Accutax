"""FTA document records, tier rules (R1) and the superseded list (R2)."""
from __future__ import annotations

import csv
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

CORE, EXTRA, SKIP = "core", "extra", "skip"

# Subjects outside UAE VAT. "Unincorporated" must not match, hence the word boundary.
_OTHER_TAX = re.compile(
    r"\bcorporat|\btobacco|\bexcise|economic substance|decree-law no\. 7 of 2017|seized and abandoned", re.I)

# Tax-agent and authority housekeeping: useful context, not VAT law.
_ADMIN = re.compile(
    r"tax agen(t|cy)|professional (development|standards)|establishment of the federal tax authority|"
    r"refund of fees of private clarification", re.I)

# Guide titles that are technical VAT guidance; everything else in "VAT Guides" is a portal manual.
_TECHNICAL_GUIDE = re.compile(
    r"\bVATG|sector\b|\bscheme\b|apportionment|administrative exceptions guide|charities guide|"
    r"tax groups guide|mosques|real estate guide|^e-commerce$|financial services|insurance guide|"
    r"designated zones vat guide|taxable person guide|private clarifications", re.I)

# R2: replaced by later law. Reviewed list; extend when the FTA consolidates a law or decision.
SUPERSEDED = {
    r"^amendment of penalties$": "TAXP001, 2021 penalty regime (replaced by Cabinet Decision 129/2025)",
    r"^redetermination of administrative penalties levied prior": "TAXP002/TAXP004, penalties before 2021",
    r"^cabinet decision no\. 49 of 2021": "merged into the consolidated Cabinet Decision 40/2017",
    r"^performing the function of director on a board of directors by a natural person$": "VATP031, replaced by VATP037",
    r"^directors services$": "2018 guide; director services by natural persons are out of scope since 2023",
    r"^date of supply for independent directors$": "pre-2023 director treatment",
    r"^temporary zero-rating of certain medical equipment$": "expired temporary measure",
    r"^fta decision no\.? 1 of 2019 on maximum amount of cash refunds": "AED 7,000 cash limit replaced by FTA Decision No. 6 of 2022 (AED 35,000)",
}


def superseded_reason(title: str) -> str | None:
    t = title.strip().lower()
    for pattern, reason in SUPERSEDED.items():
        if re.search(pattern, t):
            return reason
    return None


# R2 for single passages: a guide that is otherwise current can still quote a rule replaced later.
# Matching chunks are skipped at query time; the rest of the guide stays searchable.
STALE_PASSAGES = {
    r"1\s*%\s*daily penalty|4\s*%\s*is due on the seventh day":
        "2017 late-payment schedule (2%, 4%, 1% daily), replaced in 2021 and by Cabinet Decision 129/2025",
    # Designated Zones VAT Guide (2018), section 3.5 and its summary table: a zone sale to a buyer who
    # imports the goods was taxed, with the import VAT recovered. Article 51(5)(c) as amended by Cabinet
    # Decision 88/2021 makes that sale outside the scope once import VAT is paid (VATP027).
    r"irrespective of the person.s normal input tax recovery percentage|subject to VAT again when imported by the same person"
    r"|VAT incurred in respect of the purchase and import"
    r"|intended to be consumed by the purchaser.{0,120}Supplier charges VAT on sale":
        "2018 designated-zone treatment of goods sold in a zone and imported to the mainland, replaced by "
        "Article 51(5)(c) (Cabinet Decision 88/2021, VATP027)",
}
_STALE = [(re.compile(p, re.I | re.S), reason) for p, reason in STALE_PASSAGES.items()]


def stale_passage_reason(text: str) -> str | None:
    for pattern, reason in _STALE:
        if pattern.search(text):
            return reason
    return None


def classify(title: str, category: str, from_legislation: bool) -> str:
    """Tier for one FTA listing item: core (indexed), extra (portal manuals, bulletins) or skip."""
    if "archive" in category.lower() or _OTHER_TAX.search(title) or superseded_reason(title):
        return SKIP
    if from_legislation:
        return EXTRA if _ADMIN.search(title) else CORE
    if category == "Public Clarifications":
        return CORE
    if category in ("VAT Guides", "General Procedure", "General Procedures"):
        return CORE if _TECHNICAL_GUIDE.search(title) and not _ADMIN.search(title) else EXTRA
    return EXTRA


@dataclass
class Document:
    """One downloadable FTA file. An FTA listing item with several PDFs becomes several documents."""
    title: str
    category: str
    source_page: str          # "legislation" or "guides"
    issue_date: str           # as shown on the FTA page, e.g. "Nov 04, 2025" or "NA"
    url: str
    file: str                 # file name under pdfs/
    tier: str
    effective_date: str = ""  # filled in by the build from the PDF text

    @property
    def doc_type(self) -> str:
        return "statute" if self.source_page == "legislation" else "guidance"


def write_documents(path: Path, docs: list[Document]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[x.name for x in fields(Document)])
        writer.writeheader()
        writer.writerows(asdict(d) for d in docs)


def read_documents(path: Path) -> list[Document]:
    with open(path, newline="", encoding="utf-8") as f:
        return [Document(**row) for row in csv.DictReader(f)]
