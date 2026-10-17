"""
policy.py — What each field the model may see is, and what the privacy layer does with it (PII plan, phase 1).

Every key in a tool result or tool input falls in one class:

  token      names a person, business or contact detail: replaced by a typed placeholder ([SUPPLIER_3], [TRN_1])
  free_text  descriptions, references, notes: kept, but searched for names and identifiers inside
  keep       figures, dates, document numbers, ids, statuses, account and item names: passed as they are and
             never scanned, so a pattern can never damage a figure
  scan       any other text: kept, searched for identifiers by pattern only

Amounts and VAT stay visible to the model (decision D1, 17 Oct 2026): the model totals, compares and explains
them, and the figure verifier checks them. Names of public bodies, banks and utilities stay visible too: they
tell the model what a payment is ("DEWA" is electricity), and they identify no private person or customer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

TOKEN, FREE_TEXT, KEEP, SCAN = "token", "free_text", "keep", "scan"


@dataclass(frozen=True)
class FieldRule:
    cls: str
    #: The placeholder kind for token fields: SUPPLIER, CUSTOMER, PARTY, PERSON, ORG, TRN, EMAIL, PHONE, IBAN, ADDRESS.
    kind: Optional[str] = None


_SUPPLIER_KEYS = {"vendor_name", "supplier_name", "seller_name", "payee", "payee_name", "vendor", "supplier"}
_CUSTOMER_KEYS = {"customer_name", "buyer_name", "payer", "payer_name", "customer", "buyer"}
_PARTY_KEYS = {"contact_name", "contactname", "counterparty_name", "counterparty", "party_name", "beneficiary",
               "beneficiary_name", "account_holder", "account_holder_name", "bill_to", "ship_to"}
_PERSON_KEYS = {"employee_name", "person_name", "salesperson", "sales_person", "created_by_name", "approver_name"}
_ORG_KEYS = {"organization_name", "organisation_name", "org_name", "company_name", "entity"}
_TRN_KEYS = {"trn", "tax_id", "vat_number", "vat_registration_number", "tax_registration_number", "gstin", "pan"}
_FREE_TEXT_KEYS = {"description", "line_description", "journal_description", "memo", "narration", "notes", "note",
                   "reference", "remarks", "credit_reason", "text", "comment", "comments", "particulars"}
_KEEP_KEYS = {
    "amount", "total", "net", "gross", "vat", "tax", "balance", "outstanding", "price", "quantity", "rate", "date",
    "period", "month", "year", "quarter", "week", "number", "id", "status", "currency", "document_type", "type",
    "account_name", "account_code", "account_type", "item_name", "item_sku", "sku", "category", "project_name",
    "aging_bucket", "document_weekday", "granularity", "view", "measure", "dimension", "rule", "finding", "severity",
    "kind", "unit", "uom", "branch_name", "warehouse_name", "bank_account_name", "vat_category", "tax_code",
}
_KEEP_SUFFIXES = (
    "_amount", "_total", "_vat", "_tax", "_net", "_gross", "_balance", "_price", "_quantity", "_qty", "_rate", "_pct",
    "_percent", "_date", "_at", "_period", "_month", "_year", "_number", "_no", "_id", "_ids", "_code", "_count",
    "_status", "_type", "_category", "_days", "_bucket", "_sales", "_purchases", "_revenue", "_cost", "_expense",
    "_expenses", "_profit", "_margin", "_vat_rate", "_outstanding", "_paid", "_due",
)


def field_rule(key: Any) -> FieldRule:
    """The class of one JSON field, from its key. Cube keys are qualified ("purchases.vendor_trn"): the part after
    the last dot decides."""
    k = str(key).rsplit(".", 1)[-1].strip().lower()
    if k in _SUPPLIER_KEYS:
        return FieldRule(TOKEN, "SUPPLIER")
    if k in _CUSTOMER_KEYS:
        return FieldRule(TOKEN, "CUSTOMER")
    if k in _PARTY_KEYS:
        return FieldRule(TOKEN, "PARTY")
    if k in _PERSON_KEYS:
        return FieldRule(TOKEN, "PERSON")
    if k in _ORG_KEYS:
        return FieldRule(TOKEN, "ORG")
    if k in _TRN_KEYS or k.endswith("_trn") or k.endswith("_tax_id") or k.endswith("_gstin"):
        return FieldRule(TOKEN, "TRN")
    if "email" in k:
        return FieldRule(TOKEN, "EMAIL")
    if "phone" in k or "mobile" in k:
        return FieldRule(TOKEN, "PHONE")
    if "iban" in k:
        return FieldRule(TOKEN, "IBAN")
    if k == "address" or k.endswith("_address"):
        return FieldRule(TOKEN, "ADDRESS")
    if k in _FREE_TEXT_KEYS or k.endswith("_description") or k.endswith("_memo") or k.endswith("_notes"):
        return FieldRule(FREE_TEXT)
    if k in _KEEP_KEYS or k.endswith(_KEEP_SUFFIXES):
        return FieldRule(KEEP)
    return FieldRule(SCAN)


#: Placeholder kind -> the kind the sentinel and its report count.
REPORT_KIND = {"SUPPLIER": "PARTY_NAME", "CUSTOMER": "PARTY_NAME", "PARTY": "PARTY_NAME", "PERSON": "PARTY_NAME",
               "ORG": "ORG_NAME"}


def report_kind(kind: str) -> str:
    return REPORT_KIND.get(kind, kind)


# ── Names ───────────────────────────────────────────────────────────────────

#: Legal-form words dropped from the end of a name before comparing ("Al Noor Trading L.L.C" = "AL NOOR TRADING LLC").
LEGAL_SUFFIXES = {
    "llc", "l l c", "fze", "fzco", "fz", "fzc", "fz llc", "ltd", "limited", "est", "establishment", "co", "company",
    "pvt", "private", "pvt ltd", "private limited", "inc", "corp", "corporation", "plc", "pjsc", "psc", "wll", "spc",
    "llp", "sole proprietorship", "sole proprietorship llc", "dmcc", "opc",
}
_SUFFIX_WORDS = sorted(LEGAL_SUFFIXES, key=lambda s: -len(s))


def normalize_name(name: Any) -> str:
    """A name in the form two spellings of the same name share: lower case, '&' as 'and', no punctuation, single
    spaces, no leading 'the', no legal form at the end."""
    s = str(name or "").casefold().replace("&", " and ")
    s = re.sub(r"[^\w\s]|_", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    if s.startswith("the "):
        s = s[4:]
    changed = True
    while changed and s:
        changed = False
        for suffix in _SUFFIX_WORDS:
            if s.endswith(" " + suffix) and len(s) > len(suffix) + 1:
                s = s[: -len(suffix) - 1].rstrip()
                changed = True
                break
    return s


#: Public bodies, banks, utilities and large companies whose names stay visible, with what they are.
#: Keys are normalized names.
_PUBLIC: Dict[str, str] = {}
for _hint, _names in {
    "government": ("federal tax authority", "fta", "dubai municipality", "abu dhabi municipality", "rta",
                   "roads and transport authority", "dubai customs", "abu dhabi customs", "ministry of human resources "
                   "and emiratisation", "mohre", "general directorate of residency and foreigners affairs", "gdrfa",
                   "icp", "federal authority for identity citizenship customs and port security", "dubai police",
                   "dubai courts", "dubai economy and tourism", "ded", "department of economic development",
                   "trakhees", "jafza", "dmcc authority", "income tax department", "gst council", "cbic"),
    "utility": ("dewa", "dubai electricity and water authority", "sewa", "sharjah electricity water and gas authority",
                "addc", "aadc", "fewa", "etihad water and electricity", "empower", "tabreed", "salik", "darb",
                "adnoc distribution", "enoc", "emarat", "nakheel"),
    "telecom": ("etisalat", "e and", "du", "emirates integrated telecommunications", "virgin mobile"),
    "bank": ("emirates nbd", "adcb", "abu dhabi commercial bank", "fab", "first abu dhabi bank", "mashreq",
             "mashreq bank", "rakbank", "national bank of ras al khaimah", "dubai islamic bank", "dib", "adib",
             "abu dhabi islamic bank", "cbd", "commercial bank of dubai", "hsbc", "standard chartered", "citibank",
             "emirates islamic", "ajman bank", "sharjah islamic bank", "bank of baroda", "state bank of india",
             "hdfc bank", "icici bank", "axis bank", "kotak mahindra bank", "wio bank", "liv"),
    "airline": ("emirates", "emirates airline", "flydubai", "etihad", "etihad airways", "air arabia", "air india",
                "indigo"),
    "logistics": ("aramex", "dhl", "fedex", "ups", "emirates post", "india post", "blue dart"),
    "retail": ("carrefour", "lulu", "lulu hypermarket", "spinneys", "choithrams", "amazon", "noon", "ikea", "ace"),
    "software": ("microsoft", "google", "apple", "adobe", "zoho", "oracle", "aws", "amazon web services", "openai",
                 "anthropic", "slack", "atlassian", "dropbox", "tally"),
}.items():
    for _n in _names:
        _PUBLIC[normalize_name(_n)] = _hint


def public_entity(name: Any) -> Optional[str]:
    """What a public body, bank or utility is ("bank", "utility" ...), or None for any other name."""
    return _PUBLIC.get(normalize_name(name))
