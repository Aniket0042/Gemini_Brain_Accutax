"""
cost_optimizer.py — Engine tool pruning, result compression, and TTL caching.

Extracted from model_arena/backend/cost_optimizer.py lines 27-198.
Reduces tool definition and row payload token usage for DB engine calls.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gemini_brain.sql_fallback.cost_optimizer")

# Fields emitted separately — skip them in the STATS section
_SKIP_KEYS = frozenset({
    "period", "summary", "results", "success", "task",
    "sql", "row_count", "_truncated", "_total_rows", "error",
})

# In-process TTL cache
_result_cache: Dict[str, Tuple[Any, float]] = {}

_TASK_TTL: Dict[str, int] = {
    "profit_and_loss": 300,
    "trial_balance": 300,
    "balance_sheet": 300,
    "ar_aging": 120,
    "ap_aging": 120,
    "top_customers": 300,
    "top_vendors": 300,
    "bank_balances": 60,
    "bank_transactions": 60,
    "invoice_status_summary": 120,
    "expense_by_category": 300,
    "monthly_revenue_trend": 300,
    "customer_overdue_summary": 120,
    "overdue_invoices": 120,
    "chart_of_accounts": 600,
}
_DEFAULT_TTL = 180


def select_tools(complexity: str, question: str, all_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return a pruned tool list based on classification tier and question content."""
    if complexity == "COMPLEX":
        return all_tools

    q = question.lower()
    needed = {"finance_agent"}
    if any(w in q for w in ("vat", "tax", "excise", "tariff", "zakat", "levy", "withholding")):
        needed.add("tax_agent")

    return [t for t in all_tools if t["toolSpec"]["name"] in needed]


def compact_tool_result(agent_result: Any, task: str) -> str:
    """Convert a finance_agent result dict to a compact, LLM-readable string."""
    if isinstance(agent_result, list):
        agent_result = {"results": agent_result}
    elif not isinstance(agent_result, dict):
        return str(agent_result)

    parts: List[str] = []

    period = agent_result.get("period", "")
    summary = agent_result.get("summary")
    if period:
        parts.append(f"PERIOD: {period}")
    if summary and isinstance(summary, dict):
        items = [f"{k}={v}" for k, v in summary.items() if k != "period"]
        parts.append("SUMMARY: " + " | ".join(items))

    extras = {
        k: v for k, v in agent_result.items()
        if k not in _SKIP_KEYS and not isinstance(v, (list, dict))
    }
    if extras:
        parts.append("STATS: " + " | ".join(f"{k}={v}" for k, v in extras.items()))

    results = agent_result.get("results", [])
    if results and isinstance(results, list):
        row_parts: List[str] = []
        for row in results[:20]:
            if not isinstance(row, dict):
                row_parts.append(str(row)[:120])
                continue
            items = []
            for k, v in row.items():
                if v is None or k == "id":
                    continue
                if isinstance(v, float) and abs(v) >= 1000:
                    items.append(f"{k}:AED {v:,.2f}")
                elif isinstance(v, float):
                    items.append(f"{k}:{v:.4f}")
                else:
                    items.append(f"{k}:{v}")
            if items:
                row_parts.append(" | ".join(items[:5]))

        if row_parts:
            parts.append("ROWS:\n" + "\n".join(row_parts))

        try:
            total = int(agent_result.get("row_count") or len(results))
        except (TypeError, ValueError):
            total = len(results)
        if total > len(row_parts):
            parts.append(f"(+{total - len(row_parts)} more rows not shown)")

    if "error" in agent_result:
        parts.append(f"ERROR: {agent_result['error']}")

    if not parts:
        return json.dumps(agent_result, default=str)[:2000]

    return "\n".join(parts)


def _cache_key(org_id: int, task: str, params: Dict[str, Any]) -> str:
    normalized = {k: v for k, v in sorted(params.items()) if k != "organization_id"}
    raw = f"{org_id}:{task}:{json.dumps(normalized, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()


def get_cached_result(org_id: int, task: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return cached result if still valid."""
    key = _cache_key(org_id, task, params)
    entry = _result_cache.get(key)
    if entry is None:
        return None
    result, expiry = entry
    if time.time() > expiry:
        del _result_cache[key]
        return None
    logger.debug("Cache HIT: %s (org=%s)", task, org_id)
    return result


def set_cached_result(org_id: int, task: str, params: Dict[str, Any], result: Dict[str, Any]) -> None:
    """Store result in TTL cache."""
    ttl = _TASK_TTL.get(task, _DEFAULT_TTL)
    key = _cache_key(org_id, task, params)
    _result_cache[key] = (result, time.time() + ttl)


def get_cache_stats() -> Dict[str, int]:
    """Return snapshot of cache stats."""
    now = time.time()
    alive = sum(1 for _, (_, exp) in _result_cache.items() if exp > now)
    return {
        "total_entries": len(_result_cache),
        "alive": alive,
        "expired": len(_result_cache) - alive,
    }
