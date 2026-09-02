"""
test_phase1.py — Unit tests for Phase 1 optimizations.

Tests:
1. Pure function model selector (pick_model) for Haiku vs Sonnet routing.
2. Prefix-cache restructured prompt formatting in endpoint_selector.
3. 2000-token payload capping and truncation formatting in claude_reasoner.
4. Narration-failure fallback to the formatted table (see test_orchestrator_outcomes.py
   and test_phase3.py for full narration-always coverage).
5. Thinking budget configuration parameter.
"""
import pytest
from unittest.mock import MagicMock, patch

from gemini_brain.endpoints.endpoint_selector import API_SELECTOR_SYSTEM_PROMPT
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.reasoning.claude_reasoner import (
    ANALYST_SYSTEM_PROMPT,
    _format_payload_and_system,
    reason_over_data,
)
from gemini_brain.reasoning.model_selector import pick_model


def test_pick_model_defaults_to_haiku():
    """Regular data lookups and reports should route to fast Claude Haiku."""
    model_id, label = pick_model(intent=4, payload_or_tokens={"total": 12000})
    assert "haiku" in model_id.lower()
    assert "Haiku" in label

    model_id, label = pick_model(intent=3, payload_or_tokens=100)
    assert "haiku" in model_id.lower()


def test_pick_model_forecast_and_advice():
    """Forecasts (intent 5) and Strategic Advice (intent 7) route to Sonnet."""
    model_id, label = pick_model(intent=5, payload_or_tokens=100)
    assert "sonnet" in model_id.lower()
    assert "Sonnet" in label

    model_id, label = pick_model(intent=7, payload_or_tokens=100)
    assert "sonnet" in model_id.lower()


def test_pick_model_large_payload():
    """Very large payloads (>1200 tokens) route to Sonnet for deep synthesis."""
    model_id, label = pick_model(intent=4, payload_or_tokens=1500)
    assert "sonnet" in model_id.lower()


def test_prefix_cache_prompt_structure():
    """Verify that catalog is at the top of the selector prompt and context is at the bottom."""
    cat_idx = API_SELECTOR_SYSTEM_PROMPT.find("API CATALOG:")
    ctx_idx = API_SELECTOR_SYSTEM_PROMPT.find("DYNAMIC CONTEXT:")
    assert cat_idx != -1
    assert ctx_idx != -1
    # Catalog must appear before dynamic context for prefix caching
    assert cat_idx < ctx_idx
    # Redundant question parameter should not be in the template
    assert "{question}" not in API_SELECTOR_SYSTEM_PROMPT


def test_analyst_system_prompt_appendix_b():
    """Verify concise narration prompt from Appendix B."""
    assert "VAT is 5%" in ANALYST_SYSTEM_PROMPT
    assert "Never recompute, re-sum, re-average" in ANALYST_SYSTEM_PROMPT
    assert "Maximum 120 words" in ANALYST_SYSTEM_PROMPT


def test_payload_truncation_notice():
    """Verify row count truncation formatting when dataset exceeds max items."""
    large_items = [{"id": i, "amount": i * 10} for i in range(100)]
    payload = {"items": large_items}

    system, user_msg = _format_payload_and_system(
        query="List transactions",
        data=payload,
        endpoint="/income/list",
    )
    assert "[payload truncated — 40 of 100 rows shown]" in user_msg


from gemini_brain.resilience.outcomes import Outcome, Retrieved


def test_narration_failure_falls_back_to_formatted_table():
    """Every answer is LLM-narrated; the formatted table is used only as a
    fallback when Bedrock narration fails after retries -- never as a default
    zero-LLM shortcut."""
    runner = GeminiBrainRunner(api_key="test-key")

    runner._enforce_tenant_isolation = MagicMock(return_value=27)
    runner.classify_intent = MagicMock(return_value=({"type": 4, "reason": "Data"}, 10, 10))

    with patch("gemini_brain.orchestrator.gemini_brain_runner.classify_intent") as mock_classify, \
         patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint") as mock_select, \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=Retrieved(Outcome.OK, payload={"total_sales": "15000.00"}, tier="live_api", endpoint="/income/total")), \
         patch("gemini_brain.orchestrator.gemini_brain_runner.reason_over_data", side_effect=Exception("bedrock down")) as mock_reason:

        mock_classify.return_value = ({"type": 4, "reason": "Data"}, 10, 10)
        mock_select.return_value = ({"endpoint": "/income/total", "path_params": {}, "query_params": {}}, 20, 20)

        res = runner.run(
            query="What is our total income?",
            organization_id=27,
            use_api=True,
        )

        assert "Total Sales" in res["answer"] or "Metric" in res["answer"]
        assert mock_reason.call_count == 2  # retried once before falling back to the table
        assert res["status"] == "partial"
        assert "complexity_judge" not in (res.get("query_trace", {}).get("stages") or {})
