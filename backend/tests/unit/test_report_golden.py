"""Golden snapshots: every fixture's report content, frozen after review.

A change to figures, chart forms, colors, takeaways, warnings or the summary
fails here and shows the exact difference. When a change is intended, review
the diff, then refresh the snapshots:

    UPDATE_REPORT_GOLDENS=1 python -m pytest tests/unit/test_report_golden.py

and commit the updated files in tests/report_golden/snapshots/.
"""
import json
import os

import pytest

from tests.report_golden.fixtures import fixture_names
from tests.report_golden.harness import build, load_snapshot, normalised, snapshot, write_snapshot

UPDATE = os.environ.get("UPDATE_REPORT_GOLDENS") == "1"


@pytest.mark.parametrize("name", fixture_names())
def test_report_matches_golden(name):
    actual = normalised(snapshot(build(name)))
    if UPDATE:
        write_snapshot(name, actual)
        return
    expected = load_snapshot(name)
    assert expected is not None, f"No golden for {name!r}: run with UPDATE_REPORT_GOLDENS=1 and review it."
    if actual != expected:
        diffs = [
            f"{key}:\n  expected {json.dumps(expected.get(key), ensure_ascii=False)[:600]}\n"
            f"  actual   {json.dumps(actual.get(key), ensure_ascii=False)[:600]}"
            for key in sorted(set(expected) | set(actual)) if expected.get(key) != actual.get(key)
        ]
        pytest.fail(f"Report {name!r} changed:\n" + "\n".join(diffs))
