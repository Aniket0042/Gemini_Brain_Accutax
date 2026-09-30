"""guide_loader.py — Source of truth for LEFT-path (App Guidance / FAQ) answers.

For each how-to query, guide_context_for() picks the sidebar orientation plus
the top matching sections of accutax_guide.md, strips route paths, and the
runner injects that into the direct-answer system prompt — not the whole
guide (~9K tokens at 39 sections).

Cached by (path, mtime, size) rather than a plain lru_cache so a content
editor's change to accutax_guide.md is picked up on the next request without
a redeploy or process restart.
"""
from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("gemini_brain.knowledge.guide_loader")

_GUIDE_PATH = Path(__file__).parent / "accutax_guide.md"


@functools.lru_cache(maxsize=1)
def _read_cached(path: str, mtime_ns: int, size: int) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def load_app_guide() -> str:
    """Return the RAW Accutax product guide, or '' if missing/unreadable.

    This is the maintainer-facing copy — it still has the backtick-quoted
    route paths (`/tax-rates`, `Route: ...`) used to build SECTION_ROUTES and
    to keep the doc auditable against the live app. Never inject this
    directly into a user-facing prompt — use load_app_guide_for_prompt()
    for that; it strips those paths so the model can't copy a raw internal
    URL into what it says to a business user. Only guide_coverage_for's
    section parsing (headings + a "TODO" substring check) reads this one
    directly, and neither is affected by route text being present.

    Never raises — a missing guide degrades LEFT-path answers back to the
    model's own knowledge, it must not break the query.
    """
    try:
        stat = _GUIDE_PATH.stat()
    except OSError as e:
        logger.warning("Could not stat Accutax app guide at %s: %s", _GUIDE_PATH, e)
        return ""
    try:
        return _read_cached(str(_GUIDE_PATH), stat.st_mtime_ns, stat.st_size)
    except OSError as e:
        logger.warning("Could not load Accutax app guide from %s: %s", _GUIDE_PATH, e)
        return ""


_ROUTE_LINE_RE = re.compile(r"^[^\n]*\bRoute:[^\n]*\n?", re.MULTILINE)
_BACKTICK_PATH_RE = re.compile(r"`/[^`\s]*`")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"[ \t]+([,.;:)])")


def _sanitize_text(text: str) -> str:
    text = _ROUTE_LINE_RE.sub("", text)
    text = _BACKTICK_PATH_RE.sub("", text)
    text = _EMPTY_PARENS_RE.sub("", text)
    text = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", text)
    return text


@functools.lru_cache(maxsize=1)
def _sanitize(guide_text: str) -> str:
    return _sanitize_text(guide_text)


def load_app_guide_for_prompt() -> str:
    """Return the guide with every internal route path stripped — this is
    the version to inject into the system prompt.

    A raw path like `/tax-rates` or a `Route: ...` traceability line is
    useful for us maintaining the doc, but has no meaning to a business
    user, and the model was observed copying it verbatim into an answer
    (e.g. "Go to the Items section (`/items`)"). That leaks an internal
    implementation detail the same way a raw SQL query or schema name
    would — see NEVER_EXPOSE_BACKEND_RULE. Stripped deterministically here
    rather than left to a prompt instruction, so it can't be missed.
    """
    return _sanitize(load_app_guide())


# ─────────────────────────────────────────────────────────────
# Section matching — picks the guide sections a query is about. Drives what
# is injected into the prompt (guide_context_for), the "Open in Accutax"
# button (SECTION_ROUTES) and coverage observability (agent_trace / METRICS).
# ─────────────────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "for", "to", "in", "on", "at", "is", "are", "do",
    "does", "how", "can", "i", "you", "my", "me", "and", "or", "with", "it",
    "up", "other", "from", "into", "by", "we", "our", "what", "where",
})

#: Generic words, mostly verbs. They still add a little score (so "create a
#: journal entry" leans to the creation section over the viewing one), but
#: a match made of these alone doesn't count — "how do I add something"
#: shouldn't link a random section just because it contains "add".
_VERBS = frozenset({
    "create", "creating", "new", "add", "adding", "make", "record",
    "recording", "enter", "set", "setting", "setup", "view", "viewing", "see",
    "show", "check", "open", "run", "generate", "get", "use", "using",
    "manage", "managing", "change", "edit", "update", "find", "raise",
    "issue", "send", "post", "configure", "connect", "delete", "remove",
    "details", "info", "information",
})

#: Abbreviations users type, expanded before tokenizing (same approach as
#: router/tool_retriever.py).
_ALIASES = (
    (re.compile(r"\bp\s*&\s*l\b", re.I), " profit loss "),
    (re.compile(r"\bpnl\b", re.I), " profit loss "),
    (re.compile(r"\ba/r\b", re.I), " receivables "),
    (re.compile(r"\bje\b", re.I), " journal entry "),
    (re.compile(r"\bpo\b", re.I), " purchase order "),
    (re.compile(r"\bgl\b", re.I), " general ledger "),
    (re.compile(r"\bcoa\b", re.I), " chart accounts "),
)


def _tokenize(text: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP_WORDS and len(t) > 1}


def _expand(query: str) -> str:
    text = f" {query} "
    for pattern, repl in _ALIASES:
        text = pattern.sub(repl, text)
    return text


#: Section heading -> live-app route, for deep-linking answers back into
#: Accutax. Only hub/list/create pages are listed here — a page that needs a
#: runtime ID in its path (a specific invoice, a specific bank account) has
#: no static link, so the nearest list/hub page is used instead. Verified
#: against the live app on 2026-09-22/23 (see accutax_guide.md header) —
#: keys must match that file's `##` headings exactly.
SECTION_ROUTES: dict = {
    "Creating an Invoice": "/create-new/invoice",
    "Creating a Quote": "/create-new/quote",
    "Creating a Proforma Invoice": "/create-new/proforma",
    "Creating a Cash Invoice": "/create-new/cash_invoice",
    "Setting Up a Recurring Invoice": "/create-new/invoice",
    "Creating a Credit Note": "/create-new/credit_note",
    "Adding a Customer": "/create-new-customer",
    "Adding / Applying Tax (VAT)": "/tax-rates",
    "Creating a Tax Rule": "/tax-rules",
    "Using the Tax Calculator": "/tax-calculator",
    "Setting Up VAT Registration (VAT Configuration)": "/vat-configuration",
    "Viewing the VAT Summary": "/tax-reports/vat-summary",
    "Filing a VAT Return (FTA Form 201)": "/reports/vat-report",
    "Viewing the Profit & Loss Report": "/reports/profit-loss",
    "Viewing the Balance Sheet": "/reports/balance-sheet",
    "Viewing the Cash Flow Statement": "/reports/cash-flow",
    "Viewing the Aged Receivables (AR Aging) Report": "/reports/aged-receivables",
    "Viewing Other Financial Reports": "/reports",
    "Recording a Customer Payment": "/income/customer-payments",
    "Recording an Expense or Vendor Bill": "/create-expense",
    "Creating a Purchase Order": "/create-expense/purchase-order",
    "Recording a Cash Expense": "/create-expense/cash-expense",
    "Recording a Vendor Credit (Debit Note)": "/create-vendor-credit",
    "Recording a Supplier Payment": "/create-supplier-payment",
    "Adding a Vendor / Supplier": "/create-new-vendor",
    "Bank Reconciliation": "/banking",
    "Creating an Item / Product": "/items/create-new-item",
    "Creating a Manual Journal Entry": "/manual-journal",
    "Viewing & Reversing Journal Entries": "/journal-entries",
    "Setting Up the Company Profile": "/userprofile?tab=organization",
    "Inviting Users and Managing Roles": "/usermanagement",
    "Customizing Document Templates": "/settings/document-templates",
    # /settings/custom-fields crashes in the 2026-09-23 build ("Component
    # Crashed!", TypeError: a.map is not a function). Link the Settings page
    # (which has the Custom Fields tile) until that's fixed.
    "Adding Custom Fields": "/settings",
    "Managing Currencies and Exchange Rates": "/currency-management",
    "Importing Data": "/settings/data-import",
    "Adding a Branch": "/branches",
    "Adding a Project": "/projects",
    "Adding a Cost Center": "/cost-centers",
    "Connecting Google Drive, Dropbox or SharePoint": "/settings",
    # The delivery-note create form hung on load during verification; the
    # list page (with its Create Delivery Note button) loads reliably.
    "Creating a Delivery Note": "/income/delivery-notes",
    "Managing the Chart of Accounts": "/chart-of-accounts",
    "Adding Expense Categories": "/categories",
    "Recording an Inventory Adjustment": "/inventory/adjustments",
    "Processing Supplier Invoices in the Email Inbox": "/inbox",
    "Uploading and Organizing Documents": "/documents",
    "Setting Up Bank Auto-Match Rules": "/transaction-rules",
    "Setting Invoice Numbering": "/invoice-settings",
    "Viewing Sales by Customer and Statements of Account": "/reports/sales-by-contact",
    "Changing Your Password": "/changepassword",
    # MFA is turned on from a toggle on the Settings page; the enrollment
    # page itself starts setup, so link the toggle rather than the flow.
    "Enabling Two-Factor Authentication (MFA)": "/settings",
}

#: Extra recall terms per section, scored alongside the heading's own words.
#: The heading alone misses real phrasing ("create" vs "creating", "service"
#: never in "Item / Product") since matching is plain token overlap with no
#: stemming. Add a synonym here rather than building a stemmer: keeps
#: matching predictable and easy to extend as real phrasing shows up in
#: traces. Scoring weights each word by how rare it is across sections, so a
#: distinctive word ("proforma") outweighs a shared one ("invoice").
SECTION_KEYWORDS: dict = {
    # "bill" deliberately NOT an invoice synonym: Accutax's vendor bills
    # (BILL-2026-...) are purchase-side expenses, not sales invoices.
    "Creating an Invoice": {"invoice", "invoices", "customer", "customers", "create", "new", "make", "raise", "issue", "send"},
    "Creating a Quote": {"quote", "quotes", "quotation", "quotations", "estimate", "estimates", "convert", "conversion", "create", "new", "send"},
    "Creating a Proforma Invoice": {"proforma", "proformas", "pro", "forma", "create", "new"},
    "Creating a Cash Invoice": {"cash", "sale", "sales", "counter", "create", "new"},
    "Setting Up a Recurring Invoice": {"recurring", "recur", "repeat", "repeating", "schedule", "scheduled", "subscription", "automatic", "monthly", "weekly", "set", "setup"},
    "Creating a Credit Note": {"credit", "note", "notes", "customer", "refund", "return", "returns", "create", "new"},
    "Adding a Customer": {"customer", "customers", "client", "clients", "contact", "contacts", "add", "create", "new", "edit", "update", "delete", "remove", "details"},
    "Adding / Applying Tax (VAT)": {"tax", "taxes", "vat", "rate", "rates", "apply", "add", "set", "setup", "percentage"},
    "Creating a Tax Rule": {"rule", "rules", "automate", "automatic", "condition", "conditions", "reverse", "charge", "exempt", "exemption", "zero", "create", "new"},
    "Using the Tax Calculator": {"calculator", "calculate", "calculation", "engine", "compute"},
    "Setting Up VAT Registration (VAT Configuration)": {"vat", "registration", "register", "registered", "scheme", "schemes", "filing", "frequency", "configuration", "configure", "set", "setup"},
    "Viewing the VAT Summary": {"vat", "summary", "output", "input", "due", "view", "see", "check"},
    "Filing a VAT Return (FTA Form 201)": {"vat", "return", "returns", "file", "filing", "submit", "submission", "fta", "201", "form", "payable"},
    "Viewing the Profit & Loss Report": {"profit", "loss", "income", "statement", "margin", "report", "view", "see", "run", "generate"},
    "Viewing the Balance Sheet": {"balance", "sheet", "assets", "liabilities", "equity", "report", "view", "see", "run", "generate"},
    "Viewing the Cash Flow Statement": {"cash", "flow", "operating", "investing", "financing", "statement", "report", "view", "see", "run", "generate"},
    "Viewing the Aged Receivables (AR Aging) Report": {"aged", "aging", "ageing", "receivable", "receivables", "overdue", "outstanding", "debtors", "ar", "report", "view", "see", "run", "generate"},
    "Viewing Other Financial Reports": {"trial", "balance", "general", "ledger", "journal", "report", "reports", "financial", "view", "see", "run", "generate"},
    "Recording a Customer Payment": {"payment", "payments", "receive", "received", "receipt", "collect", "customer", "client", "advance", "paid", "record"},
    # "invoice": a supplier's invoice is a bill here ("record a vendor invoice").
    "Recording an Expense or Vendor Bill": {"expense", "expenses", "bill", "bills", "vendor", "supplier", "invoice", "invoices", "receipt", "scan", "ocr", "purchase", "spend", "record", "enter", "add"},
    "Creating a Purchase Order": {"purchase", "order", "orders", "convert", "create", "new", "raise"},
    "Recording a Cash Expense": {"cash", "expense", "expenses", "petty", "record", "enter"},
    "Recording a Vendor Credit (Debit Note)": {"vendor", "supplier", "credit", "credits", "debit", "note", "notes", "dn", "return", "returns", "returned", "goods", "refund", "record", "create"},
    "Recording a Supplier Payment": {"supplier", "suppliers", "vendor", "vendors", "payment", "payments", "pay", "paying", "bill", "bills", "settle", "record", "make"},
    "Adding a Vendor / Supplier": {"vendor", "vendors", "supplier", "suppliers", "contact", "contacts", "add", "create", "new", "edit", "update", "delete", "remove", "details"},
    "Bank Reconciliation": {"bank", "banking", "reconcile", "reconciled", "reconciliation", "transaction", "transactions", "statement", "match", "matching", "account"},
    "Creating an Item / Product": {"item", "items", "product", "products", "service", "services", "goods", "sku", "inventory", "create", "add", "new"},
    "Creating a Manual Journal Entry": {"manual", "journal", "journals", "entry", "entries", "debit", "credit", "adjustment", "adjusting", "accrual", "create", "new", "add", "post", "make", "record"},
    "Viewing & Reversing Journal Entries": {"journal", "journals", "entry", "entries", "ledger", "reverse", "reversal", "reversing", "history", "posted", "view", "see"},
    "Setting Up the Company Profile": {"company", "organization", "organisation", "profile", "business", "trn", "industry", "address", "emirate", "set", "setup", "update", "edit"},
    "Inviting Users and Managing Roles": {"user", "users", "invite", "team", "member", "members", "role", "roles", "permission", "permissions", "access", "staff", "employee", "add"},
    "Customizing Document Templates": {"template", "templates", "layout", "design", "logo", "branding", "customize", "customise", "invoice", "invoices", "document", "documents", "change"},
    "Adding Custom Fields": {"custom", "field", "fields", "add", "create"},
    "Managing Currencies and Exchange Rates": {"currency", "currencies", "exchange", "rate", "rates", "forex", "fx", "convert", "conversion", "converter", "add"},
    "Importing Data": {"import", "importing", "upload", "migrate", "migration", "quickbooks", "tally", "zoho", "csv", "excel", "spreadsheet", "bulk"},
    "Adding a Branch": {"branch", "branches", "location", "locations", "office", "add", "create", "new"},
    "Adding a Project": {"project", "projects", "add", "create", "new"},
    "Adding a Cost Center": {"cost", "center", "centre", "centers", "centres", "add", "create", "new"},
    "Connecting Google Drive, Dropbox or SharePoint": {"google", "drive", "dropbox", "sharepoint", "onedrive", "integration", "integrations", "connect", "sync"},
    "Creating a Delivery Note": {"delivery", "deliveries", "dispatch", "shipment", "challan", "note", "notes", "convert", "create", "new"},
    "Managing the Chart of Accounts": {"chart", "accounts", "account", "code", "codes", "ledger", "add", "create", "new"},
    "Adding Expense Categories": {"category", "categories", "add", "create", "new"},
    "Recording an Inventory Adjustment": {"inventory", "stock", "adjustment", "adjustments", "adjust", "count", "damage", "damaged", "theft", "expiry", "expired", "record"},
    "Processing Supplier Invoices in the Email Inbox": {"inbox", "email", "emailed", "emails", "forward", "forwarded", "mail", "supplier", "invoices"},
    "Uploading and Organizing Documents": {"document", "documents", "file", "files", "folder", "folders", "upload", "attachment", "attachments", "store", "storage"},
    "Setting Up Bank Auto-Match Rules": {"bank", "auto", "match", "matching", "rule", "rules", "categorize", "categorization", "automatic", "transaction", "transactions", "set", "setup"},
    "Setting Invoice Numbering": {"invoice", "invoices", "numbering", "number", "numbers", "prefix", "suffix", "sequence", "due", "change", "set", "update"},
    "Viewing Sales by Customer and Statements of Account": {"sales", "customer", "customers", "statement", "statements", "supplier", "report", "view", "see", "run", "generate"},
    "Changing Your Password": {"password", "passwords", "login", "change", "update", "reset"},
    "Enabling Two-Factor Authentication (MFA)": {"two", "factor", "2fa", "mfa", "authentication", "authenticator", "otp", "security", "verification", "enable", "setup", "set"},
}

#: If any of these tokens appear in the query, the section is skipped
#: entirely for that query — regardless of heading/keyword overlap. Used
#: where a sales-side section shares words with its purchase-side twin:
#: confirmed live, "how do I record an advance payment from a vendor" matched
#: the customer payment section on the bare word "payment" and linked the
#: customer invoice list — the wrong screen for a vendor bill.
_VENDOR_CONTEXT = frozenset({"vendor", "vendors", "supplier", "suppliers", "bill", "bills", "purchase", "purchases"})
SECTION_EXCLUDE_KEYWORDS: dict = {
    "Recording a Customer Payment": _VENDOR_CONTEXT,
    "Creating an Invoice": frozenset({"vendor", "vendors", "supplier", "suppliers"}),
    "Creating a Credit Note": frozenset({"vendor", "vendors", "supplier", "suppliers", "debit"}),
}

#: A specialised section is only eligible when the query contains one of its
#: distinctive words. Without this, "record an expense" tied between the
#: general expense section and "Recording a Cash Expense" (both match
#: "expense") and the specialised one could win.
SECTION_REQUIRE_KEYWORDS: dict = {
    "Creating a Cash Invoice": frozenset({"cash", "counter"}),
    "Recording a Cash Expense": frozenset({"cash", "petty"}),
    "Creating a Proforma Invoice": frozenset({"proforma", "proformas", "pro", "forma"}),
    "Setting Up a Recurring Invoice": frozenset({"recurring", "recur", "repeat", "repeating", "schedule", "scheduled", "subscription", "automatic", "monthly", "weekly"}),
    "Viewing the Cash Flow Statement": frozenset({"flow", "operating", "investing", "financing"}),
    "Processing Supplier Invoices in the Email Inbox": frozenset({"inbox", "email", "emailed", "emails", "forward", "forwarded", "mail"}),
}

#: Verbs still break ties between otherwise equal sections, but at a
#: fraction of a content word's weight.
_VERB_WEIGHT = 0.2


@dataclass(frozen=True)
class GuideSection:
    heading: str
    body: str
    status: str  # "verified" or "stub" (body still contains a TODO)


def _parse_sections(guide_text: str) -> list:
    if not guide_text:
        return []
    matches = list(_SECTION_RE.finditer(guide_text))
    sections = []
    for i, m in enumerate(matches):
        heading = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(guide_text)
        body = guide_text[start:end].strip()
        status = "stub" if "TODO" in body else "verified"
        sections.append(GuideSection(heading=heading, body=body, status=status))
    return sections


@functools.lru_cache(maxsize=1)
def _sections_for(guide_text: str) -> tuple:
    return tuple(_parse_sections(guide_text))


def _app_url_for(heading: str) -> Optional[str]:
    """Build a deep link into the live Accutax app for a guide section, or
    None when the section has no mapped route or the app base URL isn't
    configured. Never raises.
    """
    route = SECTION_ROUTES.get(heading)
    if not route:
        return None
    try:
        from gemini_brain.config.settings import settings
        base = (settings.accutax_app_url or "").rstrip("/")
    except Exception as e:
        logger.warning("Could not read accutax_app_url setting: %s", e)
        return None
    if not base:
        return None
    return base + route


@functools.lru_cache(maxsize=1)
def _match_index(guide_text: str) -> tuple:
    """(entries, df): per-section token sets, and how many sections use each
    token. Rebuilt only when the guide text changes."""
    entries = []
    df: dict = {}
    for order, section in enumerate(_sections_for(guide_text)):
        heading_tokens = frozenset(_tokenize(section.heading))
        tokens = frozenset(heading_tokens | SECTION_KEYWORDS.get(section.heading, set()))
        entries.append((section, tokens, heading_tokens, order))
        for token in tokens:
            df[token] = df.get(token, 0) + 1
    return tuple(entries), df


def _rank_sections(guide_text: str, query: str) -> list:
    """Eligible sections for `query`, best first. Empty when nothing matches."""
    entries, df = _match_index(guide_text)
    query_tokens = _tokenize(_expand(query))
    if not query_tokens:
        return []
    scored = []
    for section, tokens, heading_tokens, order in entries:
        exclude = SECTION_EXCLUDE_KEYWORDS.get(section.heading)
        if exclude and (query_tokens & exclude):
            continue
        require = SECTION_REQUIRE_KEYWORDS.get(section.heading)
        if require and not (query_tokens & require):
            continue
        matched = query_tokens & tokens
        if not (matched - _VERBS):
            continue
        score = sum((_VERB_WEIGHT if t in _VERBS else 1.0) / df[t] for t in matched)
        heading_hits = len((matched - _VERBS) & heading_tokens)
        scored.append(((round(-score, 9), -heading_hits, len(heading_tokens), order), section))
    scored.sort(key=lambda item: item[0])
    return [section for _, section in scored]


def _orientation(guide_text: str) -> str:
    """The sidebar-layout paragraph from the guide's preamble — gives the
    model the app's overall menu map even when no section matches."""
    first = _SECTION_RE.search(guide_text)
    preamble = guide_text[: first.start()] if first else guide_text
    start = preamble.find("Sidebar layout")
    return preamble[start:].strip() if start != -1 else ""


#: How many matched sections to inject. The best match plus two runners-up,
#: so a slightly-off ranking still puts the right steps in front of the model.
CONTEXT_SECTIONS = 3


def guide_context_for(query: str, top_n: int = CONTEXT_SECTIONS) -> str:
    """Guide text to inject into the system prompt for `query`: the sidebar
    orientation plus the top matching sections, route paths stripped.

    Replaces injecting the whole guide (~9K tokens at 39 sections) on every
    how-to call. With no matching section only the orientation is returned
    (the caller adds the no-match guardrail). Returns '' if the guide is
    missing. Never raises.
    """
    try:
        guide_text = load_app_guide()
        if not guide_text:
            return ""
        parts = [_orientation(guide_text)]
        for section in _rank_sections(guide_text, query)[:top_n]:
            parts.append(f"## {section.heading}\n\n{section.body}")
        return _sanitize_text("\n\n".join(p for p in parts if p)).strip()
    except Exception as e:
        logger.warning("guide_context_for failed: %s", e)
        return ""


def guide_coverage_for(query: str) -> dict:
    """Classify how well the app guide covers `query`.

    Returns {"guide_loaded": bool, "matched_section": str | None,
    "status": "verified" | "stub" | "no_match" | "guide_missing",
    "app_url": str | None}. `app_url` is only ever set when status is
    "verified" and the matched section has a mapped, static (no runtime ID)
    route — see SECTION_ROUTES.

    Lexical, like the rest of the router: each query word that appears in a
    section's heading or keywords scores 1 / (number of sections containing
    it), so rare words decide ("proforma" beats a shared "invoice"). Verbs
    and other generic words count at a fraction and never make a match on
    their own. Specialised sections need their distinctive word (see
    SECTION_REQUIRE_KEYWORDS). Ties go to the section whose heading contains
    the matched word, then the shorter heading. Never raises.
    """
    guide_text = load_app_guide()
    if not guide_text:
        return {"guide_loaded": False, "matched_section": None, "status": "guide_missing", "app_url": None}

    ranked = _rank_sections(guide_text, query)
    if not ranked:
        return {"guide_loaded": True, "matched_section": None, "status": "no_match", "app_url": None}

    best = ranked[0]
    app_url = _app_url_for(best.heading) if best.status == "verified" else None
    return {
        "guide_loaded": True,
        "matched_section": best.heading,
        "status": best.status,
        "app_url": app_url,
    }
