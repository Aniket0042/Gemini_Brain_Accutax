"""Token-budgeted thread history (plan step 1.2): whole newest turns, a figure-free summary of older ones."""
from gemini_brain.agent import history, loop, preview


def _thread(n, answer_chars=60):
    out = []
    for i in range(1, n + 1):
        out += [{"role": "user", "content": f"Question {i}"},
                {"role": "assistant", "content": f"Answer {i}: AED {i},000.00 " + "x" * answer_chars}]
    return out


def test_short_threads_go_whole_and_past_six_messages():
    summary, kept = history.select(_thread(6))
    assert summary is None
    assert [m["content"] for m in kept if m["role"] == "user"] == [f"Question {i}" for i in range(1, 7)]


def test_long_threads_keep_the_newest_whole_turns_and_summarize_the_rest():
    summary, kept = history.select(_thread(10, answer_chars=3000), budget=4000)
    questions = [m["content"] for m in kept if m["role"] == "user"]
    assert questions and questions[-1] == "Question 10"
    assert sum(history.estimate_tokens(m["content"]) for m in kept) <= 4000 * history.KEEP_SHARE
    assert summary.startswith(history.SUMMARY_HEAD)
    assert "1. Asked: Question 1" in summary
    assert "AED" not in summary  # never figures from the answers


def test_a_turn_larger_than_the_budget_is_still_sent():
    summary, kept = history.select(_thread(2, answer_chars=50000), budget=1000)
    assert [m["content"] for m in kept if m["role"] == "user"] == ["Question 2"]
    assert "Question 1" in summary


def test_summary_lists_what_each_old_turn_queried():
    calls = [{"name": "query_metrics", "ok": True, "input": {
        "view": "pnl", "measures": ["pnl.revenue"], "period": {"preset": "last_quarter"},
        "filters": [{"member": "pnl.net_margin_pct", "operator": "gt", "values": ["20"]}]}},
        {"name": "list_documents", "ok": False, "input": {"type": "bills"}}]
    state = history.add_spec({"context_tokens_used": 5}, "Question 1", calls)
    assert state["context_tokens_used"] == 5
    summary, _ = history.select(_thread(3, answer_chars=3000), state[history.STATE_KEY], budget=2500)
    assert "queried: query_metrics pnl; revenue; period last_quarter; where net_margin_pct gt 20" in summary
    assert "bills" not in summary  # failed calls are not listed


def test_specs_are_capped():
    state = {}
    for i in range(history.MAX_SPECS + 5):
        state = history.add_spec(state, f"q{i}", [])
    assert len(state[history.STATE_KEY]) == history.MAX_SPECS
    assert state[history.STATE_KEY][-1]["q"] == f"q{history.MAX_SPECS + 4}"


def test_summary_is_capped_and_says_older_turns_were_dropped():
    older = [[{"role": "user", "content": f"Question {i} " + "y" * 150}] for i in range(200)]
    text = history.summary(older)
    assert history.estimate_tokens(text) <= history.SUMMARY_TOKENS + 100
    assert "even older turns not shown" in text and "Question 199" in text


def test_leading_answers_are_dropped():
    assert history.turns([{"role": "assistant", "content": "hi"}, {"role": "user", "content": "q"}]) == [
        [{"role": "user", "content": "q"}]]


def test_loop_puts_the_summary_before_the_newest_turns():
    messages = loop._messages(_thread(10, answer_chars=12000), "go back to my first question", [])
    assert [m["role"] for m in messages][0] == "user"
    first = messages[0]["content"][0]["text"]
    assert first.startswith(history.SUMMARY_HEAD) and "Question 1" in first
    assert messages[-1]["content"][0]["text"] == "go back to my first question"
    roles = [m["role"] for m in messages]
    assert all(a != b for a, b in zip(roles, roles[1:]))


def _fake_thread_store(monkeypatch, record):
    from gemini_brain.memory import session_memory as memory
    store = {"state": dict(record.get("conversation_state") or {}), "limit": None}

    def fake_history(sid, limit=10, db_name=""):
        store["limit"] = limit
        return _thread(2)

    monkeypatch.setattr(memory, "get_session_record",
                        lambda sid, db_name="": {**record, "conversation_state": dict(store["state"])})
    monkeypatch.setattr(memory, "get_history_by_session", fake_history)
    monkeypatch.setattr(memory, "get_state_by_session", lambda sid, db_name="": dict(store["state"]))
    monkeypatch.setattr(memory, "update_state_by_session",
                        lambda sid, new_state, db_name="": store.__setitem__("state", new_state))
    return store


def test_preview_reads_the_thread_and_saves_the_turn(monkeypatch):
    from gemini_brain.artifacts.delivery import Delivery
    store = _fake_thread_store(monkeypatch, {"user_id": 7, "organization_ids": [24], "conversation_state": {
        "context_tokens_used": 9, history.STATE_KEY: [{"q": "old", "calls": []}]}})
    thread = preview.thread_context("sid", [24], 7)
    assert store["limit"] == history.FETCH_MESSAGES and len(thread.messages) == 4
    assert thread.specs == [{"q": "old", "calls": []}] and thread.delivery is None and thread.figures == []
    preview.record_turn("sid", "Revenue by month as Excel", [
        {"name": "query_metrics", "ok": True, "input": {"view": "pnl", "measures": ["revenue"]}}],
        Delivery(mode="file", format="xlsx"),
        [{"tool": "query_metrics", "result": {"rows": [{"pnl.revenue": "120400.00"}], "row_count": 1}}])
    state = store["state"]
    assert state["context_tokens_used"] == 9
    assert state[history.STATE_KEY][-1] == {"q": "Revenue by month as Excel", "calls": ["query_metrics pnl; revenue"]}
    assert state[history.DELIVERY_KEY] == {"mode": "file", "format": "xlsx", "chart_hint": None}
    again = preview.thread_context("sid", [24], 7)
    assert 120400.0 in again.figures and again.delivery["format"] == "xlsx"
    assert preview.thread_context(None, [24], 7) == preview.Thread()


def test_a_thread_of_another_user_or_scope_gives_no_context(monkeypatch):
    """Plan step 1.7: tenant scope comes from the request; a thread's history never crosses users or scopes."""
    _fake_thread_store(monkeypatch, {"user_id": 7, "organization_ids": [24, 25], "conversation_state": {
        history.FIGURES_KEY: [[999.0]], history.DELIVERY_KEY: {"mode": "chart"}}})
    assert preview.thread_context("sid", [24, 25], 8) == preview.Thread()   # another user
    assert preview.thread_context("sid", [24], 7) == preview.Thread()       # another set of organizations
    assert preview.thread_context("sid", [25, 24], 7).figures == [999.0]    # same scope, any order


def test_a_new_or_unreadable_thread_gives_no_context(monkeypatch):
    from gemini_brain.memory import session_memory as memory
    monkeypatch.setattr(memory, "get_session_record", lambda sid, db_name="": None)
    assert preview.thread_context("sid", [24], 7) == preview.Thread()

    def broken(sid, db_name=""):
        raise RuntimeError("db down")
    monkeypatch.setattr(memory, "get_session_record", broken)
    assert preview.thread_context("sid", [24], 7) == preview.Thread()


def test_figures_keep_the_last_turns_only():
    state = {}
    for i in range(history.FIGURE_TURNS + 2):
        state = history.add_figures(state, [{"result": {"rows": [{"x": str(i), "flag": True}]}}])
    assert history.earlier_figures(state) == [2.0, 3.0, 4.0]


def test_verifier_accepts_figures_from_earlier_turns():
    text = "Overdue is AED 14,445,699.00."
    assert loop.verify_answer(text, loop._evidence([]), enforce=False).to_public()["grounded"] is False
    report = loop.verify_answer(text, loop._evidence([], [14445699.0]), enforce=False).to_public()
    assert report["grounded"] is True and report["unmatched"] == []
