"""
Tax Agent — Middle East VAT regime computation.

Responsibilities:
- Apply UAE VAT (5%) or KSA VAT (15%)
- Handle zero-rated vs exempt categories
- Compute tax payable from invoice/expense data
- Never guess rates — all rates are hardcoded per jurisdiction

This agent NEVER executes SQL directly.
It receives financial data from the coordinator (which got it from finance_agent)
and applies deterministic tax rules.
"""

import logging
from typing import Dict, Any, Optional, List
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("agents.tax")

# ──────────────────────────────────────────────
# Tax Regime Definitions (Authoritative)
# ──────────────────────────────────────────────

TAX_REGIMES = {
    "UAE": {
        "country": "United Arab Emirates",
        "standard_rate": Decimal("0.05"),       # 5%
        "rate_display": "5%",
        "effective_from": "2018-01-01",
        "zero_rated_categories": [
            "export",
            "international_transport",
            "first_supply_residential",
            "crude_oil",
            "natural_gas",
            "investment_precious_metals",
        ],
        "exempt_categories": [
            "financial_services",
            "residential_rent",
            "bare_land",
            "local_passenger_transport",
            "life_insurance",
        ],
        "authority": "Federal Tax Authority (FTA)",
        "currency": "AED",
    },
    "KSA": {
        "country": "Kingdom of Saudi Arabia",
        "standard_rate": Decimal("0.15"),       # 15%
        "rate_display": "15%",
        "effective_from": "2020-07-01",
        "zero_rated_categories": [
            "export",
            "international_transport",
            "medicines_medical_equipment",
        ],
        "exempt_categories": [
            "financial_services",
            "residential_rent",
            "life_insurance",
        ],
        "authority": "Zakat, Tax and Customs Authority (ZATCA)",
        "currency": "SAR",
    },
    "BHR": {
        "country": "Bahrain",
        "standard_rate": Decimal("0.10"),       # 10%
        "rate_display": "10%",
        "effective_from": "2022-01-01",
        "zero_rated_categories": ["export", "oil_gas"],
        "exempt_categories": ["financial_services", "residential_rent"],
        "authority": "National Bureau for Revenue (NBR)",
        "currency": "BHD",
    },
    "OMN": {
        "country": "Oman",
        "standard_rate": Decimal("0.05"),       # 5%
        "rate_display": "5%",
        "effective_from": "2021-04-16",
        "zero_rated_categories": ["export", "medicines", "essential_food"],
        "exempt_categories": ["financial_services", "residential_rent", "bare_land"],
        "authority": "Tax Authority",
        "currency": "OMR",
    },
}

# Default jurisdiction if none detected
DEFAULT_JURISDICTION = "UAE"


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def handle(task: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Tax Agent entry point.

    Supported tasks:
      - compute_vat           → compute VAT for a given amount + jurisdiction
      - get_tax_rate          → return the VAT rate for a jurisdiction
      - classify_category     → zero-rated / exempt / standard for a category
      - compute_invoice_tax   → full tax breakdown for invoice data
      - get_regime_info       → full regime details for a jurisdiction
      - list_jurisdictions    → all supported jurisdictions
    """
    params = params or {}

    dispatch = {
        "compute_vat":         _task_compute_vat,
        "get_tax_rate":        _task_get_tax_rate,
        "classify_category":   _task_classify_category,
        "compute_invoice_tax": _task_compute_invoice_tax,
        "get_regime_info":     _task_get_regime_info,
        "list_jurisdictions":  _task_list_jurisdictions,
    }

    handler = dispatch.get(task)
    if handler:
        try:
            return handler(params)
        except Exception as e:
            logger.exception(f"Tax agent task '{task}' failed")
            return {"error": f"Tax agent error: {str(e)}"}
    return {"error": f"Unknown tax_agent task: {task}"}


# ──────────────────────────────────────────────
# Task implementations
# ──────────────────────────────────────────────

def _resolve_jurisdiction(params: Dict) -> str:
    """Resolve jurisdiction from params, defaulting to UAE."""
    jur = (params.get("jurisdiction") or params.get("region") or params.get("country") or DEFAULT_JURISDICTION).upper().strip()
    # Allow common aliases
    aliases = {
        "UAE": "UAE", "UNITED ARAB EMIRATES": "UAE", "DUBAI": "UAE", "ABU DHABI": "UAE",
        "KSA": "KSA", "SAUDI": "KSA", "SAUDI ARABIA": "KSA",
        "BAHRAIN": "BHR", "BHR": "BHR",
        "OMAN": "OMN", "OMN": "OMN",
    }
    return aliases.get(jur, jur)


def _task_compute_vat(params: Dict) -> Dict:
    """Compute VAT for a given amount."""
    jurisdiction = _resolve_jurisdiction(params)
    regime = TAX_REGIMES.get(jurisdiction)
    if not regime:
        return {"error": f"Unsupported jurisdiction: {jurisdiction}. Supported: {list(TAX_REGIMES.keys())}"}

    amount = Decimal(str(params.get("amount", 0)))
    category = (params.get("category") or "").lower().strip()
    is_inclusive = params.get("vat_inclusive", False)

    # Determine effective rate
    if category in regime["zero_rated_categories"]:
        effective_rate = Decimal("0")
        classification = "zero_rated"
    elif category in regime["exempt_categories"]:
        effective_rate = Decimal("0")
        classification = "exempt"
    else:
        effective_rate = regime["standard_rate"]
        classification = "standard"

    if is_inclusive and effective_rate > 0:
        # Amount includes VAT: extract it
        net_amount = (amount / (1 + effective_rate)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        vat_amount = (amount - net_amount).quantize(Decimal("0.01"), ROUND_HALF_UP)
    else:
        net_amount = amount
        vat_amount = (amount * effective_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)

    gross_amount = net_amount + vat_amount

    return {
        "jurisdiction": jurisdiction,
        "country": regime["country"],
        "rate": str(effective_rate),
        "rate_display": regime["rate_display"] if classification == "standard" else "0%",
        "classification": classification,
        "net_amount": float(net_amount),
        "vat_amount": float(vat_amount),
        "gross_amount": float(gross_amount),
        "currency": regime["currency"],
        "authority": regime["authority"],
    }


def _task_get_tax_rate(params: Dict) -> Dict:
    jurisdiction = _resolve_jurisdiction(params)
    regime = TAX_REGIMES.get(jurisdiction)
    if not regime:
        return {"error": f"Unsupported jurisdiction: {jurisdiction}"}
    return {
        "jurisdiction": jurisdiction,
        "country": regime["country"],
        "standard_rate": str(regime["standard_rate"]),
        "rate_display": regime["rate_display"],
        "effective_from": regime["effective_from"],
        "authority": regime["authority"],
    }


def _task_classify_category(params: Dict) -> Dict:
    jurisdiction = _resolve_jurisdiction(params)
    regime = TAX_REGIMES.get(jurisdiction)
    if not regime:
        return {"error": f"Unsupported jurisdiction: {jurisdiction}"}

    category = (params.get("category") or "").lower().strip()
    if category in regime["zero_rated_categories"]:
        return {"category": category, "classification": "zero_rated", "rate": "0%", "jurisdiction": jurisdiction}
    elif category in regime["exempt_categories"]:
        return {"category": category, "classification": "exempt", "rate": "0%", "jurisdiction": jurisdiction}
    else:
        return {"category": category, "classification": "standard", "rate": regime["rate_display"], "jurisdiction": jurisdiction}


def _task_compute_invoice_tax(params: Dict) -> Dict:
    """Compute full tax breakdown for invoice line items."""
    jurisdiction = _resolve_jurisdiction(params)
    regime = TAX_REGIMES.get(jurisdiction)
    if not regime:
        return {"error": f"Unsupported jurisdiction: {jurisdiction}"}

    line_items = params.get("line_items", [])
    if not line_items:
        # If just a total amount is provided
        amount = params.get("total_amount", 0)
        if amount:
            return _task_compute_vat({"amount": amount, "jurisdiction": jurisdiction, "category": params.get("category", "")})
        return {"error": "Either 'line_items' or 'total_amount' required"}

    breakdown = []
    total_net = Decimal("0")
    total_vat = Decimal("0")

    for item in line_items:
        item_amount = Decimal(str(item.get("amount", item.get("cost", 0))))
        category = (item.get("category") or item.get("type") or "").lower().strip()

        if category in regime["zero_rated_categories"]:
            rate = Decimal("0")
            classification = "zero_rated"
        elif category in regime["exempt_categories"]:
            rate = Decimal("0")
            classification = "exempt"
        else:
            rate = regime["standard_rate"]
            classification = "standard"

        vat = (item_amount * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
        total_net += item_amount
        total_vat += vat

        breakdown.append({
            "item": item.get("name", "Item"),
            "amount": float(item_amount),
            "classification": classification,
            "rate": str(rate),
            "vat": float(vat),
        })

    return {
        "jurisdiction": jurisdiction,
        "country": regime["country"],
        "standard_rate": regime["rate_display"],
        "line_items": breakdown,
        "subtotal": float(total_net),
        "total_vat": float(total_vat),
        "grand_total": float(total_net + total_vat),
        "currency": regime["currency"],
    }


def _task_get_regime_info(params: Dict) -> Dict:
    jurisdiction = _resolve_jurisdiction(params)
    regime = TAX_REGIMES.get(jurisdiction)
    if not regime:
        return {"error": f"Unsupported jurisdiction: {jurisdiction}"}
    return {
        "jurisdiction": jurisdiction,
        **{k: v if not isinstance(v, Decimal) else str(v) for k, v in regime.items()},
    }


def _task_list_jurisdictions(params: Dict) -> Dict:
    return {
        "jurisdictions": [
            {"code": k, "country": v["country"], "rate": v["rate_display"]}
            for k, v in TAX_REGIMES.items()
        ]
    }
