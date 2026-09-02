"""
test_routing_harness.py — Unit tests for the golden evaluation dataset and routing harness.
"""
from pathlib import Path
import pytest

from scripts.evaluate_routing_harness import (
    calculate_percentiles,
    evaluate_query,
    load_golden_dataset,
    run_evaluation,
)


def test_golden_dataset_schema_and_size():
    """Verify that golden dataset contains 87 items and each adheres to the required schema."""
    dataset = load_golden_dataset()
    assert len(dataset) == 87, f"Expected 87 queries, got {len(dataset)}"

    required_keys = {"id", "query", "expected_intent", "category", "query_type", "expected_endpoint_or_task", "expected_layer"}
    for idx, item in enumerate(dataset):
        missing = required_keys - set(item.keys())
        assert not missing, f"Item {idx} ({item.get('id')}) missing keys: {missing}"
        assert 1 <= item["expected_intent"] <= 7, f"Invalid intent in item {item.get('id')}"
        assert item["query"].strip() != "", f"Empty query in item {item.get('id')}"


def test_percentile_calculation():
    """Test standard percentile computation."""
    data = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    p = calculate_percentiles(data)
    assert p["min"] == 10.0
    assert p["max"] == 100.0
    assert p["avg"] == 55.0
    assert 50.0 <= p["p50"] <= 60.0
    assert 90.0 <= p["p95"] <= 100.0


def test_evaluate_query_fast_router():
    """Test that evaluate_query correctly identifies a canonical fast router query.

    income_total resolves to a direct DB report (rpt_income_total), not REST
    /income/total -- that endpoint was found to ignore organization_id and
    return the identical figure for every org, including ones that don't exist.
    organization_id is checked rather than start_date/end_date because "this
    year" is relative to today's date and would drift; see golden_routing_queries.json
    Q01/Q03 for the same reasoning applied to the real dataset.
    """
    item = {
        "id": "Q_TEST_1",
        "query": "What is our total revenue this year?",
        "expected_intent": 4,
        "category": "Data Lookup",
        "query_type": "canonical",
        "expected_endpoint_or_task": "rpt_income_total",
        "expected_layer": "layer1_fast",
        "expected_params_subset": {"organization_id": "27"},
    }
    res = evaluate_query(item, mode="offline")
    assert res["layer_matched"] == "layer1_fast"
    assert res["is_correct_intent"] is True
    assert res["is_correct_target"] is True
    assert res["is_correct_params"] is True
    assert res["is_fully_correct"] is True


def test_evaluate_query_concept_guard():
    """Test that concept guard vetoes data routing for definitional queries."""
    item = {
        "id": "Q_TEST_2",
        "query": "what is accounts receivable aging?",
        "expected_intent": 6,
        "category": "Concept",
        "query_type": "concept_guard",
        "expected_endpoint_or_task": "gemini_direct",
        "expected_layer": "left_path",
        "expected_params_subset": {},
    }
    res = evaluate_query(item, mode="offline")
    assert res["layer_matched"] == "left_path"
    assert res["is_correct_intent"] is True
    assert res["is_correct_target"] is True
    assert res["is_fully_correct"] is True


def test_run_evaluation_offline_pipeline(tmp_path):
    """Test running full evaluation over golden dataset."""
    dataset = load_golden_dataset()
    md_out = tmp_path / "test_baseline.md"
    json_out = tmp_path / "test_baseline.json"

    metrics = run_evaluation(dataset, mode="offline", output_md=md_out, output_json=json_out)

    assert metrics["summary"]["total_queries"] == 87
    assert metrics["summary"]["layer1"]["hits"] >= 20
    assert metrics["summary"]["layer1"]["accuracy_on_hits_pct"] >= 85.0
    assert md_out.exists()
    assert json_out.exists()
