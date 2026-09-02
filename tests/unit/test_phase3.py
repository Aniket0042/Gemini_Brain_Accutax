"""
test_phase3.py — Comprehensive unit tests for Phase 3 tool registry, formatters, and result cache.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from gemini_brain.cache.result_cache import ResultCache, make_cache_key, result_cache
from gemini_brain.cache.versions import bump_data_version, get_data_version, reset_data_versions
from gemini_brain.orchestrator.gemini_brain_runner import GeminiBrainRunner
from gemini_brain.resilience.outcomes import Outcome, Retrieved
from gemini_brain.tools.context import RequestCtx
from gemini_brain.tools.formatters import format_aed, render, render_aging_buckets, render_kv_summary, render_row_table
from gemini_brain.tools.registry import REGISTRY, gemini_declarations
from gemini_brain.tools.schemas import (
    IncomeTotalParams,
    InvoiceListParams,
    ItemListParams,
    JournalEntriesParams,
    ProfitLossParams,
)


def test_no_param_schema_contains_org_or_user_id():
    """Acceptance criterion: Assert NO tool param schema contains org_id or user_id fields."""
    for name, spec in REGISTRY.items():
        fields = spec.params.model_fields.keys()
        assert "org_id" not in fields, f"Security violation: tool '{name}' schema contains org_id"
        assert "organization_id" not in fields, f"Security violation: tool '{name}' schema contains organization_id"
        assert "user_id" not in fields, f"Security violation: tool '{name}' schema contains user_id"
        assert "userId" not in fields, f"Security violation: tool '{name}' schema contains userId"


def test_param_model_injects_tenant_from_context():
    """Verify that to_query() correctly injects org_id and user_id from RequestCtx."""
    ctx = RequestCtx(org_id=42, user_id=99)

    # IncomeTotal
    p_inc = IncomeTotalParams(period="2026")
    q_inc = p_inc.to_query(ctx)
    assert q_inc["organization_id"] == 42
    assert q_inc["user_id"] == "99"
    assert q_inc["filter_year"] == "2026"

    # InvoiceList
    p_inv = InvoiceListParams(period="this year", status="unpaid", limit=15)
    q_inv = p_inv.to_query(ctx)
    assert q_inv["userId"] == 99
    assert q_inv["limit"] == 15
    assert q_inv["status"] == "unpaid"

    # JournalEntries
    p_je = JournalEntriesParams(limit=25)
    q_je = p_je.to_query(ctx)
    assert q_je["userId"] == 99
    assert q_je["organizationId"] == 42
    assert q_je["limit"] == 25


def test_format_aed():
    assert format_aed(1234567.89) == "AED 1,234,567.89"
    assert format_aed("50000") == "AED 50,000.00"
    assert format_aed(0) == "AED 0.00"
    assert format_aed(None) == "AED 0.00"


def test_formatters_render_markdown():
    # KV Summary
    kv = {"total_sales": 500000, "net_profit": 120000}
    res_kv = render("kv_summary", kv)
    assert "| **Total Sales** | AED 500,000.00 |" in res_kv
    assert "| **Net Profit** | AED 120,000.00 |" in res_kv

    # Row Table
    rows = [
        {"invoice_number": "INV-001", "customer_name": "Acme Corp", "total": 5000, "status": "paid"},
        {"invoice_number": "INV-002", "customer_name": "Globex", "total": 12000, "status": "unpaid"},
    ]
    res_table = render("row_table", rows)
    assert "| Invoice Number |" in res_table
    assert "Customer Name" in res_table
    assert "INV-001" in res_table
    assert "AED 5,000.00" in res_table

    # Aging buckets
    aging = {"current": 10000, "days_1_30": 5000, "days_31_60": 2000, "days_90_plus": 1500}
    res_aging = render("aging_buckets", aging)
    assert "| Aging Bucket | Outstanding Amount |" in res_aging
    assert "AED 10,000.00" in res_aging


def test_result_cache_operations():
    reset_data_versions()
    cache = ResultCache()

    key1 = make_cache_key(org_id=27, tool_name="profit_loss", params={"start_date": "2026-01-01"})
    key2 = make_cache_key(org_id=27, tool_name="profit_loss", params={"start_date": "2026-01-01"})
    assert key1 == key2

    # Set and Get
    cache.set_sync(key1, {"net_income": 50000}, ttl=60)
    hit = cache.get_sync(key1)
    assert hit == {"net_income": 50000}

    # Version bump creates different key
    bump_data_version(org_id=27)
    key_after_mutation = make_cache_key(org_id=27, tool_name="profit_loss", params={"start_date": "2026-01-01"})
    assert key_after_mutation != key1
    assert cache.get_sync(key_after_mutation) is None


def test_gemini_declarations_structure():
    decls = gemini_declarations()
    assert len(decls) == len(REGISTRY)
    names = [d["name"] for d in decls]
    assert "profit_loss" in names
    assert "invoice_list" in names
    assert "answer_directly" in names
    assert "unsupported" in names


def test_all_tools_are_narrated_including_former_lookup_tools():
    """Verify every tool's result -- including simple lookups like invoice_list that
    used to skip narration entirely -- is narrated by Bedrock. The formatted table is
    still produced and exposed separately via table_markdown for the UI, but it is
    never the `answer` on the happy path."""
    runner = GeminiBrainRunner(api_key="test-key")
    runner._enforce_tenant_isolation = MagicMock(return_value=27)

    invoice_data = [
        {"invoice_number": "INV-101", "name": "Client A", "total": 1500, "status": "paid"},
        {"invoice_number": "INV-102", "name": "Client B", "total": 3200, "status": "unpaid"},
    ]

    with patch("gemini_brain.orchestrator.gemini_brain_runner.select_endpoint", return_value=({"endpoint": "/income/list", "query_params": {}}, 10, 10)), \
         patch("gemini_brain.api_client.accutax_client.call_api_resilient", return_value=Retrieved(Outcome.OK, payload=invoice_data, tier="live_api", endpoint="/income/list")), \
         patch(
             "gemini_brain.orchestrator.gemini_brain_runner.reason_over_data",
             return_value=("You have 2 invoices: INV-101 (paid) and INV-102 (unpaid).", "Claude Haiku 4.5", 10, 5),
         ) as mock_reason:

        res = runner.run(
            query="Show recent sales invoices",
            organization_id=27,
            use_api=True,
        )

        # Bedrock IS called even for a former narrate=False tool
        mock_reason.assert_called_once()
        assert res["answer"] == "You have 2 invoices: INV-101 (paid) and INV-102 (unpaid)."
        # The deterministic table is still built and exposed separately for the UI
        assert "| Invoice Number | Name | Total | Status |" in res["table_markdown"]
        assert "INV-101" in res["table_markdown"]
        assert "AED 1,500.00" in res["table_markdown"]
