"""lookups.py: what a draft needs from Accutax, read as the person: customers, items, tax rates, numbering.

The calls mirror what the create form (accutax_bk_frontend/src/pages/CreateNew.jsx) does, so the assistant sees the
same records the person sees:
  customers   GET contact/list_filter  contact_type_id=4, the organization's customers (the endpoint has no search,
              so names are matched here, like the form's own dropdown)
  items       GET item/list
  tax rates   GET tax-rate/list        is_active=true
  number      GET income/invoice_setting/{user_id} (invoices), GET income/document-number/{type} (quotes and others)

Accutax sometimes answers HTTP 200 with {"error": true} or a "code" of 400 and up in the body; unwrap() turns those
into AccutaxApiError like a real HTTP error.
"""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from gemini_brain.accutax_api.client import AccutaxApiError, AccutaxClient, _kind

CUSTOMER_TYPE_ID = 4
MAX_CUSTOMERS = 1000
MAX_ITEMS = 1000
MAX_TAX_RATES = 500


def unwrap(body: Any) -> Any:
    """The `data` of an Accutax answer; raises for an error in the body."""
    if isinstance(body, dict):
        code = body.get("code")
        failed = body.get("error") is True or (isinstance(code, int) and code >= 400)
        if failed:
            message = body.get("message")
            if isinstance(message, list):
                message = "; ".join(str(m) for m in message[:5])
            status = code if isinstance(code, int) and code >= 400 else 400
            raise AccutaxApiError(_kind(status), status, str(message or "")[:300] if status < 500 else "")
        if "data" in body:
            return body["data"]
    return body


def _rows(data: Any) -> List[Dict[str, Any]]:
    """A list of records from data, data.data or data.rows."""
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        for key in ("data", "rows", "items", "contacts"):
            if isinstance(data.get(key), list):
                return [r for r in data[key] if isinstance(r, dict)]
    return []


def _norm(text: Any) -> str:
    text = unicodedata.normalize("NFKC", str(text or "")).casefold()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\b(llc|l l c|fze|fzco|fz|est|establishment|trading|co|company|ltd|limited|the)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


# ── customers ────────────────────────────────────────────────

@dataclass(frozen=True)
class Customer:
    id: int
    name: str
    organization_name: str = ""
    email: str = ""
    trn: str = ""
    currency: str = ""
    payment_terms: str = ""

    @property
    def label(self) -> str:
        return self.name or self.organization_name or f"Customer {self.id}"


def _customer(row: Dict[str, Any]) -> Optional[Customer]:
    try:
        cid = int(row.get("id"))
    except (TypeError, ValueError):
        return None
    return Customer(id=cid, name=str(row.get("name") or "").strip(),
                    organization_name=str(row.get("organization_name") or "").strip(),
                    email=str(row.get("email") or "").strip(), trn=str(row.get("trn_number") or "").strip(),
                    currency=str(row.get("currency") or "").strip().upper(),
                    payment_terms=str(row.get("payment_terms") or "").strip())


_customer_cache: Dict[tuple, tuple] = {}
_customer_lock = threading.Lock()
CUSTOMER_CACHE_SECONDS = 120.0


def reset_cache(organization_id: Optional[int] = None) -> None:
    """Forget the cached customer lists (all, or one organization's). Called after a customer is created."""
    with _customer_lock:
        for key in [k for k in _customer_cache if organization_id is None or k[0] == int(organization_id)]:
            del _customer_cache[key]


def customers(client: AccutaxClient, user_id: int) -> List[Customer]:
    """The organization's customers. Kept for two minutes: the list takes about ten seconds on a big organization
    and every draft, copy and change asks for it."""
    key = (client.organization_id, int(user_id))
    with _customer_lock:
        hit = _customer_cache.get(key)
    if hit and time.monotonic() - hit[0] < CUSTOMER_CACHE_SECONDS:
        return list(hit[1])
    data = unwrap(client.get("contact/list_filter", {
        "contact_type_id": str(CUSTOMER_TYPE_ID), "page": 1, "pageSize": MAX_CUSTOMERS,
        "organization_id": client.organization_id, "user_id": str(user_id)}))
    found = [c for c in (_customer(r) for r in _rows(data)) if c is not None]
    with _customer_lock:
        _customer_cache[key] = (time.monotonic(), found)
    return list(found)


@dataclass(frozen=True)
class CustomerMatch:
    found: Optional[Customer]
    candidates: Sequence[Customer] = ()


def match_customer(text: str, pool: Sequence[Customer], customer_id: Optional[int] = None) -> CustomerMatch:
    """One customer when the name (or id) points to exactly one; else the candidates to choose from (maybe none).
    Never picks among several close names."""
    if customer_id is not None:
        hit = [c for c in pool if c.id == int(customer_id)]
        return CustomerMatch(hit[0] if hit else None)
    want = _norm(text)
    if not want:
        return CustomerMatch(None)
    exact = [c for c in pool if want in (_norm(c.name), _norm(c.organization_name))]
    if len(exact) == 1:
        return CustomerMatch(exact[0])
    if exact:
        return CustomerMatch(None, exact[:8])
    words = set(want.split())
    scored = []
    for c in pool:
        names = [_norm(c.name), _norm(c.organization_name)]
        if any(n and (want in n or n in want) for n in names):
            scored.append((2, c))
            continue
        overlap = max((len(words & set(n.split())) for n in names if n), default=0)
        if overlap and overlap >= max(1, len(words) - 1):
            scored.append((1, c))
    scored.sort(key=lambda s: (-s[0], s[1].label))
    best = [c for score, c in scored if score == scored[0][0]] if scored else []
    if len(best) == 1 and scored[0][0] == 2:
        return CustomerMatch(best[0])
    return CustomerMatch(None, [c for _, c in scored][:8])


# ── items ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Item:
    id: int
    name: str
    unit_price: Optional[float]
    revenue_account_id: Optional[int]
    revenue_tax_rate_id: Optional[int]


def items(client: AccutaxClient, user_id: int) -> List[Item]:
    data = unwrap(client.get("item/list", {"organization_id": client.organization_id, "user_id": str(user_id),
                                           "page": 1, "pageSize": MAX_ITEMS}))
    out = []
    for r in _rows(data):
        try:
            iid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        acc, rate = _num(r.get("revenue_account_id")), _num(r.get("revenue_tax_rate_id"))
        out.append(Item(id=iid, name=str(r.get("name") or r.get("description") or "").strip(),
                        unit_price=_num(r.get("unit_price")) if _num(r.get("unit_price")) is not None else _num(r.get("cost")),
                        revenue_account_id=int(acc) if acc is not None else None,
                        revenue_tax_rate_id=int(rate) if rate is not None else None))
    return out


def match_item(text: str, pool: Sequence[Item]) -> Optional[Item]:
    """The catalog item whose name is exactly this text (ignoring case and punctuation), only when there is one."""
    want = _norm(text)
    hits = [i for i in pool if want and _norm(i.name) == want]
    return hits[0] if len(hits) == 1 else None


# ── tax rates ────────────────────────────────────────────────

@dataclass(frozen=True)
class TaxRate:
    id: int
    name: str
    rate: float
    tax_type: str      # standard, zero_rated, exempt, no_vat, out_of_scope
    rate_type: str     # sales, purchase, exempt, out_of_scope
    is_default: bool

    @property
    def label(self) -> str:
        return f"{self.rate:g}%" if self.tax_type == "standard" else self.tax_type.replace("_", " ").capitalize()


def tax_rates(client: AccutaxClient, user_id: int) -> List[TaxRate]:
    # Accutax pages this list 10 at a time unless asked (pageSize), and a limit parameter is ignored: without
    # pageSize the newest ten rates are all we would see and the right one is often missing.
    data = unwrap(client.get("tax-rate/list", {"user_id": str(user_id), "organization_id": client.organization_id,
                                               "is_active": "true", "page": 1, "pageSize": MAX_TAX_RATES}))
    out = []
    for r in _rows(data):
        rate = _num(r.get("tax_rate")) if _num(r.get("tax_rate")) is not None else _num(r.get("rate"))
        try:
            rid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if rate is None or r.get("is_active") is False:
            continue
        out.append(TaxRate(id=rid, name=str(r.get("name") or "").strip(), rate=rate,
                           tax_type=str(r.get("tax_type") or "standard").lower(),
                           rate_type=str(r.get("rate_type") or "sales").lower(), is_default=bool(r.get("is_default"))))
    return out


def sales_rates(rates: Sequence[TaxRate]) -> List[TaxRate]:
    return [r for r in rates if r.rate_type != "purchase"]


def standard_choices(rates: Sequence[TaxRate]) -> List[str]:
    """The standard sales rates as short labels ("5%", "18%"), for a warning that asks which to use."""
    return list(dict.fromkeys(r.label for r in sales_rates(rates) if r.tax_type == "standard"))


def pick_rate(rates: Sequence[TaxRate], treatment: Optional[str] = None, rate_id: Optional[int] = None,
              percent: Optional[float] = None) -> Optional[TaxRate]:
    """The tax rate for a line: the given id (an item's own rate), else the standard sales rate of the percent the
    person asked for, else the asked treatment, else the organization's default sales rate, else its 5% standard
    sales rate (UAE VAT), else its one standard sales rate. None when nothing fits or several do: the draft then
    warns and asks, it never guesses."""
    sales = sales_rates(rates)
    if rate_id is not None:
        hit = [r for r in sales if r.id == rate_id]
        if hit:
            return hit[0]
    if percent is not None:
        hit = [r for r in sales if r.tax_type == "standard" and abs(r.rate - percent) < 1e-9]
        return (next((r for r in hit if r.is_default), None) or hit[0]) if hit else None
    if treatment and treatment != "standard":
        hit = [r for r in sales if r.tax_type == treatment]
        if not hit and treatment == "exempt":
            hit = [r for r in sales if r.rate_type == "exempt"]
        if not hit and treatment == "out_of_scope":
            hit = [r for r in sales if r.rate_type == "out_of_scope" or r.tax_type == "no_vat"]
        return hit[0] if len(hit) >= 1 else None
    standard = [r for r in sales if r.tax_type == "standard"]
    defaults = [r for r in standard if r.is_default]
    if defaults:
        return defaults[0]
    five = [r for r in standard if abs(r.rate - 5.0) < 1e-9]
    if five:
        return five[0]
    return standard[0] if len(standard) == 1 else None


# ── numbering ────────────────────────────────────────────────

@dataclass(frozen=True)
class Numbering:
    suggested: str
    due_days: Optional[int]
    has_custom_columns: bool
    #: The person's Accutax sign-in is on another organization, so the settings could not be read for this one.
    other_organization: bool = False


def numbering(client: AccutaxClient, user_id: int, income_type: str) -> Numbering:
    """The next number Accutax suggests, the default days to the due date and whether line custom columns are set."""
    if client.token_organization_id != client.organization_id:
        # Accutax answers invoice_setting and document-number for the organization of the token, not the one in
        # the request. A number taken from there would be the wrong organization's series.
        return Numbering(suggested="", due_days=None, has_custom_columns=False, other_organization=True)
    due_days, custom = None, False
    suggested = ""
    try:
        setting = unwrap(client.get(f"income/invoice_setting/{int(user_id)}", {"userId": int(user_id)})) or {}
    except AccutaxApiError as e:
        if e.kind not in ("not_found", "validation"):
            raise
        setting = {}
    if isinstance(setting, dict):
        days = _num(setting.get("due_date_days_after"))
        due_days = int(days) if days is not None and days >= 0 else None
        custom = bool(setting.get("custom_columns"))
        if income_type == "INVOICE":
            suggested = str(setting.get("suggested_invoice_number") or "").strip()
            if not suggested and setting.get("next_invoice_number") is not None:
                suggested = (f"{(setting.get('invoice_prefix') or '').strip()}{setting.get('next_invoice_number')}"
                             f"{setting.get('invoice_suffix') or ''}")
    if income_type != "INVOICE":
        doc = unwrap(client.get(f"income/document-number/{income_type}")) or {}
        if isinstance(doc, dict):
            suggested = str(doc.get("suggested_number") or "").strip() or (
                f"{doc.get('prefix') or ''}{doc.get('next_number') or ''}{doc.get('suffix') or ''}".strip())
    return Numbering(suggested=suggested, due_days=due_days, has_custom_columns=custom)
