"""The host model is told not to count capped lists and not to restate legal figures as settled."""
from gemini_brain.mcp_server.server import INSTRUCTIONS


def test_capped_lists_are_not_counted():
    assert "never count its rows" in INSTRUCTIONS
    assert "search always is" in INSTRUCTIONS


def test_legal_figures_come_only_from_the_latest_result():
    assert "Penalty rates, recovery deadlines" in INSTRUCTIONS
    assert "never carry a rate or deadline from one answer into another" in INSTRUCTIONS
