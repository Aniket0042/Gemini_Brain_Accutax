"""Unit tests for JSON extraction helper."""
from gemini_brain.utils.json_parser import extract_json


def test_extract_json_markdown():
    text = '```json\n{"type": 4, "reason": "test"}\n```'
    parsed = extract_json(text)
    assert parsed == {"type": 4, "reason": "test"}


def test_extract_json_raw():
    text = '{"type": 1}'
    parsed = extract_json(text)
    assert parsed == {"type": 1}


def test_extract_json_substring():
    text = 'Preamble text... {"type": 2, "reason": "ok"} postscript'
    parsed = extract_json(text)
    assert parsed == {"type": 2, "reason": "ok"}
