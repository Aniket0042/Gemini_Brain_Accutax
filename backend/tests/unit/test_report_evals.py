"""Quality gates over the evaluation sets (tests/report_golden/evals.py).

The full scorecard, with live model narration, is scripts/eval/report_eval.py.
"""
from tests.report_golden.evals import run_chart_intent, run_narrative


def test_every_requested_chart_form_is_honoured_or_explained():
    scored = run_chart_intent()
    assert scored.rate == 1.0, "\n".join(scored.failures)


def test_every_wrong_statement_is_removed():
    scored = run_narrative()["wrong_removed"]
    assert scored.rate == 1.0, "wrong statements kept:\n" + "\n".join(scored.failures)


def test_true_statements_survive():
    scored = run_narrative()["true_kept"]
    assert scored.rate == 1.0, "true statements removed:\n" + "\n".join(scored.failures)
