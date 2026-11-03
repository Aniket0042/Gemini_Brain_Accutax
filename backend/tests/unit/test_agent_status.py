"""Live run card and agent card status: plans, step events from the loop, and status_for."""
import pytest

from gemini_brain.agent import loop, profiles
from gemini_brain.agent.status import status_for


def test_plans_only_name_the_agents_tools_or_the_files_step():
    for p in profiles.all_profiles():
        for tool, label, _ in p.plan:
            assert tool == "files" or p.allows(tool), (p.key, tool)
            assert label
        public = p.to_public()["plan"]
        assert [s["tool"] for s in public] == [t for t, _, _ in p.plan]


# ── status_for ───────────────────────────────────────────────────────────────

def _review(**kw):
    return [{"tool": "review_vat", "result": {"side": "purchases", "bills_checked": 329, **kw}}]


def test_review_status_counts_groups_and_findings():
    s = status_for(profiles.get("vat"), _review(groups_found=3, findings_found=293,
                                                findings_by_severity={"high": 252}), [])
    assert s == {"headline": "Checked 329 bills", "badge": "3 duplicate groups · 293 findings", "tone": "danger"}
    assert status_for(profiles.get("ap"), _review(groups_found=1, findings_found=0), [])["badge"] == "1 duplicate group"
    assert status_for(profiles.get("ap"), _review(groups_found=0, findings_found=0), [])["tone"] == "success"


def test_sales_review_says_invoices():
    data = [{"tool": "review_vat", "result": {"side": "sales", "bills_checked": 12}}]
    assert status_for(profiles.get("vat"), data, [])["headline"] == "Checked 12 invoices"


def _cash(lowest, overdue="0"):
    weeks = [{"week_start": "2026-10-25", "week_end": "2026-10-25", "closing_cash": "900"},
             {"week_start": "2026-10-26", "week_end": "2026-11-01", "closing_cash": lowest}]
    return [{"tool": "cash_forecast", "result": {"organizations": [
        {"currency": "AED", "weeks": weeks, "lowest_closing_cash": lowest, "overdue_receivables": overdue}]}}]


def test_cash_status():
    s = status_for(profiles.get("cash"), _cash("776282", "14562189"), [])
    assert s == {"headline": "Lowest week AED 776K (week of 26 Oct)", "badge": "AED 14.6M overdue to collect",
                 "tone": "warning"}
    assert status_for(profiles.get("cash"), _cash("-50"), [])["tone"] == "danger"
    assert status_for(profiles.get("cash"), _cash("500"), [])["badge"] == "No overdue amounts"


def test_report_status_needs_the_files():
    data = [{"tool": "query_metrics", "result": {"rows": [{"pnl.transaction_date.month": "2026-07-01T00:00:00.000"},
                                                          {"pnl.transaction_date.month": "2026-09-01T00:00:00.000"}]}}]
    files = [{"type": "artifact", "kind": "xlsx", "primary": True}, {"type": "artifact", "kind": "pdf", "primary": True},
             {"type": "artifact", "kind": "csv", "primary": False}]
    assert status_for(profiles.get("reporting"), data, files) == {
        "headline": "P&L pack: Jul 2026 to Sep 2026", "badge": "Excel + PDF ready", "tone": "accent"}
    assert status_for(profiles.get("reporting"), data, []) is None


def test_no_status_for_the_general_agent_or_without_the_main_tool():
    assert status_for(profiles.get(None), _review(), []) is None
    assert status_for(profiles.get("vat"), [], []) is None


# ── Step events from the loop ────────────────────────────────────────────────

class FakeAdapter:
    step = 0

    def __init__(self, model_id, label=""):
        self.model_id = model_id

    def converse_with_tools(self, system, messages, tools_, max_tokens=0, purpose=""):
        FakeAdapter.step += 1
        if FakeAdapter.step == 1:
            return {"stopReason": "tool_use", "output": {"message": {"role": "assistant", "content": [
                {"toolUse": {"toolUseId": "a", "name": "cash_forecast", "input": {}}}]}}}
        return {"stopReason": "end_turn", "output": {"message": {"role": "assistant", "content": [{"text": "Done."}]}}}

    def get_token_usage(self):
        return {"llm_calls": 1, "cost_usd": 0.0}


def test_tool_done_reports_each_finished_call(monkeypatch):
    FakeAdapter.step = 0
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "cash_forecast"}}])
    monkeypatch.setattr(loop.tools, "execute", lambda name, params, ctx: ({"row_count": 7}, True))
    started, done = [], []
    loop.run_agent("Cash?", [25], {25: {"name": "Org"}}, profile=profiles.get("cash"),
                   progress=lambda name, params: started.append(name),
                   tool_done=lambda name, ok, ms, rows: done.append((name, ok, rows)))
    assert started == ["cash_forecast"] and done == [("cash_forecast", True, 7)]


def test_a_failing_step_callback_never_breaks_the_answer(monkeypatch):
    FakeAdapter.step = 0
    monkeypatch.setattr(loop, "redact_pii", lambda text: (text, {}))
    monkeypatch.setattr(loop, "BedrockAdapter", FakeAdapter)
    monkeypatch.setattr(loop.tools, "specs", lambda ctx: [{"toolSpec": {"name": "cash_forecast"}}])
    monkeypatch.setattr(loop.tools, "execute", lambda name, params, ctx: ({"row_count": 1}, True))

    def boom(*a):
        raise RuntimeError("socket closed")
    result = loop.run_agent("Cash?", [25], {25: {"name": "Org"}}, tool_done=boom)
    assert result.status == "ok" and result.answer == "Done."
