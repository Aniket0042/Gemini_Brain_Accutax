"""
status.py — The one-line status a specialist's card shows after a run, built in code from the tool results.

{"headline": "Checked 329 bills", "badge": "293 to review", "tone": "danger"}. Figures come
from the tool results, never from the model's text. Tones: danger, warning, success, accent.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence


def status_for(profile: Any, data: Sequence[Dict[str, Any]], blocks: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The status for a specialist's run; None for the general agent or when its main tool did not run."""
    key = getattr(profile, "key", "")
    if key in ("vat", "ap"):
        return _review(_last(data, "review_vat"))
    if key == "cash":
        return _cash(_last(data, "cash_forecast"))
    if key == "reporting":
        return _report(_last(data, "query_metrics"), blocks)
    return None


def _last(data: Sequence[Dict[str, Any]], tool: str) -> Optional[Dict[str, Any]]:
    return next((d.get("result") or {} for d in reversed(list(data or [])) if d.get("tool") == tool), None)


def _review(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not result:
        return None
    noun = "invoices" if result.get("side") == "sales" else "bills"
    groups, findings = int(result.get("groups_found") or 0), int(result.get("findings_found") or 0)
    parts = []
    if groups:
        parts.append(f"{groups:,} duplicate group{'s' if groups != 1 else ''}")
    if findings:
        parts.append(f"{findings:,} finding{'s' if findings != 1 else ''}")
    high = int((result.get("findings_by_severity") or {}).get("high") or 0)
    tone = "danger" if high else ("warning" if parts else "success")
    return {"headline": f"Checked {int(result.get('bills_checked') or 0):,} {noun}",
            "badge": " · ".join(parts) or "All clear", "tone": tone}


def _cash(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    orgs = [o for o in (result or {}).get("organizations") or [] if o.get("weeks")]
    if len(orgs) != 1:
        return None
    org = orgs[0]
    currency = org.get("currency") or ""
    lowest = _dec(org.get("lowest_closing_cash"))
    week = next((w for w in org["weeks"] if _dec(w.get("closing_cash")) == lowest), None)
    when = f" (week of {_monday(week)})" if week else ""
    overdue = _dec(org.get("overdue_receivables"))
    if lowest is not None and lowest < 0:
        badge, tone = "Cash below zero", "danger"
    elif overdue:
        badge, tone = f"{_short(overdue, currency)} overdue to collect", "warning"
    else:
        badge, tone = "No overdue amounts", "success"
    return {"headline": f"Lowest week {_short(lowest, currency)}{when}", "badge": badge, "tone": tone}


def _report(result: Optional[Dict[str, Any]], blocks: Sequence[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    kinds: List[str] = [b.get("kind") for b in blocks or [] if b.get("type") == "artifact" and b.get("primary")]
    if not result or not kinds:
        return None
    months = sorted({str(v)[:7] for r in result.get("rows") or [] for k, v in r.items() if k.endswith(".month") and v})
    span = ""
    if months:
        first, last = (dt.date.fromisoformat(m + "-01").strftime("%b %Y") for m in (months[0], months[-1]))
        span = f": {first}" + (f" to {last}" if last != first else "")
    files = " + ".join(k.upper() if k != "xlsx" else "Excel" for k in dict.fromkeys(kinds))
    return {"headline": f"P&L pack{span}", "badge": f"{files} ready", "tone": "accent"}


def _dec(value: Any) -> Optional[Decimal]:
    try:
        return None if value in (None, "") else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _short(value: Optional[Decimal], currency: str) -> str:
    """AED 14.6M, AED 776K, AED 950."""
    if value is None:
        return "n/a"
    size = abs(value)
    if size >= 1_000_000:
        text = f"{value / 1_000_000:,.1f}M"
    elif size >= 10_000:
        text = f"{value / 1000:,.0f}K"
    else:
        text = f"{value:,.0f}"
    return f"{currency} {text}".strip()


def _monday(week: Dict[str, Any]) -> str:
    try:
        return (dt.date.fromisoformat(str(week.get("week_end"))[:10]) - dt.timedelta(days=6)).strftime("%d %b")
    except ValueError:
        return str(week.get("week_start") or "")
