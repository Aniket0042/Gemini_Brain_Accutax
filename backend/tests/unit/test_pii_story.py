"""The privacy snapshot's story: what was hidden, step by step, and the answer, without repeating the record."""
import json

from gemini_brain.privacy import snapshot_html, snapshot_story
from gemini_brain.privacy.masking import PLACEHOLDER_RULES

VAULT = {"TRN_1": "100234567800003", "SUPPLIER_1": "Michael", "SUPPLIER_2": "Theodore", "ORG_1": "Org Two"}


def _use(uid, inp):
    return {"role": "assistant", "content": [{"toolUse": {"toolUseId": uid, "name": "query_metrics", "input": inp}}]}


def _result(uid, body):
    return {"role": "user", "content": [{"toolResult": {"toolUseId": uid, "content": [{"json": body}]}}]}


def _call(question_raw, question_sent):
    trn_filter = {"view": "purchases", "filters": [{"member": "purchases.vendor_trn", "operator": "equals",
                                                     "values": ["[TRN_1]"]}]}
    empty = {"rows": [], "row_count": 0}
    rows_sent = {"rows": [{"purchases.vendor_name": "[SUPPLIER_1]", "purchases.organization_name": "[ORG_1]",
                           "purchases.vendor_trn": "[TRN_1]", "net": "71366.0000", "organization_id": "25"},
                          {"purchases.vendor_name": "[SUPPLIER_2]", "purchases.organization_name": "[ORG_1]",
                           "purchases.vendor_trn": "", "net": "68326.0000", "organization_id": "25"}], "row_count": 2}
    rows_raw = json.loads(json.dumps(rows_sent).replace("[SUPPLIER_1]", "Michael").replace("[SUPPLIER_2]", "Theodore")
                          .replace("[ORG_1]", "Org Two").replace("[TRN_1]", "100234567800003"))

    def convo(question, rows):
        return [{"role": "user", "content": [{"text": "Who are our suppliers?"}]},
                {"role": "assistant", "content": [{"text": "[SUPPLIER_1] and others."}]},
                {"role": "user", "content": [{"text": question}]},
                _use("a", {**trn_filter, "measures": ["net"]}), _result("a", {"error": "Unknown measure(s) ['net']"}),
                _use("b", {**trn_filter, "period": {"preset": "ytd"}}), _result("b", empty),
                _use("c", {**trn_filter, "period": {"preset": "last_year"}}), _result("c", empty),
                _use("d", {"view": "purchases", "group_by": ["purchases.vendor_name"], "limit": 5}), _result("d", rows)]

    return {"purpose": "agent", "question_index": 2, "sentinel": {},
            "sent": {"system": "Organizations: [ORG_1] (AED)\n" + PLACEHOLDER_RULES, "messages": convo(question_sent, rows_sent)},
            "raw": {"system": "Organizations: Org Two (AED)", "messages": convo(question_raw, rows_raw)},
            "reply_model": "The biggest is **[SUPPLIER_1]** at AED 71,366.00 for [ORG_1].",
            "reply_user": "The biggest is **Michael** at AED 71,366.00 for Org Two."}


def test_the_story_says_each_thing_once():
    story = snapshot_story.build(_call("Paid TRN 100234567800003?", "Paid TRN [TRN_1]?"), VAULT)
    s = story["summary"]
    assert s["sentinel_pass"] is True and s["lookups"] == 4
    assert s["figures_checked"] == 7 and s["figures_changed"] == 0     # net, organization_id (2 rows), 3 row counts
    assert {h["placeholder"]: h["value"] for h in story["hidden"]} == {
        "[TRN_1]": "100234567800003", "[SUPPLIER_1]": "Michael", "[SUPPLIER_2]": "Theodore", "[ORG_1]": "Org Two"}
    where = {h["placeholder"]: h["where"] for h in story["hidden"]}
    assert where["[TRN_1]"] == ["question", "filters", "data"]          # not "instructions": rule examples hide nothing
    assert where["[SUPPLIER_1]"] == ["earlier turns", "data", "answer"]
    assert story["question"] == {"raw": "Paid TRN 100234567800003?", "sent": "Paid TRN [TRN_1]?"}
    assert story["earlier_turns"] == 1
    trn, top = story["steps"]
    assert trn["tries"] == 3 and trn["failed"] == 1 and trn["rows"] == 0  # retries grouped into one step
    assert trn["inputs"] == [{"placeholder": "[TRN_1]", "value": "100234567800003"}]
    assert top["tries"] == 1 and top["rows"] == 2
    assert {f["field"]: f["placeholders"] for f in top["fields"]} == {
        "vendor name": "[SUPPLIER_1], [SUPPLIER_2]", "organization name": "[ORG_1]", "vendor trn": "[TRN_1]"}
    assert story["answer"]["user"].startswith("The biggest is **Michael**")


def test_without_the_raw_side_the_story_shows_the_sent_side():
    call = _call("x", "Paid TRN [TRN_1]?")
    call.pop("raw")
    story = snapshot_story.build(call, {})
    assert story["question"] == {"raw": None, "sent": "Paid TRN [TRN_1]?"}
    assert story["answer"]["user"] is None and story["summary"]["figures_checked"] == 0
    assert all(h["value"] is None for h in story["hidden"]) and len(story["hidden"]) == 4


def test_ranges_and_report_answers_read_well():
    assert snapshot_story._range(["SUPPLIER_1", "SUPPLIER_2", "SUPPLIER_3"]) == "[SUPPLIER_1–3]"
    assert snapshot_story._range(["SUPPLIER_1", "SUPPLIER_3"]) == "[SUPPLIER_1], [SUPPLIER_3]"
    text = snapshot_story._readable(json.dumps({"headline": "[SUPPLIER_1] leads", "summary": ["One [F1]."], "risks": []}))
    assert text == "**[SUPPLIER_1] leads**\n\nSummary:\n- One [F1]."
    assert snapshot_story._readable("plain text") == "plain text"


def test_old_snapshots_without_a_question_index_still_find_the_question():
    call = _call("q real", "q sent")
    call.pop("question_index")
    assert snapshot_story.build(call, VAULT)["question"]["sent"] == "q sent"


def test_export_leads_with_the_story_and_keeps_the_full_record():
    snap = snapshot_story.attach({"id": "abc", "mode": "on", "vault": VAULT, "stats": {"hidden": {}},
                                  "calls": [_call("Paid TRN 100234567800003?", "Paid TRN [TRN_1]?")]})
    page = snapshot_html.render(snap)
    assert page.index("What was hidden") < page.index("Step by step") < page.index("The answer") \
        < page.index("Full record of this call")
    assert "Nothing personal sent" in page and "3 tries" in page and "1 failed and retried" in page
    assert "<script" not in page


def test_the_question_is_the_title_and_one_line_when_nothing_was_hidden_in_it():
    hidden_q = snapshot_story.attach({"id": "a", "mode": "on", "vault": VAULT, "stats": {},
                                      "calls": [_call("Paid TRN 100234567800003?", "Paid TRN [TRN_1]?")]})
    page = snapshot_html.render(hidden_q)
    assert page.index("class='question'") < page.index("Request a")
    assert "Paid TRN <mark class=\"raw\">100234567800003</mark>?" in page.split("Request a")[0]
    plain = snapshot_story.attach({"id": "b", "mode": "on", "vault": VAULT, "stats": {},
                                   "calls": [_call("top 10 customers", "top 10 customers")]})
    page = snapshot_html.render(plain)
    assert "“top 10 customers”" in page and "Nothing to hide in it: sent as typed." in page
    assert "<span class='note'>Real:</span> top 10 customers" not in page


def test_the_list_shows_the_question_as_sent(tmp_path, monkeypatch):
    from gemini_brain.config.settings import settings
    from gemini_brain.privacy import snapshots
    monkeypatch.setattr(settings, "pii_snapshot_dir", str(tmp_path))
    day = tmp_path / "2026-10-19"
    day.mkdir()
    call = _call("Paid TRN 100234567800003?", "Paid TRN [TRN_1]?")
    (day / "0123456789abcdef.json").write_text(json.dumps(
        {"id": "0123456789abcdef", "ts": "2026-10-19T06:38:00+00:00", "user_id": 5, "mode": "on",
         "calls": [{k: call[k] for k in ("purpose", "sent", "reply_model", "sentinel", "question_index")}]}),
        encoding="utf-8")
    [item] = snapshots.recent()
    assert item["question"] == "Paid TRN [TRN_1]?"                     # the list never holds real values
