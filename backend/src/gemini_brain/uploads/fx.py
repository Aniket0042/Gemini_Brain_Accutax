"""The value of an amount in AED, at the Central Bank of the UAE rate, saved with the date used.

A UAE tax invoice in another currency must show its amounts in AED. When a bill does not, the assistant can still say what
the amount is worth, and says which rate it used and the date of that rate. The rates are in config/aed_rates.json (or the
file named by AED_RATES_FILE) and are kept by finance: a currency that is not in the file is not converted, and nothing is
guessed. The file holds only what the Central Bank published; USD is the official fixed rate.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from gemini_brain.config.settings import settings

logger = logging.getLogger("gemini_brain.uploads.fx")
_DEFAULT_FILE = Path(__file__).resolve().parents[1] / "config" / "aed_rates.json"


def _path() -> Path:
    raw = (getattr(settings, "aed_rates_file", "") or "").strip()
    return Path(raw) if raw else _DEFAULT_FILE


@lru_cache(maxsize=4)
def _load(path: str) -> Dict[str, Any]:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        logger.warning("AED rates not loaded from %s: %s", path, e)
        return {}


def rate_for(currency: Optional[str]) -> Optional[Dict[str, Any]]:
    """{"currency", "rate", "as_of", "source"} for a currency in the rates file; None when it is not there."""
    code = (currency or "").strip().upper()
    if not code or code == "AED":
        return None
    data = _load(str(_path()))
    entry = (data.get("rates") or {}).get(code)
    try:
        rate = float(entry["rate"]) if isinstance(entry, dict) else None
    except (KeyError, TypeError, ValueError):
        rate = None
    if not rate or rate <= 0:
        return None
    return {"currency": code, "rate": rate, "as_of": str(entry.get("as_of") or ""), "source": data.get("source") or ""}


def to_aed(amount: Optional[float], currency: Optional[str]) -> Optional[Dict[str, Any]]:
    """The amount in AED with the rate used: {"aed", "rate", "as_of", "source", "currency", "amount"}; None when it cannot be."""
    info = rate_for(currency)
    if info is None or amount is None:
        return None
    return {**info, "amount": amount, "aed": round(amount * info["rate"], 2)}


def sentence(conv: Optional[Dict[str, Any]]) -> str:
    """'AED 3,672.50 (USD 1,000.00 at 3.6725, Central Bank of the UAE, fixed rate)'. Empty when there is no conversion."""
    if not conv:
        return ""
    when = f", {conv['as_of']}" if conv.get("as_of") else ""
    return (f"AED {conv['aed']:,.2f} ({conv['currency']} {conv['amount']:,.2f} at {conv['rate']:g}, "
            f"{conv['source'] or 'rate file'}{when})")
