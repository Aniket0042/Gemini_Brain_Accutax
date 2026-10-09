"""KPI tiles for agent answers: one headline query becomes tiles; anything else gets none."""
from gemini_brain.agent import loop, preview, tiles


def _pnl(params, rows, notes=("Period: 2026-01-01 to 2026-10-08.",)):
    return {"tool": "query_metrics", "input": params, "result": {"view": "pnl", "rows": rows, "notes": list(notes)}}


ROW = {"pnl.organization_id": 24, "pnl.organization_name": "A", "pnl.currency": "AED",
       "pnl.revenue": "5809352.00", "pnl.net_profit": "-143160.5", "pnl.net_margin_pct": "12.345"}
PARAMS = {"view": "pnl", "measures": ["pnl.revenue", "pnl.net_profit", "pnl.net_margin_pct"]}


def test_a_headline_query_becomes_tiles_with_its_period():
    [block] = tiles.kpi_tiles([_pnl(PARAMS, [ROW])])
    assert block["type"] == "kpi_grid" and block["period"] == "2026-01-01 to 2026-10-08"
    assert [(i["label"], i["value"]) for i in block["items"]] == [
        ("Revenue", "AED 5,809,352.00"), ("Net profit", "AED -143,160.50"), ("Net margin %", "12.3%")]


def test_balances_counts_and_ratios_are_shown_in_their_own_units():
    data = [{"tool": "query_metrics",
             "input": {"view": "balance_sheet", "measures": ["balance_sheet.current_ratio", "balance_sheet.cash_and_bank"]},
             "result": {"view": "balance_sheet", "notes": ["Balances as of 2026-10-08."],
                        "rows": [{"balance_sheet.currency": "AED", "balance_sheet.current_ratio": "1.256",
                                  "balance_sheet.cash_and_bank": "100"}]}},
            {"tool": "app_guide", "input": {}, "result": {}}]
    [block] = tiles.kpi_tiles(data)
    assert block["period"] == "As of 2026-10-08"
    assert [i["value"] for i in block["items"]] == ["1.26", "AED 100.00"]
    inv = [{"tool": "query_metrics", "input": {"view": "inventory", "measures": ["inventory.quantity_on_hand", "inventory.item_count"]},
            "result": {"view": "inventory", "notes": [], "rows": [{"inventory.quantity_on_hand": "8524320.0000",
                                                                   "inventory.item_count": "200"}]}}]
    assert [i["value"] for i in tiles.kpi_tiles(inv)[0]["items"]] == ["8,524,320", "200"]


def test_comparisons_breakdowns_trends_and_several_queries_get_no_tiles():
    two_orgs = [ROW, {**ROW, "pnl.organization_id": 25}]
    assert tiles.kpi_tiles([_pnl(PARAMS, two_orgs)]) == []
    assert tiles.kpi_tiles([_pnl({**PARAMS, "group_by": ["pnl.account_name"]}, [ROW])]) == []
    assert tiles.kpi_tiles([_pnl({**PARAMS, "granularity": "month"}, [ROW])]) == []
    assert tiles.kpi_tiles([_pnl(PARAMS, [ROW]), _pnl(PARAMS, [ROW])]) == []
    assert tiles.kpi_tiles([{"tool": "list_documents", "input": {}, "result": {"rows": [ROW]}}]) == []
    assert tiles.kpi_tiles([]) == []


def test_preview_puts_the_tiles_first(monkeypatch):
    monkeypatch.setattr(loop, "run_agent", lambda *a, **k: loop.AgentResult(
        answer="Revenue AED 5,809,352.", status="ok", data=[_pnl(PARAMS, [ROW])]))
    out = preview.answer("P&L this year", [24], lambda: {}, session_id=None, user_id=1)
    assert out["blocks"][0]["type"] == "kpi_grid"


def test_cells_drop_the_midnight_time_part():
    from decimal import Decimal
    from gemini_brain.semantic.query_metrics import cell_text
    assert cell_text("2026-03-01T00:00:00.000") == "2026-03-01"
    assert cell_text("2026-03-01T14:30:00.000") == "2026-03-01T14:30:00.000"
    assert cell_text(Decimal("0E-20")) == "0" and cell_text("INV-0012") == "INV-0012" and cell_text(7) == 7


def test_chart_and_file_rows_use_readable_period_labels():
    data = [{"tool": "query_metrics", "input": {"view": "payments", "granularity": "month"},
             "result": {"rows": [{"payments.organization_id": 25, "payments.payment_date.month": "2026-03-01",
                                  "payments.amount_paid": "401541.00"}]}}]
    assert preview.export_rows(data) == [{"month": "Mar 2026", "amount_paid": "401541.00"}]
    assert preview.period_label("2026-04-01", "quarter") == "Q2 2026"
    assert preview.period_label("2026-03-02", "week") == "Week of 02 Mar 2026"
    assert preview.period_label("2026-01-01", "year") == "2026"
