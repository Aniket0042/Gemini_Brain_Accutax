"""
Reasoning Agent — cross-result comparison, narrative synthesis,
insight generation, and role-based answer formatting.

Wraps the existing post_sql_reasoner.py and adds:
  - Agent-compatible handle(task, params) interface
  - LLM-powered narrative synthesis via Bedrock
  - Multi-result comparison / delta analysis
  - Confidence scoring

This agent is purely analytical — it NEVER executes SQL.
It receives pre-computed data from the Coordinator.
"""

import logging
import json
from typing import Dict, Any, Optional, List
from decimal import Decimal

logger = logging.getLogger("agents.reasoning")


# ──────────────────────────────────────────────
# Lazy imports (we don't want to fail at module load)
# ──────────────────────────────────────────────

def _get_reasoner():
    """Lazy-import post_sql_reasoner."""
    try:
        import post_sql_reasoner as psr
        return psr
    except ImportError:
        logger.warning("post_sql_reasoner not importable — limited reasoning")
        return None


def _get_bedrock():
    """Lazy-import shared bedrock client for LLM-powered synthesis."""
    try:
        from gemini_brain.agents.bedrock_client import converse
        return converse
    except ImportError:
        return None


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def handle(task: str, params: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Reasoning Agent entry point.

    Supported tasks:
      - synthesize_answer       → generate a natural-language answer from SQL results
      - compare_results         → compare two or more result sets (delta, % change)
      - derive_insights         → derive business insights from raw facts
      - format_for_role         → re-phrase an answer for a specific audience
      - compute_confidence      → assess confidence level of a result
      - narrative_synthesis     → LLM-powered free-form narrative from data
    """
    params = params or {}

    dispatch = {
        "synthesize_answer":   _task_synthesize_answer,
        "compare_results":     _task_compare_results,
        "derive_insights":     _task_derive_insights,
        "format_for_role":     _task_format_for_role,
        "compute_confidence":  _task_compute_confidence,
        "narrative_synthesis": _task_narrative_synthesis,
    }

    handler = dispatch.get(task)
    if handler:
        try:
            return handler(params)
        except Exception as e:
            logger.exception(f"Reasoning agent task '{task}' failed")
            return {"error": f"Reasoning agent error: {str(e)}"}
    return {"error": f"Unknown reasoning_agent task: {task}. Available: {list(dispatch.keys())}"}


# ──────────────────────────────────────────────
# Task implementations
# ──────────────────────────────────────────────

def _task_synthesize_answer(params: Dict) -> Dict:
    """
    Generate a human-readable answer from SQL result rows + query plan.

    Expected params:
      plan         - query plan dict (question_type, metric, entity, grain, etc.)
      sql_results  - list of row dicts
      question     - original user question (optional, for context)
    """
    psr = _get_reasoner()
    if psr is None:
        return _fallback_synthesize(params)

    plan = params.get("plan", {})
    sql_results = params.get("sql_results", [])

    # Use the comprehensive reason_over_results entry point
    try:
        result = psr.reason_over_results(plan, sql_results)
        return {
            "answer": result.get("answer", ""),
            "role_answers": result.get("answers", {}),
            "confidence": result.get("confidence", 0.5),
            "data": result.get("data", []),
        }
    except Exception as e:
        logger.warning("reason_over_results failed: %s, using fallback", e)
        return _fallback_synthesize(params)


def _task_compare_results(params: Dict) -> Dict:
    """
    Compare two or more datasets and compute deltas.

    Expected params:
      datasets  - list of {label, value} or {label, rows: [{...}]}
      metric    - name of numeric field to compare (default: auto-detect)
    """
    datasets = params.get("datasets", [])
    metric_key = params.get("metric")

    if len(datasets) < 2:
        return {"error": "At least 2 datasets required for comparison"}

    # Extract scalar values from each dataset
    values = []
    for ds in datasets:
        label = ds.get("label", f"Dataset {len(values)+1}")
        if "value" in ds:
            values.append({"label": label, "value": _to_float(ds["value"])})
        elif "rows" in ds:
            # Aggregate from rows
            rows = ds["rows"]
            if metric_key:
                total = sum(_to_float(r.get(metric_key, 0)) for r in rows)
            else:
                total = _extract_aggregate(rows)
            values.append({"label": label, "value": total})

    if len(values) < 2:
        return {"error": "Could not extract comparable values from datasets"}

    # Compute pairwise comparisons
    comparisons = []
    base = values[0]
    for other in values[1:]:
        delta = other["value"] - base["value"]
        pct = (delta / base["value"] * 100) if base["value"] != 0 else None
        direction = "up" if delta > 0 else ("down" if delta < 0 else "flat")
        comparisons.append({
            "from": base["label"],
            "to": other["label"],
            "base_value": base["value"],
            "compare_value": other["value"],
            "delta": round(delta, 2),
            "pct_change": round(pct, 2) if pct is not None else None,
            "direction": direction,
        })

    return {
        "values": values,
        "comparisons": comparisons,
        "summary": _comparison_narrative(comparisons),
    }


def _task_derive_insights(params: Dict) -> Dict:
    """
    Derive business insights from raw facts using post_sql_reasoner.

    Expected params:
      plan        - query plan dict
      sql_results - list of row dicts
      assumptions - optional dict of assumptions
    """
    psr = _get_reasoner()
    if psr is None:
        return {"insights": [], "note": "post_sql_reasoner not available"}

    plan = params.get("plan", {})
    sql_results = params.get("sql_results", [])
    assumptions = params.get("assumptions", {})

    try:
        # Build a FactCollection from sql_results
        facts = psr.FactCollection()
        for i, row in enumerate(sql_results):
            for k, v in row.items():
                facts.add(k, v, source=f"row_{i}")

        derived = psr.derive_facts(facts, plan, assumptions)
        insight = psr.synthesize_insight(facts, plan, assumptions)

        return {
            "findings": insight.get("findings", []),
            "risks": insight.get("risks", []),
            "opportunities": insight.get("opportunities", []),
            "confidence": insight.get("confidence", 0.5),
            "assumptions": insight.get("assumptions", []),
            "evidence": insight.get("evidence", {}),
        }
    except Exception as e:
        logger.warning("derive_facts/synthesize_insight failed: %s", e)
        return {"findings": [], "note": f"Insight derivation failed: {str(e)}"}


def _task_format_for_role(params: Dict) -> Dict:
    """
    Re-phrase an answer for a specific audience/role.

    Expected params:
      answer  - the raw answer string
      role    - one of: CEO, CFO, ANALYST, ACCOUNTANT, AUDITOR
      plan    - query plan (optional)
    """
    psr = _get_reasoner()
    answer = params.get("answer", "")
    role = (params.get("role") or "ANALYST").upper()

    if psr is None:
        return {"formatted_answer": answer, "role": role}

    try:
        # Use role-based formatting from post_sql_reasoner
        plan = params.get("plan", {})
        sql_results = params.get("sql_results", [])

        styled = psr.generate_answer_by_style(
            plan, sql_results,
            profile={"role": role},
            style_name=role.lower(),
        )
        return {"formatted_answer": styled, "role": role}
    except Exception as e:
        logger.warning("Role formatting failed: %s", e)
        return {"formatted_answer": answer, "role": role}


def _task_compute_confidence(params: Dict) -> Dict:
    """
    Assess confidence level of a result.

    Heuristics:
      - Data completeness (null ratio)
      - Sample size
      - Metric type stability
    """
    sql_results = params.get("sql_results", [])
    plan = params.get("plan", {})

    if not sql_results:
        return {"confidence": 0.0, "reason": "No data available"}

    row_count = len(sql_results)
    # Check for nulls
    total_cells = 0
    null_cells = 0
    for row in sql_results:
        for v in row.values():
            total_cells += 1
            if v is None:
                null_cells += 1

    null_ratio = null_cells / total_cells if total_cells > 0 else 0
    completeness_score = 1.0 - null_ratio

    # Sample size factor
    if row_count >= 100:
        size_score = 1.0
    elif row_count >= 10:
        size_score = 0.8
    elif row_count >= 1:
        size_score = 0.6
    else:
        size_score = 0.0

    # Aggregate type factor
    qtype = plan.get("question_type", "")
    type_scores = {
        "aggregate": 0.95,
        "count": 0.95,
        "extreme": 0.9,
        "list": 0.85,
        "comparison": 0.8,
        "trend": 0.75,
        "prediction": 0.6,
    }
    type_score = type_scores.get(qtype, 0.8)

    confidence = round(completeness_score * 0.4 + size_score * 0.3 + type_score * 0.3, 2)
    confidence = min(confidence, 1.0)

    reasons = []
    if null_ratio > 0.2:
        reasons.append(f"High null ratio ({null_ratio:.0%})")
    if row_count < 5:
        reasons.append(f"Small sample size ({row_count} rows)")
    if qtype == "prediction":
        reasons.append("Prediction-type queries have inherent uncertainty")

    return {
        "confidence": confidence,
        "components": {
            "data_completeness": round(completeness_score, 2),
            "sample_size": round(size_score, 2),
            "query_type": round(type_score, 2),
        },
        "reasons": reasons if reasons else ["Good data quality"],
        "row_count": row_count,
    }


def _task_narrative_synthesis(params: Dict) -> Dict:
    """
    LLM-powered free-form narrative synthesis from structured data.
    Uses Bedrock to generate a human-readable narrative.

    Expected params:
      question     - original user question
      data         - structured data (dict or list)
      context      - optional additional context string
      style        - brief|detailed|executive (default: detailed)
    """
    bedrock_converse = _get_bedrock()
    question = params.get("question", "")
    data = params.get("data", {})
    context = params.get("context", "")
    style = params.get("style", "detailed")

    # Serialize data for the prompt
    data_str = json.dumps(data, indent=2, default=str)[:4000]  # Cap at 4K chars

    system_prompt = f"""You are a financial analyst generating a {style} narrative summary.

Rules:
- Write in clear business English
- Use specific numbers from the data provided
- For 'brief' style: 2-3 sentences
- For 'detailed' style: structured paragraphs with key metrics highlighted
- For 'executive' style: bullet-point summary suitable for C-level executives
- Never make up numbers — only reference data provided
- If data is insufficient, say so explicitly"""

    user_msg = f"""Question: {question}

Data:
{data_str}

{f"Additional context: {context}" if context else ""}

Generate a {style} narrative answer."""

    if bedrock_converse is None:
        # Fallback: build a basic narrative without LLM
        return {
            "narrative": _basic_narrative(question, data),
            "style": style,
            "llm_used": False,
        }

    try:
        messages = [{"role": "user", "content": [{"text": user_msg}]}]
        response = bedrock_converse(system_prompt, messages)
        narrative = response if isinstance(response, str) else str(response)
        return {
            "narrative": narrative,
            "style": style,
            "llm_used": True,
        }
    except Exception as e:
        logger.warning("LLM narrative synthesis failed: %s", e)
        return {
            "narrative": _basic_narrative(question, data),
            "style": style,
            "llm_used": False,
            "note": f"LLM fallback: {str(e)}",
        }


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _to_float(v) -> float:
    """Coerce to float safely."""
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, str):
        try:
            import re
            cleaned = re.sub(r'[^0-9.\-]', '', v)
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            return 0.0
    return 0.0


def _extract_aggregate(rows: List[Dict]) -> float:
    """Auto-detect the first numeric column and sum it."""
    if not rows:
        return 0.0
    # Find first numeric column
    for key in rows[0]:
        val = rows[0][key]
        if isinstance(val, (int, float, Decimal)):
            return sum(_to_float(r.get(key, 0)) for r in rows)
    return 0.0


def _comparison_narrative(comparisons: List[Dict]) -> str:
    """Build a human-readable summary of comparisons."""
    parts = []
    for c in comparisons:
        direction_word = "increased" if c["direction"] == "up" else "decreased" if c["direction"] == "down" else "remained flat"
        pct_str = f" ({c['pct_change']:+.1f}%)" if c["pct_change"] is not None else ""
        parts.append(
            f"{c['to']} {direction_word} by {abs(c['delta']):,.2f}{pct_str} "
            f"compared to {c['from']}"
        )
    return ". ".join(parts) + "." if parts else "No comparison available."


def _fallback_synthesize(params: Dict) -> Dict:
    """Basic answer synthesis without post_sql_reasoner."""
    sql_results = params.get("sql_results", [])
    question = params.get("question", "")
    plan = params.get("plan", {})

    if not sql_results:
        return {"answer": "No data found for this query.", "confidence": 0.0}

    # Simple auto-formatting
    if len(sql_results) == 1 and len(sql_results[0]) == 1:
        # Single value result
        key = list(sql_results[0].keys())[0]
        val = sql_results[0][key]
        if isinstance(val, (int, float, Decimal)):
            formatted = f"{float(val):,.2f}"
        else:
            formatted = str(val)
        return {
            "answer": f"The result is {formatted}.",
            "confidence": 0.8,
            "data": sql_results,
        }

    row_count = len(sql_results)
    col_count = len(sql_results[0]) if sql_results else 0
    return {
        "answer": f"Found {row_count} results with {col_count} columns.",
        "confidence": 0.7,
        "data": sql_results,
    }


def _basic_narrative(question: str, data: Any) -> str:
    """Basic non-LLM narrative."""
    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            if isinstance(v, (int, float)):
                parts.append(f"{k.replace('_', ' ').title()}: {v:,.2f}")
            elif isinstance(v, str):
                parts.append(f"{k.replace('_', ' ').title()}: {v}")
        if parts:
            return f"Based on the data: {'; '.join(parts)}."
    elif isinstance(data, list) and data:
        return f"The query returned {len(data)} results."
    return "Unable to generate a narrative from the available data."
