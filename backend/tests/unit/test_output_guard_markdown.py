"""strip_emojis must keep Markdown structure: nested list indentation and hard line breaks."""
from gemini_brain.resilience.envelope import normalize_envelope
from gemini_brain.resilience.output_guard import strip_emojis

NESTED = "**How to calculate**  \n- **Step 1.** Find the value [1].  \n- **Step 2.**  \n  - Divide by (1 + margin)\n  - Use last year [1].\n"


def test_text_without_emojis_is_untouched():
    assert strip_emojis(NESTED) == NESTED


def test_an_emoji_is_removed_without_breaking_indentation_or_line_breaks():
    assert strip_emojis("Done \u2705  now\n  - sub item  \nnext") == "Done now\n  - sub item  \nnext"


def test_the_envelope_keeps_nested_lists_and_line_breaks():
    answer = normalize_envelope({"answer": NESTED})["answer"]
    assert "\n  - Divide by" in answer and "**How to calculate**  \n" in answer
