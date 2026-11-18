"""Filing-deadline questions are searched with the law's own wording (Article 64: the 28th day)."""
import pytest

from gemini_brain.vat_kb.expansion import expand


@pytest.mark.parametrize("q", [
    "What is the deadline to file a VAT return?",
    "When is the VAT return due?",
    "By when must we submit our tax return?",
    "How long do I have to file the VAT return?",
])
def test_deadline_questions_get_the_legal_wording(q):
    assert "twenty eighth) day following the end of the Tax Period" in expand(q)


@pytest.mark.parametrize("q", [
    "What's the penalty for filing a VAT return late?",
    "What is the penalty for late VAT payment?",
    "Are exports of goods zero-rated in the UAE?",
    "What is the mandatory VAT registration threshold?",
])
def test_other_questions_are_not_pulled_toward_the_deadline(q):
    assert "twenty eighth" not in expand(q)
