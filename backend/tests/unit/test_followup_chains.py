"""The follow-up chain set and the replay harness checks that grade it (plan step 1.1)."""
import datetime
import importlib.util
import json
from pathlib import Path

import pytest

from gemini_brain.semantic import periods

BACKEND = Path(__file__).resolve().parents[2]
CHAINS = BACKEND / "tests" / "data" / "followup_chains.json"
TODAY = datetime.date(2026, 10, 11)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def builder():
    return _load("build_followup_chains", BACKEND / "scripts" / "eval" / "build_followup_chains.py")


@pytest.fixture(scope="module")
def replay():
    return _load("replay_real_questions", BACKEND / "scripts" / "eval" / "replay_real_questions.py")


def test_json_matches_generator(builder):
    assert json.loads(CHAINS.read_text(encoding="utf-8")) == builder.build(), \
        "followup_chains.json is stale: run scripts/eval/build_followup_chains.py"


def test_chain_shape(builder):
    cases = builder.build()["cases"]
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    chains = {c["chain"] for c in cases}
    assert 25 <= len(chains) <= 35
    seen = set()
    for case in cases:
        assert all(cid in seen for cid in case["context"]), case["id"]
        expect = case["expect"]
        if case["kind"] == "followup":
            assert expect.get("carry") or expect.get("clarify"), case["id"]
        for call in expect.get("carry") or []:
            period = call.get("period")
            if isinstance(period, str):
                assert period in periods.PRESETS, case["id"]
        seen.add(case["id"])


def _call(name="query_metrics", ok=True, **given):
    return {"name": name, "ok": ok, "input": given}


def test_carry_matches_period_preset_or_dates(replay):
    want = [{"tool": "query_metrics", "view": "sales", "measures": ["net_sales"], "period": "last_quarter"}]
    by_preset = _call(view="sales", measures=["sales.net_sales"], period={"preset": "last_quarter"})
    start, end = periods.resolve({"preset": "last_quarter"}, TODAY)
    by_dates = _call(view="sales", measures=["net_sales"], period={"start": start.isoformat(), "end": end.isoformat()})
    assert replay.carry_check(want, [by_preset], TODAY)["ok"]
    assert replay.carry_check(want, [by_dates], TODAY)["ok"]
    lost = replay.carry_check(want, [_call(view="sales", measures=["net_sales"], period={"preset": "ytd"})], TODAY)
    assert lost == {"ok": False, "closest_misses": ["period"]}


def test_carry_group_by_filters_and_limit(replay):
    want = [{"tool": "query_metrics", "view": "purchases", "measures": ["net_purchases"],
             "group_by": ["vendor_name"], "not_group_by": ["expense_category"], "limit": 5,
             "filters": [["organization_name", "notEquals", "*"]]}]
    good = _call(view="purchases", measures=["net_purchases"], group_by=["purchases.vendor_name"], limit=5,
                 filters=[{"member": "purchases.organization_name", "operator": "notEquals", "values": ["X"]}])
    assert replay.carry_check(want, [good], TODAY)["ok"]
    stale = _call(view="purchases", measures=["net_purchases"], group_by=["vendor_name", "expense_category"], limit=5)
    misses = replay.carry_check(want, [stale], TODAY)["closest_misses"]
    assert misses == ["not_group_by", "filter organization_name"]


def test_carry_no_call_alternative_and_failed_calls(replay):
    want = [{"tool": "query_metrics", "view": "pnl", "measures": ["revenue"]}, {"tool": None}]
    assert replay.carry_check(want, [], TODAY) == {"ok": True, "matched": "no call"}
    failed = _call(view="pnl", measures=["revenue"], ok=False)
    assert replay.carry_check(want, [failed], TODAY)["ok"]  # a failed call is not a figure call
    other = _call(view="sales", measures=["net_sales"])
    assert not replay.carry_check(want, [other], TODAY)["ok"]


def test_carry_star_measure_and_document_type(replay):
    assert replay.carry_check([{"tool": "query_metrics", "view": "receivables", "measures": ["*"],
                                "group_by": ["aging_bucket"]}],
                              [_call(view="receivables", measures=["outstanding"], group_by=["aging_bucket"])],
                              TODAY)["ok"]
    want = [{"tool": "list_documents", "type": "bills", "filters": [["vendor_name", "*", "*"]]}]
    call = _call("list_documents", type="bills", filters=[{"member": "vendor_name", "operator": "equals", "values": ["A"]}])
    assert replay.carry_check(want, [call], TODAY)["ok"]
    assert replay.carry_check(want, [_call("list_documents", type="bills")], TODAY)["closest_misses"] == ["filter vendor_name"]


def test_carry_flags_filters_left_over_from_the_earlier_turn(replay):
    want = [{"tool": "list_documents", "type": "bills", "limit": 10, "not_filters": ["net_purchases"]}]
    clean = _call("list_documents", type="bills", limit=10)
    stale = _call("list_documents", type="bills", limit=10,
                  filters=[{"member": "purchases.net_purchases", "operator": "gt", "values": ["100000"]}])
    assert replay.carry_check(want, [clean], TODAY)["ok"]
    assert replay.carry_check(want, [stale], TODAY)["closest_misses"] == ["stale filter net_purchases"]


def test_followup_summary_counts(replay):
    report = [{"category": "period", "checks": {"carry": {"ok": True}}},
              {"category": "period", "checks": {"carry": {"ok": False}}},
              {"category": "clarify", "checks": {"asks": True}},
              {"category": None, "checks": {}}]
    lines = replay.followup_summary(report)
    assert "- period: carry 1/2" in lines
    assert "- clarify: asks 1/1" in lines
    assert replay.followup_summary([{"checks": {}}]) == []
