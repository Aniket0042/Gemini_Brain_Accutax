from types import SimpleNamespace as NS

from gemini_brain.vat_kb import answer


def _chunk(url, title, pos, body):
    return NS(url=url, title=title, position=pos, text="HEADER\n" + body, doc_type="guide",
              issue_date="2024-01-01", effective_date="", category="")


def test_compact_block_short_rules_few_passages_and_budget():
    hits = [NS(chunk=_chunk(f"u{i % 3}", f"Doc {i % 3}", i, ("rule text %d\n" % i) * 60)) for i in range(12)]
    result = NS(hits=hits, sources=lambda: [h.chunk for h in hits[:3]])
    text = answer.compact_block("Are exports zero-rated?", result, max_passages=6, max_chars=3000)
    assert text.startswith("UAE VAT law") and "cite them as [n]" in text
    assert len(text) < 3000 + len(answer.COMPACT_RULES) + 400
    assert "rule text 6" not in text  # beyond the six best passages
    assert answer.VAT_ANSWER_RULES.strip()[:40] not in text
    assert "[1] Doc 0" in text
