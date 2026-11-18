"""The host model is told not to count capped lists and not to restate legal figures as settled."""
from gemini_brain.mcp_server.server import INSTRUCTIONS


def test_capped_lists_are_not_counted():
    assert "never count its rows" in INSTRUCTIONS
    assert "search always is" in INSTRUCTIONS


def test_legal_figures_come_only_from_the_latest_result():
    assert "Penalty rates, recovery deadlines" in INSTRUCTIONS
    assert "never carry a rate or deadline from one answer into another" in INSTRUCTIONS


def test_signs_are_never_flipped_and_ledger_periods_are_named():
    assert "Never change a figure's sign" in INSTRUCTIONS
    assert "Name the period or as-of date" in INSTRUCTIONS


def _run(rows):
    return lambda name, args, scope: {"rows": rows, "row_count": len(rows)}


def test_negative_pnl_line_carries_the_sign_note():
    from gemini_brain.api import mcp_data
    out = mcp_data.report({"report": "profit_and_loss", "period": {"preset": "ytd"}},
                          _run([{"revenue": "-1,237,856.00", "operating_expenses": "764,371.00"}]), [27])
    assert "do not flip signs" in out["reading_note"]


def test_normal_pnl_has_no_sign_note():
    from gemini_brain.api import mcp_data
    out = mcp_data.report({"report": "profit_and_loss", "period": {"preset": "ytd"}},
                          _run([{"revenue": "1,000.00", "operating_expenses": "400.00", "net_profit": "-5.00"}]), [24])
    assert "reading_note" not in out  # a loss is a loss, not an opposite-side posting


def test_tables_state_their_row_total():
    from gemini_brain.mcp_server.server import render_table
    rows = [{"a": i} for i in range(35)]
    assert "35 rows in total" in render_table({"rows": rows, "total_rows": 35}, 50)


def test_record_links_only_for_https_app_address(monkeypatch):
    from gemini_brain.agent import links
    from gemini_brain.api import mcp_data
    monkeypatch.setattr(links, "_app_url", lambda: "http://106.51.80.81:5558")
    assert mcp_data._url("bill", 12135) == ""
    monkeypatch.setattr(links, "_app_url", lambda: "https://app.example.ae")
    assert mcp_data._url("bill", 12135).startswith("https://app.example.ae/")
