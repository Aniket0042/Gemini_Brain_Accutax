"""Tests for the grounding verifier and model labels and prices."""
from gemini_brain.policy.verifier import verify_answer


# ── Grounding verifier ────────────────────────────────────────────────

PAYLOAD = {"rows": [{"customer": "A", "revenue": 1203455.0}, {"customer": "B", "revenue": 500000}]}


def test_verifier_accepts_figures_present_in_payload():
    r = verify_answer("A billed AED 1,203,455 and B billed AED 500,000.", PAYLOAD)
    assert r.grounded and r.checked == 2


def test_verifier_accepts_rounded_prose():
    assert verify_answer("A billed roughly AED 1.2M.", PAYLOAD).grounded


def test_verifier_accepts_totals_and_shares():
    assert verify_answer("Together they billed AED 1,703,455.", PAYLOAD).grounded
    assert verify_answer("A was 70.6% of revenue.", PAYLOAD).grounded


def test_verifier_catches_invented_figures():
    r = verify_answer("Revenue was AED 9,999,999.", PAYLOAD)
    assert not r.grounded
    assert "9,999,999" in r.unmatched


def test_verifier_catches_number_at_end_of_sentence():
    """Regression: the sentence-ending period once hid the figure entirely."""
    assert verify_answer("Revenue was AED 8,888,888.", PAYLOAD).checked == 1


def test_verifier_ignores_years():
    r = verify_answer("In 2026 revenue was AED 1,203,455.", PAYLOAD)
    assert r.grounded and r.skipped >= 1


def test_verifier_is_a_noop_on_empty_answer():
    assert verify_answer("", PAYLOAD).grounding_rate == 1.0


def test_labels_and_prices_follow_the_model_id():
    from gemini_brain.config.constants import HAIKU45_ID, SONNET5_ID, model_label
    from gemini_brain.config.pricing import gemini_brain_cost

    assert model_label(HAIKU45_ID) == "Claude Haiku 4.5"
    assert model_label(SONNET5_ID) == "Claude Sonnet 5"
    assert model_label("anthropic.claude-3-haiku-20240307-v1:0") == "Claude Haiku 3"
    assert gemini_brain_cost(0, 0, 1_000_000, 1_000_000, HAIKU45_ID) == 6.0
    assert gemini_brain_cost(0, 0, 1_000_000, 1_000_000, SONNET5_ID) == 12.0
