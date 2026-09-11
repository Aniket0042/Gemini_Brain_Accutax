"""
test_phase4.py — Unit tests for Phase 4 Analytical SQL Functions and RLS Hardening.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock, patch
import pytest

from gemini_brain.router.dates import Window
from gemini_brain.router.fast_router import fast_route
from gemini_brain.tools.context import RequestCtx
from gemini_brain.tools.formatters import render, render_project_expense_rollup, render_inventory_movement, render_gl_profitability
from gemini_brain.tools.registry import REGISTRY
from gemini_brain.tools.schemas import (
    GLProfitabilityParams,
    InventoryMovementParams,
    ProjectExpenseRollupParams,
)
from gemini_brain.sql_fallback.db_connection import execute_sql_function


def test_phase4_schemas_contain_no_org_or_user_id():
    """Security Invariant: No Phase 4 schema may contain org_id or user_id."""
    for model_cls in [ProjectExpenseRollupParams, InventoryMovementParams, GLProfitabilityParams]:
        fields = set(model_cls.model_fields.keys())
        assert "org_id" not in fields
        assert "organization_id" not in fields
        assert "user_id" not in fields
        assert "userId" not in fields


def test_phase4_schemas_inject_tenant_from_context():
    """Verify to_sql_args injects org_id and date boundaries from RequestCtx."""
    ctx = RequestCtx(org_id=45, user_id="18")
    
    pe = ProjectExpenseRollupParams(period="2026")
    args_pe = pe.to_sql_args(ctx)
    assert args_pe[0] == 45
    assert args_pe[1] == datetime.date(2026, 1, 1)
    assert args_pe[2] == datetime.date(2026, 12, 31)

    inv = InventoryMovementParams(period="2026")
    args_inv = inv.to_sql_args(ctx)
    assert args_inv[0] == 45
    assert args_inv[1] == datetime.date(2026, 1, 1)
    assert args_inv[2] == datetime.date(2026, 12, 31)

    gl = GLProfitabilityParams(period="2026")
    args_gl = gl.to_sql_args(ctx)
    assert args_gl[0] == 45
    assert args_gl[1] == datetime.date(2026, 1, 1)
    assert args_gl[2] == datetime.date(2026, 12, 31)


def test_phase4_formatters():
    """Verify Phase 4 formatters render tables with AED formatting."""
    pe_data = [
        {
            "project_name": "Ahmedabad Care Center",
            "vendor_contact_name": "Hemang",
            "bank_account_name": "ADCB Operating",
            "transaction_count": 10,
            "total_spend": 40980.00,
        }
    ]
    md_pe = render("project_expense_rollup", pe_data)
    assert "Ahmedabad Care Center" in md_pe
    assert "Hemang" in md_pe
    assert "AED 40,980.00" in md_pe

    inv_data = [
        {
            "item_name": "Clinical Documentation System",
            "sku": "SKU-0081-8350",
            "warehouse_name": "Dubai Central",
            "units_sold_invoices": 277,
            "units_dispatched_delivery_notes": 0,
        }
    ]
    md_inv = render("inventory_movement", inv_data)
    assert "Clinical Documentation System" in md_inv
    assert "SKU-0081-8350" in md_inv
    assert "Dubai Central" in md_inv

    gl_data = [
        {
            "account_type": "Operating Income",
            "account_count": 5,
            "total_income": 500000.00,
            "total_expense": 100000.00,
            "net_margin": 400000.00,
        }
    ]
    md_gl = render("gl_profitability", gl_data)
    assert "Operating Income" in md_gl
    assert "AED 500,000.00" in md_gl
    assert "AED 400,000.00" in md_gl


def test_phase4_fast_router_matches_complex_queries():
    """Verify FastRouter regex rules route complex analytical queries directly to SQL function tools."""
    r1 = fast_route("List all project expenses for our organization", 45)
    assert r1 is not None
    assert r1.endpoint == "fn_project_expense_rollup"

    r2 = fast_route("List inventory items with their warehouse location and units sold and dispatched", 45)
    assert r2 is not None
    assert r2.endpoint == "fn_inventory_movement"

    r3 = fast_route("Show GL profitability by account type", 45)
    assert r3 is not None
    assert r3.endpoint == "fn_gl_profitability"


def test_execute_sql_function_applies_rls_and_timeout():
    """Verify execute_sql_function applies SET LOCAL app.current_org and statement_timeout."""
    mock_conn = MagicMock()
    mock_cur = MagicMock()
    mock_conn.cursor.return_value.__enter__.return_value = mock_cur
    mock_cur.description = [("col1",), ("col2",)]
    mock_cur.fetchall.return_value = [(10, 20)]

    with patch("gemini_brain.sql_fallback.db_connection.get_connection", return_value=mock_conn):
        rows = execute_sql_function("fn_project_expense_rollup", (45, "2026-01-01", "2026-12-31"), org_id=45)
        
        # Verify RLS session setting was called
        mock_cur.execute.assert_any_call("SET LOCAL app.current_org = %s;", ("45",))
        mock_cur.execute.assert_any_call("SET LOCAL statement_timeout = '10s';")
        mock_cur.execute.assert_any_call("SELECT * FROM fn_project_expense_rollup(%s, %s, %s);", (45, "2026-01-01", "2026-12-31"))
        assert rows == [{"col1": 10, "col2": 20}]
