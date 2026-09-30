"""Offline half of the multi-org evaluation: every case gets its expected layout.

The layout of a multi-org answer is decided without a model call for all but
the "direct" cases, so it is checked here on every test run. The live half
(scripts/eval/multi_org_eval.py) runs the same cases end to end.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gemini_brain.orchestrator.multi_org_metrics import match_metrics, match_series
from gemini_brain.orchestrator.multi_org_present import choose_shape, is_list_question

_CASES = json.loads(
    (Path(__file__).resolve().parents[1] / "data" / "multi_org_eval_cases.json").read_text(encoding="utf-8")
)["cases"]


def offline_layout(question: str) -> tuple:
    """(layout, metric key or keys) as the pipeline decides them before any model call."""
    series = match_series(question)
    if series is not None:
        return "series", series[0].key
    metrics = match_metrics(question)
    if len(metrics) > 1:
        return "multi_metric", [m.key for m in metrics]
    if metrics:
        return "metric", metrics[0].key
    if is_list_question(question):
        return choose_shape(question), None
    return "collapsed", None


@pytest.mark.parametrize("case", [c for c in _CASES if c.get("offline", True)], ids=lambda c: c["id"])
def test_case_gets_expected_layout(case):
    layout, metric = offline_layout(case["question"])
    assert layout == case["layout"]
    if case["layout"] in ("metric", "series"):
        assert metric == case["metric"]
    if case["layout"] == "multi_metric":
        assert metric == case["metrics"]


def test_every_screenshot_question_is_covered():
    assert sum(1 for c in _CASES if c.get("screenshot")) == 6


def test_case_ids_are_unique():
    ids = [c["id"] for c in _CASES]
    assert len(ids) == len(set(ids))
