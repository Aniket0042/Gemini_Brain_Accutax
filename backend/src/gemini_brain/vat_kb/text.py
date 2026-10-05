"""PDF text extraction, cleaning (R3) and chunking (R4)."""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# Arabic letters, marks and presentation forms. FTA PDFs print Arabic and English side by
# side; the extracted text interleaves the two, which an English embedding model can't use.
_ARABIC = re.compile(r"[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]")
_ENGLISH_WORD = re.compile(r"[A-Za-z]{2,}")
_SENTENCE_END = re.compile(r"(?<=[.;:?!])\s+(?=[A-Z0-9(•\"“–-])")
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_EFFECTIVE = re.compile(
    rf"(?:effective\s+(?:from|as\s+of|on)|with\s+effect\s+from)\s+(\d{{1,2}}\s+(?:{_MONTHS})\s+\d{{4}})", re.I)


def pdf_text(path: Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def clean(raw: str) -> str:
    """Remove Arabic script, drop lines with no English word left, join into running text."""
    lines = (line.strip() for line in _ARABIC.sub(" ", raw).splitlines())
    return re.sub(r"\s+", " ", " ".join(line for line in lines if _ENGLISH_WORD.search(line))).strip()


def effective_date(text: str) -> str:
    """First 'effective from <date>' / 'with effect from <date>' in the text, e.g. '14 January 2026'."""
    m = _EFFECTIVE.search(text)
    return m.group(1) if m else ""


def chunk(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sentence-aware chunks of about `size` characters. Each chunk after the first starts with
    the last ~`overlap` characters of the previous one, cut at a word boundary."""
    chunks: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > size:                      # one very long sentence: hard cut
            if current:
                chunks.append(current)
                current = ""
            chunks.append(sentence[:size])
            sentence = sentence[size - overlap:]
        if current and len(current) + 1 + len(sentence) > size:
            chunks.append(current)
            tail = current[-overlap:]
            current = (tail.split(" ", 1)[-1] if " " in tail else tail) + " " + sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        chunks.append(current)
    return chunks


def header(title: str, issue_date: str, effective: str) -> str:
    """First line of every chunk, so search and the answer model know the instrument and its dates."""
    parts = [title]
    if issue_date and issue_date.upper() != "NA":
        parts.append(f"issued {issue_date}")
    if effective:
        parts.append(f"effective {effective}")
    return " | ".join(parts)
